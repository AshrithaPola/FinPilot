"""The executor: runs a plan's steps in order, each through its agent, as
the caller. Plain code, except the documents agent's one retry below.

  financial_data  the accounts tools (the caller's own data, under RLS) and
                  the market tools (public reference data)
  documents       hybrid search over the documents the caller may see; when
                  the evidence is weak (nothing found, a low rerank score,
                  or no passage holding most of the query's words) it first
                  drops a doc_type guess, then asks an LLM to rewrite the
                  query (keeping the document, company or date the question
                  names) and searches once more; still weak, or not about
                  that target, the step fails as not found rather than
                  answering from another document
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
from ingestion.retriever import StoreRetriever, chunk_of
from tools.accounts import TOOLS as ACCOUNT_TOOLS
from tools.calculator import calculate
from tools.common import AccessRefused, ToolError, langchain_tool, run_langchain_tool
from tools.market import TOOLS as MARKET_TOOLS
import tracing

_REF = re.compile(r"^\$(s\d+)((?:\[(?:\d+|\*)\]|\.[a-z_]+)*)$")
_PART = re.compile(r"\[(\d+|\*)\]|\.([a-z_]+)")
MAX_ROWS_SHOWN = 25  # rows of one tool result shown to the summarizer
DOC_TYPES = {"fee_schedule", "policy", "agreement", "rate_table", "product_comparison", "api_spec",
             "sec_10k", "fund_prospectus", "consumer_guide", "internal_memo"}
BANK_TERMS = ("fee_schedule", "policy", "agreement", "rate_table", "product_comparison")  # one family to search
# Market tools and the argument that dates them: missing means "today", and
# nothing later than today is ever read (the demo's world ends on DEMO_TODAY).
AS_OF_TODAY = {"get_price": "as_of", "get_rate": "as_of", "get_company_financials": "as_of",
               "get_price_history": "end"}
RERANK_FLOOR = 0.0  # cross-encoder logit; below it the best passage likely doesn't answer
STOPWORDS = frozenset("the and for with what how does are was were from that this have has about "
                      "which when why can you your my our its into than then them they".split())


class StepError(Exception):
    pass


class NotFound(StepError):
    """The documents don't hold what was asked about; another plan won't find it either."""


def _words(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.casefold()) if len(t) > 2 and t not in STOPWORDS}


def mentions(target: str, text: str) -> bool:
    """Does `text` name the target ("fraud rules memo", "Apple 10-K")? Every
    content word of it appears (as part of a word: "card" in "cardmember")."""
    words, haystack = _words(target), text.casefold()
    return all(w in haystack for w in words) if words else True


def _haystack(hit) -> str:
    return f"{hit.filename} {hit.section} {hit.text}"


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


def capped(arguments: dict, key: str, today: date) -> dict:
    """`arguments` with `key` set to today when it's missing or later; an
    unreadable date is left for the tool's own validation to refuse."""
    value = arguments.get(key)
    try:
        later = value is None or date.fromisoformat(str(value)) > today
    except ValueError:
        return arguments
    return {**arguments, key: today.isoformat()} if later else arguments


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


def _day(iso) -> str | None:
    """"2026-04-06" as "Apr 6, 2026"; None if it isn't a date."""
    try:
        d = date.fromisoformat(str(iso))
    except ValueError:
        return None
    return f"{d:%b} {d.day}, {d.year}"


def _period(args: dict) -> str:
    start, end = _day(args.get("start")), _day(args.get("end"))
    if start and start == end:
        return f" on {start}"
    if start and end:
        return f", {start} to {end}"
    return f" from {start}" if start else f" through {end}" if end else ""


_DOC_WORDS = {"10k": "10-K", "api": "API", "aapl": "AAPL", "msft": "MSFT", "ko": "KO", "vti": "VTI",
              "bnd": "BND", "schd": "SCHD", "cfpb": "CFPB", "etf": "ETF"}
_CALCULATIONS = {"afford": "Affordability", "total": "Total", "interest": "Interest",
                 "percent_change": "Percent change", "market_value": "Market value", "gain": "Gain"}


def doc_label(filename: str, section: str) -> str:
    """"public/fee-schedule-2026.md", "Fees > 1. Overdraft" as "Fee Schedule 2026 › 1. Overdraft"."""
    stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    title = " ".join(_DOC_WORDS.get(w.casefold(), w.capitalize()) for w in re.split(r"[-_\s]+", stem) if w)
    part = (section or "").split(" > ")[-1].strip()
    return f"{title} › {part}" if part else title


