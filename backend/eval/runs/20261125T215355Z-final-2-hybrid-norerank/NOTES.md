# Run notes

Final run at eval-freeze (25d570a), strict pass (`eval.run.passed`). Hybrid without rerank, the 14 document questions.

## Human-reviewed

- `b1-memo-review`: flag upheld (doesn't say the other rules decline, block or step up).

## Retrieval varies between runs, not only by setting

Runs 1–3 differ in retrieval setup, but each run's planner (the generation model)
writes its own search queries, and may differ from run to run. Their recall@5 and MRR
therefore mix the setting with the queries. The like-for-like comparison is the
retrieval replay (`20261125T223500Z-retrieval-replay`): run 1's queries through all
three setups.

After these runs the reranker was turned off by default (hybrid stays), decided from
the retrieval replay on this 14-question eval set.
