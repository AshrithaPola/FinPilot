"""/docs, /redoc and /openapi.json answer locally but 404 on Cloud Run (which sets K_SERVICE)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
# A fresh interpreter: main builds the app at import, so the env must be set first.
# No `with TestClient`: the lifespan (Firebase, database) never runs.
PROBE = """
import json, main
from fastapi.testclient import TestClient
client = TestClient(main.app)
print(json.dumps({p: client.get(p).status_code for p in ("/docs", "/redoc", "/openapi.json")}))
"""


def _statuses(k_service):
    env = {k: v for k, v in os.environ.items() if k != "K_SERVICE"}
    if k_service:
        env["K_SERVICE"] = k_service
    out = subprocess.run([sys.executable, "-c", PROBE], cwd=BACKEND, env=env,
                         capture_output=True, text=True, check=True).stdout
    return set(json.loads(out.splitlines()[-1]).values())


@pytest.mark.parametrize("k_service,expected", [(None, {200}), ("finpilot-backend", {404})])
def test_api_docs_only_off_cloud_run(k_service, expected):
    assert _statuses(k_service) == expected
