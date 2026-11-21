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
    and email addresses; card last-4 digits.

The full, access-controlled record of every request stays in Postgres
(tracing.py, the traces table under row-level security). Langfuse is on
only when LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL
(or LANGFUSE_HOST) are all set; otherwise `callbacks()` is empty and
nothing is sent.
"""
import dataclasses
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


def callbacks() -> list:
    """The LangChain callbacks for one engine run: Langfuse's handler, or none."""
    keys = _configured()
    if keys is None:
        return []
    from langfuse.langchain import CallbackHandler

    _client(*keys)
    return [CallbackHandler(public_key=keys[0])]


def flush() -> None:
    """Send what's buffered (a CLI run exits before the background export)."""
    if (keys := _configured()) is not None:
        _client(*keys).flush()
