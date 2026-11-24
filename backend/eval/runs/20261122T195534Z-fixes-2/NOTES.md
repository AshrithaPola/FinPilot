# Run notes: after the second round of fixes (61585af)

Same settings as the baseline and the clean run: hybrid retrieval with rerank, full validation, the 33 questions as corrected (including b1-apple-supply-chain's 10-K fact moved to the gold source). All 33 answered; 2 timeouts, both retried after the 60 s wait; 17 Gemini 503 retries.

## Fixes in 61585af

- A guess at one bank-terms document type (fee schedule, policy, agreement, rate table, product comparison) searches the whole family.
- A search with a past as_of also searches today's version; the summarizer says what changed.
- An answer must state its plan's final calculation result (code check, regenerated otherwise).
- The "why" retry names the document passages to cite.
- list_fees gives each transaction-caused fee the balance right after that transaction.
- EVAL_VALIDATION=code (eval only).

## Changes against the clean run worth knowing

- b2-overdrafts-may-6 regressed to refused_unverified: both drafts held "$60.44", a figure in no evidence (code auditor). Likely the new balance figures invited arithmetic; the drafts aren't stored, so not confirmed.
- fee-why-0036 regressed: the planner searched only "fee schedule" (the charge example no longer carries an overdraft search hint), and the family search let the deposit agreement's generic fee clause satisfy the "why" check without the $50 cushion.
- Probable judge errors: b2-late-fees-year-0036 ("$125.00 total" called unsupported; it is the calculator's result), b1-other-customer (judged as hinting the customer exists).
