"""Holdings: purchased_on, the month-end whose real close set the cost basis
(bank/synthetic.py load). Nullable, so rows loaded before it stay valid.

Revision ID: 0015
Revises: 0014
"""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE holdings ADD COLUMN purchased_on date")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE holdings DROP COLUMN purchased_on")
