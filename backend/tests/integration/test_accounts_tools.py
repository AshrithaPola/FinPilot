"""Accounts tools against real Postgres, as finpilot_app under RLS, with the
attacks a planner (or a prompt injection steering it) could try: another
customer's ids, hostile text that passes validation, and writes. Skipped
without TEST_POSTGRES_URL."""
import pytest
import sqlalchemy as sa

from bank import synthetic
from db.access import Access, set_access
from tests.integration.test_corpus_load import _store
from tools.accounts import AccountsTools, ToolError, read_only

ME = Access(org_id=synthetic.BANK_ORG, clearance=0, customer_id="cust-0001")
OTHER = Access(org_id=synthetic.BANK_ORG, clearance=0, customer_id="cust-0002")
EVERY_TOOL = ["list_accounts", "get_balance", "list_cards", "list_transactions", "list_fees", "list_holdings"]
OWNED = ["accounts", "cards", "transactions", "fees", "holdings"]


@pytest.fixture
def setup(pg_url, app_url):
    _store(pg_url, app_url)  # migrates, as the owner
    synthetic.load(pg_url, synthetic.generate(customers=3))
    owner = sa.create_engine(pg_url)
    yield AccountsTools(sa.create_engine(app_url)), owner
    owner.dispose()


def _counts(owner) -> dict:
    with owner.connect() as conn:  # the test database's owner bypasses RLS: true totals
        return {t: conn.execute(sa.text(f"SELECT count(*) FROM {t}")).scalar() for t in OWNED}


def test_a_customer_sees_only_their_own_rows(setup):
    tools, _ = setup
    for name in EVERY_TOOL:
        rows = tools.call(ME, name, {"limit": 200} if name in ("list_transactions", "list_fees") else {})
        ids = [r.get("account_id") for r in rows]
        assert all(i.startswith("cust-0001-") for i in ids), name
    assert tools.call(ME, "list_accounts") and tools.call(ME, "list_transactions")


def test_another_customers_ids_return_nothing(setup):
    tools, owner = setup
    theirs = "cust-0002-chk"
    with owner.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM transactions WHERE account_id = :a"),
                            {"a": theirs}).scalar() > 0  # it exists; it just isn't ours
    assert tools.call(ME, "list_transactions", {"account_id": theirs}) == []
    assert tools.call(ME, "list_fees", {"account_id": theirs}) == []
    mine = {r["txn_id"] for r in tools.call(ME, "list_transactions", {"limit": 200})}
    theirs_seen = {r["txn_id"] for r in tools.call(OTHER, "list_transactions", {"limit": 200})}
    assert mine and theirs_seen and not mine & theirs_seen  # one tool object, two callers, no overlap


def test_hostile_arguments_change_nothing(setup):
    tools, owner = setup
    before = _counts(owner)
    for arguments in ({"account_id": "cust-0001-chk' OR '1'='1"}, {"category": "x'; DELETE FROM fees; --"},
                      {"customer_id": "cust-0002"}):
        with pytest.raises(ToolError):
            tools.call(ME, "list_transactions", arguments)
    # Valid by the rules, still only data: a bind parameter, never SQL.
    assert tools.call(ME, "list_transactions", {"category": "or_one_eq_one"}) == []
    assert tools.call(ME, "list_transactions", {"account_id": "cust-0001-chk--"}) == []
    assert _counts(owner) == before


def test_a_write_fails_twice_over(setup, app_url):
    tools, owner = setup
    update = sa.text("UPDATE accounts SET status = 'closed' WHERE customer_id = 'cust-0001'")
    with pytest.raises(sa.exc.DBAPIError, match="read-only transaction"):
        with read_only(tools._engine, ME) as conn:  # the tools' own transaction
            conn.execute(update)
    with pytest.raises(sa.exc.DBAPIError, match="permission denied"):
        with sa.create_engine(app_url).begin() as conn:  # and finpilot_app can't write anyway
            set_access(conn, ME)
            conn.execute(update)
    with owner.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM accounts WHERE status = 'closed'")).scalar() == 0


def test_balances_are_starting_balance_plus_transactions_minus_fees(setup):
    tools, _ = setup
    data = synthetic.generate(customers=3)  # what setup loaded
    expected = {a["account_id"]: a["starting_balance_cents"] for a in data["accounts"]
                if a["customer_id"] == ME.customer_id}
    for t in data["transactions"]:
        if t["account_id"] in expected:
            expected[t["account_id"]] += t["amount_cents"]
    for f in data["fees"]:
        if f["account_id"] in expected and not f["waived"]:
            expected[f["account_id"]] -= f["amount_cents"]
    got = {r["account_id"]: r["balance_cents"] for r in tools.call(ME, "get_balance")}
    assert got == expected and all(isinstance(v, int) for v in got.values())
    [checking] = tools.call(ME, "get_balance", {"account_id": "cust-0001-chk"})
    last = max([t["posted_on"] for t in data["transactions"] if t["account_id"] == "cust-0001-chk"]
               + [f["assessed_on"] for f in data["fees"] if f["account_id"] == "cust-0001-chk"])
    assert checking["as_of"] == last.isoformat() and checking["starting_balance_on"] == "2025-07-01"
    assert tools.call(ME, "get_balance", {"account_id": "cust-0002-chk"}) == []  # exists, isn't ours


def test_a_balance_can_be_asked_by_account_type(setup):
    tools, _ = setup
    [checking] = tools.call(ME, "get_balance", {"account_type": "checking"})
    assert checking["account_id"] == "cust-0001-chk"
    with pytest.raises(ToolError):
        tools.call(ME, "get_balance", {"account_type": "checking'; --"})
