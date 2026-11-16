"""Accounts tools refuse bad calls before any SQL runs (engine=None would
crash if they reached it): no customer, unknown tools, a smuggled
customer_id, injection in ids and categories, oversized limits."""
import pytest

from db.access import Access
from tools.accounts import TOOLS, AccountsTools, ToolError

CUSTOMER = Access(org_id="finpilot-bank", clearance=0, customer_id="cust-0001")
STAFF = Access(org_id="finpilot-bank", clearance=2, role="admin")
tools = AccountsTools(engine=None)


@pytest.mark.parametrize("principal", [None, STAFF])
def test_only_a_customer_can_call(principal):
    with pytest.raises(ToolError, match="only for signed-in bank customers"):
        tools.call(principal, "list_accounts")


@pytest.mark.parametrize("name", ["delete_transactions", "update_account", "list_accounts; DROP TABLE x", ""])
def test_there_is_no_tool_but_the_read_tools(name):
    with pytest.raises(ToolError, match="No such tool"):
        tools.call(CUSTOMER, name)
    assert set(TOOLS) == {"list_accounts", "get_balance", "list_cards", "list_transactions", "list_fees",
                          "list_holdings"}  # all read-only


@pytest.mark.parametrize("name, arguments", [
    ("list_accounts", {"customer_id": "cust-0002"}),          # the caller can't pick the customer
    ("get_balance", {"customer_id": "cust-0002"}),
    ("get_balance", {"account_id": "cust-0002-chk' UNION SELECT * FROM accounts --"}),
    ("list_transactions", {"customer_id": "cust-0002", "account_id": "cust-0002-chk"}),
    ("list_transactions", {"account_id": "cust-0001-chk' OR '1'='1"}),
    ("list_transactions", {"account_id": "x; DROP TABLE transactions"}),
    ("list_transactions", {"category": "groceries'; DELETE FROM fees; --"}),
    ("list_transactions", {"category": "GROCERIES"}),
    ("list_fees", {"fee_type": "overdraft' OR 1=1 --"}),
    ("list_transactions", {"limit": 10_000}),
    ("list_transactions", {"limit": 0}),
    ("list_transactions", {"start": "2026-03-01", "end": "2026-02-01"}),
    ("list_transactions", {"start": "yesterday"}),
])
def test_bad_arguments_are_refused_before_sql(name, arguments):
    with pytest.raises(ToolError, match=f"Invalid arguments for {name}"):
        tools.call(CUSTOMER, name, arguments)
