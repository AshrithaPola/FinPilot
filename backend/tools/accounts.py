"""Accounts tools: the signed-in customer's own accounts, cards, transactions,
fees and holdings, for the financial-data agent (the Phase 6 executor calls
them by name with the planner's arguments).

Each layer below would stop a cross-customer read on its own:

  - No tool takes a customer id. The customer is the caller
    (auth.Principal.customer_id), never an argument; staff and anonymous
    callers have none and are refused before any SQL.
  - Arguments are validated by strict models: an unknown argument (say,
    `customer_id`) is refused, ids must look like the bank's ids (lowercase
    letters, digits, hyphens), dates are dates, limits are capped.
  - Every query is parameterized; no argument is ever formatted into SQL.
  - Each call runs in a READ ONLY transaction bound to the caller
    (set_access: app.customer_id), as finpilot_app, which has SELECT only on
    customer data and is under forced row-level security: another
    customer's rows don't exist for it. An id that isn't the caller's
    returns nothing, the same as an id that doesn't exist, so nothing leaks
    about other customers.

Money is integer cents (negative = money out, or owed on a card);
quantities are strings, so no float rounding. A balance is the account's
starting balance plus its transactions minus its fees that weren't waived,
as of its last posted activity; other arithmetic is the calculator's job.
"""
import re
from datetime import date
from typing import Annotated, Literal

import sqlalchemy as sa
from pydantic import Field, field_validator, model_validator

from tools.common import AccessRefused, Args, NoArgs, Tool, ToolError, read_only, rows, validate

__all__ = ["TOOLS", "AccountsTools", "ToolError", "read_only"]

Id = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
Limit = Annotated[int, Field(ge=1, le=200)]
FeeType = Literal["overdraft", "nsf", "monthly_maintenance", "atm", "foreign_transaction", "wire",
                  "late_payment"]


AccountType = Literal["checking", "savings", "credit", "brokerage"]


class BalanceArgs(Args):
    account_id: Id | None = None
    account_type: AccountType | None = None  # the planner knows "checking", not the account's id


class _Period(Args):
    account_id: Id | None = None
    start: date | None = None
    end: date | None = None
    limit: Limit = 50

    @model_validator(mode="after")
    def _ordered(self):
        if self.start and self.end and self.start > self.end:
            raise ValueError("start must be on or before end")
        return self


class TransactionArgs(_Period):
    category: Annotated[str, Field(pattern=r"^[a-z_]{1,32}$")] | None = None


FEE_TYPE_ALIASES = {"late": "late_payment", "late_fee": "late_payment", "late_payment_fee": "late_payment",
                    "maintenance": "monthly_maintenance", "monthly": "monthly_maintenance",
                    "monthly_fee": "monthly_maintenance", "maintenance_fee": "monthly_maintenance",
                    "foreign": "foreign_transaction", "fx": "foreign_transaction",
                    "foreign_fee": "foreign_transaction", "foreign_transaction_fee": "foreign_transaction",
                    "atm_fee": "atm", "overdraft_fee": "overdraft", "od": "overdraft",
                    "returned_item": "nsf", "nsf_fee": "nsf", "wire_fee": "wire"}


class FeeArgs(_Period):
    fee_type: FeeType | None = None
    include_waived: bool = True

    @field_validator("fee_type", mode="before")
    @classmethod
    def _alias(cls, value):
        """Common names for a fee type ("late", "late fee", "maintenance") as the bank's
        own; anything else is left for the strict check to refuse."""
        if not isinstance(value, str):
            return value
        key = re.sub(r"[\s-]+", "_", value.strip().lower())
        return FEE_TYPE_ALIASES.get(key, key)


def _accounts(conn, args: NoArgs) -> list[dict]:
    return rows(conn, "SELECT account_id, type, product, opened_on, status FROM accounts "
                       "ORDER BY opened_on, account_id")


def _cards(conn, args: NoArgs) -> list[dict]:
    return rows(conn, "SELECT card_id, account_id, last4, network, status, expires_on FROM cards "
                       "ORDER BY card_id")


def _transactions(conn, args: TransactionArgs) -> list[dict]:
    return rows(conn, """
        SELECT txn_id, account_id, card_id, posted_on, amount_cents, merchant, category, description
        FROM transactions
        WHERE (CAST(:account_id AS text) IS NULL OR account_id = :account_id)
          AND (CAST(:start AS date) IS NULL OR posted_on >= :start)
          AND (CAST(:end AS date) IS NULL OR posted_on <= :end)
          AND (CAST(:category AS text) IS NULL OR category = :category)
        ORDER BY posted_on DESC, txn_id DESC
        LIMIT :limit
    """, args.model_dump())


