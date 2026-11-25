"""scripts/gcp_deploy.sh and gcp_teardown.sh --apply against a fake gcloud: never the real one.

FINPILOT_FAKE_GCLOUD puts the scripts in test mode, where they refuse to run
unless the gcloud on PATH is tests/fixtures/fake_gcloud (scripts/gcp_test_mode.sh);
the deploy also needs ENV_FILE to be a test .env (never the repo's real one).
"""
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
FAKE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_gcloud"
_GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
# On Windows, `bash` on PATH may be WSL's, which can't run this; use Git's.
BASH = str(_GIT_BASH) if sys.platform == "win32" else shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH or not Path(BASH).exists(), reason="needs bash")

ENV = {
    "NEON_APP_DATABASE_URL": "postgresql://app@db/x",
    "GEMINI_API_KEY": "eval-key",
    "DEPLOY_GEMINI_API_KEY": "deploy-key",
    "DEPLOY_GEMINI_PROJECT_ID": "key-proj",  # the fake answers its billing per FAKE_KEY_BILLING
    "OBSERVABILITY_SALT": "salt",
    "LANGFUSE_PUBLIC_KEY": "pk",
    "LANGFUSE_SECRET_KEY": "sk",
    "LANGFUSE_BASE_URL": "https://us.cloud.langfuse.com",
    "FIREBASE_PROJECT_ID": "fb-proj",
    "VITE_FIREBASE_API_KEY": "web",
    "VITE_FIREBASE_AUTH_DOMAIN": "fb-proj.firebaseapp.com",
    "VITE_FIREBASE_PROJECT_ID": "fb-proj",
    "VITE_FIREBASE_APP_ID": "app",
}


def _exe(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _deploy(tmp_path, *, env_overrides=None, decoy_first=False, env_file=True, script="gcp_deploy.sh"):
    key = tmp_path / "firebase-key.json"
    key.write_text('{"type": "service_account"}')
    values = {**ENV, "GOOGLE_APPLICATION_CREDENTIALS": key.as_posix(), **(env_overrides or {})}
    (tmp_path / "test.env").write_text("".join(f"{k}={v}\n" for k, v in values.items()), newline="\n")
    fake = _exe(tmp_path / "fake" / "gcloud", FAKE.read_text())
    # Stands in for the real gcloud: if anything runs it, the test sees the file.
    decoy = _exe(tmp_path / "real" / "gcloud", f"#!/usr/bin/env bash\ntouch '{(tmp_path / 'REAL-CALLED').as_posix()}'\n")
    dirs = [decoy.parent, fake.parent] if decoy_first else [fake.parent]
    env = {**os.environ, "PROJECT_ID": "test-proj", "BIND_WAIT_S": "0",
           "FAKE_LOG": (tmp_path / "gcloud.log").as_posix(),
           "PATH": os.pathsep.join([*map(str, dirs), os.environ["PATH"]])}
    env["FINPILOT_FAKE_GCLOUD"] = fake.as_posix()
    if env_file:
        env["ENV_FILE"] = (tmp_path / "test.env").as_posix()
    result = subprocess.run([BASH, f"scripts/{script}", "--apply"], cwd=REPO, env=env,
                            capture_output=True, text=True, timeout=120)
    log = tmp_path / "gcloud.log"
    return result, log.read_text() if log.exists() else ""


@pytest.mark.parametrize("script", ["gcp_deploy.sh", "gcp_teardown.sh"])
def test_test_mode_refuses_when_another_gcloud_comes_first(tmp_path, script):
    result, log = _deploy(tmp_path, decoy_first=True, script=script)
    assert result.returncode == 2, result.stderr
    assert "not the fake" in result.stderr
    assert not (tmp_path / "REAL-CALLED").exists() and log == ""


def test_test_mode_refuses_the_real_env_file(tmp_path):
    result, log = _deploy(tmp_path, env_file=False)
    assert result.returncode == 2 and "test .env" in result.stderr
    assert log == ""


def test_apply_against_the_fake(tmp_path):
    result, log = _deploy(tmp_path)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "REAL-CALLED").exists()
    assert "exists, kept" in result.stdout  # creates that say "already exists"
    assert "unchanged, kept: finpilot-database-url" in result.stdout
    assert result.stderr.count("IAM binding not accepted yet") == 2  # the fake refuses twice
    assert "data: deploy-key" in log and "eval-key" not in log
    assert "BACKEND_URL=https://finpilot-backend-abc123-uc.a.run.app" in log
    assert "billing budgets create" in log
    assert "finpilot-frontend-abc123-uc.a.run.app" in result.stdout  # the host to authorize


def test_an_existing_budget_is_not_duplicated(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_BUDGET_EXISTS", "1")
    result, log = _deploy(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "billing budgets create" not in log


def test_the_eval_key_is_refused_before_any_gcloud_call(tmp_path):
    result, log = _deploy(tmp_path, env_overrides={"DEPLOY_GEMINI_API_KEY": "eval-key"})
    assert result.returncode == 1 and "different from the eval key" in result.stderr
    assert log == ""


def test_the_key_project_must_not_be_the_deploy_project(tmp_path):
    result, log = _deploy(tmp_path, env_overrides={"DEPLOY_GEMINI_PROJECT_ID": "test-proj"})
    assert result.returncode == 1 and "other than PROJECT_ID" in result.stderr
    assert log == ""


def test_billing_on_for_the_key_project_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KEY_BILLING", "on")
    result, log = _deploy(tmp_path)
    assert result.returncode == 1 and "Billing is on for key-proj" in result.stderr
    assert "create" not in log and "enable" not in log  # stopped before creating anything


@pytest.mark.parametrize("override,code", [(None, 1), ("yes", 0)])
def test_unreadable_key_project_billing_needs_the_override(tmp_path, monkeypatch, override, code):
    monkeypatch.setenv("FAKE_KEY_BILLING", "denied")
    monkeypatch.delenv("DEPLOY_KEY_PROJECT_UNBILLED", raising=False)
    if override:
        monkeypatch.setenv("DEPLOY_KEY_PROJECT_UNBILLED", override)
    result, log = _deploy(tmp_path)
    assert result.returncode == code, result.stderr
    assert "Could not read billing for key-proj" in result.stderr and "PERMISSION_DENIED" in result.stderr
    assert ("run deploy" in log) == bool(override)


def test_teardown_against_the_fake(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_BUDGET_EXISTS", "1")
    result, log = _deploy(tmp_path, script="gcp_teardown.sh")
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "REAL-CALLED").exists()
    for gone in ("run services delete finpilot-backend", "run services delete finpilot-frontend",
                 "artifacts repositories delete finpilot", "secrets delete finpilot-gemini-api-key",
                 "iam service-accounts delete finpilot-backend@test-proj",
                 "billing budgets delete billingAccounts/x/budgets/y"):
        assert gone in log, gone
