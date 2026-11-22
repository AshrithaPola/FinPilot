"""The planner's worked examples must not hand it an eval question's answer:
no example shares a transaction category plus a month, a dollar amount, or a
security (ticker or company name) with any question in eval/questions.jsonl."""
import re

from engine.plan import PLANNER_SYSTEM
from eval import questions as ev

CATEGORIES = ("groceries", "dining", "transport", "shopping", "health", "home", "travel", "housing",
              "subscriptions", "income", "transfer", "cash", "card_payment", "payment", "interest")
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december")
SECURITIES = {"VTI": ("vti",), "BND": ("bnd",), "SCHD": ("schd",), "AAPL": ("aapl", "apple"),
              "MSFT": ("msft", "microsoft"), "KO": ("ko", "coca-cola", "coke")}


def examples() -> list[str]:
    """The quoted questions in the planner's "How to plan common questions" list."""
    block = PLANNER_SYSTEM.split("How to plan common questions:", 1)[1].split("{today}", 1)[0]
    return re.findall(r'\("([^"]+)"\)', block)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z][a-z_-]*", text.lower()))


def _amounts(text: str) -> set[str]:
    return {a.replace(",", "").rstrip("0").rstrip(".") for a in re.findall(r"\$(\d[\d,]*(?:\.\d+)?)", text)}


def _securities(text: str) -> set[str]:
    words = _words(text) | set(re.findall(r"\b[A-Z]{2,5}\b", text.upper()))
    return {t for t, names in SECURITIES.items() if any(n in words or n.upper() in words for n in names)}


def overlaps(example: str, question: str) -> list[str]:
    e, q = _words(example), _words(question)
    found = []
    shared_category = {c for c in CATEGORIES if c in e and c in q}
    shared_month = {m for m in MONTHS if m in e and m in q}
    if shared_category and shared_month:
        found.append(f"category {', '.join(shared_category)} + month {', '.join(shared_month)}")
    if amounts := _amounts(example) & _amounts(question):
        found.append(f"amount ${', $'.join(sorted(amounts))}")
    if securities := _securities(example) & _securities(question):
        found.append(f"security {', '.join(sorted(securities))}")
    return found


def test_the_check_catches_the_old_examples():
    assert overlaps("how much did I spend on groceries in May", "How much did I spend on groceries in May 2026?")
    assert overlaps("why was I charged $15 on ...", "Why was I charged $15 on April 6, 2026?")
    assert overlaps("should I buy more VTI?", "What's VTI's expense ratio?")
    assert overlaps("should I sell my Apple shares?", "What supply-chain risks did Apple list?")
    assert not overlaps("what did transport cost me in February?", "How much did I spend on groceries in May?")


def test_no_planner_example_overlaps_an_eval_question():
    found = examples()
    assert len(found) >= 5  # the list is read: charge, affordability, spending, comparing, advice
    questions, errors = ev.load()
    assert not errors
    clashes = [f"{ex!r} ~ {q.id}: {'; '.join(why)}" for ex in found for q in questions
               if (why := overlaps(ex, q.question))]
    assert clashes == []
