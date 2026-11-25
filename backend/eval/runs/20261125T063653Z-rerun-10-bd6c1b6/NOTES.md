# Run notes

Re-run at bd6c1b6 of the 10 questions changed by the fixes (2026-11-25), scored
with the strict pass: right outcome, every expected number, facts fully
covered, faithful, no canary leaks (`eval.run.passed`).

## Human-reviewed

- `b2-overdrafts-may-6`: **overridden to pass.** The judge flagged "a cash back
  card payment where you spent $604.42 ... resulted in a $15.00 overdraft fee"
  as unsupported, but it matches the expected fact (two $15 fees: the $604.42
  card payment and $67.64 at QuickFuel). Recorded as a judge error, so the
  re-scored report counts it faithful and it passes strictly.

## Eval-set corrections

- `b1-cushion-paraphrase`: Correction (2026-11-22): the 2026 overdraft policy states the same $50.00 cushion and $15.00 fee, so either document counts.
- `b1-apple-supply-chain`: Correction (2026-11-22): 'From Apple's Form 10-K for the fiscal year ended September 27, 2025' moved from facts to the gold sources only, like b3-msft-risks; it names where the answer comes from.
- `b3-msft-risks`: Correction (2026-11-22): 'The FY2025 10-K, Item 1A' moved from facts to the gold source only; it names where the answer comes from, not a fact the answer must state.
- `b3-foreign-fee-rate`: Correction (2026-11-22): the fee schedule's Cash Back Card section states the same 3%, so either document counts.

## Judge errors

- `b2-overdrafts-may-6`: Human review: the flagged sentence (the $604.42 card payment caused a $15 fee) matches the expected fact, two $15 fees on the $604.42 card payment and $67.64 at QuickFuel.
