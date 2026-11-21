"""Langfuse gets masked data only: amounts, merchants, account/card ids,
customer names and emails never leave the app. And the engine's runs reach
LangChain callbacks as tool and retriever runs."""
import json

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage

import observability
from engine import graph as engine_graph
from ingestion.store import RetrievedChunk
from tests.helpers import FakeLLM

# What a summarizer prompt holds for cust-0036 (the evidence as JSON text), and a question with PII.
EVIDENCE = json.dumps([
    {"account_id": "cust-0036-chk", "type": "checking", "balance_cents": 267723, "balance_dollars": "$2,677.23",
     "as_of": "2026-06-30"},
    {"txn_id": "cust-0036-t00412", "card_id": "cust-0036-dc", "posted_on": "2026-04-06", "amount_cents": -8213,
     "amount_dollars": "-$82.13", "merchant": "Corner Grocer", "description": "Corner Grocer purchase",
     "category": "groceries"},
    {"card_id": "cust-0036-cc1", "last4": "4821", "network": "mastercard"},
])
QUESTION = "I'm Avery Okafor (avery.okafor@example.com). Why was I charged $15 on April 6, 2026 at FreshFields Market?"
SECRETS = ["267723", "2,677.23", "8213", "82.13", "$15", "Corner Grocer", "FreshFields Market", "cust-0036",
           "4821", "Avery Okafor", "avery.okafor@example.com"]


def _text(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def test_a_summarizer_prompt_is_masked_before_it_leaves():
    out = observability.mask(data=[SystemMessage("You are FinPilot Bank's assistant."),
                                   HumanMessage(f"Evidence:\n[1] Result of get_balance:\n{EVIDENCE}\n\nQuestion: {QUESTION}")])
    text = _text(out)
    assert not [s for s in SECRETS if s in text], text
    # What isn't anyone's money stays, so the trace is still readable.
    for kept in ("checking", "groceries", "2026-04-06", "Why was I charged", "[amount]", "[merchant]", "[id]",
                 "[name]", "[email]"):
        assert kept in text, kept


def test_structured_values_are_masked_by_field_and_by_pattern():
    state = {"question": QUESTION,
             "results": {"s1": [{"balance_cents": 267723, "account_id": "cust-0036-chk", "full_name": "Rowan Haddad"}],
                         "s2": {"remaining_cents": 147723, "amounts_cents": [-8213, -1500]}},
             "evidence": [RetrievedChunk(chunk_id="c1", text="Overdraft fee: $15.00 per item.", filename="fees.md",
                                         page_number=1, score=0.1, dense_rank=1, sparse_rank=None, similarity=0.5)],
             "documents": [Document(page_content="Paid to QuickFuel: $41.20", metadata={"merchant": "QuickFuel"})],
             "access": object()}
    text = _text(observability.mask(data=state))
    for secret in SECRETS + ["147723", "1500", "Rowan Haddad", "QuickFuel", "41.20"]:
        assert secret not in text, secret
    assert "Overdraft fee" in text and "fees.md" in text


def test_nothing_is_sent_without_all_three_settings(monkeypatch):
    assert observability.callbacks() == []  # tests run with the keys blanked (conftest)


def test_the_langfuse_client_is_built_with_the_mask(monkeypatch):
    import langfuse

    made = {}

    class FakeLangfuse:
        def __init__(self, **kwargs):
            made.update(kwargs)

    monkeypatch.setattr(langfuse, "Langfuse", FakeLangfuse)
    observability._client.cache_clear()
    try:
        observability._client("pk", "sk", "https://langfuse.example")
    finally:
        observability._client.cache_clear()
    assert made["mask"] is observability.mask and made["base_url"] == "https://langfuse.example"


def test_accounts_tools_as_langchain_tools_keep_the_strict_schema():
    from tools.accounts import TOOLS
    from tools.common import ToolError, langchain_tool, run_langchain_tool

    calls = []
    tool = langchain_tool(TOOLS["get_balance"], lambda args: calls.append(args) or [{"balance_cents": 1}])
    assert tool.args_schema is TOOLS["get_balance"].args and tool.name == "get_balance"
    assert run_langchain_tool(tool, {"account_type": "checking"}) == [{"balance_cents": 1}]
    with pytest.raises(ToolError, match="customer_id: Extra inputs are not permitted"):
        run_langchain_tool(tool, {"customer_id": "cust-0002"})  # refused before anything runs
    assert calls == [{"account_id": None, "account_type": "checking"}]  # the schema's fields, validated


class Recorder(BaseCallbackHandler):
    def __init__(self):
        self.events = []

    def on_tool_start(self, serialized, input_str, **kwargs):
        self.events.append(("tool", kwargs.get("name") or (serialized or {}).get("name")))

    def on_retriever_start(self, serialized, query, **kwargs):
        self.events.append(("retriever", query))


class OneChunkStore:
    def scoped(self, principal):
        return self

    async def search(self, query, filters=None):
        return [RetrievedChunk(chunk_id="c1", text="The overdraft fee is $15.00 per item.", filename="fees.md",
                               page_number=1, score=0.1, dense_rank=1, sparse_rank=None, similarity=0.5,
                               rerank_score=4.0, section="Fees")]


async def test_the_engine_run_reaches_langchain_callbacks_as_tool_and_retriever_runs(monkeypatch):
    from tests.unit.test_engine import FakeAccounts, _step

    llm, recorder = FakeLLM(), Recorder()
    monkeypatch.setattr(engine_graph, "get_llm", lambda: llm)
    monkeypatch.setattr(engine_graph, "get_accounts_tools", lambda: FakeAccounts())
    monkeypatch.setattr(engine_graph, "get_market_tools", lambda: None)
    monkeypatch.setattr(engine_graph, "get_store", lambda: OneChunkStore())
    monkeypatch.setattr(observability, "callbacks", lambda: [recorder])
    llm.plan = {"intent": "fee", "out_of_scope": False, "steps": [
        _step("s1", "financial_data", "get_balance", {"account_type": "checking"}),
        _step("s2", "documents", "search", {"query": "overdraft fee"})]}
    llm.answer = "Checking has $3,452.02 as of 2026-06-30 [1]; the overdraft fee is $15.00 [2]."
    result = await engine_graph.answer("What's my balance, and what's the overdraft fee?", object())
    assert result["outcome"] == "answered"
    assert ("tool", "get_balance") in recorder.events and ("retriever", "overdraft fee") in recorder.events
