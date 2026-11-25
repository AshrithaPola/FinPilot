"""Re-score a stored eval run against the current eval set, reusing its answers.

    python -m eval.rescore eval/runs/<run> --judge-error "b3-memo-all-rules=the memo states it in 5. Review"

No engine run. Recomputed from what the run stored: the outcome, numbers and
canaries (from the answer), gold citations (from the stored citations, or
for a run that didn't store them, rebuilt from the plan when it was a single
search, whose evidence [n] is the n-th hit), and retrieval (from the stored
top-5 lists). The judge is called again only for a question whose expected
facts changed; a fact list that's now empty isn't judged. --judge-error marks
a faithfulness verdict the reviewer found wrong. Writes report.rescored.json
and NOTES.md (the eval-set corrections and judge errors) next to the run.
"""
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

from eval import questions as ev
from eval import run


def _hits(top5: list[str]) -> list[dict]:
    return [{"filename": h.split(" > ", 1)[0], "section": h.split(" > ", 1)[1] if " > " in h else ""} for h in top5]


def _rebuilt_citations(record: dict) -> list[dict] | None:
    """For a run without stored citations: a plan that was one search has evidence
    [n] = the n-th hit, so the answer's [n] can be read back (n <= 5, the stored top 5)."""
    plan, retrieval = record.get("plan") or [], record.get("retrieval") or {}
    if len(plan) != 1 or plan[0]["tool"] != "documents.search" or not retrieval.get("top5"):
        return None
    hits = _hits(retrieval["top5"][0])
    cited = {int(n) for group in re.findall(r"\[(\d+(?:\s*,\s*\d+)*)\]", record["answer"])
             for n in re.findall(r"\d+", group)}
    return [{"kind": "document", **hits[n - 1]} for n in sorted(cited) if 1 <= n <= len(hits)]


async def rescore_record(record: dict, q: ev.Question, judge_errors: dict[str, str]) -> dict:
    r = dict(record)
    answer = r["answer"]
    r["outcome_group"], r["outcome_ok"] = run.score_outcome(q.expected_answer.outcome, r["outcome"])
    r["expected_outcome"] = q.expected_answer.outcome
    r["numbers"] = [{"value": n.value, "what": n.what, "found": run.number_found(n.value, answer)}
                    for n in q.expected_answer.numbers]
    r["numbers_ok"] = all(n["found"] for n in r["numbers"]) if r["numbers"] else None
    stored_canary = {c["string"]: c for c in record.get("canary", [])}
    r["canary"] = [{"string": c, "in_answer": c in answer,
                    "in_evidence": stored_canary.get(c, {}).get("in_evidence")}
                   for c in q.expected_answer.must_not_contain]
    r["canary_leaks"] = sum(c["in_answer"] for c in r["canary"])

    citations = record.get("citations")
    if citations is not None:
        r["gold_cited"] = run.gold_cited(q, citations)
    elif any(s.alternatives for s in q.gold_sources) and (rebuilt := _rebuilt_citations(record)) is not None:
        # Only where the gold changed: the rebuild sees the top 5 alone, so a citation the
        # run counted (an [6]) stands, and the rebuild can only add the alternatives.
        stored = {d["source"]: d["cited"] for d in record["gold_cited"]["detail"]}
        detail = [{**d, "cited": d["cited"] or stored.get(d["source"], False)}
                  for d in run.gold_cited(q, rebuilt)["detail"]]
        r["gold_cited"] = {"cited": sum(d["cited"] for d in detail), "of": len(detail), "detail": detail,
                           "rebuilt_from_plan": True}

    changed = any(s.alternatives for s in q.gold_sources)
    if changed and record.get("retrieval") and record["retrieval"].get("top5") is not None:
        # Only where the gold changed: the stored top 5 keep each section's last part, enough for
        # the alternatives' one-part sections. The run's own MRR saw rank 6 too: keep the better.
        again = run.retrieval_scores(q, [_hits(t) for t in record["retrieval"]["top5"]])
        if again:
            r["retrieval"] = {**again, "recall_at_5": max(again["recall_at_5"], record["retrieval"]["recall_at_5"]),
                              "mrr": max(again["mrr"], record["retrieval"]["mrr"])}

    stored_facts = [f["fact"] for f in record.get("facts") or []]
    if q.expected_answer.facts != stored_facts:
        r["facts"] = await run.judge_facts(q, answer)
        r["facts_rejudged"] = True
    r["facts_covered"] = (round(sum(f["covered"] for f in r["facts"]) / len(r["facts"]), 3)
                          if r.get("facts") else None)

    if q.id in judge_errors and r.get("faithfulness"):
        r["faithfulness"] = {"faithful": True, "unsupported": [],
                             "judge_error": {"overruled": record["faithfulness"], "reason": judge_errors[q.id]}}
    r["passed"] = run.passed(r)
    return r


