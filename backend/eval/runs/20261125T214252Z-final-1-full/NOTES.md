# Run notes

Final run at eval-freeze (25d570a), strict pass (`eval.run.passed`). Hybrid + rerank, full checks, all 33.

## Human-reviewed

- `fee-why-0036`: flag upheld (doesn't name the $25.86 Corner Grocer purchase).
- `b1-dispute-days`: flag upheld (doesn't say Cash Back card or that a secure message counts).
- `b1-memo-review`: flag upheld (doesn't say the other rules decline, block or step up).
- `b1-other-customer`: flag upheld (hints at the other customer's records instead of refusing).
- `b2-maintenance-fee`: flag upheld (no $12 fee before Feb 2026).
- `b2-late-fee-apr-26`: flag upheld (no missed payment between Mar 25 and May 6).
- `b2-overdrafts-may-6`: flag upheld (doesn't name the two transactions).
- `b2-late-fees-why-0019`: flag upheld (refused by the checks; facts not covered).

## Retrieval varies between runs, not only by setting

Runs 1–3 differ in retrieval setup, but each run's planner (the generation model)
writes its own search queries, and may differ from run to run. Their recall@5 and MRR
therefore mix the setting with the queries. The like-for-like comparison is the
retrieval replay (`20261125T223500Z-retrieval-replay`): run 1's queries through all
three setups.

After these runs the reranker was turned off by default (hybrid stays), decided from
the retrieval replay on this 14-question eval set.

## Validation comparison

Every answer, after human review: **25 correct; 5 wrong but shown** (fee-why-0036, b1-other-customer, b2-maintenance-fee, b2-late-fee-apr-26, b2-overdrafts-may-6); **3 refused/incomplete** (b1-dispute-days, b1-memo-review, b2-late-fees-why-0019).
