"""Starting balances, and the market data the market tools read.

accounts.starting_balance_cents / starting_balance_on: each account's
balance on the day the synthetic data starts. The current balance is that
plus the account's transactions minus its fees that weren't waived
(tools/accounts.py get_balance). Same table, same per-customer policy.

company_facts: annual figures from SEC filings (data.sec.gov company facts:
revenue, net income, EPS...), one row per company, concept, unit and fiscal
year, with the form, filing date and accession number they came from.
rate_observations: daily values of FRED series (prime rate, fed funds,
10-year Treasury). Both are public reference data, the same for everyone,
like securities and prices: no customer_id, no clearance, no RLS; the app
role reads them only. Fetched values live here, never in the repo.

Revision ID: 0013
Revises: 0012
"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

APP_ROLE = "finpilot_app"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("""
        ALTER TABLE accounts
            ADD COLUMN starting_balance_cents bigint NOT NULL DEFAULT 0,
            ADD COLUMN starting_balance_on    date
    """)
    op.execute("""
        CREATE TABLE company_facts (
            ticker        text NOT NULL,
            cik           integer NOT NULL,
            metric        text NOT NULL,   -- revenue, net_income, ... (bank/market.py METRICS)
            concept       text NOT NULL,   -- the us-gaap concept it came from
            label         text NOT NULL,
            unit          text NOT NULL,
            fiscal_year   integer NOT NULL,
            period_end    date NOT NULL,
            value         numeric NOT NULL,
            form          text NOT NULL,
            filed         date NOT NULL,
            accession     text NOT NULL,
            PRIMARY KEY (ticker, metric, unit, fiscal_year)
        );
        CREATE TABLE rate_observations (
            series_id text NOT NULL,
            obs_date  date NOT NULL,
            value     numeric NOT NULL,
            PRIMARY KEY (series_id, obs_date)
        );
    """)
    op.execute(f"GRANT SELECT ON company_facts, rate_observations TO {APP_ROLE}")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TABLE rate_observations, company_facts")
    op.execute("ALTER TABLE accounts DROP COLUMN starting_balance_cents, DROP COLUMN starting_balance_on")
