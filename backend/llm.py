"""The single LLM gateway. Every model call in FinPilot goes through this
module — pipeline nodes never touch a provider SDK directly, so swapping
providers (as this project has done twice: OpenAI -> Anthropic -> Gemini)
means changing LLM_PROVIDER, not call sites.

Two operations cover everything the pipeline needs:
  - complete_text: generation (the answer itself)
  - complete_json: the two checker calls (relevance grade, groundedness),
    using each API's structured outputs so the reply is schema-valid JSON
    rather than "hopefully JSON".

Two providers implement them: GeminiLLM (default — Google AI Studio free
tier) and ClaudeLLM (Anthropic). build_llm() picks one from settings.

Failure discipline: a refusal, a safety block, or a malformed checker reply
must fail SAFE. complete_json returns {} (callers treat missing keys as "no"),
and a refused/blocked generation returns "" (which then fails the
groundedness gate) — the pipeline refuses rather than crashing or waving an
answer through.

Rate limits: transient errors (429 rate limit, Gemini 500 INTERNAL, 503/529 overloaded,
and a Gemini request exceeding LLM_REQUEST_TIMEOUT_S) are retried with bounded exponential backoff, logged per attempt. A Gemini
per-DAY quota 429 is not retried — it won't clear for hours — and the quota
id/limit from the error is logged so the real free-tier numbers are visible.

Usage tracking: inside `with track_usage() as usage:`, every attempt is
counted per model and every failed attempt is kept with its full error text
(including a 429's quota id and limit), so a caller such as /ask can
report exactly what a request spent and what failed. Each successful call
made with a `role` (grade, generate, groundedness) also records the
provider's own token counts (prompt, output, thinking), per call and summed
per role.
"""
import asyncio
import base64
import contextvars
import json
import logging
import time
from contextlib import contextmanager

import anthropic
from anthropic import AsyncAnthropic
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from langchain_core.messages import HumanMessage, SystemMessage

import tracing

logger = logging.getLogger(__name__)

_usage: contextvars.ContextVar[dict | None] = contextvars.ContextVar("llm_usage", default=None)

TOKEN_FIELDS = ("prompt_tokens", "output_tokens", "thinking_tokens")


@contextmanager
def track_usage():
    """Collect {"attempts_per_model": {model: n}, "failures": [...],
    "calls": [...], "tokens_per_role": {role: totals}} for every gateway call
    made inside the block (LangGraph nodes included: they run in a copy of
    this context, which shares the same dict)."""
    usage = {"attempts_per_model": {}, "failures": [], "calls": [], "tokens_per_role": {}}
    token = _usage.set(usage)
    try:
        yield usage
    finally:
        _usage.reset(token)


class _BackoffLLM:
    """Shared retry loop. Subclasses say which exceptions are retryable."""

    trace_kind = "llm"  # the span kind each call records (tracing.py)

    def __init__(self, max_attempts: int = 5, base_delay_s: float = 2.0, max_delay_s: float = 60.0,
                 request_timeout_s: float | None = None, call_timeout_s: float | None = None):
        self._max_attempts = max_attempts
        self._base_delay_s = base_delay_s
        self._max_delay_s = max_delay_s
        # Per attempt; None = wait indefinitely. A hung request raises
        # TimeoutError, which the subclass may treat as retryable.
        self._request_timeout_s = request_timeout_s
        # The whole call, retries and their waits included: one slow or
        # overloaded model can't hold a request longer than this.
        self._call_timeout_s = call_timeout_s

    def _retry_delay(self, exc: Exception) -> float | None:
        """Seconds the server asked us to wait (0.0 = no hint), or None if
        `exc` must not be retried."""
        raise NotImplementedError

    def _token_counts(self, response) -> dict:
        """{prompt_tokens, output_tokens, thinking_tokens} from the provider's
        usage metadata; None where the provider doesn't report one."""
        return dict.fromkeys(TOKEN_FIELDS)

    async def _call(self, model: str, request, role: str = "", details: dict | None = None):
        """One call with retries, traced as one span: model, role, attempts,
        each failed attempt's error, tokens and the estimated cost."""
        with tracing.span(self.trace_kind, model, role=role, **(details or {})) as span:
            span["failures"] = []
            response = await self._attempts(model, request, role, span)
            counts = self._token_counts(response)
            if any(counts.values()):
                span.update(counts)
            span["est_cost_usd"] = tracing.cost(model, counts.get("prompt_tokens") or span.get("est_tokens"),
                                                counts.get("output_tokens"), counts.get("thinking_tokens"))
            return response

    async def _attempts(self, model: str, request, role: str, span: dict):
        usage = _usage.get()
        deadline = None if self._call_timeout_s is None else time.monotonic() + self._call_timeout_s
        for attempt in range(1, self._max_attempts + 1):
            span["attempts"] = attempt
            if usage is not None:
                attempts = usage["attempts_per_model"]
                attempts[model] = attempts.get(model, 0) + 1
            timeout = self._request_timeout_s
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"{model} gave no answer within {self._call_timeout_s:.0f} s")
                timeout = remaining if timeout is None else min(timeout, remaining)
            try:
                response = await asyncio.wait_for(request(), timeout)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                if usage is not None:
                    usage["failures"].append({"model": model, "attempt": attempt, "error": error})
                span["failures"].append({"attempt": attempt, "error": error[:tracing.MAX_TEXT]})
                hint = self._retry_delay(exc)
                if hint is None or attempt == self._max_attempts:
                    raise
                delay = min(
                    self._max_delay_s,
                    hint or self._base_delay_s * 2 ** (attempt - 1),
                )
                if deadline is not None and time.monotonic() + delay >= deadline:
                    raise TimeoutError(f"{model} gave no answer within {self._call_timeout_s:.0f} s") from exc
                logger.warning(
                    "%s on %s (attempt %d/%d); retrying in %.1fs",
                    type(exc).__name__, model, attempt, self._max_attempts, delay,
                )
                await asyncio.sleep(delay)
            else:
                if usage is not None and role:
                    _record_tokens(usage, role, model, self._token_counts(response))
                return response


