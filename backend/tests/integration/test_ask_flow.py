"""/ask and /ask/stream through the FastAPI app, with the LLM faked: the
small-talk shortcut, PII masking, the query log and feedback, the live
stage events, and failures shown as a friendly message (provider details
for admins only).

Each test runs against its own fresh Postgres + pgvector database; the
client signs in as an admin of TEST_ORG (staff: no customer data), so the
plans here use the documents agent and the calculator.
"""
import json

import sqlalchemy as sa

from db import session as db_session
from tests.helpers import add_text_document

QUESTION = "How do vacation days accrue?"
SEARCH = {"intent": "policy", "out_of_scope": False, "steps": [
    {"id": "s1", "agent": "documents", "tool": "search", "arguments_json": '{"query": "vacation days accrue"}',
     "purpose": "find the accrual rule"},
    {"id": "s2", "agent": "calculation", "tool": "total", "arguments_json": '{"amounts_cents": [150, 150]}',
     "purpose": "two months"}]}
ANSWER = "Vacation days accrue at 1.5 per month [1]."


def _setup(app_env):
    from tests.conftest import TEST_ORG

    add_text_document("The HR handbook covers vacation. Vacation days accrue at 1.5 per month.",
                      "handbook.txt", org_id=TEST_ORG)
    app_env.plan, app_env.answer = SEARCH, ANSWER


def _last_log():
    from db.access import Access
    from tests.conftest import TEST_ORG

    logs = db_session.fetch_logs(1, access=Access(org_id=TEST_ORG, clearance=0))
    assert logs, "expected a query log row"
    return logs[0]


def _stream(client, question=QUESTION, headers=None):
    with client.stream("POST", "/ask/stream", json={"question": question}, headers=headers or {}) as response:
        assert response.status_code == 200
        return [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]


def _as_viewer(pg_url):
    """Headers for a viewer in the client's org (diagnostics are admin-only)."""
    from tests.conftest import TEST_ORG

    engine = sa.create_engine(pg_url)
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO users (firebase_uid, org_id, role, clearance) "
                             "VALUES ('uid-viewer', :org, 'viewer', 0)"), {"org": TEST_ORG})
    engine.dispose()
    return {"Authorization": "Bearer token:uid-viewer"}


def test_small_talk_costs_no_model_call_and_is_logged(client, app_env):
    body = client.post("/ask", json={"question": "hello!"}).json()
    assert body["outcome"] == "small_talk" and "FinPilot" in body["answer"]
    assert app_env.chat_calls == [] and app_env.embed_calls == []
    row = _last_log()
    assert (row["route"], row["outcome"]) == ("ask", "small_talk") and body["query_id"] == row["id"]


def test_pii_never_reaches_a_model_the_log_or_the_trace(client, app_env):
    _setup(app_env)
    ssn = "123-45-6789"
    body = client.post("/ask", json={"question": f"Is SSN {ssn} in the vacation doc?"}).json()
    assert ssn not in app_env.prompts_sent() and "[SSN_REDACTED]" in app_env.prompts_sent()
    row = _last_log()
    assert ssn not in row["question"] and row["redacted"] is True and row["redaction_categories"] == ["ssn"]
    trace = client.get(f"/admin/traces/{body['trace_id']}").json()
    assert ssn not in trace["question"] and "[SSN_REDACTED]" in trace["question"]


def test_feedback_links_to_the_ask_and_upserts(client, app_env):
    _setup(app_env)
    query_id = client.post("/ask", json={"question": QUESTION}).json()["query_id"]
    assert isinstance(query_id, int)
    for payload in ({"thumbs_up": False}, {"thumbs_up": False, "comment": "missed the rate"}):
        assert client.post("/feedback", json={"query_id": query_id, **payload}).status_code == 200
    feedback = client.get("/admin/stats").json()["feedback"]
    assert (feedback["total"], feedback["thumbs_down"]) == (1, 1)
    assert feedback["recent_negative"][0]["comment"] == "missed the rate"
    assert client.post("/feedback", json={"query_id": 424242, "thumbs_up": True}).status_code == 404


def test_the_stream_shows_the_plan_each_stage_and_step_then_the_checked_answer(client, app_env):
    _setup(app_env)
    events = _stream(client)
    kinds = [e["type"] for e in events]
    assert kinds[0] == "stage" and kinds[-1] == "done" and "error" not in kinds

    [plan] = [e for e in events if e["type"] == "plan"]
    assert [s["tool"] for s in plan["steps"]] == ["search", "total"]
    stages = [(e["stage"], e["state"]) for e in events if e["type"] == "stage"]
    assert stages == [("plan", "start"), ("plan", "done"), ("summarize", "start"), ("summarize", "done"),
                      ("check", "start"), ("check", "done")]
    steps = [(e["id"], e["label"], e["state"]) for e in events if e["type"] == "step"]
    assert steps == [("s1", "Searching documents", "start"), ("s1", "Searching documents", "done"),
                     ("s2", "Calculating", "start"), ("s2", "Calculating", "done")]
    # The answer comes once, after the check: never before it.
    assert kinds.index("done") > max(i for i, e in enumerate(events) if e["type"] == "stage")
    done = events[-1]
    assert done["answer"] == ANSWER and done["outcome"] == "answered" and isinstance(done["query_id"], int)
    assert [t["stage"] for t in done["timeline"]][-1] == "check" and done["demo_today"] == "2026-06-30"


def test_a_model_timeout_is_a_friendly_retryable_message(client, app_env, pg_url):
    _setup(app_env)
    app_env.generate_error = TimeoutError("gemini-3.5-flash-lite gave no answer within 45 s")
    [error] = [e for e in _stream(client, headers=_as_viewer(pg_url)) if e["type"] == "error"]
    assert error == {"type": "error", "kind": "timeout", "retryable": True,
                     "message": "The model is busy right now and didn't answer in time. Please try again."}

    response = client.post("/ask", headers={"Authorization": "Bearer token:uid-viewer"}, json={"question": QUESTION})
    assert response.status_code == 503
    body = response.json()
    assert (body["error"], body["retryable"], body["stage"]) == ("timeout", True, "summarize")
    assert "diagnostic" not in body and "llm_usage" not in body  # provider details stay in the server log


def test_admins_get_diagnostics_and_usage(client, app_env):
    _setup(app_env)
    assert set(client.post("/ask", json={"question": QUESTION}).json()["llm_usage"]) == {
        "attempts_per_model", "failures", "calls", "tokens_per_role"}
    app_env.generate_error = ValueError("bug")
    response = client.post("/ask", json={"question": QUESTION})
    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "internal" and body["diagnostic"] == "ValueError: bug"


def test_a_rejected_question_is_an_error_event_not_an_answer(client, app_env):
    [error] = [e for e in _stream(client, question="x" * 2001) if e["type"] == "error"]
    assert error["kind"] == "rejected_input" and error["retryable"] is False and "2001 characters" in error["message"]
    assert client.post("/ask", json={"question": "x" * 2001}).status_code == 422
