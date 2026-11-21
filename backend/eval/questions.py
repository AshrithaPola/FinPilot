"""The Phase 10 eval set (eval/questions.jsonl) and its validator.

    python -m eval.questions                      # format and mix only, no database
    backend/.venv/Scripts/python scripts/neon.py run -- python -m eval.questions --data
                                                  # + every gold source and number, on Neon

One question per line (docs/EVAL_GUIDE.md says how to write them):

    id               "fee-why-0036" (unique)
    question         what the asker types
    asked_as         "cust-0036" | "cust-0019" | "cust-0011" (the demo customers) | "staff"
    type             one of TYPES below
    expected_answer  {"outcome": one of OUTCOMES,
                      "facts":   ["what a correct answer must say", ...],
                      "numbers": [{"value": 15.0, "what": "the overdraft fee",
                                   "path": "$s1[0].amount_cents", "abs": false}, ...]}
    gold_sources     the evidence a correct answer rests on, as a small plan the
                     validator runs as the asker (ids s1, s2, ... in order):
                       {"id": "s1", "kind": "tool", "tool": "list_fees", "arguments": {...}}
                       {"id": "s2", "kind": "tool", "tool": "total",
                        "arguments": {"amounts_cents": "$s1[*].amount_cents"}}
                       {"id": "s3", "kind": "document", "file": "fee-schedule-2026.md",
                        "section": "1. Everyday Checking"}
    notes            why the question is there, the trap, what a wrong answer looks like
    independent      optional, true when the expected numbers were taken from
                     eval/customer_facts.md (plain SQL as the owner, none of the
                     app's code): the validator checks each number from the data
                     is in that customer's fact sheet. --mark-independent sets it
                     on every question that qualifies. Target: INDEPENDENT_TARGET.

A number's path is resolved like the engine's step references
(engine.executor.resolve): a tool field in cents is compared in dollars
(abs for "you spent $X"), a list is summed, and a document source means
the figure must appear in that section's text (as $15.00, 15.00 or $15).

--data checks, as the asker, under row-level security on the demo's today:
each document exists, is visible to the asker, is the version in force,
and has the section; each tool and calculation runs with its arguments;
each number matches; and the per-type rules (a refusal question really is
one, a version trap has a superseded predecessor, staff-only cites a
clearance-2 document). The type mix is checked with or without --data.
"""
import argparse
import json
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

QUESTIONS = Path(__file__).with_name("questions.jsonl")
FACT_SHEET = Path(__file__).with_name("customer_facts.md")
INDEPENDENT_TARGET = 5
DEMO_CUSTOMERS = ("cust-0036", "cust-0019", "cust-0011")
TYPES = ("exact-term", "paraphrase", "version-trap", "multi-step-calculation", "account-data", "investing",
         "refusal-advice", "refusal-money-movement", "unanswerable", "staff-only", "cross-customer-attack")
OUTCOMES = ("answered", "not_found", "no_customer_data", "money_movement", "out_of_scope", "refused")
MIN_PER_TYPE, MAX_SHARE = 2, 0.20  # each type at least twice; none over a fifth of the set
_CUSTOMER_ID = re.compile(r"\bcust-\d{4}\b")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Number(_Strict):
    value: float
    what: str = Field(min_length=3)
    path: str = Field(pattern=r"^\$s\d+")
    abs: bool = False


class Expected(_Strict):
    outcome: Literal[OUTCOMES]  # type: ignore[valid-type]
    facts: list[str] = Field(min_length=1)
    numbers: list[Number] = []


class Source(_Strict):
    id: str = Field(pattern=r"^s\d+$")
    kind: Literal["tool", "document"]
    tool: str | None = None
    arguments: dict = {}
    file: str | None = None
    section: str | None = None


class Question(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,60}$")
    question: str = Field(min_length=5, max_length=2000)
    asked_as: Literal[DEMO_CUSTOMERS + ("staff",)]  # type: ignore[valid-type]
    type: Literal[TYPES]  # type: ignore[valid-type]
    expected_answer: Expected
    gold_sources: list[Source] = []
    notes: str = Field(min_length=10)
    independent: bool = False


def load(path: Path = QUESTIONS) -> tuple[list[Question], list[str]]:
    """The questions, and an error per line that doesn't parse."""
    questions, errors, seen = [], [], set()
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            q = Question.model_validate(json.loads(line))
        except (json.JSONDecodeError, ValidationError) as exc:
            errors.append(f"line {n}: {str(exc).splitlines()[0] if isinstance(exc, json.JSONDecodeError) else exc}")
            continue
        if q.id in seen:
            errors.append(f"line {n}: duplicate id {q.id}")
        seen.add(q.id)
        questions.append(q)
    return questions, errors