def source_label(step: Step, rows: list) -> str:
    """What a customer reads as the source of a tool result: "Your fees on
    Apr 6, 2026", "Checking balance as of Jun 30, 2026". Dates come from the
    step's arguments or the rows themselves, never from the model's text."""
    args = step.arguments if isinstance(step.arguments, dict) else {}
    first = rows[0] if rows and isinstance(rows[0], dict) else {}
    tool = step.tool
    if step.agent == "calculation":
        return f"{_CALCULATIONS.get(tool, tool.replace('_', ' ').capitalize())} (calculated)"
    if tool == "get_balance":
        kind = args.get("account_type") or (first.get("type") if len(rows) == 1 else None)
        dates = [r["as_of"] for r in rows if isinstance(r, dict) and r.get("as_of")]
        as_of = _day(max(dates)) if dates else None
        return (f"{kind.capitalize()} balance" if kind else "Your balances") + (f" as of {as_of}" if as_of else "")
    if tool == "list_fees":
        return "Your fees" + _period(args)
    if tool == "list_transactions":
        category = str(args.get("category") or "").replace("_", " ")
        return f"Your {category + ' ' if category else ''}transactions" + _period(args)
    if tool == "get_price":
        day = _day(first.get("price_date"))
        return f"{first.get('symbol') or args.get('symbol', '')} price" + (f" as of {day}" if day else "")
    if tool == "get_price_history":
        return f"{args.get('symbol', '')} price history" + _period(args)
    if tool == "get_company_financials":
        return f"{first.get('ticker') or args.get('ticker', '')} 10-K: {first.get('label') or args.get('metric', '')}"
    if tool == "get_rate":
        day = _day(first.get("obs_date"))
        return f"{first.get('title') or args.get('series_id', '')}" + (f" as of {day}" if day else "")
    return {"list_accounts": "Your accounts", "list_cards": "Your cards",
            "list_holdings": "Your holdings"}.get(tool, tool.replace("_", " ").capitalize())