def _record_tokens(usage: dict, role: str, model: str, counts: dict) -> None:
    usage["calls"].append({"role": role, "model": model, **counts})
    totals = usage["tokens_per_role"].setdefault(
        role, {"calls": 0, **dict.fromkeys(TOKEN_FIELDS, 0)}
    )
    totals["calls"] += 1
    for field in TOKEN_FIELDS:
        totals[field] += counts.get(field) or 0


# --- Gemini (default) ------------------------------------------------------------

# Finish reasons that mean "the model declined / was stopped by policy" —
# treated exactly like an Anthropic refusal.
_GEMINI_BLOCKED = {
    genai_types.FinishReason.SAFETY,
    genai_types.FinishReason.RECITATION,
    genai_types.FinishReason.BLOCKLIST,
    genai_types.FinishReason.PROHIBITED_CONTENT,
    genai_types.FinishReason.SPII,
    genai_types.FinishReason.LANGUAGE,
    genai_types.FinishReason.OTHER,
}
_GEMINI_BLOCKED_NAMES = {reason.name for reason in _GEMINI_BLOCKED}  # as LangChain reports them


def _api_error(exc: BaseException) -> genai_errors.APIError | None:
    """The Gemini API error behind `exc`: itself, or the one LangChain's chat
    model wrapped (it re-raises ClientError/ServerError as its own types)."""
    seen = 0
    while exc is not None and seen < 5:
        if isinstance(exc, genai_errors.APIError):
            return exc
        exc, seen = exc.__cause__, seen + 1
    return None


def _gemini_error_details(exc: genai_errors.APIError) -> list[dict]:
    body = exc.details if isinstance(exc.details, dict) else {}
    details = body.get("error", {}).get("details", [])
    return details if isinstance(details, list) else []


def gemini_retry_delay(exc: Exception) -> float | None:
    """The Gemini retry decision, shared by the LLM gateway and the Gemini
    embedding provider: seconds the server asked us to wait (0.0 = no hint),
    or None if `exc` must not be retried."""
    # A request that hung past the per-request timeout: free-tier
    # requests occasionally stall for minutes, so retry it.
    if isinstance(exc, TimeoutError):
        return 0.0
    # 429 rate limit, 500 transient INTERNAL, 503 overloaded.
    exc = _api_error(exc)
    if exc is None or exc.code not in (429, 500, 503):
        return None
    hint = 0.0
    for detail in _gemini_error_details(exc):
        kind = detail.get("@type", "")
        if kind.endswith("QuotaFailure"):
            for violation in detail.get("violations", []):
                quota_id = violation.get("quotaId", "")
                logger.warning(
                    "Gemini quota hit: %s (limit %s)",
                    quota_id, violation.get("quotaValue", "?"),
                )
                if "PerDay" in quota_id:
                    return None  # daily cap: retrying within seconds is pointless
        elif kind.endswith("RetryInfo"):
            try:
                hint = float(str(detail.get("retryDelay", "0s")).rstrip("s"))
            except ValueError:
                pass
    return hint


