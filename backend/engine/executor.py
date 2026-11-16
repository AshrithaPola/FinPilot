"""The executor: runs a plan's steps in order, each through its agent, as
the caller. Plain code, except the documents agent's one retry below.

  financial_data  the accounts tools (the caller's own data, under RLS) and
                  the market tools (public reference data)
  documents       hybrid search over the documents the caller may see; when
                  the evidence is weak (nothing found, a low rerank score,
                  or no passage holding most of the query's words) it asks
                  an LLM to rewrite the query and searches once more
  calculation     the calculator, with references to earlier results
                  resolved here ("$s1[0].balance_cents")

Dates: a search, price or rate with no as_of is as of the demo's today
(DEMO_TODAY), the date "today" means everywhere in the engine.

A step that fails (a refused argument, a reference to nothing) is recorded
with its error and the rest still run; the summarizer says what's missing.
Every result becomes numbered evidence the answer cites: tool rows with
their attribution, document passages with their file and section. Amounts
in cents are also given in dollars, formatted here, so the summarizer
never divides by 100.
"""
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from engine.plan import Plan, Step
from ingestion.store import Filters
from tools.accounts import TOOLS as ACCOUNT_TOOLS
from tools.calculator import calculate
from tools.common import AccessRefused, ToolError

_REF = re.compile(r"^\$(s\d+)((?:\[(?:\d+|\*)\]|\.[a-z_]+)*)$")
_PART = re.compile(r"\[(\d+|\*)\]|\.([a-z_]+)")
MAX_ROWS_SHOWN = 25  # rows of one tool result shown to the summarizer
DOC_TYPES = {"fee_schedule", "policy", "agreement", "rate_table", "product_comparison", "api_spec",
             "sec_10k", "fund_prospectus", "consumer_guide", "internal_memo"}
AS_OF_TODAY = {"get_price", "get_rate"}  # market tools whose missing as_of means "today"
RERANK_FLOOR = 0.0  # cross-encoder logit; below it the best passage likely doesn't answer
STOPWORDS = frozenset("the and for with what how does are was were from that this have has about "
                      "which when why can you your my our its into than then them they".split())


class StepError(Exception):
    pass


def resolve(value, results: dict):
    """Replace "$sN..." references (in strings, lists and dicts) with values
    from earlier steps' results; anything else is returned as it is."""
    if isinstance(value, dict):
        return {k: resolve(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, results) for v in value]
    if not isinstance(value, str) or not value.startswith("$"):
        return value
    match = _REF.match(value)
    if not match or match.group(1) not in results:
        raise StepError(f"{value} refers to no earlier step")
    current = [results[match.group(1)]]
    many = False
    for index, key in _PART.findall(match.group(2)):
        nxt = []
        for item in current:
            try:
                if index == "*":
                    nxt += list(item)
                    many = True
                elif index:
                    nxt.append(item[int(index)])
                else:
                    nxt.append(item[key])
            except (IndexError, KeyError, TypeError):
                raise StepError(f"{value} matches nothing in {match.group(1)}'s result") from None
        current = nxt
    return current if many else current[0]


