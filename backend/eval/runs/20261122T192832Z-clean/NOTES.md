# Run notes: clean run after the fixes (official current result)

Commit 9d68f5d. Same settings as the baseline (20261122T001150Z-baseline): hybrid retrieval with rerank, validation on, the 33 questions as corrected after the baseline review. All 33 answered (no timeouts); 37 Gemini 503 retries, all absorbed.

## Fixes since the baseline

ed52758: the planner's catalog shows each argument's allowed values (fee types, account types...) and fee types accept common aliases; a difference tool; the money-movement router finds a request anywhere in the message, with a read-only fallback when the planner finds nothing in scope; the summarizer returns a found flag (not found is nothing_found, no citations, still validated), sees the records' range, and a step before it is marked; the runner waits 60 s before a retry and counts 503 retries.

9d68f5d: the planner's worked examples no longer mirror eval questions (a wire fee; transport in February; dining, June vs March; selling Coca-Cola shares), with a unit test that keeps it so.

## Reading the comparison

fee-why-0036, groceries-may-0036, b2-groceries-apr-vs-may and b1-advice-schd were mirrored by planner examples in the baseline (and in the partial contaminated run), not in this one: compare them on their own.