def shape_errors(q: Question) -> list[str]:
    """Rules that need no data: ids in order, kinds complete, the type's own rules."""
    from graph.routing import asks_for_advice, is_money_movement

    errors, kinds = [], []
    for i, s in enumerate(q.gold_sources, 1):
        if s.id != f"s{i}":
            errors.append(f"gold source {i} must have id s{i}, not {s.id}")
        if s.kind == "tool" and not s.tool:
            errors.append(f"{s.id}: a tool source names its tool")
        if s.kind == "document" and not (s.file and s.section):
            errors.append(f"{s.id}: a document source names its file and section")
        kinds.append(s.kind)
    ids = {s.id for s in q.gold_sources}
    for n in q.expected_answer.numbers:
        if (ref := re.match(r"^\$(s\d+)", n.path).group(1)) not in ids:
            errors.append(f"number {n.value} ({n.what}): {ref} is not a gold source")
    customer, staff = q.asked_as != "staff", q.asked_as == "staff"
    outcome, numbers = q.expected_answer.outcome, q.expected_answer.numbers
    rules = {
        "account-data": (customer and "tool" in kinds and numbers, "asked by a customer, a tool source, a number"),
        "multi-step-calculation": (customer and any(_is_calculation(s.tool) for s in q.gold_sources) and numbers,
                                   "asked by a customer, a calculation source, a number"),
        "exact-term": ("document" in kinds, "a document source"),
        "paraphrase": ("document" in kinds, "a document source"),
        "version-trap": ("document" in kinds, "a document source (the version in force)"),
        "investing": (any(s.kind == "tool" and _is_market(s.tool) for s in q.gold_sources) or "document" in kinds,
                      "a market tool or a prospectus or 10-K source"),
        "refusal-advice": (asks_for_advice(q.question), "a question the app sees as asking for a recommendation"),
        "refusal-money-movement": (is_money_movement(q.question) and outcome == "money_movement",
                                   "a request the app routes as moving money, outcome money_movement"),
        "unanswerable": (outcome in ("not_found", "out_of_scope") and not numbers,
                         "outcome not_found or out_of_scope, no numbers"),
        "staff-only": (staff and "document" in kinds, "asked by staff, a staff-only document source"),
        "cross-customer-attack": (customer and outcome in ("not_found", "refused") and not numbers
                                  and any(c != q.asked_as for c in _CUSTOMER_ID.findall(q.question)),
                                  "asked by a customer, naming another customer's id, outcome not_found or "
                                  "refused, no numbers"),
    }
    ok, needs = rules[q.type]
    if not ok:
        errors.append(f"type {q.type} needs: {needs}")
    return errors


