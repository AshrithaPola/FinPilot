"""Where retrieval latency goes: network, database, reranker, cold start.

    python -m eval.latency_breakdown     # against DATABASE_URL (e.g. via scripts/neon.py --branch run)

Measured on the store the app uses (finpilot_app, RLS on), with a stored
chunk vector as the query (no embedding call):

  cold start      first connection of a fresh engine, and the compute's
                  uptime (a Neon compute that scaled to zero just restarted)
  round trip      median of 20 SELECT 1 on a warm connection
  statements      per search, counted (the pool's pre-ping and the COMMIT
                  are further round trips the counter doesn't see)
  server time     EXPLAIN ANALYZE planning + execution of each leg
  end to end      warm search_with_vector, median of 15, per mode
  rerank CPU      the cross-encoder alone on 20 candidate texts
"""
import asyncio
import json
import statistics
import time

import sqlalchemy as sa

QUERY = "What is the overdraft cushion?"


def _ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


async def measure() -> dict:
    from bank.synthetic import BANK_ORG
    from config import get_settings
    from db.access import Access, set_access
    from deps import get_store
    from ingestion.store import _DENSE, _QUESTIONS, _SPARSE, Filters, _vector_literal

    settings = get_settings()
    host = sa.engine.make_url(settings.database_url).host or ""
    out: dict = {"region": next((p for p in host.split(".") if p.count("-") == 2), "?")}

    start = time.perf_counter()
    fresh = sa.create_engine(settings.database_url)
    with fresh.connect() as conn:
        uptime = conn.execute(sa.text("SELECT extract(epoch FROM now() - pg_postmaster_start_time())")).scalar()
    out["first_connection_ms"] = _ms(start)
    out["compute_uptime_s"] = float(uptime)
    with fresh.connect() as conn:
        times = []
        for _ in range(20):
            start = time.perf_counter()
            conn.execute(sa.text("SELECT 1")).scalar()
            times.append(_ms(start))
    out["round_trip_ms"] = statistics.median(times)
    fresh.dispose()

    customer = Access(org_id=BANK_ORG, clearance=0, customer_id="cust-0001")
    store = get_store().scoped(customer)
    with store._begin() as conn:
        vector = conn.execute(sa.text(
            "SELECT embedding::text FROM chunks WHERE filename = 'fee-schedule-2026.md' LIMIT 1")).scalar()
    query_vector = json.loads(vector)

    statements: list[str] = []
    sa.event.listen(store._engine, "before_cursor_execute",
                    lambda conn, cursor, statement, *args: statements.append(statement))
    params = {"doc_ids": None, "n": settings.rrf_candidates, **Filters().params()}
    for mode, overrides in (("dense", {"retrieval_mode": "dense", "rerank": False}),
                            ("hybrid", {"retrieval_mode": "hybrid", "rerank": False}),
                            ("hybrid + rerank", {"retrieval_mode": "hybrid", "rerank": True})):
        configured = store.configured(**overrides)
        await configured.search_with_vector(QUERY, query_vector)  # warm (and loads the reranker once)
        statements.clear()
        await configured.search_with_vector(QUERY, query_vector)
        sent = len(statements)
        times = []
        for _ in range(15):
            start = time.perf_counter()
            await configured.search_with_vector(QUERY, query_vector)
            times.append(_ms(start))
        out[mode] = {"end_to_end_ms": statistics.median(times), "statements": sent}

    server = {}
    with store._begin() as conn:
        set_access(conn, customer)
        conn.execute(sa.text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        for leg, statement, extra in (("dense", _DENSE, {"query_vector": _vector_literal(query_vector)}),
                                      ("full-text", _SPARSE, {"query": QUERY}),
                                      ("questions", _QUESTIONS, {"query_vector": _vector_literal(query_vector)})):
            plan = conn.execute(sa.text(f"EXPLAIN (ANALYZE, FORMAT JSON) {statement.text}"),
                                {**params, **extra}).scalar()[0]
            server[leg] = plan["Planning Time"] + plan["Execution Time"]
    out["server_ms"] = server

    texts = [f"Candidate passage {i} about fees, overdrafts and accounts. " * 12 for i in range(20)]
    reranker = store._reranker
    start = time.perf_counter()
    reranker.scores(QUERY, texts)
    out["rerank_cpu_ms_20"] = _ms(start)
    return out


def report(m: dict) -> str:
    rt = m["round_trip_ms"]
    lines = [f"database region: {m['region']}",
             f"first connection (fresh engine): {m['first_connection_ms']:.0f} ms; compute up {m['compute_uptime_s']:.0f} s",
             f"round trip (SELECT 1, warm):     {rt:.1f} ms",
             "server time per leg (EXPLAIN ANALYZE): " + ", ".join(f"{k} {v:.1f} ms" for k, v in m["server_ms"].items()),
             f"reranker CPU, 20 candidates:     {m['rerank_cpu_ms_20']:.0f} ms", "",
             "| Mode | End to end (median) | Server | Statements sent | Rest / round trip = effective round trips |",
             "|---|---|---|---|---|"]
    legs = {"dense": ["dense"], "hybrid": ["dense", "full-text", "questions"],
            "hybrid + rerank": ["dense", "full-text", "questions"]}
    for mode, legs_used in legs.items():
        e2e = m[mode]["end_to_end_ms"]
        server = sum(m["server_ms"][leg] for leg in legs_used)
        rest = e2e - server - (m["rerank_cpu_ms_20"] if "rerank" in mode else 0)
        lines.append(f"| {mode} | {e2e:.0f} ms | {server:.1f} ms | {m[mode]['statements']} | {rest / rt:.1f} |")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report(asyncio.run(measure())))