def _fees(conn, args: FeeArgs) -> list[dict]:
    return rows(conn, """
        SELECT fee_id, account_id, txn_id, fee_type, amount_cents, assessed_on, waived
        FROM fees
        WHERE (CAST(:account_id AS text) IS NULL OR account_id = :account_id)
          AND (CAST(:start AS date) IS NULL OR assessed_on >= :start)
          AND (CAST(:end AS date) IS NULL OR assessed_on <= :end)
          AND (CAST(:fee_type AS text) IS NULL OR fee_type = :fee_type)
          AND (:include_waived OR NOT waived)
        ORDER BY assessed_on DESC, fee_id DESC
        LIMIT :limit
    """, args.model_dump())


def _balance(conn, args: BalanceArgs) -> list[dict]:
    return rows(conn, """
        SELECT a.account_id, a.type, a.product,
               CAST(a.starting_balance_cents + coalesce(t.total, 0) - coalesce(f.total, 0) AS bigint)
                   AS balance_cents,  -- sum() of bigint is numeric; cents stay integers
               greatest(a.starting_balance_on, t.last, f.last) AS as_of,
               a.starting_balance_cents, a.starting_balance_on
        FROM accounts a
        LEFT JOIN (SELECT account_id, sum(amount_cents) AS total, max(posted_on) AS last
                   FROM transactions GROUP BY account_id) t USING (account_id)
        LEFT JOIN (SELECT account_id, sum(amount_cents) FILTER (WHERE NOT waived) AS total,
                          max(assessed_on) AS last
                   FROM fees GROUP BY account_id) f USING (account_id)
        WHERE (CAST(:account_id AS text) IS NULL OR a.account_id = :account_id)
          AND (CAST(:account_type AS text) IS NULL OR a.type = :account_type)
        ORDER BY a.account_id
    """, args.model_dump())


def _holdings(conn, args: NoArgs) -> list[dict]:
    return rows(conn, """
        SELECT h.account_id, h.symbol, s.name, s.type, h.quantity, h.cost_basis_cents
        FROM holdings h JOIN securities s USING (symbol)
        ORDER BY h.account_id, h.symbol
    """)


TOOLS = {t.name: t for t in (
    Tool("list_accounts", "The customer's accounts: type, product, opened date, status.", NoArgs, _accounts),
    Tool("get_balance", "Current balance of each of the customer's accounts, or of one account_type "
         "(checking, savings, credit, brokerage) or account_id, in cents, as of its last posted activity; "
         "negative means overdrawn, or owed on a card.",
         BalanceArgs, _balance),
    Tool("list_cards", "The customer's cards: last 4 digits, network, status, expiry.", NoArgs, _cards),
    Tool("list_transactions", "The customer's transactions, newest first (amount_cents negative = money "
         "out); optional account, date range (start, end), category (groceries, dining, transport, shopping, "
         "health, home, travel, housing, subscriptions, income, transfer, cash, card_payment, payment, "
         "interest) and limit (at most 200).", TransactionArgs, _transactions),
    Tool("list_fees", "Fees charged to the customer, newest first; optional account, date range, "
         "fee type, include_waived and limit (at most 200).", FeeArgs, _fees),
    Tool("list_holdings", "The customer's investment holdings: symbol, name, quantity, cost basis.",
         NoArgs, _holdings),
)}


class AccountsTools:
    """Runs the tools above for one caller at a time, on the app's connection
    (finpilot_app; the store refuses to start on anything that bypasses RLS)."""

    def __init__(self, engine: sa.Engine):
        self._engine = engine

    def call(self, principal, name: str, arguments: dict | None = None) -> list[dict]:
        if principal is None or not getattr(principal, "customer_id", None):
            raise AccessRefused("Account tools are only for signed-in bank customers.")
        tool, args = validate(TOOLS, name, arguments)
        with read_only(self._engine, principal) as conn:
            return tool.run(conn, args)

    def first_name(self, principal) -> str | None:
        """The signed-in customer's first name, for the UI's greeting (not a
        planner tool). Same layers: the caller's own row only, under RLS."""
        if principal is None or not getattr(principal, "customer_id", None):
            raise AccessRefused("Account tools are only for signed-in bank customers.")
        with read_only(self._engine, principal) as conn:
            found = rows(conn, "SELECT full_name FROM customers")
        return found[0]["full_name"].split()[0] if found else None