def dollars(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${Decimal(abs(cents)) / 100:,.2f}"


def _usd(value: str, unit: str | None) -> str | None:
    """A reported figure, readable: "$47.94 billion", "$7.25 per share", "$337.35"."""
    try:
        amount = Decimal(value)
    except (ArithmeticError, TypeError, ValueError):
        return None
    if unit == "USD/shares":
        return f"${amount:,.2f} per share"
    sign = "-" if amount < 0 else ""
    for scale, word in ((Decimal(10) ** 12, "trillion"), (Decimal(10) ** 9, "billion"), (Decimal(10) ** 6, "million")):
        if abs(amount) >= scale:
            short = (abs(amount) / scale).quantize(Decimal("0.01"), ROUND_HALF_UP)
            return f"{sign}${short} {word}"
    return f"{sign}${abs(amount):,.2f}"


def presentable(value):
    """Results as the summarizer reads them, formatted here so it never has to:
    next to every *_cents amount the same amount in dollars, and readable
    forms of SEC figures (value in USD) and prices (close)."""
    if isinstance(value, list):
        return [presentable(v) for v in value]
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out[k] = presentable(v)
            if k.endswith("_cents") and isinstance(v, int) and not isinstance(v, bool):
                out[k[: -len("_cents")] + "_dollars"] = dollars(v)
        if "value" in value and str(value.get("unit", "")).startswith("USD"):
            out["value_readable"] = _usd(value["value"], value["unit"])
        if "close" in value:
            out["close_dollars"] = _usd(value["close"], None)
        return out
    return value


def weak(query: str, hits) -> bool:
    """Is this evidence too weak to answer from? Nothing found, the best
    rerank score under RERANK_FLOOR, or (not reranked) no passage holding at
    least half of the query's content words."""
    if not hits:
        return True
    scores = [h.rerank_score for h in hits if h.rerank_score is not None]
    if scores:
        return max(scores) < RERANK_FLOOR
    terms = {t for t in re.findall(r"[a-z0-9]+", query.casefold()) if len(t) > 2 and t not in STOPWORDS}
    if not terms:
        return False
    return max(sum(t in h.text.casefold() for t in terms) for h in hits) < len(terms) / 2


class Executor:
    """`rewrite(query, hits) -> str | None` is the documents agent's LLM
    (one call per weak search); `today` the date a missing as_of means."""

    def __init__(self, accounts_tools, market_tools, store, rewrite=None, today: date | None = None):
        self._accounts, self._market, self._store = accounts_tools, market_tools, store
        self._rewrite, self._today = rewrite, today

    async def run(self, plan: Plan, principal, results: dict | None = None,
                  evidence: list[dict] | None = None) -> list[dict]:
        """Run the steps still planned (a re-plan adds more, which can use the
        earlier `results`); returns the evidence, numbered from 1."""
        results = {} if results is None else results
        evidence = [] if evidence is None else evidence
        for step in plan.steps:
            if step.status != "planned":
                continue
            try:
                step.result = await self._step(step, resolve(step.arguments, results), principal)
                step.status = "done"
                results[step.id] = step.result
            except (StepError, ToolError) as exc:
                step.status, step.error = "failed", str(exc)
                step.retryable = not isinstance(exc, AccessRefused)
                continue
            for item in self._evidence(step):
                evidence.append({"n": len(evidence) + 1, **item})
                step.evidence.append(len(evidence))
        return evidence

    async def _step(self, step: Step, arguments: dict, principal):
        if step.agent == "calculation":
            return calculate(step.tool, arguments)
        if step.agent == "documents":
            return await self._search(step, arguments, principal)
        if step.tool in AS_OF_TODAY and self._today and not arguments.get("as_of"):
            arguments = {**arguments, "as_of": self._today.isoformat()}
        tools = self._accounts if step.tool in ACCOUNT_TOOLS else self._market
        return tools.call(principal, step.tool, arguments)

    async def _search(self, step: Step, arguments: dict, principal) -> list[dict]:
        query = arguments.get("query")
        if not isinstance(query, str) or not 2 <= len(query) <= 500:
            raise StepError("search needs a query of 2 to 500 characters")
        doc_type = arguments.get("doc_type")
        if doc_type is not None and doc_type not in DOC_TYPES:
            doc_type = None  # an unknown type would only empty the result
        try:
            as_of = date.fromisoformat(arguments["as_of"]) if arguments.get("as_of") else None
        except (TypeError, ValueError):
            raise StepError("as_of must be a date, YYYY-MM-DD") from None
        filters = Filters(doc_type=doc_type, as_of=as_of or self._today)
        store = self._store.scoped(principal)
        hits = await store.search(query, filters=filters)
        if self._rewrite and weak(query, hits):
            better = await self._rewrite(query, hits)
            if better and better.strip().casefold() != query.casefold() and 2 <= len(better) <= 500:
                again = await store.search(better, filters=filters)
                keep = not weak(better, again)
                step.note = f"weak evidence; searched again as {better!r}" + ("" if keep else ", no better")
                hits = again if keep else hits
        return [{"chunk_id": h.chunk_id, "filename": h.filename, "section": h.section, "text": h.text}
                for h in hits]

    @staticmethod
    def _evidence(step: Step) -> list[dict]:
        if step.agent == "documents":
            return [{"kind": "document", "step": step.id, "chunk_id": h["chunk_id"], "filename": h["filename"],
                     "section": h["section"], "text": h["text"]} for h in step.result]
        rows = step.result if isinstance(step.result, list) else [step.result]
        if not rows:
            return [{"kind": "tool", "step": step.id, "tool": step.tool, "data": "no rows"}]
        attribution = next((r.get("attribution") for r in rows if isinstance(r, dict) and r.get("attribution")), None)
        shown = presentable(rows[:MAX_ROWS_SHOWN])
        note = f" (first {MAX_ROWS_SHOWN} of {len(rows)} rows)" if len(rows) > MAX_ROWS_SHOWN else ""
        return [{"kind": "tool", "step": step.id, "tool": step.tool, "attribution": attribution,
                 "data": shown, "note": note}]