def with_failures(records: list[dict], errors: list[dict], questions: dict[str, ev.Question]) -> dict:
    """Overall numbers over every question, a question that never got an answer
    (it timed out twice) counted as a failure on every measure it has."""
    total = len(records) + len(errors)
    has_numbers = [r for r in records if r.get("numbers_ok") is not None] + \
        [e for e in errors if questions[e["id"]].expected_answer.numbers]
    has_facts = [r for r in records if r.get("facts_covered") is not None] + \
        [e for e in errors if questions[e["id"]].expected_answer.facts]
    return {"questions": total, "unanswered": len(errors),
            "passed": round(sum(bool(r.get("passed")) for r in records) / total, 3),
            "outcome_ok": round(sum(bool(r["outcome_ok"]) for r in records) / total, 3),
            "numbers_ok": round(sum(bool(r.get("numbers_ok")) for r in has_numbers) / len(has_numbers), 3),
            "facts_covered": round(sum(r.get("facts_covered") or 0 for r in has_facts) / len(has_facts), 3)}


def notes(questions: list[ev.Question], judge_errors: dict[str, str]) -> str:
    lines = ["# Run notes", "", "## Eval-set corrections", ""]
    for q in questions:
        for part in re.findall(r"(Correction \([^)]*\): [^.]*(?:\.[^ ][^.]*)*\.)", q.notes):
            lines.append(f"- `{q.id}`: {part}")
    lines += ["", "## Judge errors", ""]
    lines += [f"- `{qid}`: {reason}" for qid, reason in judge_errors.items()] or ["None."]
    return "\n".join(lines) + "\n"


async def main_async(args) -> int:
    folder = Path(args.run)
    report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    questions, errors = ev.load()
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 2
    by_id = {q.id: q for q in questions}
    judge_errors = dict(item.split("=", 1) for item in args.judge_error)
    scored, unanswered = [], []
    for record in report["records"]:
        if "error" in record:
            unanswered.append(record)
        else:
            scored.append(await rescore_record(record, by_id[record["id"]], judge_errors))
    rescored = {**report, "rescored_against": "the eval set as corrected", "judge_errors": judge_errors,
                "summary": run.summarize(scored),
                "overall_with_unanswered_as_failures": with_failures(scored, unanswered, by_id),
                "records": scored + unanswered}
    (folder / "report.rescored.json").write_text(json.dumps(rescored, indent=2, default=str), encoding="utf-8")
    # The corrections and judge errors replace their own sections; anything else in NOTES.md stays.
    notes_file = folder / "NOTES.md"
    kept = notes_file.read_text(encoding="utf-8") if notes_file.exists() else ""
    kept = kept.split("\n## Eval-set corrections", 1)[0].rstrip()
    generated = notes(questions, judge_errors).split("\n", 2)[2]  # without its "# Run notes" title
    notes_file.write_text((kept + "\n\n" if kept else "# Run notes\n\n") + generated, encoding="utf-8")
    print(json.dumps({"overall": rescored["summary"]["overall"],
                      "with_unanswered_as_failures": rescored["overall_with_unanswered_as_failures"],
                      "false_refusal_rate": rescored["summary"]["false_refusal_rate"]}, indent=1))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-score a stored run against the current eval set.")
    parser.add_argument("run", help="the run's folder, eval/runs/<timestamp>-<label>")
    parser.add_argument("--judge-error", action="append", default=[], metavar="ID=REASON",
                        help="a faithfulness verdict the reviewer found wrong")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
