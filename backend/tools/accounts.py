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

Money is integer cents (negative = money out); quantities are strings, so
no float rounding. There are no balances: the data holds no opening
balances, so a total would be invented; arithmetic is the calculator's job.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Callable, Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from db.access import set_access

Id = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
Limit = Annotated[int, Field(ge=1, le=200)]
FeeType = Literal["overdraft", "nsf", "monthly_maintenance", "atm", "foreign_transaction", "wire",
                  "late_payment"]


class ToolError(Exception):
    """A refused call: the message is safe to show (it names no other customer's data)."""


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class _Period(_Args):
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


class FeeArgs(_Period):
    fee_type: FeeType | None = None
    include_waived: bool = True


def _rows(conn, sql: str, params: dict | None = None) -> list[dict]:
    out = []
    for row in conn.execute(sa.text(sql), params or {}).mappings():
        out.append({k: v.isoformat() if isinstance(v, date) else str(v) if k == "quantity" else v
                    for k, v in row.items()})
    return out


def _accounts(conn, args: NoArgs) -> list[dict]:
    return _rows(conn, "SELECT account_id, type, product, opened_on, status FROM accounts "
                       "ORDER BY opened_on, account_id")


def _cards(conn, args: NoArgs) -> list[dict]:
    return _rows(conn, "SELECT card_id, account_id, last4, network, status, expires_on FROM cards "
                       "ORDER BY card_id")


def _transactions(conn, args: TransactionArgs) -> list[dict]:
    return _rows(conn, """
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
    return _rows(conn, """
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


def _holdings(conn, args: NoArgs) -> list[dict]:
    return _rows(conn, """
        SELECT h.account_id, h.symbol, s.name, s.type, h.quantity, h.cost_basis_cents
        FROM holdings h JOIN securities s USING (symbol)
        ORDER BY h.account_id, h.symbol
    """)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str  # for the planner (Phase 6)
    args: type[_Args]
    run: Callable


TOOLS = {t.name: t for t in (
    Tool("list_accounts", "The customer's accounts: type, product, opened date, status.", NoArgs, _accounts),
    Tool("list_cards", "The customer's cards: last 4 digits, network, status, expiry.", NoArgs, _cards),
    Tool("list_transactions", "The customer's transactions, newest first; optional account, date "
         "range (start, end), category and limit (at most 200).", TransactionArgs, _transactions),
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
            raise ToolError("Account tools are only for signed-in bank customers.")
        tool = TOOLS.get(name)
        if tool is None:
            raise ToolError(f"No such tool: {name!r}.")
        try:
            args = tool.args.model_validate(arguments or {})
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}"
                                 for e in exc.errors())
            raise ToolError(f"Invalid arguments for {name}: {problems}") from None
        with read_only(self._engine, principal) as conn:
            return tool.run(conn, args)


@contextmanager
def read_only(engine: sa.Engine, principal):
    """A READ ONLY transaction bound to the caller: a write fails in Postgres
    even if a tool ever tried one."""
    with engine.begin() as conn:
        conn.execute(sa.text("SET TRANSACTION READ ONLY"))  # must come before any query
        set_access(conn, principal)
        yield conn
