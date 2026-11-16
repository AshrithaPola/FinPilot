"""Ask the engine from the command line, as one synthetic bank customer.

    python -m engine.cli --customer cust-0036 "Can I afford $1,200 this month and stay above $500?"

Uses DATABASE_URL (finpilot_app; e.g. through scripts/neon.py run) and the
configured LLM. Prints each question's plan, its steps, the answer with its
citations, and the LLM calls it took.
"""
import argparse
import asyncio
import json

from bank.synthetic import BANK_ORG
from db.access import Access
from engine.graph import answer


def show(question: str, result: dict) -> str:
    lines = [f"Q: {question}"]
    plan = result.get("plan") or {}
    for s in plan.get("steps", []):
        status = "ok" if s["status"] == "done" else f"FAILED ({s['error']})"
        lines.append(f"  {s['id']} {s['agent']}.{s['tool']} {json.dumps(s['arguments'])} -> {status}")
    lines += [f"A: {result['answer']}", "Citations:"]
    for c in result["citations"]:
        source = (f"{c['filename']} > {c['section']}" if c["kind"] == "document"
                  else f"{c['tool']}" + (f" ({c['attribution']})" if c.get("attribution") else ""))
        lines.append(f"  [{c['n']}] {source}")
    lines.append(f"LLM calls: {result['llm_calls']} {result['llm_calls_by_role']}; "
                 f"{result['timings']['total_ms'] / 1000:.1f} s; outcome {result['outcome']}")
    return "\n".join(lines)


async def main(customer: str, questions: list[str]) -> None:
    principal = Access(org_id=BANK_ORG, clearance=0, customer_id=customer)
    for question in questions:
        print(show(question, await answer(question, principal)), end="\n\n", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--customer", required=True, help="a synthetic customer id, e.g. cust-0036")
    parser.add_argument("questions", nargs="+")
    args = parser.parse_args()
    asyncio.run(main(args.customer, args.questions))
