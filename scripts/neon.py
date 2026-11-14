"""Set up FinPilot on Neon, or run a backend command against it. Prints no URL.

    backend/.venv/Scripts/python scripts/neon.py setup
    backend/.venv/Scripts/python scripts/neon.py run -- python -m bank.corpus load
    backend/.venv/Scripts/python scripts/neon.py --branch setup     # the Neon branch
    backend/.venv/Scripts/python scripts/neon.py --branch run -- python -m eval.branch_experiment run

Reads the repo-root .env: NEON_MIGRATION_DATABASE_URL (the direct owner URL) and
NEON_POOLED_DATABASE_URL (the pooled host). `setup` first writes
NEON_APP_DATABASE_URL there if it is missing: finpilot_app on the pooled
host, with a fresh random password. Then it runs the migrate step, which
migrates the schema, creates finpilot_app and sets that password.

`run` maps the Neon URLs onto the names the code reads
(MIGRATION_DATABASE_URL = owner, DATABASE_URL = finpilot_app) and runs the
command in backend/. Neon's postgresql+psycopg:// scheme is handled by
config.sqlalchemy_url.
"""
import secrets
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
ENV = BACKEND.parent / ".env"  # the repo's one env file


def _values() -> dict:
    from dotenv import dotenv_values

    return dotenv_values(ENV)


def _urls(branch: bool) -> tuple[str, str]:
    """(owner URL, finpilot_app URL), on the main branch or on the Neon branch
    NEON_BRANCH_DATABASE_URL (its owner URL; finpilot_app there has the same
    name and password, copied with the branch and set again by the migrate step)."""
    from sqlalchemy import make_url

    values = _values()
    if not branch:
        return values["NEON_MIGRATION_DATABASE_URL"], values["NEON_APP_DATABASE_URL"]
    owner, app = make_url(values["NEON_BRANCH_DATABASE_URL"]), make_url(values["NEON_APP_DATABASE_URL"])
    return (owner.render_as_string(hide_password=False),
            owner.set(username=app.username, password=app.password).render_as_string(hide_password=False))


def ensure_app_url() -> None:
    """Add NEON_APP_DATABASE_URL to .env unless it is there."""
    from sqlalchemy import make_url

    values = _values()
    if values.get("NEON_APP_DATABASE_URL"):
        return
    app = make_url(values["NEON_POOLED_DATABASE_URL"]).set(
        username="finpilot_app", password=secrets.token_urlsafe(32))
    with ENV.open("a", encoding="utf-8") as f:
        f.write(f"\nNEON_APP_DATABASE_URL={app.render_as_string(hide_password=False)}\n")
    print("Added NEON_APP_DATABASE_URL to .env (finpilot_app, new random password).")


def run(command: list[str], branch: bool = False) -> int:
    import os

    values = _values()
    needed = ["NEON_APP_DATABASE_URL", "NEON_BRANCH_DATABASE_URL" if branch else "NEON_MIGRATION_DATABASE_URL"]
    missing = [k for k in needed if not values.get(k)]
    if missing:
        print(f".env lacks {', '.join(missing)} (run `neon.py setup` first for the app URL).", file=sys.stderr)
        return 2
    owner, app = _urls(branch)
    env = dict(os.environ, MIGRATION_DATABASE_URL=owner, DATABASE_URL=app)
    if command[0] == "python":  # this interpreter (the venv), found the same way on every OS
        command = [sys.executable, *command[1:]]
    return subprocess.call(command, env=env, cwd=BACKEND)


def main() -> int:
    args = sys.argv[1:]
    branch = "--branch" in args[:2]
    args = [a for i, a in enumerate(args) if not (i < 2 and a == "--branch")]
    if args[:1] == ["setup"]:
        if not branch:
            ensure_app_url()
        return run(["python", "migrate.py"], branch)
    if args[:1] == ["run"] and len(args) > 1:
        return run(args[2:] if args[1] == "--" else args[1:], branch)
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
