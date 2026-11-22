# Run notes: after the fixes (ed52758), contaminated prompt, partial

**Partial: 11 of 33 questions.** The run's window closed after question 11 (b1-memo-review, 14:08); the process was gone with no error written (its log was buffered). Kept as the record of the contaminated-prompt run; the clean run after the prompt fix is the official result.

Same settings as the baseline (20261122T001150Z-baseline): hybrid retrieval with rerank, validation on, the same 33 questions (the eval set as corrected after the baseline review).

## Fixes in ed52758

- **Fee-type names.** The planner's catalog shows each argument's allowed values from the tool's own schema (fee_type, account_type, metric, series_id); fee_type accepts common aliases ("late", "late fee", "maintenance", "fx"...), anything else is still refused.
- **Difference tool.** The calculator has `difference` (a minus b, and its absolute value); the planner has an example for comparing two totals.
- **Money-movement router.** A request anywhere in the message (after "hey", "ugh", with "just" between), passive requests ("I need $300 moved") and bare amounts count; non-requests stay questions. When the planner finds nothing in scope, a money-moving verb plus an account word gets the read-only reply.
- **Not-found labelling.** The summarizer returns a structured found flag: not found is outcome nothing_found with no citations, and is still validated (the auditor fails it if the evidence holds the answer). The summarizer sees the records' range (2025-07-01 to 2026-06-30); an account-data step asking only about earlier dates is marked.
- **Timeouts.** The eval runner waits 60 s before retrying a timeout or quota error and counts 503 retries per question. The app's 45 s call limit is unchanged.

## Caveat: a planner example close to an eval question

The planner example added in ed52758 for comparing two totals read: "Comparing two amounts ("how much more on groceries in April than in May")". That is nearly `b2-groceries-apr-vs-may` itself (same category, months and wording), and this run used it. **This run's result for b2-groceries-apr-vs-may can't be trusted as a test of the difference fix.** The example has since been changed (uncommitted) to "was my dining bill bigger in June than in March?"; the question needs a re-run with the new prompt.

Older planner examples, from the Phase 6 demo questions and in every run so far, also mirror eval questions: "why was I charged $15 on ..." (fee-why-0036), "how much did I spend on groceries in May" (groceries-may-0036, almost word for word), and "should I buy more VTI?" (the refusal-advice questions, by pattern). Not changed; for the reviewer to decide.

## Resolved

All three older examples were reworded (a wire fee; transport in February; selling Coca-Cola shares) along with the comparison example (dining, June vs March); tests/unit/test_planner_examples.py checks that no example shares a category plus month, a dollar amount, or a security with any eval question.
