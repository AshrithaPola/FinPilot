"""Traces without a database: spans, costs, the gateway's retries and its
per-call timeout, and the engine's stages."""
import asyncio

import pytest

import tracing
from engine import graph as engine_graph
from llm import _BackoffLLM
from tests.unit.test_engine import AFFORD, GOOD, ME, _afford_plan, engine  # noqa: F401  (engine: a fixture)


def test_costs_are_list_price_estimates():
    # 1,000 prompt + 200 output + 300 thinking tokens on flash-lite: $0.30 and $2.50 per million.
    assert tracing.cost("gemini-3.5-flash-lite", 1_000, 200, 300) == pytest.approx(0.0003 + 0.00125)
    assert tracing.cost("gemini-embedding-2", 10_000) == pytest.approx(0.002)
    assert tracing.cost("some-unpriced-model", 1_000) is None


def test_a_span_records_its_error_and_reraises():
    with tracing.tracing() as trace:
        with pytest.raises(ValueError):
            with tracing.span("step", "x"):
                raise ValueError("boom")
    [span] = trace.spans
    assert span["error"] == "ValueError: boom" and span["ms"] >= 0
    with tracing.span("step", "untraced"):  # no trace open: nothing recorded, nothing breaks
        pass


class Flaky(_BackoffLLM):
    def __init__(self, **kw):
        super().__init__(base_delay_s=0.01, **kw)

    def _retry_delay(self, exc):
        return 0.0  # everything is retryable here


async def test_retries_are_traced_with_their_errors():
    calls = []

    async def request():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("503 high demand")
        return object()

    with tracing.tracing() as trace:
        await Flaky()._call("gemini-3.5-flash-lite", request, role="planner")
    [span] = trace.spans
    assert span["kind"] == "llm" and span["role"] == "planner" and span["attempts"] == 2
    assert span["failures"] == [{"attempt": 1, "error": "RuntimeError: 503 high demand"}]


async def test_one_slow_call_cannot_hang_the_request():
    async def hangs():
        await asyncio.sleep(30)

    llm = Flaky(request_timeout_s=10, call_timeout_s=0.3)
    with tracing.tracing() as trace:
        with pytest.raises(TimeoutError, match="no answer within"):
            await asyncio.wait_for(llm._call("gemini-3.5-flash-lite", hangs, role="planner"), 5)
    assert trace.spans[0]["error"].startswith("TimeoutError")


async def test_the_engine_traces_its_stages_steps_and_checks(engine):  # noqa: F811
    llm, _ = engine
    _afford_plan(llm)
    llm.answer = GOOD
    with tracing.tracing() as trace:
        result = await engine_graph.answer(AFFORD, ME)
    assert [t["stage"] for t in result["timeline"]] == ["intake", "route", "plan", "execute", "summarize", "validate"]
    assert result["trace_id"] == trace.trace_id
    kinds = [(s["kind"], s["name"]) for s in trace.spans]
    assert ("step", "financial_data.get_balance") in kinds and ("tool", "get_balance") in kinds
    assert ("step", "calculation.afford") in kinds
    checks = [s for s in trace.spans if s["kind"] == "check"]
    assert {c["name"] for c in checks} == {"gatekeeper", "auditor", "strategist"}
    assert all(c["passed"] and c["by"] == "llm" for c in checks)
    totals = trace.totals()
    assert totals["other_ms"] >= 0 and totals["retries"] == 0
