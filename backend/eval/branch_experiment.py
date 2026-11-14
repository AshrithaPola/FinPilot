"""Phase 4 branching experiment: which retrieval configuration is the default.

    python -m eval.branch_experiment check   # offline: questions vs the corpus
    python -m eval.branch_experiment run     # against DATABASE_URL (the Neon branch)

Six configurations: dense, hybrid (chunk vectors + full-text + question
index) and hybrid + rerank, each without and with parent-section expansion.
Every question (eval/branch_questions.yaml) is embedded once and asked as a
customer (clearance 0) with the default filter, versions in force today.
Per configuration:

  recall@k          a gold section's chunk is in the context of the top_k results
  MRR               mean of 1 / rank of the first result whose context holds one
  answer-in-context the final context contains an answer snippet
  context tokens    tokens of the final context (what the model would read)
  latency           search time per question, query embedding excluded
                    (median and p95 of the median of 3 rounds per question)

A design decision for Phase 4, not the Phase 10 evaluation: 19 questions.
"""
import asyncio
import re
import statistics
import sys
import time
from pathlib import Path

import yaml

QUESTIONS = Path(__file__).with_name("branch_questions.yaml")
CONFIGS = [
    ("dense", {"retrieval_mode": "dense", "rerank": False, "parent_expansion": False}),
    ("dense + parents", {"retrieval_mode": "dense", "rerank": False, "parent_expansion": True}),
    ("hybrid", {"retrieval_mode": "hybrid", "rerank": False, "parent_expansion": False}),
    ("hybrid + parents", {"retrieval_mode": "hybrid", "rerank": False, "parent_expansion": True}),
    ("hybrid + rerank", {"retrieval_mode": "hybrid", "rerank": True, "parent_expansion": False}),
    ("hybrid + rerank + parents", {"retrieval_mode": "hybrid", "rerank": True, "parent_expansion": True}),
]
ROUNDS = 3


def load_questions() -> list[dict]:
    return yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))["questions"]


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("**", "")).strip().lower()


def is_gold(filename: str, section: str, gold: list[list[str]]) -> bool:
    """A chunk is gold when its file matches and its section, after the
    document title, starts with a gold section (so a part covers its
    subsections)."""
    tail = section.split(" > ", 1)[-1]
    return any(filename == f and (section == s or tail.startswith(s)) for f, s in gold)


def check() -> list[str]:
    """Problems with the question file, checked against the corpus as parsed."""
    from bank.corpus import load_corpus
    from config import Settings

    docs = {d.file: d for d in load_corpus(Settings(_env_file=None, anthropic_api_key="x"))}
    old = " ".join(norm(c.text) for f, d in docs.items() if d.meta.get("superseded_on") for c in d.chunks)
    problems = []
    for i, q in enumerate(load_questions(), 1):
        gold_text = [norm(c.text) for d in docs.values() for c in d.chunks
                     if is_gold(d.file, c.section, q["gold"])]
        for f, s in q["gold"]:
            if not any(is_gold(f, c.section, [[f, s]]) for c in docs[f].chunks):
                problems.append(f"Q{i}: no chunk for {f} › {s}")
        if not any(norm(a) in t for a in q["answers"] for t in gold_text):
            problems.append(f"Q{i}: no answer snippet appears in its gold chunks")
        if q["kind"] == "version" and any(norm(a) in old for a in q["answers"]):
            problems.append(f"Q{i}: a 2026 answer also appears in a superseded document")
    return problems


async def run() -> list[dict]:
    from bank.synthetic import BANK_ORG
    from db.access import Access
    from deps import get_store
    from ingestion.chunking import tokens

    import sqlalchemy as sa

    questions = load_questions()
    customer = Access(org_id=BANK_ORG, clearance=0, customer_id="cust-0001")
    store = get_store().scoped(customer)
    with store._begin() as conn:
        where = dict(((r.chunk_id), (r.filename, r.section)) for r in conn.execute(
            sa.text("SELECT chunk_id, filename, section FROM chunks")))
    vectors = [await store.embed_query(q["q"]) for q in questions]  # one Gemini call each
    store.configured(rerank=True)._reranker.scores("warm up", ["load the model before timing"])

    rows = []
    for name, overrides in CONFIGS:
        configured = store.configured(**overrides)
        per_question = []
        for q, vector in zip(questions, vectors):
            times, hits = [], []
            for _ in range(ROUNDS):
                start = time.perf_counter()
                hits = await configured.search_with_vector(q["q"], vector)
                times.append((time.perf_counter() - start) * 1000)
            rank = next((r for r, h in enumerate(hits, 1)
                         if any(is_gold(*where[c], q["gold"]) for c in h.context_chunk_ids if c in where)), None)
            context = "\n\n".join(h.text for h in hits)
            per_question.append({"rank": rank, "answer": any(norm(a) in norm(context) for a in q["answers"]),
                                 "tokens": tokens(context), "ms": statistics.median(times)})
        n = len(per_question)
        ms = sorted(p["ms"] for p in per_question)
        rows.append({
            "config": name,
            "recall": sum(p["rank"] is not None for p in per_question) / n,
            "recall1": sum(p["rank"] == 1 for p in per_question) / n,
            "recall3": sum(p["rank"] is not None and p["rank"] <= 3 for p in per_question) / n,
            "ranks": [p["rank"] for p in per_question],
            "mrr": sum(1 / p["rank"] for p in per_question if p["rank"]) / n,
            "answer": sum(p["answer"] for p in per_question) / n,
            "tokens": statistics.mean(p["tokens"] for p in per_question),
            "p50": statistics.median(ms), "p95": ms[min(n - 1, round(0.95 * (n - 1)))],
            "misses": [i for i, p in enumerate(per_question, 1) if p["rank"] is None],
            "no_answer": [i for i, p in enumerate(per_question, 1) if not p["answer"]],
        })
    return rows


def table(rows: list[dict], top_k: int) -> str:
    out = [f"| Configuration | Recall@1 | Recall@3 | Recall@{top_k} | MRR | Answer in context | "
           "Context tokens | p50 ms | p95 ms | Missed | No answer |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['config']} | {r['recall1']:.2f} | {r['recall3']:.2f} | {r['recall']:.2f} | "
                   f"{r['mrr']:.2f} | {r['answer']:.2f} | {r['tokens']:.0f} | {r['p50']:.0f} | {r['p95']:.0f} | "
                   f"{', '.join(f'Q{i}' for i in r['misses']) or '-'} | "
                   f"{', '.join(f'Q{i}' for i in r['no_answer']) or '-'} |")
    # Rank of the first result holding a gold chunk, per question (- = missed).
    out += ["", "| Q | " + " | ".join(r["config"] for r in rows) + " |", "|---|" + "---|" * len(rows)]
    for i in range(len(rows[0]["ranks"])):
        out.append(f"| Q{i + 1} | " + " | ".join(str(r["ranks"][i] or "-") for r in rows) + " |")
    return "\n".join(out)


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "check":
        problems = check()
        print("\n".join(problems) or f"{len(load_questions())} questions: gold and answers check out")
        return 1 if problems else 0
    if command == "run":
        from config import get_settings

        print(table(asyncio.run(run()), get_settings().top_k))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
