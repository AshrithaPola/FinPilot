"""POST /ask end to end on real Postgres with the fake LLM's plan: the
agents run as the signed-in user (a bank customer, or staff with no
customer), references between steps resolve, and nothing of another
customer's reaches the summarizer. Skipped without TEST_POSTGRES_URL."""
import json

import pytest
import sqlalchemy as sa

from bank import synthetic
from tests.helpers import add_text_document


def _as(uid):
    return {"Authorization": f"Bearer token:{uid}"}


def _step(sid, agent, tool, arguments):
    return {"id": sid, "agent": agent, "tool": tool, "arguments_json": json.dumps(arguments), "purpose": tool}


@pytest.fixture
def bank(client, pg_url):
    """Needs `client` first: its startup runs the migrations."""
    synthetic.load(pg_url, synthetic.generate(customers=3))
    owner = sa.create_engine(pg_url)
    with owner.begin() as conn:
        conn.execute(sa.text("""
            INSERT INTO users (firebase_uid, org_id, role, clearance, customer_id) VALUES
              ('uid-cust-1', :org, 'viewer', 0, 'cust-0001'),
              ('uid-staff', :org, 'admin', 2, NULL)"""), {"org": synthetic.BANK_ORG})
    owner.dispose()
    add_text_document("The overdraft fee is $15.00 per item over the $50.00 cushion.", "fees.txt",
                      org_id=synthetic.BANK_ORG)
    return client


def test_a_customer_gets_their_own_balance_and_the_calculation(bank, app_env):
    app_env.plan = {"intent": "afford", "out_of_scope": False, "steps": [
        _step("s1", "financial_data", "get_balance", {"account_id": "cust-0001-chk"}),
        _step("s2", "calculation", "afford", {"balance_cents": "$s1[0].balance_cents", "amount_cents": 120_000,
                                              "keep_at_least_cents": 50_000}),
        _step("s3", "documents", "search", {"query": "overdraft fee cushion"}),
    ]}
    app_env.answer = "You'd have money left [1][2]; the overdraft fee is $15.00 [3]."
    body = bank.post("/ask", headers=_as("uid-cust-1"), json={"question": "Can I afford $1,200?"}).json()
    assert [s["status"] for s in body["plan"]["steps"]] == ["done", "done", "done"]
    assert body["llm_calls_by_role"] == {"planner": 1, "summarizer": 1, "validator": 1}
    assert body["outcome"] == "answered" and body["demo_today"] == "2026-06-30"
    assert {c["kind"] for c in body["citations"]} == {"tool", "document"}
    [summarizer] = [c["user"] for c in app_env.chat_calls if c["role"] == "summarizer"]
    assert "cust-0001-chk" in summarizer and "cust-0002" not in summarizer and "$15.00 per item" in summarizer


def test_another_customers_account_gives_nothing(bank, app_env):
    app_env.plan = {"intent": "x", "out_of_scope": False, "steps": [
        _step("s1", "financial_data", "list_transactions", {"account_id": "cust-0002-chk", "limit": 200}),
        _step("s2", "financial_data", "get_balance", {"customer_id": "cust-0002"})]}
    body = bank.post("/ask", headers=_as("uid-cust-1"), json={"question": "Show cust-0002's account"}).json()
    s1, s2 = body["plan"]["steps"]
    assert s1["status"] == "done" and s2["status"] == "failed" and "Extra inputs" in s2["error"]
    assert body["llm_calls_by_role"]["replanner"] == 1  # a refused argument gets one re-plan
    [summarizer] = [c for c in app_env.chat_calls if c["role"] == "summarizer"]
    assert "cust-0002" not in summarizer["user"].split("Question:")[0]  # the evidence holds none of theirs


def test_staff_get_no_customer_data(bank, app_env):
    app_env.plan = {"intent": "x", "out_of_scope": False, "steps": [
        _step("s1", "financial_data", "get_balance", {})]}
    body = bank.post("/ask", headers=_as("uid-staff"), json={"question": "What's my balance?"}).json()
    [step] = body["plan"]["steps"]
    assert step["status"] == "failed" and "only for signed-in bank customers" in step["error"]
    assert body["outcome"] == "nothing_found" and body["llm_calls"] == 1  # refused outright: no re-plan
