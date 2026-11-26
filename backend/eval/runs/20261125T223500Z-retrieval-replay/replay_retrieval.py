"""Retrieval replay: every documents.search step run 1 planned, through the app's
own search path (Executor._search: type family, past-version and weak-type
fallbacks) with the retrieval setup the environment gives (RETRIEVAL_MODE,
RERANK). No LLM: the query rewrite is off (rewrite=None); embeddings only.
Usage: replay_retrieval.py RUN1_DIR OUT_JSON"""
import asyncio, json, sys
sys.path.insert(0, "D:/Projects/FinPilot/backend")

import tracing
from bank.synthetic import BANK_ORG
from config import get_settings
from db.access import Access
from deps import get_accounts_tools, get_market_tools, get_store
from engine.executor import Executor
from engine.graph import DATA_FROM
from engine.plan import Step
from eval import questions as ev
from eval.run import retrieval_scores

run1, out_path = sys.argv[1], sys.argv[2]
records = {r["id"]: r for r in json.load(open(f"{run1}/report.json", encoding="utf-8"))["records"]}
questions = {q.id: q for q in ev.load()[0]}
searches: list = []
original = Executor._traced_search


async def recorded(store, query, filters):
    hits = await original(store, query, filters)
    searches.append([{"filename": h.filename, "section": h.section} for h in hits])
    return hits


Executor._traced_search = staticmethod(recorded)


async def main():
    s = get_settings()
    ex = Executor(get_accounts_tools(), get_market_tools(), get_store(), rewrite=None,
                  today=s.demo_today, data_from=DATA_FROM)
    out = {"setup": {"retrieval_mode": s.retrieval_mode, "rerank": s.rerank, "top_k": s.top_k}, "questions": {}}
    with tracing.tracing() as trace:
        for qid, r in records.items():
            q = questions[qid]
            if not any(g.kind == "document" for g in q.gold_sources):
                continue
            access = (Access(org_id=BANK_ORG, clearance=2, role="admin") if q.asked_as == "staff"
                      else Access(org_id=BANK_ORG, clearance=0, customer_id=q.asked_as))
            searches.clear()
            steps = [p for p in r["plan"] if p["tool"] == "documents.search"]
            notes = [p.get("note") for p in steps if p.get("note")]
            for p in steps:
                step = Step(id=p["id"], agent="documents", tool="search", arguments=p["arguments"], purpose="replay")
                await ex._search(step, p["arguments"], access)
            out["questions"][qid] = {"queries": [p["arguments"] for p in steps], "run1_notes": notes,
                                     **retrieval_scores(q, list(searches))}
        out["embedding_calls"] = trace.totals().get("embedding_calls")
        out["llm_calls"] = trace.totals().get("llm_calls")
    json.dump(out, open(out_path, "w", encoding="utf-8"), indent=1, default=str)
    sc = [v for v in out["questions"].values()]
    print(out["setup"], f"questions {len(sc)}", "recall@5", round(sum(v["recall_at_5"] for v in sc) / len(sc), 3),
          "MRR", round(sum(v["mrr"] for v in sc) / len(sc), 3), "embedding calls", out["embedding_calls"],
          "llm calls", out["llm_calls"])


asyncio.run(main())
