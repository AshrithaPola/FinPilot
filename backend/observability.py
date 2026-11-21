"""Langfuse: the engine's LangGraph runs (nodes, LLM calls, tools, the
retriever), sent through Langfuse's LangChain callback handler, masked
before anything leaves the app.

What Langfuse gets is for watching the system (latency, model calls,
retries, the shape of each run), not a record of anyone's money: every
input, output and metadata value goes through `mask` first, which replaces

  - amounts: dollar figures in text ("$1,200.00", "-$15.00", "$47.94
    billion") and every *_cents / *_dollars / amount / balance field;
  - merchant names (the synthetic merchants and subscriptions, and every
    merchant / description field);
  - account, card, transaction and fee ids, and customer ids (cust-0036...);
  - customer names (the synthetic first and last names, full_name fields)
    and email addresses; card last-4 digits;
  - who asked: the user, Firebase, org, team and customer ids in the access
    object (and anywhere else under those names) become salted one-way
    hashes ("user:3f9a..."), so a user's traces still group together
    without the id itself leaving (OBSERVABILITY_SALT; unset, "[id]").

The trace reads as our pipeline: LangChain's internals (sequences,
lambdas, parallel maps, output parsers, LangGraph's channel writes) are
left out, and their children hang on the nearest step we name: the
nodes (intake, route, plan, execute, replan, summarize, validate),
llm:<role>, tool:<name> and search.

The full, access-controlled record of every request stays in Postgres
(tracing.py, the traces table under row-level security). Langfuse is on
only when LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL
(or LANGFUSE_HOST) are all set; otherwise `callbacks()` is empty and
nothing is sent.
"""
import dataclasses
import hashlib
import hmac
import re
from functools import lru_cache

from bank.synthetic import FIRST, FOREIGN_MERCHANTS, LAST, MERCHANTS, SUBSCRIPTIONS
from config import get_settings

AMOUNT, MERCHANT, ID, NAME, EMAIL, MASKED = "[amount]", "[merchant]", "[id]", "[name]", "[email]", "[masked]"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_ID = re.compile(r"\bcust-\d{4}(?:-[a-z0-9]+)?\b", re.I)
_AMOUNT = re.compile(r"-?\$\s?-?\d[\d,]*(?:\.\d+)?(?:\s+(?:thousand|million|billion|trillion)\b)?", re.I)
_NAME = re.compile(rf"\b(?:{'|'.join(FIRST)})\s+(?:{'|'.join(LAST)})\b")
_MERCHANTS = sorted({m[0] for m in (*MERCHANTS, *FOREIGN_MERCHANTS, *SUBSCRIPTIONS)}, key=len, reverse=True)
_MERCHANT = re.compile("|".join(re.escape(m) for m in _MERCHANTS))
# Sensitive fields inside JSON text (the evidence as the summarizer reads it).
_MONEY_FIELD = re.compile(r'("[a-z_]*(?:_cents|_dollars)"|"(?:amount|balance)")(\s*:\s*)(\[[^\]]*\]|"[^"]*"|-?[\d.]+)',
                          re.I)
_TEXT_FIELD = re.compile(r'("(?:merchant|description|full_name|email|last4)")(\s*:\s*)("[^"]*")')
_MONEY_KEYS = ("_cents", "_dollars")
_FIELD_MASKS = {"amount": AMOUNT, "balance": AMOUNT, "merchant": MERCHANT, "description": MERCHANT,
                "full_name": NAME, "email": EMAIL, "last4": MASKED}
# Identifiers of who asked (auth.Principal, db.access.Access): hashed, with what they are.
_IDENTIFIERS = {"user_id": "user", "firebase_uid": "user", "org_id": "org", "team_ids": "team",
                "customer_id": "customer", "uploaded_by": "user"}


