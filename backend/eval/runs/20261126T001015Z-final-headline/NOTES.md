# Run notes

The headline run: all 33 questions at eval-freeze (c3c5dfa) on the app's defaults
(hybrid, rerank off, full checks), strict pass (`eval.run.passed`). No setting
overridden. No daily-limit 429.

## Capacity

- `b1-transfer-200`: unanswered. The app gave its fixed money-movement reply (no
  model call), but the facts judge (gemini-3.1-flash-lite) timed out twice at 45 s.
  A judge failure, not an app failure. Re-judged afterwards (see below).

## Human-reviewed

- `fee-why-0036`: the faithfulness flag is **overridden**, as in run 5: list_fees returns the balance after each overdraft (the generator charges one only below the -$50.00 cushion; run 5's row shows -6749). Still fails: it doesn't give the $25.86 purchase amount.
- `b1-dispute-days`: flag upheld (doesn't say Cash Back card or that a secure message counts).
- `b1-memo-review`: flag upheld (doesn't say the other rules decline, block or step up; no $250).
- `b2-maintenance-fee`: flag upheld (no $12 fee before Feb 2026).
- `b2-late-fee-apr-26`: flag upheld (no missed payment between Mar 25 and May 6).
- `b1-transfer-200`: re-judged. The route answers before any model call, so the question was run again through the engine at the same code (0 model calls, same fixed reply) and only the facts judge called: fact covered, passed. Written to report.rescored.json; report.json is the run as recorded.

No override changes a result. With the re-judge: **28 of 33 pass** (27 as recorded).

## Validation comparison

Every answer, after human review: **28 correct; 4 wrong but shown** (fee-why-0036, b1-memo-review, b2-maintenance-fee, b2-late-fee-apr-26); **1 refused/incomplete** (b1-dispute-days).