class Executor:
    """`rewrite(query, hits) -> str | None` is the documents agent's LLM
    (one call per weak search); `today` the date a missing as_of means."""

    def __init__(self, accounts_tools, market_tools, store, rewrite=None, today: date | None = None,
                 data_from: date | None = None):
        self._accounts, self._market, self._store = accounts_tools, market_tools, store
        self._rewrite, self._today, self._data_from = rewrite, today, data_from

    async def run(self, plan: Plan, principal, results: dict | None = None,
                  evidence: list[dict] | None = None) -> list[dict]:
        """Run the steps still planned (a re-plan adds more, which can use the
        earlier `results`); returns the evidence, numbered from 1."""
        results = {} if results is None else results
        evidence = [] if evidence is None else evidence
        for step in plan.steps:
            if step.status != "planned":
                continue
            with tracing.span("step", f"{step.agent}.{step.tool}", step=step.id, round=step.round) as span:
                try:
                    step.result = await self._step(step, resolve(step.arguments, results), principal)
                    step.status = "done"
                    results[step.id] = step.result
                    step.note = step.note or self._before_records(step)
                except (StepError, ToolError) as exc:
                    step.status, step.error = "failed", str(exc)
                    step.refused = isinstance(exc, AccessRefused)
                    step.retryable = not isinstance(exc, (AccessRefused, NotFound))
                span.update(status=step.status, error=step.error, note=step.note,
                            rows=len(step.result) if isinstance(step.result, list) else None)
            if step.status == "failed":
                continue
            for item in self._evidence(step):
                evidence.append({"n": len(evidence) + 1, **item})
                step.evidence.append(len(evidence))
        return evidence

    def _before_records(self, step: Step) -> str | None:
        """A note for an account-data step asking only about days before the records
        start, so the answer can say why it found nothing."""
        if step.tool not in ACCOUNT_TOOLS or not self._data_from:
            return None
        try:
            end = date.fromisoformat(str(step.arguments.get("end") or step.arguments.get("start") or ""))
        except ValueError:
            return None
        start = self._data_from.isoformat()
        return f" (the customer's records start on {start}: nothing before that)" if end < self._data_from else None

    async def _step(self, step: Step, arguments: dict, principal):
        if step.agent == "calculation":
            return calculate(step.tool, arguments)
        if step.agent == "documents":
            return await self._search(step, arguments, principal)
        if step.tool in AS_OF_TODAY and self._today:
            arguments = capped(arguments, AS_OF_TODAY[step.tool], self._today)
        owner, registry = ((self._accounts, ACCOUNT_TOOLS) if step.tool in ACCOUNT_TOOLS
                           else (self._market, MARKET_TOOLS))
        # A LangChain tool bound to this caller (callbacks see it as a tool run).
        tool = langchain_tool(registry[step.tool], lambda args: owner.call(principal, step.tool, args))
        with tracing.span("tool", step.tool):
            return run_langchain_tool(tool, arguments)

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
        # The bank's own terms are spread over schedules, policies and agreements (a card's
        # late fee is in the fee schedule; the minimum payment that caps it, in the cardmember
        # agreement): a guess at one of those types searches all of them.
        type_filter = BANK_TERMS if doc_type in BANK_TERMS else doc_type
        filters = Filters(doc_type=type_filter, as_of=as_of or self._today)
        store = self._store.scoped(principal)
        hits = await self._traced_search(store, query, filters)
        if type_filter is BANK_TERMS:
            # The family can outrank the guessed type's own rule (an agreement's generic fee
            # clause above the schedule's cushion rule): the guessed type's hits come first.
            own = await self._traced_search(store, query, Filters(doc_type=doc_type, as_of=filters.as_of))
            seen = {h.chunk_id for h in own}
            hits = own + [h for h in hits if h.chunk_id not in seen]
        if as_of and self._today and as_of < self._today:
            # A past date finds the version in force then; today's version is searched too,
            # so the answer can say what changed since.
            current = await self._traced_search(store, query, Filters(doc_type=type_filter, as_of=self._today))
            seen = {h.chunk_id for h in hits}
            hits = hits + [h for h in current if h.chunk_id not in seen]
            step.note = f"searched the version in force on {as_of.isoformat()} and today's"
        if doc_type and weak(query, hits):
            # A wrong type guess (a memo searched as a "policy") hides the right
            # document entirely: search every type once, with no model call.
            filters = Filters(as_of=filters.as_of)
            hits = await self._traced_search(store, query, filters)
            step.note = f"weak evidence as {doc_type}; searched every document type"
        if self._rewrite and weak(query, hits):
            better, target = await self._rewrite(query, hits)
            if better and target and not mentions(target, better):
                better = f"{target} {better}"  # the rewrite keeps what the customer asked about
            if better and better.strip().casefold() != query.casefold() and 2 <= len(better) <= 500:
                again = await self._traced_search(store, better, filters)
                found = not weak(better, again) and (not target or any(mentions(target, _haystack(h)) for h in again))
                step.note = f"weak evidence; searched again as {better!r}" + ("" if found else ", still not found")
                if not found:
                    # Better nothing than an answer from another document.
                    raise NotFound(f"couldn't find {target or query!r} in the documents")
                hits = again
        return [{"chunk_id": h.chunk_id, "filename": h.filename, "section": h.section, "text": h.text}
                for h in hits]

    @staticmethod
    async def _traced_search(store, query: str, filters: Filters) -> list:
        with tracing.span("search", query[: tracing.MAX_TEXT]) as span:
            # Through the store's LangChain retriever (callbacks see it as a retriever run).
            hits = [chunk_of(d) for d in await StoreRetriever(store=store, filters=filters).ainvoke(
                query, config={"run_name": "search"})]
            scores = [h.rerank_score for h in hits if h.rerank_score is not None]
            span.update(hits=len(hits), top_rerank=max(scores) if scores else None, weak=weak(query, hits),
                        sections=[f"{h.filename} > {h.section}"[:120] for h in hits[:3]])
        return hits

    @staticmethod
    def _evidence(step: Step) -> list[dict]:
        if step.agent == "documents":
            return [{"kind": "document", "step": step.id, "chunk_id": h["chunk_id"], "filename": h["filename"],
                     "section": h["section"], "text": h["text"], "label": doc_label(h["filename"], h["section"])}
                    for h in step.result]
        rows = step.result if isinstance(step.result, list) else [step.result]
        label = source_label(step, rows)
        if not rows:
            return [{"kind": "tool", "step": step.id, "tool": step.tool, "data": "no rows", "label": label,
                     "note": step.note or ""}]
        attribution = next((r.get("attribution") for r in rows if isinstance(r, dict) and r.get("attribution")), None)
        shown = presentable(rows[:MAX_ROWS_SHOWN])
        note = (step.note or "") + (f" (first {MAX_ROWS_SHOWN} of {len(rows)} rows)"
                                    if len(rows) > MAX_ROWS_SHOWN else "")
        return [{"kind": "tool", "step": step.id, "tool": step.tool, "attribution": attribution,
                 "data": shown, "note": note, "label": label}]
