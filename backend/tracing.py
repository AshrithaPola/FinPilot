"""Per-request traces (Phase 8): what one /ask did, step by step, with times
and estimated costs, stored in Postgres under RLS by org.

Inside `with tracing() as trace:`, code adds spans: the engine's nodes, each
agent step, tool call and search, every LLM and embedding call (model,
tokens, estimated cost, attempts, and each failed attempt's error), and the
validators' verdicts. A span is {kind, name, start_ms, ms, ...details},
start_ms counted from the start of the request; spans are flat, and nesting
shows in the times. The trace object is shared by every context copied from
the one that opened it (LangGraph nodes, asyncio.to_thread), like
llm.track_usage.

Costs are ESTIMATES at Gemini's paid list prices (the project runs on the
free tier, which costs nothing): PRICES below, per million tokens, thinking
billed as output. Embedding tokens are counted with the chunker's tokenizer,
an estimate of Gemini's own count.
"""
import contextvars
import json
import logging
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

import sqlalchemy as sa

logger = logging.getLogger(__name__)

# USD per 1M tokens: (input, output). Standard tier list prices from
# https://ai.google.dev/gemini-api/docs/pricing, checked 2026-11-16.
PRICES = {
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-embedding-2": (0.20, 0.0),
}
MAX_TEXT = 300  # characters of a text kept in a span (queries, errors)

_current: contextvars.ContextVar["Trace | None"] = contextvars.ContextVar("trace", default=None)


def cost(model: str, prompt_tokens: int | None, output_tokens: int | None = 0,
         thinking_tokens: int | None = 0) -> float | None:
    """Estimated USD at list price, or None for a model without a listed price."""
    if model not in PRICES:
        return None
    inp, out = PRICES[model]
    return round(((prompt_tokens or 0) * inp + ((output_tokens or 0) + (thinking_tokens or 0)) * out) / 1e6, 8)


LIVE_KINDS = ("node", "step")  # spans a listener hears about as they start and end


class Trace:
    def __init__(self, listener=None):
        # `listener(event)`, if given, hears each node and step as it starts
        # and ends, and each notify(): /ask/stream turns them into SSE events.
        self.listener = listener
        self.trace_id = str(uuid.uuid4())
        self.started_at = datetime.now(timezone.utc)
        self._t0 = time.perf_counter()
        self.spans: list[dict] = []

    def add(self, kind: str, name: str, start: float, **details) -> dict:
        span = {"kind": kind, "name": name, "start_ms": round((start - self._t0) * 1000, 1),
                "ms": round((time.perf_counter() - start) * 1000, 1), **details}
        self.spans.append(span)
        return span

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self._t0) * 1000, 1)

    def totals(self) -> dict:
        """LLM calls, estimated cost, and where the time went: model calls
        (LLM and embeddings, retries' waits included), search (the database
        legs and the local reranker), the tools (their SQL on the database),
        and the rest (our code)."""
        llm = [s for s in self.spans if s["kind"] == "llm"]
        emb = [s for s in self.spans if s["kind"] == "embedding"]
        node_ms = sum(s["ms"] for s in self.spans if s["kind"] == "node")
        model_ms = sum(s["ms"] for s in llm + emb)
        search_ms = sum(s["ms"] for s in self.spans if s["kind"] in ("retrieval", "rerank"))
        tools_ms = sum(s["ms"] for s in self.spans if s["kind"] == "tool")
        return {
            "llm_calls": len(llm),
            "embedding_calls": len(emb),
            "retries": sum(len(s.get("failures", [])) for s in llm + emb),
            "est_cost_usd": round(sum(s.get("est_cost_usd") or 0 for s in llm + emb), 8),
            "llm_ms": round(sum(s["ms"] for s in llm), 1),
            "embedding_ms": round(sum(s["ms"] for s in emb), 1),
            "search_ms": round(search_ms, 1),
            "rerank_ms": round(sum(s["ms"] for s in self.spans if s["kind"] == "rerank"), 1),
            "tools_ms": round(tools_ms, 1),
            "other_ms": round(max(node_ms - model_ms - search_ms - tools_ms, 0), 1),
        }

    def timeline(self) -> list[dict]:
        """The engine's stages in order, for a UI's progress display."""
        return [{"stage": s["name"], "start_ms": s["start_ms"], "ms": s["ms"]}
                for s in self.spans if s["kind"] == "node"]