class GeminiLLM(_BackoffLLM):
    """Gemini through LangChain's ChatGoogleGenerativeAI (so the engine's
    LangGraph runs see each call, e.g. Langfuse's callback handler), on our
    one genai client. This class stays the gateway around it: LangChain's own
    retries are off; the timeouts, backoff, daily-quota handling and token
    and cost accounting are _BackoffLLM's."""

    def __init__(self, client: genai.Client, checker_thinking_level: str = "", **backoff):
        """`checker_thinking_level` applies to complete_json (the checker
        calls) only; "" leaves the model's default. Generation always runs
        at the model's default thinking level."""
        super().__init__(**backoff)
        self._client = client
        self._checker_thinking = checker_thinking_level or None
        self._chats: dict[tuple, object] = {}

    def _chat(self, model: str, max_tokens: int, thinking: str | None = None):
        """The chat model for these settings, made once and reused."""
        from langchain_google_genai import ChatGoogleGenerativeAI

        key = (model, max_tokens, thinking)
        if key not in self._chats:
            if model not in self._chats:
                # Built (and validated) once per model, about a second: the key
                # only satisfies the constructor, the requests go through
                # self._client. One attempt each: retries are the gateway's.
                base = ChatGoogleGenerativeAI(model=model, google_api_key="unused", max_retries=1)
                base.client = self._client
                self._chats[model] = base
            # Each setting is a copy sharing that client (no validation, no new client).
            self._chats[key] = self._chats[model].model_copy(
                update={"max_output_tokens": max_tokens, **({"reasoning_effort": thinking} if thinking else {})})
        return self._chats[key]

    def _retry_delay(self, exc: Exception) -> float | None:
        return gemini_retry_delay(exc)

    @staticmethod
    def _request(runnable, messages):
        """One LangChain call that raises the Gemini error itself, as the
        gateway always has (LangChain re-raises it as its own types)."""
        async def run():
            try:
                return await runnable.ainvoke(messages)
            except Exception as exc:
                api = _api_error(exc)
                if api is not None and api is not exc:
                    raise api from exc
                raise
        return run

    def _token_counts(self, response) -> dict:
        message = response.get("raw") if isinstance(response, dict) else response
        meta = getattr(message, "usage_metadata", None)
        if not meta:
            return dict.fromkeys(TOKEN_FIELDS)
        thinking = (meta.get("output_token_details") or {}).get("reasoning") or 0
        # LangChain's output_tokens include the thinking; ours count it apart.
        return {"prompt_tokens": meta.get("input_tokens"),
                "output_tokens": (meta.get("output_tokens") or 0) - thinking,
                "thinking_tokens": thinking}

    def _usable_text(self, model: str, message, what: str) -> str | None:
        """The reply's text, or None if the prompt or the answer was blocked."""
        if message is None:
            return None
        meta = message.response_metadata or {}
        block = (meta.get("prompt_feedback") or {}).get("block_reason")
        if block:
            logger.warning("Gemini %s blocked a %s prompt: %s", model, what, block)
            return None
        reason = str(meta.get("finish_reason") or "")
        if not reason and not message.content:
            logger.warning("Gemini %s returned no candidates for a %s request", model, what)
            return None
        if reason in _GEMINI_BLOCKED_NAMES:
            logger.warning("Gemini %s stopped a %s request: %s", model, what, reason)
            return None
        return message.text or ""

    async def complete_text(
        self, model: str, system: str, user: str, max_tokens: int = 4096, role: str = ""
    ) -> str:
        chat = self._chat(model, max_tokens)
        messages = [SystemMessage(system), HumanMessage(user)]
        response = await self._call(model, self._request(chat, messages), role)
        return self._usable_text(model, response, "generation") or ""

    async def complete_json(
        self, model: str, system: str, user: str, schema: dict, max_tokens: int = 1024,
        role: str = "", image: bytes | None = None,
    ) -> dict:
        """One-shot JSON call constrained by `schema`: LangChain's
        with_structured_output on Gemini's native structured output.
        `image` (PNG bytes) goes before the text, for vision extraction."""
        structured = self._chat(model, max_tokens, self._checker_thinking).with_structured_output(
            schema, method="json_schema", include_raw=True)
        content = ([{"type": "image_url", "image_url": f"data:image/png;base64,{base64.b64encode(image).decode()}"},
                    {"type": "text", "text": user}] if image else user)
        messages = [SystemMessage(system), HumanMessage(content)]
        result = await self._call(model, self._request(structured, messages), role)
        if self._usable_text(model, result.get("raw"), "checker") is None:
            return {}
        parsed = result.get("parsed")
        if result.get("parsing_error") is not None or not isinstance(parsed, dict):
            # Includes output truncated at max_tokens (Gemini counts thinking
            # tokens against that budget). The checker contract stands:
            # malformed replies are treated as a "no".
            logger.warning("Checker returned malformed JSON for model %s", model)
            return {}
        return parsed


