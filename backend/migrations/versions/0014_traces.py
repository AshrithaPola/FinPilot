"""Traces: one row per /ask request (Phase 8, tracing.py).

The request's summary (outcome, total time, LLM calls, estimated cost) in
columns for the admin list, and every span (nodes, steps, tool calls,
searches, LLM and embedding calls with tokens, costs and retries, the
validators' verdicts) as jsonb. Under RLS by org like the query log: the app
role writes into its caller's org and an admin reads only their own org's;
a customer's context sees only that customer's traces.
The question is the redacted one the engine saw.

Postgres only, like the other non-ORM tables: the SQLite log tests never
trace.

Revision ID: 0014
Revises: 0013
"""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

APP_ROLE = "finpilot_app"
# The org's traces; a bank customer's context sees only their own, so the
# per-customer leakage check holds here too (the app reads traces only for
# admins, who have no customer context).
POLICY = ("org_id = nullif(current_setting('app.org_id', true), '') AND "
          "(nullif(current_setting('app.customer_id', true), '') IS NULL "
          "OR customer_id = current_setting('app.customer_id', true))")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("""
        CREATE TABLE traces (
            trace_id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT now(),
            org_id text NOT NULL,
            user_id text,
            customer_id text,
            endpoint text NOT NULL,
            question text NOT NULL,
            outcome text,
            total_ms double precision,
            llm_calls integer,
            est_cost_usd numeric(12, 8),
            demo_today date,
            summary jsonb NOT NULL DEFAULT '{}',
            spans jsonb NOT NULL DEFAULT '[]'
        )""")
    op.execute("CREATE INDEX ix_traces_org_created ON traces (org_id, created_at DESC)")
    op.execute("ALTER TABLE traces ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY org_access ON traces USING ({POLICY}) WITH CHECK ({POLICY})")
    op.execute(f"GRANT SELECT, INSERT ON traces TO {APP_ROLE}")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TABLE traces")