def pseudonym(value, kind: str = "user") -> str:
    """A salted one-way hash of an id: the same id always gives the same
    pseudonym (traces group by user), and it can't be turned back."""
    salt = get_settings().observability_salt
    if not salt or value in (None, ""):
        return ID
    digest = hmac.new(salt.encode(), f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()[:16]
    return f"{kind}:{digest}"


def mask_text(text: str) -> str:
    text = _MONEY_FIELD.sub(lambda m: f'{m[1]}{m[2]}"{AMOUNT}"', text)
    text = _TEXT_FIELD.sub(lambda m: f'{m[1]}{m[2]}"{_FIELD_MASKS[m[1].strip(chr(34))]}"', text)
    text = _EMAIL.sub(EMAIL, text)
    text = _ID.sub(ID, text)
    text = _AMOUNT.sub(AMOUNT, text)
    text = _NAME.sub(NAME, text)
    return _MERCHANT.sub(MERCHANT, text)


def _field(key, value):
    name = str(key).casefold()
    if name in _IDENTIFIERS:
        if value is None:
            return None
        if isinstance(value, (list, tuple, set)):
            return [pseudonym(v, _IDENTIFIERS[name]) for v in value]
        return pseudonym(value, _IDENTIFIERS[name])
    if name.endswith(_MONEY_KEYS) or name in _FIELD_MASKS:
        if value is None:
            return None
        return _FIELD_MASKS.get(name, AMOUNT)
    return masked(value)


def masked(value):
    """`value` with everything above replaced, as JSON-serializable data."""
    if isinstance(value, str):
        return mask_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        return {str(k): _field(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [masked(v) for v in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return masked(dataclasses.asdict(value))
    if hasattr(value, "model_dump"):  # LangChain messages, documents, Pydantic models
        return masked(value.model_dump())
    return mask_text(repr(value))  # anything else: its text, masked


def mask(*, data, **kwargs):
    """Langfuse's mask hook: runs on every observation's input, output and metadata."""
    return masked(data)


def _configured():
    s = get_settings()
    if s.langfuse_public_key and s.langfuse_secret_key and s.langfuse_base_url:
        return s.langfuse_public_key, s.langfuse_secret_key, s.langfuse_base_url
    return None


@lru_cache
def _client(public_key: str, secret_key: str, base_url: str):
    from langfuse import Langfuse

    return Langfuse(public_key=public_key, secret_key=secret_key, base_url=base_url, mask=mask)


# LangChain and LangGraph plumbing: never an observation of its own.
_FRAMEWORK_PREFIXES = ("Runnable", "ChannelWrite", "ChannelRead", "Branch<", "_write", "_route")
_FRAMEWORK_SUFFIXES = ("OutputParser", "Parser")
HIDDEN_TAG = "langsmith:hidden"  # LangGraph tags its own internals with it


def is_framework_run(name: str, tags=None) -> bool:
    return (HIDDEN_TAG in (tags or ()) or name.startswith(_FRAMEWORK_PREFIXES)
            or name.endswith(_FRAMEWORK_SUFFIXES) or name == "<unknown>")


@lru_cache
def _pipeline_handler_class():
    from langfuse.langchain import CallbackHandler

    class PipelineHandler(CallbackHandler):
        """Langfuse's LangChain handler, showing our pipeline: a framework
        run gets no observation, and its children attach to the nearest
        run that has one (the root run is always kept)."""

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._skipped: dict = {}  # skipped run id -> its parent run id

        def _get_parent_observation(self, parent_run_id):
            while parent_run_id in self._skipped:
                parent_run_id = self._skipped[parent_run_id]
            return super()._get_parent_observation(parent_run_id)

        def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, tags=None, metadata=None,
                           **kwargs):
            if parent_run_id is not None and is_framework_run(self.get_langchain_run_name(serialized, **kwargs), tags):
                self._track_run(run_id=run_id, parent_run_id=parent_run_id, metadata=metadata)
                self._skipped[run_id] = parent_run_id
                return None
            return super().on_chain_start(serialized, inputs, run_id=run_id, parent_run_id=parent_run_id, tags=tags,
                                          metadata=metadata, **kwargs)

        def on_chain_end(self, outputs, *, run_id, parent_run_id=None, **kwargs):
            if run_id in self._skipped:
                return None
            return super().on_chain_end(outputs, run_id=run_id, parent_run_id=parent_run_id, **kwargs)

        def on_chain_error(self, error, *, run_id, parent_run_id=None, tags=None, **kwargs):
            if run_id in self._skipped:
                return None
            return super().on_chain_error(error, run_id=run_id, parent_run_id=parent_run_id, tags=tags, **kwargs)

    return PipelineHandler


def callbacks() -> list:
    """The LangChain callbacks for one engine run: Langfuse's handler, or none."""
    keys = _configured()
    if keys is None:
        return []
    _client(*keys)
    return [_pipeline_handler_class()(public_key=keys[0])]


def flush() -> None:
    """Send what's buffered (a CLI run exits before the background export)."""
    if (keys := _configured()) is not None:
        _client(*keys).flush()
