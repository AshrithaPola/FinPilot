"""Create the demo sign-ins: three synthetic bank customers and one bank staff admin. Idempotent.

    backend/.venv/Scripts/python scripts/neon.py run -- python ../scripts/setup_demo_users.py

Each account is a Firebase email/password user on the reserved .test
domain (never a real person's address) and a row in `users`, in the bank's
org:

    demo-cust-0036@finpilot.test   customer cust-0036 (retail: the $15 overdraft, late card payments)
    demo-cust-0019@finpilot.test   customer cust-0019 (premier: brokerage holdings, late payments)
    demo-cust-0011@finpilot.test   customer cust-0011 (student)
    demo-staff@finpilot.test       bank staff, admin, clearance 2 (staff-only documents, /admin/traces)

All four share one password, DEMO_PASSWORD in the repo-root .env (gitignored):
generated and written there on the first run, reused after, never printed.
Runs as the owner (MIGRATION_DATABASE_URL), like the migrate step: the app
role can only read `users`. Firebase is reached with the Admin SDK
(FIREBASE_PROJECT_ID, GOOGLE_APPLICATION_CREDENTIALS from .env). Run it
again to reset an account that was changed: it re-applies the mapping
below and the shared password.
"""
import os
import secrets
import sys
from pathlib import Path

import sqlalchemy as sa

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from bank.synthetic import BANK_ORG  # noqa: E402
from config import sqlalchemy_url  # noqa: E402

DOMAIN = "@finpilot.test"
# email local part -> (role, clearance, customer_id)
DEMO = {
    "demo-cust-0036": ("viewer", 0, "cust-0036"),
    "demo-cust-0019": ("viewer", 0, "cust-0019"),
    "demo-cust-0011": ("viewer", 0, "cust-0011"),
    "demo-staff": ("admin", 2, None),
}


def demo_password(env_file: Path = REPO / ".env") -> str:
    """DEMO_PASSWORD from .env, or a new one appended there."""
    from dotenv import dotenv_values

    password = dotenv_values(env_file).get("DEMO_PASSWORD")
    if not password:
        password = secrets.token_urlsafe(12)
        with env_file.open("a", encoding="utf-8") as f:
            f.write(f"\n# Demo sign-ins (scripts/setup_demo_users.py)\nDEMO_PASSWORD={password}\n")
    return password


def upsert_users(owner_url: str, uids: dict[str, str]) -> None:
    """`uids`: email local part -> Firebase uid. One active user row each."""
    engine = sa.create_engine(sqlalchemy_url(owner_url))
    try:
        with engine.begin() as conn:
            for local, uid in uids.items():
                role, clearance, customer = DEMO[local]
                conn.execute(sa.text("""
                    INSERT INTO users (firebase_uid, org_id, role, clearance, customer_id, active)
                    VALUES (:uid, :org, :role, :clearance, :customer, true)
                    ON CONFLICT (firebase_uid) DO UPDATE SET org_id = EXCLUDED.org_id, role = EXCLUDED.role,
                        clearance = EXCLUDED.clearance, customer_id = EXCLUDED.customer_id, active = true
                """), {"uid": uid, "org": BANK_ORG, "role": role, "clearance": clearance, "customer": customer})
    finally:
        engine.dispose()


def firebase_accounts(project: str, key_path: str, password: str) -> dict[str, str]:
    """Create (or reset the password of) each demo account; returns their uids."""
    import firebase_admin
    from firebase_admin import auth, credentials

    try:
        app = firebase_admin.get_app("setup-demo-users")
    except ValueError:
        app = firebase_admin.initialize_app(credentials.Certificate(key_path), {"projectId": project},
                                            name="setup-demo-users")
    uids = {}
    for local in DEMO:
        email = local + DOMAIN
        try:
            user = auth.get_user_by_email(email, app=app)
            auth.update_user(user.uid, password=password, disabled=False, app=app)
        except auth.UserNotFoundError:
            user = auth.create_user(email=email, password=password, app=app)
        uids[local] = user.uid
    return uids


def main() -> int:
    from dotenv import dotenv_values

    env = {**dotenv_values(REPO / ".env"), **{k: v for k, v in os.environ.items() if v}}
    owner = os.environ.get("MIGRATION_DATABASE_URL")
    key_path = env.get("GOOGLE_APPLICATION_CREDENTIALS") or ""
    if not owner:
        print("Run through scripts/neon.py run (it sets MIGRATION_DATABASE_URL, the owner).", file=sys.stderr)
        return 2
    if not key_path or Path(key_path).resolve().is_relative_to(REPO):
        print("GOOGLE_APPLICATION_CREDENTIALS must point to a key file outside the repo.", file=sys.stderr)
        return 2
    uids = firebase_accounts(env.get("FIREBASE_PROJECT_ID") or "", key_path, demo_password())
    upsert_users(owner, uids)
    for local, (role, clearance, customer) in DEMO.items():
        who = f"customer {customer}" if customer else f"staff {role}, clearance {clearance}"
        print(f"{local}{DOMAIN}: {who}")
    print("Password: DEMO_PASSWORD in .env (not printed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
