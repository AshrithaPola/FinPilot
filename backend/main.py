"""FinPilot API.

Endpoints:
    POST /ask           — the reasoning engine: plan, agents, one checked and cited answer
    POST /ask/stream    — the same over SSE: the plan, each stage and step as it runs,
                          then the answer (sent only after the validators pass it)
    GET  /documents     — documents the caller can see, newest first
    POST /feedback      — thumbs up/down on an answer (its query_id)
    GET  /admin/traces  — recent /ask traces of the admin's org; /admin/traces/{id} one
    GET  /admin/logs    — recent query logs of the admin's org (admins only)
    GET  /admin/stats   — the same org's outcome counts + latency percentiles
    GET  /me            — who the token belongs to and whether they're set up
    GET  /health        — liveness

Every endpoint except /me and /health requires a signed-in, set-up user
(auth.get_principal: 401 without a valid token, 403 if not set up); /admin/*
also checks with Firebase that the token wasn't revoked.
"""
import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from functools import partial

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from auth import Principal, get_admin_principal, get_me, get_principal
from config import get_settings
from db import session as db
import deps
from deps import get_store
from llm import classify_error, track_usage
import tracing

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("finpilot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.migration_database_url:
        # Owner credentials belong to the one-shot migrate step (migrate.py),
        # never to the serving process: with them, one bug could undo RLS.
        raise RuntimeError("MIGRATION_DATABASE_URL is set in the backend's environment. "
                           "Run migrations with `python migrate.py` (the migrate service) and "
                           "give the backend only DATABASE_URL (the finpilot_app role).")
    # Fail fast, with what to fix, rather than a 500 on the first sign-in.
    # Checked before the database: a config error shouldn't wait on Postgres.
    deps.get_token_checker().check_ready()
    logger.info("Firebase token checks ready.")
    db.init_db(settings)
    # The store fails fast with a clear error if the chunks table is missing
    # or its vector column doesn't match the embedding model. With
    # EMBEDDING_PROVIDER=local this also loads the model.
    get_store()
    logger.info("Store ready.")
    yield


app = FastAPI(title="FinPilot API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class FeedbackRequest(BaseModel):
    query_id: int
    thumbs_up: bool
    comment: str | None = Field(default=None, max_length=2000)


class AskRequest(BaseModel):
    question: str = Field(max_length=4000)  # input validation applies the real limit


# --- helpers -------------------------------------------------------------------

# What the customer sees when a request fails: no provider text, and whether
# trying again can help. Timeouts and overloads are the free tier's normal
# weather, not bugs.
FRIENDLY_ERRORS = {
    "timeout": ("The model is busy right now and didn't answer in time. Please try again.", True),
    "unavailable": ("The model is busy right now. Please try again in a moment.", True),
    "rate_limited": ("The model is busy right now. Please try again in a moment.", True),
    "daily_quota": ("The model's free daily limit is used up. Please try again tomorrow.", False),
    "internal": ("Something went wrong on our side while answering. Please try again.", True),
}

# The stage a customer sees for each engine node and agent (live, over SSE).
NODE_LABELS = {"plan": "Planning", "replan": "Re-planning", "summarize": "Writing the answer",
               "check": "Checking the answer"}
AGENT_LABELS = {"documents": "Searching documents", "calculation": "Calculating"}


def _step_label(agent_tool: str) -> str:
    agent, _, tool = agent_tool.partition(".")
    if agent == "financial_data":
        from tools.market import TOOLS as MARKET_TOOLS

        return "Looking up market data" if tool in MARKET_TOOLS else "Looking up your accounts"
    return AGENT_LABELS.get(agent, agent)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _log_record(result: dict, principal: Principal) -> dict:
    """The /ask request as a query-log row (what feedback links to). The
    question is the redacted one the engine saw."""
    categories = result.get("redaction_categories") or []
    return {"user_id": principal.user_id, "org_id": principal.org_id, "question": result.get("question", ""),
            "redacted": bool(categories), "redaction_categories": categories or None,
            "answer": result.get("answer"), "outcome": result.get("outcome", "error"), "route": "ask",
            "regenerated": len(result.get("validation") or []) > 1,
            "total_ms": result.get("timings", {}).get("total_ms")}


class AskFailed(Exception):
    def __init__(self, exc: Exception, kind: str, llm_usage: dict):
        super().__init__(kind)
        self.exc, self.kind, self.llm_usage = exc, kind, llm_usage


async def _run_ask(question: str, principal: Principal, listener=None) -> dict:
    """The engine for one question, traced and logged; the result carries
    `query_id` (for feedback) and, for admins, the LLM usage. Raises
    AskFailed with a provider-neutral kind when the engine fails."""
    from engine.graph import answer
    from graph.redaction import redact

    with tracing.tracing(listener) as trace, track_usage() as llm_usage:
        try:
            result = await answer(question, principal)
        except Exception as exc:
            kind = classify_error(exc) or "internal"
            failed = {"outcome": f"error: {kind}", "timings": {"total_ms": trace.elapsed_ms()}}
            await _save_trace(trace, principal, failed, redact(question)[0])
            raise AskFailed(exc, kind, llm_usage) from exc
    result["trace"] = trace.totals()
    await _save_trace(trace, principal, result, result.get("question", ""))
    result["query_id"] = await asyncio.to_thread(partial(db.log_query, _log_record(result, principal),
                                                         access=principal))
    if principal.role == "admin":
        result["llm_usage"] = llm_usage
    for internal in ("question", "redaction_categories"):
        result.pop(internal, None)
    return result


# --- endpoints -----------------------------------------------------------------


@app.get("/documents")
async def list_documents(principal: Principal = Depends(get_principal)):
    """Documents the caller can see, newest first."""
    return {"documents": get_store().scoped(principal).list_documents()}


def _engine_error(failure: AskFailed, diagnostics: bool) -> JSONResponse:
    """/ask's failure: which engine node failed, a provider-neutral kind and
    a message fit to show (raw provider messages stay in the server log).
    Admins (`diagnostics`) also get the full error text and every LLM
    attempt. Model capacity errors are 503 (retry later); anything else 500."""
    exc = failure.exc
    # LangGraph notes the failing node: "During task with name 'plan' and id '...'".
    match = re.search(r"During task with name '(\w+)'", " ".join(getattr(exc, "__notes__", [])))
    stage = match.group(1) if match else "engine"
    logger.exception("/ask failed at %s (%s)", stage, failure.kind, exc_info=exc)
    message, retryable = FRIENDLY_ERRORS[failure.kind]
    content = {"detail": message, "stage": stage, "error": failure.kind, "retryable": retryable}
    if diagnostics:
        content |= {"diagnostic": f"{type(exc).__name__}: {exc}", "llm_usage": failure.llm_usage}
    return JSONResponse(status_code=500 if failure.kind == "internal" else 503, content=content)


@app.post("/ask")
async def ask(request: AskRequest, principal: Principal = Depends(get_principal)):
    """The reasoning engine: plan, run the agents as the caller, answer with
    citations, check the answer. Returns the answer with its plan and steps,
    citations, the validators' verdicts, the stage timeline, the LLM calls
    and the trace id, and `query_id` for feedback."""
    try:
        result = await _run_ask(request.question, principal)
    except AskFailed as failure:
        return _engine_error(failure, diagnostics=principal.role == "admin")
    if result["outcome"] == "rejected_input":
        raise HTTPException(status_code=422, detail=result["answer"])
    return result


@app.post("/ask/stream")
async def ask_stream(request: AskRequest, principal: Principal = Depends(get_principal)):
    """/ask over SSE, so the UI shows progress while the engine works:

        {"type": "plan", "round", "intent", "steps": [{id, agent, tool, purpose}]}
        {"type": "stage", "stage", "label", "state": "start" | "done", "ms"?}
        {"type": "step", "id", "label", "agent", "tool", "state": "start" | "done" | "failed", ...}
        {"type": "done", ...the /ask response}
        {"type": "error", "kind", "message", "retryable"}

    The answer itself arrives once, in "done", after the validators passed it:
    nothing unchecked is ever shown."""
    queue: asyncio.Queue = asyncio.Queue()

    def listen(event: dict) -> None:
        if event.get("type") == "plan":
            queue.put_nowait(event)
            return
        ending = event["phase"] == "end"
        if event["kind"] == "node" and event["name"] in NODE_LABELS:
            queue.put_nowait({"type": "stage", "stage": event["name"], "label": NODE_LABELS[event["name"]],
                              "state": "done" if ending else "start", **({"ms": event["ms"]} if ending else {})})
        elif event["kind"] == "step":
            state = ("failed" if event.get("status") == "failed" else "done") if ending else "start"
            step = {"type": "step", "id": event.get("step"), "label": _step_label(event["name"]),
                    "agent_tool": event["name"], "round": event.get("round"), "state": state}
            if ending:
                step |= {"ms": event["ms"], "error": event.get("error"), "note": event.get("note")}
            queue.put_nowait(step)

    async def run() -> None:
        try:
            result = await _run_ask(request.question, principal, listener=listen)
        except AskFailed as failure:
            logger.exception("/ask/stream failed (%s)", failure.kind, exc_info=failure.exc)
            message, retryable = FRIENDLY_ERRORS[failure.kind]
            queue.put_nowait({"type": "error", "kind": failure.kind, "message": message, "retryable": retryable})
        except Exception:
            logger.exception("/ask/stream failed")
            message, retryable = FRIENDLY_ERRORS["internal"]
            queue.put_nowait({"type": "error", "kind": "internal", "message": message, "retryable": retryable})
        else:
            if result["outcome"] == "rejected_input":
                queue.put_nowait({"type": "error", "kind": "rejected_input", "message": result["answer"],
                                  "retryable": False})
            else:
                queue.put_nowait({"type": "done", **result})
        finally:
            queue.put_nowait(None)

    async def events():
        task = asyncio.create_task(run())
        try:
            while (event := await queue.get()) is not None:
                yield _sse(event)
        finally:
            if not task.done():  # the client went away: stop spending model calls
                task.cancel()

    return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/feedback")
async def submit_feedback(request: FeedbackRequest, principal: Principal = Depends(get_principal)):
    """Thumbs up/down (plus optional comment) for a logged query.

    Upserts: one feedback row per query, so re-clicking or adding a comment
    updates the verdict instead of double-counting it in /admin/stats.
    """
    status = await asyncio.to_thread(
        partial(db.log_feedback, request.query_id, request.thumbs_up, request.comment,
                access=principal, user_id=principal.user_id)
    )
    if status == "unknown_query":
        raise HTTPException(status_code=404, detail=f"No query with id {request.query_id}.")
    if status == "disabled":
        raise HTTPException(
            status_code=503, detail="Feedback requires query logging (DATABASE_URL) to be enabled."
        )
    if status == "error":
        raise HTTPException(status_code=500, detail="Could not store feedback.")
    return {"status": "ok"}


@app.get("/admin/logs")
async def admin_logs(limit: int = 20, principal: Principal = Depends(get_admin_principal)):
    """The admin's own org's logs: RLS hides every other org's rows."""
    return {"logs": await asyncio.to_thread(partial(db.fetch_logs, min(limit, 100),
                                                    access=principal))}


async def _save_trace(trace, principal: Principal, result: dict, question: str) -> None:
    store = get_store()
    if store is not None:
        await asyncio.to_thread(partial(tracing.save, store.engine, principal, trace, result,
                                        user_id=principal.user_id, question=question))


@app.get("/admin/traces")
async def admin_traces(limit: int = 20, principal: Principal = Depends(get_admin_principal)):
    """The admin's own org's recent /ask requests: latency, LLM calls,
    estimated cost and outcome (RLS hides every other org's)."""
    return {"traces": await asyncio.to_thread(tracing.recent, get_store().engine, principal,
                                              max(1, min(limit, 100)))}


@app.get("/admin/traces/{trace_id}")
async def admin_trace(trace_id: str, principal: Principal = Depends(get_admin_principal)):
    """One request's trace, every span; 404 for another org's."""
    trace = await asyncio.to_thread(tracing.one, get_store().engine, principal, trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="No such trace.")
    return trace


@app.get("/admin/stats")
async def admin_stats(principal: Principal = Depends(get_admin_principal)):
    return await asyncio.to_thread(partial(db.fetch_stats, access=principal))


@app.get("/me")
async def me(body: dict = Depends(get_me)):
    # The demo's "today" (the synthetic data's last day), so the UI can show
    # it before the first question.
    return {**body, "demo_today": get_settings().demo_today.isoformat()}


@app.get("/health")
async def health():
    # No chunk count: /health is unauthenticated, and under RLS a count is
    # per caller anyway.
    return {"status": "ok"}