@contextmanager
def tracing(listener=None):
    trace = Trace(listener)
    token = _current.set(trace)
    try:
        yield trace
    finally:
        _current.reset(token)


def current() -> Trace | None:
    return _current.get()


@contextmanager
def span(kind: str, name: str, **details):
    """Time the block as one span; the caller may add details to the dict it
    gets. An exception is recorded on the span and re-raised."""
    trace, start = _current.get(), time.perf_counter()
    live = trace is not None and trace.listener is not None and kind in LIVE_KINDS
    if live:
        trace.listener({"phase": "start", "kind": kind, "name": name, **details})
    try:
        yield details
    except BaseException as exc:
        details["error"] = f"{type(exc).__name__}: {exc}"[:MAX_TEXT]
        raise
    finally:
        if trace is not None:
            span = trace.add(kind, name, start, **details)
            if live:
                trace.listener({"phase": "end", **span})


def notify(event: dict) -> None:
    """Tell the request's listener something that isn't a span (the plan)."""
    trace = _current.get()
    if trace is not None and trace.listener is not None:
        trace.listener(event)


def save(engine: sa.Engine, access, trace: Trace, result: dict, *, user_id: str | None, question: str) -> None:
    """Store the trace in the caller's org (RLS); log and swallow a failure,
    as the query log does: a trace must never fail the answer."""
    from db.access import set_access

    totals = trace.totals()
    try:
        with engine.begin() as conn:
            set_access(conn, access)
            conn.execute(sa.text("""
                INSERT INTO traces (trace_id, created_at, org_id, user_id, customer_id, endpoint, question,
                                    outcome, total_ms, llm_calls, est_cost_usd, demo_today, summary, spans)
                VALUES (CAST(:trace_id AS uuid), :created_at, :org_id, :user_id, :customer_id, '/ask', :question,
                        :outcome, :total_ms, :llm_calls, :cost, CAST(:demo_today AS date),
                        CAST(:summary AS jsonb), CAST(:spans AS jsonb))
            """), {"trace_id": trace.trace_id, "created_at": trace.started_at, "org_id": access.org_id,
                   "user_id": user_id, "customer_id": getattr(access, "customer_id", None),
                   "question": question, "outcome": result.get("outcome"),
                   "total_ms": result.get("timings", {}).get("total_ms"), "llm_calls": totals["llm_calls"],
                   "cost": totals["est_cost_usd"], "demo_today": result.get("demo_today"),
                   "summary": json.dumps({**totals, "llm_calls_by_role": result.get("llm_calls_by_role"),
                                          "validation": result.get("validation")}, default=str),
                   "spans": json.dumps(trace.spans, default=str)})
    except Exception:
        logger.exception("Failed to write trace %s", trace.trace_id)


def recent(engine: sa.Engine, access, limit: int) -> list[dict]:
    """The caller's org's most recent traces, without their spans."""
    from db.access import set_access

    with engine.begin() as conn:
        set_access(conn, access)
        rows = conn.execute(sa.text("""
            SELECT trace_id, created_at, user_id, customer_id, question, outcome, total_ms, llm_calls,
                   est_cost_usd, summary->'retries' AS retries
            FROM traces ORDER BY created_at DESC LIMIT :limit
        """), {"limit": limit}).mappings().all()
    return [_plain(r) for r in rows]


def one(engine: sa.Engine, access, trace_id: str) -> dict | None:
    """One trace of the caller's org, with its spans; None if it isn't
    there (or is another org's: RLS hides it)."""
    from db.access import set_access

    try:
        uuid.UUID(trace_id)
    except ValueError:
        return None
    with engine.begin() as conn:
        set_access(conn, access)
        row = conn.execute(sa.text("SELECT * FROM traces WHERE trace_id = CAST(:t AS uuid)"),
                           {"t": trace_id}).mappings().first()
    return None if row is None else _plain(row)


def _plain(row) -> dict:
    out = {}
    for k, v in row.items():
        out[k] = str(v) if isinstance(v, uuid.UUID) else v.isoformat() if hasattr(v, "isoformat") else (
            float(v) if k == "est_cost_usd" and v is not None else v)
    return out
