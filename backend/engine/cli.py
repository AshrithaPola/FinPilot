"""Ask the engine from the command line, as one synthetic bank customer.

    python -m engine.cli --customer cust-0036 "Can I afford $1,200 this month and stay above $500?"

Uses DATABASE_URL (finpilot_app; e.g. through scripts/neon.py run) and the
configured LLM. Prints each question's plan, its steps, the answer with its
citations, and the LLM calls it took, and stores each request's trace
(tracing.py), like /ask.
"""
import argparse
import asyncio
import json

import observability
import tracing
from bank.synthetic import BANK_ORG
from db.access import Access
from deps import get_store
from engine.graph import answer


def show(question: str, result: dict) -> str:
    lines = [f"Q: {question}  (today is {result['demo_today']})"]
    plan = result.get("plan") or {}
    for s in plan.get("steps", []):
        status = "ok" if s["status"] == "done" else f"FAILED ({s['error']})"
        again = " [re-plan]" if s.get("round") == 2 else ""
        note = f" ({s['note']})" if s.get("note") else ""
        lines.append(f"  {s['id']}{again} {s['agent']}.{s['tool']} {json.dumps(s['arguments'])} -> {status}{note}")
    lines += [f"A: {result['answer']}", "Citations:"]
    for c in result["citations"]:
        source = (f"{c['filename']} > {c['section']}" if c["kind"] == "document"
                  else f"{c['tool']}" + (f" ({c['attribution']})" if c.get("attribution") else ""))
        lines.append(f"  [{c['n']}] {source}")
    for i, verdict in enumerate(result.get("validation", []), 1):
        lines.append(f"Check {i}: " + "; ".join(
            f"{name} {'pass' if v['passed'] else 'FAIL: ' + v['reason']} ({v['by']})" for name, v in verdict.items()))
    lines.append(f"LLM calls: {result['llm_calls']} {result['llm_calls_by_role']}; "
                 f"{result['timings']['total_ms'] / 1000:.1f} s; outcome {result['outcome']}")
    t = result["trace"]
    lines.append(f"Trace {result['trace_id']}: Gemini {t['llm_ms'] / 1000:.1f} s ({t['llm_calls']} calls, "
                 f"{t['retries']} retries), embeddings {t['embedding_ms'] / 1000:.1f} s, search "
                 f"{t['search_ms'] / 1000:.1f} s (rerank {t['rerank_ms'] / 1000:.1f} s), tools "
                 f"{t['tools_ms'] / 1000:.1f} s, other {t['other_ms'] / 1000:.1f} s; est. ${t['est_cost_usd']:.5f}")
    lines.append("Timings: " + ", ".join(f"{k[:-3]} {v / 1000:.1f} s" for k, v in result["timings"].items()))
    return "\n".join(lines)


async def main(customer: str, questions: list[str]) -> None:
    principal = Access(org_id=BANK_ORG, clearance=0, customer_id=customer)
    for question in questions:
        with tracing.tracing() as trace:
            result = await answer(question, principal)
        result["trace"] = trace.totals()
        tracing.save(get_store().engine, principal, trace, result, user_id=None, question=result["question"])
        print(show(question, result), end="\n\n", flush=True)
    observability.flush()  # the masked Langfuse traces, before the process exits


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--customer", required=True, help="a synthetic customer id, e.g. cust-0036")
    parser.add_argument("questions", nargs="+")
    args = parser.parse_args()
    asyncio.run(main(args.customer, args.questions))