# --- Anthropic ---------------------------------------------------------------------


class ClaudeLLM(_BackoffLLM):
    def __init__(self, client: AsyncAnthropic, **backoff):
        super().__init__(**backoff)
        self._client = client

    def _retry_delay(self, exc: Exception) -> float | None:
        if isinstance(exc, anthropic.RateLimitError):
            return 0.0
        if isinstance(exc, anthropic.APIStatusError) and exc.status_code == 529:  # overloaded
            return 0.0
        return None

    def _token_counts(self, response) -> dict:
        usage = getattr(response, "usage", None)
        if usage is None:
            return dict.fromkeys(TOKEN_FIELDS)
        # No thinking on these calls; Anthropic would count it in output_tokens.
        return {"prompt_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                "thinking_tokens": None}

    async def complete_text(
        self, model: str, system: str, user: str, max_tokens: int = 4096, role: str = ""
    ) -> str:
        response = await self._call(
            model,
            lambda: self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            ),
            role,
        )
        if response.stop_reason == "refusal":
            logger.warning("Model %s refused a generation request", model)
            return ""
        return "".join(block.text for block in response.content if block.type == "text")

    async def complete_json(
        self, model: str, system: str, user: str, schema: dict, max_tokens: int = 1024,
        role: str = "", image: bytes | None = None,
    ) -> dict:
        """One-shot JSON call constrained by `schema` (structured outputs).
        `image` (PNG bytes) goes before the text, for vision extraction."""
        content = user if not image else [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.b64encode(image).decode()}},
            {"type": "text", "text": user}]
        response = await self._call(
            model,
            lambda: self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            ),
            role,
        )
        if response.stop_reason == "refusal":
            logger.warning("Model %s refused a checker request", model)
            return {}
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Structured outputs should make this impossible, but the checker
            # contract stands: malformed replies are treated as a "no".
            logger.warning("Checker returned malformed JSON for model %s", model)
            return {}


# --- error classification --------------------------------------------------------------


def classify_error(exc: BaseException) -> str | None:
    """Provider-neutral kind for an LLM error that escaped the gateway's
    retries: "daily_quota", "rate_limited", "unavailable", "timeout", or None if it
    isn't a provider capacity error. Lets the API report what failed without
    leaking raw provider messages."""
    if isinstance(exc, TimeoutError):
        return "timeout"
    if (api := _api_error(exc)) is not None:
        exc = api
        if exc.code == 429:
            daily = any(
                "PerDay" in violation.get("quotaId", "")
                for detail in _gemini_error_details(exc)
                for violation in detail.get("violations", [])
            )
            return "daily_quota" if daily else "rate_limited"
        if exc.code in (500, 503):
            return "unavailable"
    if isinstance(exc, anthropic.RateLimitError):
        return "rate_limited"
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code in (500, 529):
        return "unavailable"
    return None


# --- provider selection --------------------------------------------------------------


def build_llm(settings) -> GeminiLLM | ClaudeLLM:
    """The configured provider's gateway. Fails loudly if its key is missing."""
    backoff = {"max_attempts": settings.llm_max_attempts}
    if settings.llm_provider == "gemini":
        if not settings.gemini_api_key:
            raise RuntimeError("LLM_PROVIDER=gemini but GEMINI_API_KEY is not set")
        return GeminiLLM(
            genai.Client(api_key=settings.gemini_api_key),
            checker_thinking_level=settings.grading_thinking_level,
            request_timeout_s=settings.llm_request_timeout_s,
            call_timeout_s=settings.llm_call_timeout_s,
            **backoff,
        )
    if not settings.anthropic_api_key:
        raise RuntimeError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
    # SDK-level retries off: the gateway's own backoff is the single retry layer.
    return ClaudeLLM(AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=0), **backoff)