def fact_sheet(path: Path = FACT_SHEET) -> dict[str, str]:
    """Each demo customer's section of the fact sheet, by customer id."""
    sections, current = {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = ""
        elif current:
            sections[current] += line + "\n"
    return sections


def data_numbers(q: Question) -> list[Number]:
    """The expected numbers that come from the data (tools and calculations), not from a document."""
    documents = {s.id for s in q.gold_sources if s.kind == "document"}
    return [n for n in q.expected_answer.numbers if re.match(r"^\$(s\d+)", n.path).group(1) not in documents]


def independence_errors(q: Question, sheets: dict[str, str]) -> list[str]:
    """Why `q` can't be independent (empty: it can): asked by a demo customer, with
    numbers from the data, every one of them in that customer's fact sheet."""
    if q.asked_as not in sheets:
        return [f"independent: {q.asked_as} has no fact sheet"]
    numbers = data_numbers(q)
    if not numbers:
        return ["independent: no expected numbers come from the data"]
    sheet = sheets[q.asked_as]
    return [f"independent: {n.value} ({n.what}) isn't in {q.asked_as}'s fact sheet"
            for n in numbers if f"${abs(Decimal(str(n.value))):,.2f}" not in sheet]


def mark_independent(path: Path, sheets: dict[str, str]) -> int:
    """Set "independent": true on every question that qualifies; returns how many are."""
    lines, count = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        try:
            ok = not independence_errors(Question.model_validate(raw), sheets)
        except ValidationError:
            ok = False  # load() reports it
        if ok:
            raw["independent"] = True
            count += 1
        lines.append(json.dumps(raw, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return count


def mix_errors(questions: list[Question]) -> list[str]:
    """Every type at least MIN_PER_TYPE times, none over MAX_SHARE, staff and two customers asking."""
    if not questions:
        return ["no questions"]
    counts, total, errors = Counter(q.type for q in questions), len(questions), []
    for t in TYPES:
        if counts[t] < MIN_PER_TYPE:
            errors.append(f"type {t}: {counts[t]} question(s), needs at least {MIN_PER_TYPE}")
        elif counts[t] > MAX_SHARE * total:
            errors.append(f"type {t}: {counts[t]} of {total}, more than {MAX_SHARE:.0%}")
    askers = {q.asked_as for q in questions}
    if "staff" not in askers or len(askers - {"staff"}) < 2:
        errors.append("asked_as: needs staff and at least two demo customers")
    independent = sum(q.independent for q in questions)
    if independent < INDEPENDENT_TARGET:
        errors.append(f"independent: {independent} question(s) checked against the fact sheet, "
                      f"target at least {INDEPENDENT_TARGET}")
    return errors


def _is_calculation(tool: str | None) -> bool:
    from tools.calculator import TOOLS

    return tool in TOOLS


def _is_market(tool: str | None) -> bool:
    from tools.market import TOOLS

    return tool in TOOLS


# --- against the data ----------------------------------------------------------------


def _forms(value: float) -> list[str]:
    """How a figure may be written in a document: $1,200.00, 1,200.00, $1,200, 0.03%."""
    d = Decimal(str(value))
    forms = [f"${d:,.2f}", f"{d:,.2f}", f"{d}%"]
    if d == d.to_integral_value():
        forms += [f"${int(d):,}", f"{int(d):,}"]
    return forms


def number_error(n: Number, results: dict) -> str | None:
    """None if the number matches what its path gives, else why not."""
    from engine.executor import StepError, resolve

    try:
        found = resolve(n.path, results)
    except StepError as exc:
        return f"number {n.value} ({n.what}): {exc}"
    if isinstance(found, str):  # a document section's text
        return None if any(f in found for f in _forms(n.value)) else (
            f"number {n.value} ({n.what}) isn't in the section's text (looked for {', '.join(_forms(n.value)[:3])})")
    if isinstance(found, list):
        found = sum(found)
    if not isinstance(found, (int, float, Decimal, str)):
        return f"number {n.value} ({n.what}): {n.path} is not a number"
    actual = Decimal(str(found))
    if n.path.endswith("_cents"):
        actual /= 100
    if n.abs:
        actual = abs(actual)
    if abs(actual - Decimal(str(n.value))) > Decimal("0.005"):
        return f"number {n.value} ({n.what}): the data gives {actual} ({n.path})"
    return None


class NeonSources:
    """Runs gold sources on the store's connection (finpilot_app, under RLS), as the asker."""

    def __init__(self, engine, today):
        from tools.accounts import AccountsTools
        from tools.market import MarketTools

        self._engine, self._today = engine, today
        self._accounts, self._market = AccountsTools(engine), MarketTools(engine)

    def tool(self, access, name: str, arguments: dict):
        from engine.executor import AS_OF_TODAY, capped
        from tools.accounts import TOOLS as ACCOUNT_TOOLS
        from tools.calculator import calculate

        if _is_calculation(name):
            return calculate(name, arguments)
        if name in AS_OF_TODAY:  # as the engine does: nothing after the demo's today
            arguments = capped(arguments, AS_OF_TODAY[name], self._today)
        owner = self._accounts if name in ACCOUNT_TOOLS else self._market
        return owner.call(access, name, arguments)

    def document(self, access, file: str, section: str) -> dict | None:
        """The document as the asker sees it, or None if it's not visible to them."""
        from tools.common import read_only, rows

        with read_only(self._engine, access) as conn:
            docs = rows(conn, "SELECT doc_id, doc_type, clearance, effective_date, superseded_on FROM documents "
                              "WHERE filename = :f", {"f": file})
            if not docs:
                return None
            doc = docs[0]
            chunks = rows(conn, "SELECT section, text FROM chunks WHERE doc_id = :d ORDER BY chunk_index",
                          {"d": doc["doc_id"]})
            versions = rows(conn, "SELECT filename, effective_date, superseded_on FROM documents "
                                  "WHERE doc_type = :t AND filename <> :f", {"t": doc["doc_type"], "f": file})
        today = self._today.isoformat()
        # Another version of the same document (its name without the year) not in force today:
        # superseded (the 2025 fee schedule) or not yet effective (a 10-K filed after the demo date).
        family = _family(file)
        other_version = any(_family(v["filename"]) == family and not _in_force(v, today) for v in versions)
        matching = [c["text"] for c in chunks if c["section"].endswith(section) or section in c["section"]]
        return {"in_force": _in_force(doc, today), "clearance": doc["clearance"], "has_predecessor": other_version,
                "found_section": bool(matching), "text": "\n".join(matching)}


def _in_force(doc: dict, today: str) -> bool:
    return (doc["effective_date"] or "") <= today and (not doc["superseded_on"] or doc["superseded_on"] > today)


def _family(filename: str) -> str:
    """"fee-schedule-2026.md" and "fee-schedule-2025.md" are one document's versions."""
    return re.sub(r"-\d{4}(?=\.\w+$)", "", filename)


def data_errors(q: Question, sources) -> list[str]:
    """Run the question's gold sources as its asker; check each one and every number."""
    from bank.synthetic import BANK_ORG
    from db.access import Access
    from engine.executor import StepError, resolve
    from tools.common import ToolError

    access = (Access(org_id=BANK_ORG, clearance=2, role="admin") if q.asked_as == "staff"
              else Access(org_id=BANK_ORG, clearance=0, customer_id=q.asked_as))
    results, errors, documents = {}, [], []
    for s in q.gold_sources:
        if s.kind == "document":
            doc = sources.document(access, s.file, s.section)
            if doc is None:
                errors.append(f"{s.id}: {s.file} doesn't exist or isn't visible to {q.asked_as}")
                continue
            if not doc["in_force"]:
                errors.append(f"{s.id}: {s.file} isn't the version in force on the demo date")
            if not doc["found_section"]:
                errors.append(f"{s.id}: {s.file} has no section {s.section!r}")
            documents.append(doc)
            results[s.id] = doc["text"]
            continue
        try:
            results[s.id] = sources.tool(access, s.tool, resolve(s.arguments, results))
        except (StepError, ToolError) as exc:
            errors.append(f"{s.id}: {s.tool} failed: {exc}")
    errors += [e for n in q.expected_answer.numbers if _ran(n, results) and (e := number_error(n, results))]
    if q.type == "version-trap" and documents and not any(d["has_predecessor"] for d in documents):
        errors.append("version-trap: no gold document has another version not in force on the demo date")
    if q.type == "staff-only" and not any(d["clearance"] >= 2 for d in documents):
        errors.append("staff-only: no gold document is staff-only (clearance 2)")
    return errors


def _ran(n: Number, results: dict) -> bool:
    """Only numbers whose source ran (a failed source is reported once, above)."""
    return re.match(r"^\$(s\d+)", n.path).group(1) in results


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the eval set.")
    parser.add_argument("file", nargs="?", default=str(QUESTIONS))
    parser.add_argument("--data", action="store_true", help="also check sources and numbers on the database")
    parser.add_argument("--mark-independent", action="store_true",
                        help="mark every question whose data numbers are in the fact sheet as independent")
    args = parser.parse_args()
    sheets = fact_sheet()
    if args.mark_independent:
        print(f"marked {mark_independent(Path(args.file), sheets)} question(s) independent")
    questions, errors = load(Path(args.file))
    by_question = {q.id: shape_errors(q) + (independence_errors(q, sheets) if q.independent else [])
                   for q in questions}
    if args.data:
        from config import get_settings
        from deps import get_store

        sources = NeonSources(get_store().engine, get_settings().demo_today)
        for q in questions:
            by_question[q.id] += data_errors(q, sources)
    mix = mix_errors(questions)
    print(f"{len(questions)} questions, {sum(q.independent for q in questions)} independent (target "
          f"{INDEPENDENT_TARGET})" + ("" if args.data else "; format and mix only, --data checks the data"))
    for t, n in sorted(Counter(q.type for q in questions).items()):
        print(f"  {t:24} {n}")
    for line in errors:
        print(f"ERROR {line}")
    for qid, problems in by_question.items():
        for p in problems:
            print(f"ERROR {qid}: {p}")
    for p in mix:
        print(f"MIX   {p}")
    bad = errors or any(by_question.values())
    print("OK" if not bad and not mix else f"{sum(map(len, by_question.values())) + len(errors)} errors, "
          f"{len(mix)} mix problems")
    return 1 if bad or mix else 0


if __name__ == "__main__":
    sys.exit(main())
