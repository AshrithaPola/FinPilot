# Run notes

Final run at eval-freeze (25d570a), strict pass (`eval.run.passed`). Hybrid + rerank, code checks only (EVAL_VALIDATION=code), all 33.

## Human-reviewed

- `b2-late-fees-year-0036`: **overridden to pass.** "$125.00 across 5 late payment fees ... between July 2025 and June 2026" matches the evidence (five $25 late fees in that period).
- `fee-why-0036`: **overridden.** "Overdrawn by more than $50.00 afterwards" is in the evidence: the cited list_fees row shows balance_after_cents -6749 (-$67.49) for the Apr 6 overdraft fee.
- `atm-fee-rise-0019`: flag upheld (doesn't tie the May 13 fee to the 2026 schedule).
- `b1-dispute-days`: flag upheld (doesn't say Cash Back card or that a secure message counts).
- `b1-other-customer`: flag upheld (doesn't deal with the other customer).
- `b2-maintenance-fee`: flag upheld (doesn't say charged monthly, waived every time, or $10 since Feb 2026).
- `b2-late-fee-apr-26`: flag upheld (said it couldn't provide the information).
- `b2-overdrafts-may-6`: flag upheld (said it couldn't answer instead of naming the two fees).

## Eval-set corrections

- `b1-cushion-paraphrase`: Correction (2026-11-22): the 2026 overdraft policy states the same $50.00 cushion and $15.00 fee, so either document counts.
- `b1-apple-supply-chain`: Correction (2026-11-22): 'From Apple's Form 10-K for the fiscal year ended September 27, 2025' moved from facts to the gold sources only, like b3-msft-risks; it names where the answer comes from.
- `b3-msft-risks`: Correction (2026-11-22): 'The FY2025 10-K, Item 1A' moved from facts to the gold source only; it names where the answer comes from, not a fact the answer must state.
- `b3-foreign-fee-rate`: Correction (2026-11-22): the fee schedule's Cash Back Card section states the same 3%, so either document counts.

## Judge errors

- `b2-late-fees-year-0036`: Human review: five $25 late fees, $125.00, July 2025 to June 2026 are in the evidence
- `fee-why-0036`: Human review: list_fees shows balance_after_cents -6749 for the Apr 6 overdraft fee

## Validation comparison

Every answer, after human review: **25 correct; 3 wrong but shown** (b1-memo-review, b2-maintenance-fee, b2-late-fees-why-0019); **5 refused/incomplete** (atm-fee-rise-0019, b1-dispute-days, b1-other-customer, b2-late-fee-apr-26, b2-overdrafts-may-6).
