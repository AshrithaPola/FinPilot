# Retrieval replay (2026-11-25, eval-freeze 25d570a)

Every `documents.search` step run 1 (`20261125T214252Z-final-1-full`) planned for the
14 questions with a document gold source, replayed through the app's own search path
(`Executor._search`: the bank-terms family, past-version and weak-type fallbacks) under
three setups. Same queries in every setup, so the setting is the only difference.
No generation or judge calls (the query rewrite is off); embeddings only: 25, 25 and
26 calls. Scored with `eval.run.retrieval_scores` (recall@5 over the top 5 of every
search, MRR the best gold rank). Script: `replay_retrieval.py`.

| Setup | Mean recall@5 | Mean MRR |
|---|---:|---:|
| Dense | 0.964 | 0.714 |
| Hybrid | 0.929 | 0.714 |
| Hybrid + rerank (the app's default) | 0.929 | 0.651 |

Check: hybrid + rerank reproduces run 1's live scores on 13 of 14 questions. The
exception, `b1-memo-review`, got a model-rewritten query in run 1, which the replay
doesn't make.

Per question: `dense.json`, `hybrid.json`, `hybrid-rerank.json`. Six questions differ
(dense / hybrid / hybrid + rerank): recall in `b1-dispute-days` (0.5 / 0.0 / 1.0) and
`b3-memo-all-rules` (1.0 / 1.0 / 0.0); MRR in `b1-cushion-paraphrase` and
`b1-daily-fee-cap` (1.0 / 1.0 / 0.5), `b1-memo-review` (0.25 / 0.25 / 0.2) and
`b3-msft-risks` (0.5 / 1.0 / 1.0). The other eight score the same in all three.
Fourteen questions are a small set: one question moves the mean recall by 0.071.

**Decision (2026-11-25): the reranker is off by default; hybrid stays.** Made from
this replay, on this 14-question eval set: rerank added no recall@5 and lowered
MRR. Small evidence, so a provisional call (docs/PROGRESS.md, "Retrieval defaults").
