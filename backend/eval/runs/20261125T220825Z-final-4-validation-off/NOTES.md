# Run notes

Final run at eval-freeze (25d570a), strict pass (`eval.run.passed`). Hybrid + rerank, checks off (EVAL_VALIDATION=off), all 33.

## Human-reviewed

- `b2-late-fees-year-0036`: **overridden to pass.** "$125.00 across 5 late payment fees ... between July 2025 and June 2026" matches the evidence (five $25 late fees in that period).
- `b2-overdrafts-may-6`: **overridden to pass.** The flagged sentence (QuickFuel $67.64 and the $604.42 card payment overdrew the account past the $50 cushion) matches the expected fact.
- `b2-maintenance-fee`: the faithfulness flag is **overridden**: the cited fee-schedule-2026 section 1 says the fee is waived in any month direct deposits total $250.00 or more. Its uncovered fact (no $12 fee before Feb 2026) is upheld, so it still fails.
- `fee-why-0036`: flag upheld (doesn't name the $25.86 Corner Grocer purchase).
- `b1-memo-review`: flag upheld (doesn't say the other rules decline, block or step up).
- `b1-other-customer`: flag upheld (gives a date-range reason instead of refusing).
- `b2-late-fee-apr-26`: flag upheld (no missed payment between Mar 25 and May 6).

## Eval-set corrections

- `b1-cushion-paraphrase`: Correction (2026-11-22): the 2026 overdraft policy states the same $50.00 cushion and $15.00 fee, so either document counts.
- `b1-apple-supply-chain`: Correction (2026-11-22): 'From Apple's Form 10-K for the fiscal year ended September 27, 2025' moved from facts to the gold sources only, like b3-msft-risks; it names where the answer comes from.
- `b3-msft-risks`: Correction (2026-11-22): 'The FY2025 10-K, Item 1A' moved from facts to the gold source only; it names where the answer comes from, not a fact the answer must state.
- `b3-foreign-fee-rate`: Correction (2026-11-22): the fee schedule's Cash Back Card section states the same 3%, so either document counts.

## Judge errors

- `b2-late-fees-year-0036`: Human review: five $25 late fees, $125.00, July 2025 to June 2026 are in the evidence
- `b2-overdrafts-may-6`: Human review: matches the expected fact (the $604.42 card payment and $67.64 at QuickFuel)
- `b2-maintenance-fee`: Human review: fee-schedule-2026 section 1 states the $250 direct-deposit waiver
