# FinPilot progress log

The plan, the architecture checklist and the commit budget are in
[FINPILOT_PLAN.md](FINPILOT_PLAN.md). DocuMind's own log (Phases 0–2 of the
enterprise build, its eval baselines and decisions) is in git history: see
`docs/PROGRESS.md` at commit 51ba57a.

**Commits: 27 of 28** (budget raised to 24 and then 25 on 2026-11-14, to 26 on 2026-11-16, and to 28 the same day: clean commits matter more than the count) (budget decided 2026-11-13; the Compose-name chore is outside it).

## Chores outside the phase budget

- 2026-11-13: GitHub repo renamed DocuMind → FinPilot (old URL redirects);
  `docker-compose.yml` pins `name: documind` so the existing volumes
  (`documind_postgres_data`, `documind_hf_cache`) survive renaming the
  local folder to FinPilot.

## Phase 0: cleanup and rename (done)

| # | Commit | What |
|---|---|---|
| 1 | `0fccfd7` | Remove upload and the access-change API; rename DocuMind to FinPilot; migration 0008 moves the app role to `finpilot_app` |
| spare 1 | `00fbfda` | Integration tests loaded documents through `/upload`; they now use the store (CI caught it) |
| 2 | `59f9659` | Plan, embedding pacing from `phase3-corpus`, `docs/baseline/` deleted, fresh progress log, README |

### Notes

- **Role rename without `ALTER ROLE ... RENAME`.** Roles are cluster-wide and
  every test database migrates on one server, so 0008 creates `finpilot_app`
  if missing, moves the grants to it and strips them from `documind_app` in
  that database. A test checks both directions.
- **`@documind.test` stays** in the eval-account guard: the real Firebase
  eval account and the Firebase project (`documind-29040`) use that name.
  New test accounts come with the Phase 1 users.
- **Local `.env` and dev volume untouched.** `.env` names the DB user and
  database `documind` explicitly, which still works; compose now builds
  `finpilot_app` URLs, and the migrate step runs 0008 on the existing volume.
- **From `phase3-corpus`**, only the generic parts came over: rate limiting
  by texts and tokens, paced embedding, a clean stop on the daily quota
  (`backend/ingestion/pacing.py`), and per-model embedding text counts. The
  `kb/` corpus, its manifest loader and test users stay on that branch.
- **Testing rule from here on** (your instruction, 2026-11-13): no Docker,
  no local Postgres, no Gemini calls on this machine. Light tests locally
  (backend tests without Postgres, frontend lint and build); GitHub CI runs
  the full Postgres suite on every push.

## Phase 1: customer data (done; PR #5 waits for you)

| # | Commit | What |
|---|---|---|
| 1 | `223f0ae` | Migration 0009 (customers, accounts, cards, transactions, fees, holdings, securities, prices), per-customer RLS via `app.customer_id`, `users.customer_id`, leakage tests, a Neon-style owner test |
| 2 | `acb6ae4` | Seeded synthetic generator and its loader (`backend/bank/synthetic.py`); the schema test learns the new tables (CI failure on commit 1) |

### Notes

- **Ownership can't diverge.** Child rows repeat `customer_id` and reference
  `(account_id, customer_id)` (and `(card_id, customer_id)`,
  `(txn_id, customer_id)`), so a transaction can't sit on another
  customer's account or card.
- **Staff see no customer data.** Their `customer_id` is empty, which no row
  matches. A support view, if ever needed, is a separate, audited path.
- **Neon owner.** CI runs every migration as a role with `CREATEROLE` and
  `CREATEDB` but neither superuser nor `BYPASSRLS` (stricter than Neon's
  owner), and checks that FORCE RLS applies to it: customer rows go in only
  inside that customer's context. The loader (next commit) writes that way.
- **The generator** (seed 7, 50 customers): 12 months from 2025-07-01,
  about 24,000 transactions, 790 fees, 19 holdings, in under a second. The
  fee schedule changes on 2026-02-01 (overdraft $34 → $15, NSF fee
  dropped, a $50 overdraft cushion); every fee matches the schedule in force
  on its date, and the same numbers go into the bank's fee-schedule
  documents. Each customer has one canary transaction.
- **The loader** writes each customer in one transaction bound to that
  customer, deleting with an explicit `customer_id` filter (an owner with
  BYPASSRLS isn't limited by the policy) and upserting the customer row.
  Tested as the superuser and as an owner without BYPASSRLS.
- **Not run locally** (no Postgres here); CI runs the Postgres tests.

## Phase 2: bank corpus and sources (approved as drafts, 2026-11-13)

| # | Commit | What |
|---|---|---|
| 1 | this commit | `data/bank/`: fee schedules 2025 and 2026, overdraft policies 2025 and 2026, dispute policy, account comparison, deposit agreement, rate tables (CSV and XLSX), the bank's OpenAPI spec, a staff-only fraud-rules memo with a canary; `manifest.yaml`; `data/SOURCES.md` |

### Notes

- **All drafts.** Every document says "DRAFT, pending review" and has
  `status: draft` in the manifest. Written for FinPilot; the structure
  follows the kinds of disclosure the CFPB publishes model forms for, with
  no text copied.
- **Numbers agree with the generator.** A unit test checks every fee row in
  both schedules against `FEE_SCHEDULES`; APYs and rates agree under daily
  compounding; card APRs equal prime plus the stated margins.
- **The canary** (`internal/fraud-rules-memo.md`, clearance 2) appears in no
  other file; a test checks the repo.
- **XLSX** is built from the CSVs by `data/bank/tools/build_rate_sheet.py`
  (openpyxl, installed in the local venv only; Phase 3 adds it to the
  requirements when it parses spreadsheets). The workbook has title rows
  above its headers on purpose.
- **Licences** (`data/SOURCES.md`): TabFormer (Apache-2.0 repo, no
  data-specific terms) and Berka (no licence found) are not used; Alpha
  Vantage's free key is personal and non-commercial only; FRED needs its
  notice and excludes copyrighted series; SEC needs a User-Agent with a
  contact, max 10 requests/second; CFPB content is public domain; FDIC's own
  pages make no public-domain statement (BankFind is listed as public
  domain); investor.gov's policies page couldn't be read (403).
- **Your decisions (2026-11-13)** are recorded in the plan and in
  `data/SOURCES.md`: Investor.gov reused like other SEC content, with
  attribution; real prices only for signed-in demo accounts; no TabFormer.

## Phase 3: parsing, restructuring, chunking

PR #5 (`finpilot` → `main`, GitHub's number: #1–4 are DocuMind's) holds
Phases 0–2 for your review. Phase 3 is on `finpilot-phase3`, stacked on it,
so the PR doesn't change under review.

| # | Commit | What |
|---|---|---|
| 1 | `4137f21` | Parsers for Markdown, CSV, XLSX, OpenAPI and PDF/DOCX/TXT; noise removal; structure-aware chunker (256–512 tokens); the corpus loader; full-length deposit agreement and a new cardmember agreement |
| 2 | `d51fcae` | Migration 0010: document tables as rows linked to their chunks; synthetic receipt images and one vision call each, checked against the transaction and cached; image input in the LLM gateway; recurring subscriptions and occasional (not chronic) overdrafts in the generator |
| 3 | `d2236cd` | Migration 0011: manifest metadata on documents; section, summary, keywords and questions on chunks (one LLM call per batch, cached); `python -m bank.corpus load` replaces documents in place |
| spare 2 | this commit | The corpus-load test hardcoded 29 tables; there are 32 (my miscount). It now counts them from the parser (CI caught it) |

### Notes

- **Corpus changes ride in this commit**, not a Phase 2 commit, to keep the
  spare: they exist so chunking can be tested. The deposit agreement is now
  5,391 words in 24 numbered sections with four tables (opening deposits,
  funds availability, daily limits, CD penalties). Its worked examples
  ($29.14 interest, $100.36 penalty) were recomputed. The fee schedule and
  account comparison both cited a "Cardmember Agreement" that didn't exist;
  it does now (2026, card services: pricing table, APR = prime + margin
  matching `card-rates.csv`, average daily balance interest, payments,
  rewards). Both fee schedules gained the balance-transfer fee row that
  agreement states.
- **Multi-chunk check** (counts before Phase 4's chunking change, which made 114 chunks). 13 documents, 62 chunks. Six documents span 3 or
  more chunks (deposit agreement 19, OpenAPI 13, cardmember agreement 8,
  both fee schedules and the dispute policy 3); a test keeps it at six or
  more. The short policies stay at 2 chunks: they're the version-pair
  distractors (2025 vs 2026), not chunking tests.
- **Blocks, then chunks.** Parsers turn each file into blocks a chunk may
  not cut: a paragraph, a list item, a whole table, one API operation.
  The chunker packs blocks of the same section up to 512 tokens; every
  chunk starts with its section path. (Until Phase 4 a chunk under 256 also
  took in the next section; see Phase 4 commit 1.) A table over the limit splits between rows, repeating its
  header; an operation is always its own chunk.
- **Spreadsheets.** CSVs split by `effective_date`, so the 2025 and 2026
  rates never share a chunk. In the workbook the header is the widest row,
  found under the title rows on purpose.
- **Noise.** Running headers and footers (on half the pages or more, at
  the page edges), page numbers at the edges only (a lone "12" mid-page can
  be a table cell), navigation lines, and paragraphs repeated in 3+
  documents. The draft banner is boilerplate; one copy stays (in the 2025
  fee schedule) so "not FDIC-insured" is still retrievable. Removed share
  per document: 0–6%.
- **Stable ids.** `doc_id` is a hash of the file path, so chunk ids are the
  same on every run and can key the Phase 3 metadata cache.
- **`chunk_pages` stays** for DocuMind's PDF eval tooling. FinPilot's
  corpus uses `chunk_blocks`.
- **Venv rebuilt** after the folder rename (pinned requirements, no torch,
  eval extras included): 234 passed, 163 Postgres tests skipped locally.

### Notes (commit 2)

- **Tables as rows.** `ingestion/tables.py` turns every table block into a
  `doc_tables` row (columns, section) and one `doc_table_rows` row per table
  row, with the `chunk_id` that holds it; a table split across chunks stays
  one table. Cells stay text (`"$15.00 per item"`); the calculator parses
  what it needs and cites the row. They are written in `add_document`'s
  transaction, copying org, team and clearance from the documents row in
  SQL, under the same `tenant_access` policy as chunks, so the staff memo's
  table is as hidden as its text. The corpus has 32 tables, 161 rows.
- **Receipts.** Ten PNGs in `data/bank/receipts/`, one card purchase each
  for customers 1–10, rendered from the transaction itself (merchant, date,
  total, card last 4; the items are invented but add up to the total). The
  vision call returns those four fields; `check` compares each exactly
  (merchant ignoring case and accents, total in cents, last 4 digits).
  Results go in `receipt_images` under the **customer** policy, written in
  the receipt's customer context (works for a Neon owner without
  BYPASSRLS), keyed by the image's hash and the model, so a re-run makes
  no calls. `txn_id` isn't a foreign key: reloading the synthetic data
  replaces transactions, and the cached extraction must survive that.
- **Not run yet: the real vision pass** (10 Gemini calls, about one minute
  under the 15-a-minute limit). It needs a database with the customer data
  and your go: `python -m bank.receipts extract --database-url OWNER`.
- **Generator realism** (your instruction: salary, rent, subscriptions,
  groceries, occasional overdrafts). Subscriptions were random one-off
  purchases; each customer now has 1–3 that charge the same amount on the
  same day every month. Overdrafts were chronic (31 of 50 customers, one at
  80 fees a year): customers without direct deposit paid rent sized to a
  paycheck they never received. Now everyone is paid every other Friday
  (direct deposit or mobile check), card spending is 75% of what pay
  leaves after rent (60% premier, 80% students), and a month-end sweep
  keeps checking near rent plus a per-customer cushion. Result, seed 7:
  23,115 transactions, 625 fees; 31 customers overdraw, **median 3 times a
  year** (one struggling customer at 38), 121 overdraft fees under the 2025
  schedule and 83 under 2026. Tests hold subscriptions to monthly fixed
  amounts and overdrafts to "occasional, under both schedules".

### Notes (commit 3)

- **Manifest metadata** (doc_type, department, version, effective_date,
  superseded_on) goes on `documents`, for Phase 4's "as of" filters.
  Chunks carry their `section` for parent-section joins.
- **LLM metadata**: one call per batch of up to 6 neighbouring chunks of a
  document (≤3,000 tokens): 62 chunks, 13 calls. Each reply is validated
  per chunk (non-empty summary, keywords, questions; trimmed to 400 chars,
  8 keywords, 4 questions); a chunk with an unusable reply is stored bare
  and retried on the next run. The prompt tells the model the excerpts are
  data and to ignore instructions in them (red-teamed in Phase 11).
- **Cache**: `data/.cache/chunk-metadata.json` (gitignored), keyed by hash
  of prompt version, model and chunk text, saved even if the load fails
  halfway, so quota spent is never lost. Local, not in the database: it
  holds summaries of the staff memo and is read before any access context.
- **Loader**: `python -m bank.corpus load` (or `--no-metadata`) writes all
  13 documents as an admin of the bank org, replacing each document, its
  chunks and tables in one transaction (stable ids make re-runs replace,
  not duplicate). A Postgres test loads the whole corpus: 13 documents, 32
  tables, metadata on every chunk; a customer sees 12 documents, no
  canary, no staff table.
- **Not run yet**: the real load (embeddings + 13 metadata calls) waits for
  Neon (Phase 4) and your go.

## Phase 4: Neon

PR #5 (Phases 0–2) was merged on 2026-11-14 with a merge commit
(`926ddee`); `finpilot` and `finpilot-phase3` are kept.

| # | Commit | What |
|---|---|---|
| 1 | `c6b8571` | Chunks never cross a section or hold two tables; replaced documents keep unchanged chunks' vectors; Neon setup (`scripts/neon.py`, psycopg URL schemes in config); batched synthetic inserts; read-only leakage check (`scripts/leakage_check.py`) |
| 2 | `e538303` | Metadata filters (department, document type, the version in force on a date, today by default); hypothetical-question index (migration 0012) as a third RRF leg; cross-encoder rerank toggle (fastembed, ONNX); parent-section expansion within a token cap; default scope is every visible document |
| 3 | `959b891` | Neon branching experiment: 19 reviewed questions, six configurations; defaults dense, no rerank, no parent expansion; one env file (repo-root `.env`), `scripts/neon.py --branch` |
| spare 3 | this commit | Full-text leg ranks by distinct terms and adjacent pairs (the "overdraft cushion" bug); provisional defaults hybrid + rerank; latency breakdown; real-reranker CI test with the model cached; rerun recorded |

### Notes

- **Neon is set up and loaded.** All 11 migrations ran as the owner;
  `finpilot_app` was created with a random password, kept as
  `NEON_APP_DATABASE_URL` in `backend/.env`. Logged in as it: not a superuser, no `BYPASSRLS`, owns no table. Loaded:
  50 customers (23,115 transactions, 625 fees), 13 documents, 114 chunks,
  10 receipts. No connection string is ever printed.
- **Leakage check on Neon: clean.** 13 protected tables, all 50 customers,
  7 customer-owned tables, 4 tables with clearance; staff see the 6
  clearance-2 chunks (so the check isn't passing on empty data).
  It refuses the owner role (`BYPASSRLS`). It is read-only; the pytest
  fixtures never touch Neon (they create and drop databases and reset
  `finpilot_app`'s password, which would lock the app out). A CI test turns
  RLS off on `accounts` and checks that the leak is reported, which caught a
  hole while writing it: a table whose RLS was off had dropped out of the
  check. Every table with a `customer_id` or `clearance` column (except
  `users`, the sign-in directory) must now be under forced RLS.
- **Chunking rule changed** (your call, 2026-11-14). A chunk was labelled
  with its first block's section, and a chunk under 256 tokens took in the
  next section: the deposit agreement's 8.7 (third-party disclosure) sat in
  a "section 8" chunk that was all 9. Interest, and the workbook's card and
  previous deposit rates shared a chunk. Labelling by majority would only
  have hidden it. Now a chunk never crosses a section (heading path or
  sheet) or holds two tables. Result: 114 chunks (was 62); 83 under 256
  tokens (35 under 100); median 169, maximum 500. A corpus-wide test finds
  every Markdown chunk's text in the raw file, takes the heading above it
  there, and checks the chunk has exactly that one section, one top-level
  section and at most one table.
- **Only changed chunks cost calls.** 86 of 114 chunk texts changed:
  21 metadata calls (the cache is keyed by text) and 86 embeddings (a
  replaced document now keeps the stored vector of an unchanged chunk).
  A full metadata run is now 26 calls (was 19; batches never span
  documents, so each document starts its own).
- **Synthetic load batched**: a Core `insert()` sends multi-row VALUES;
  `text()` made psycopg2 do a round trip per row. On Neon: before, 650 s
  for 17 of 50 customers, after which the connection failed (projected about 32 min
  for all 50); after, **33 s for all 50**.
- **Receipt vision pass** (gemini-3.5-flash-lite, 10 calls, 58 s):
  merchant 10/10, date 10/10, amount 10/10, card last 4 10/10. Every value
  is exact; only the merchant comparison ignores case (the receipts print
  it in capitals).
- **Budget raised to 24** (2026-11-14): Phase 4 has commits 2 (retrieval
  options) and 3 (the Neon branching experiment); one spare remains.

### Notes (commit 2)

- **Filters** (`ingestion.store.Filters`): department, doc_type, and the
  version in force on `as_of` (effective on or before it, not superseded
  by then; a document without dates always counts). Default: today, so the
  2025 fee schedule and overdraft policy drop out unless asked for
  (`as_of`, or `all_versions`). One SQL fragment on every leg, a subquery on
  `documents`, which RLS covers too. Checked read-only on Neon before
  pushing: the default leaves out both 2025 documents; `as_of=2025-06-01`
  returns only what was in force then.
- **Default scope changed.** With no `doc_ids` the API used to search only
  the newest upload (DocuMind's rule against stitched answers). With the
  version filter that job is done properly, so it now searches every
  document the caller may see; the UI text says so.
- **Question index** (migration 0012): `chunks.question_embedding`, one
  vector per chunk for its LLM-written questions joined, HNSW, same model
  and type as `embedding`, on the chunk row so the chunks policy covers it.
  A third RRF leg in hybrid mode; `RETRIEVAL_MODE=dense` uses chunk vectors
  only. Embedded on load, reused when the questions don't change.
- **Rerank** (`RERANK`, off until the experiment decides): fastembed's
  ONNX cross-encoder `Xenova/ms-marco-MiniLM-L-6-v2` (80 MB, loads on
  first use: 2.4 s locally, then about 75 ms for 3 passages) scores the
  top `RERANK_CANDIDATES` (20) fused chunks. fastembed needs
  `huggingface_hub<2`, so constraints move it from 2.2.0 to 1.33.0, which
  sentence-transformers 6.2 accepts (pip resolves the whole set).
- **Parent-section expansion** (`PARENT_EXPANSION`, off until the
  experiment decides): each hit becomes its top-level section (title >
  heading) grown outward from the hit, contiguous, while the joined text
  stays under `PARENT_MAX_TOKENS` (1,500). A second hit in the same parent
  is dropped, not repeated; page-chunked uploads (no section) stay as they
  are. `context_chunk_ids` lists the chunks a hit's text holds, which the
  experiment uses for recall.

### Retrieval defaults: provisional design decision (2026-11-14)

**Provisional default: hybrid + rerank (`RETRIEVAL_MODE=hybrid`,
`RERANK=true`), parent expansion off**, as the architecture specifies. This
is not a result: on a 114-chunk corpus the modes can't be told apart, so the
final choice is made in **Phase 10, on the full corpus** (with the public
sources, where tickers, "Item 1A" and regulation names should favour the
full-text leg). Commit 3 had picked dense only; that was withdrawn (your
review, 2026-11-14) because recall saturated, hybrid's losses came from a
bug, and the latency was unexplained.

**Setup** (both runs). Neon branch `chunking-experiment` (a copy of main:
13 documents, 114 chunks), migration 0012, reloaded (0 metadata calls, 114
question vectors). 19 reviewed questions (`backend/eval/branch_questions.yaml`):
15 paraphrases, 3 exact-term ("overdraft cushion", `POST /cards/{card_id}/lock`,
"Zero Liability"), 1 version trap (overdraft fees a day: 2 in 2026, 3 in
2025; a 2025-only hit is a miss). Gold sections and verbatim answer
snippets, checked against the corpus by a unit test. Asked as a customer,
default filter, top_k 6; latency is search only, median of 3 rounds, from
this laptop to Neon. `python -m eval.branch_experiment run` via
`scripts/neon.py --branch`.

**Run 1, before the full-text fix** (commit 3):

| Configuration | Recall@1 | Recall@3 | Recall@6 | MRR | Answer in context | Context tokens | p50 ms | p95 ms | Missed | No answer |
|---|---|---|---|---|---|---|---|---|---|---|
| dense | 0.89 | 1.00 | 1.00 | 0.93 | 1.00 | 1325 | 436 | 671 | - | - |
| dense + parents | 0.89 | 1.00 | 1.00 | 0.93 | 1.00 | 1463 | 917 | 1022 | - | - |
| hybrid | 0.79 | 0.89 | 1.00 | 0.85 | 1.00 | 1322 | 698 | 814 | - | - |
| hybrid + parents | 0.79 | 0.89 | 1.00 | 0.85 | 1.00 | 1411 | 1142 | 1304 | - | - |
| hybrid + rerank | 0.89 | 0.89 | 1.00 | 0.92 | 1.00 | 1471 | 1948 | 2223 | - | - |
| hybrid + rerank + parents | 0.89 | 0.89 | 1.00 | 0.92 | 1.00 | 1612 | 2558 | 3074 | - | - |

**The bug.** The full-text leg ORed every query term and ranked by
`ts_rank_cd`, which rewards repetition: "What is the overdraft cushion?"
ranked every overdraft-heavy section above the one row named "Overdraft
cushion". It now orders matches by distinct query terms held, then adjacent
query-term pairs held as phrases, then `ts_rank_cd` (a Postgres test checks
the cushion row ranks first). Matching is still any-term, for recall.

**Run 2, after the fix** (this commit):

| Configuration | Recall@1 | Recall@3 | Recall@6 | MRR | Answer in context | Context tokens | p50 ms | p95 ms | Missed | No answer |
|---|---|---|---|---|---|---|---|---|---|---|
| dense | 0.89 | 1.00 | 1.00 | 0.93 | 1.00 | 1325 | 406 | 576 | - | - |
| dense + parents | 0.89 | 1.00 | 1.00 | 0.93 | 1.00 | 1463 | 690 | 880 | - | - |
| hybrid | 0.89 | 0.95 | 1.00 | 0.93 | 1.00 | 1340 | 696 | 802 | - | - |
| hybrid + parents | 0.95 | 1.00 | 1.00 | 0.97 | 1.00 | 1501 | 1079 | 1261 | - | - |
| hybrid + rerank | 0.89 | 0.89 | 1.00 | 0.92 | 1.00 | 1471 | 1635 | 2061 | - | - |
| hybrid + rerank + parents | 0.89 | 0.89 | 1.00 | 0.92 | 1.00 | 1638 | 2275 | 2644 | - | - |

Rank of the first result holding a gold section, for the questions that
weren't first everywhere:

| Q | dense (before / after) | dense + parents (before / after) | hybrid (before / after) | hybrid + parents (before / after) | hybrid + rerank (before / after) | hybrid + rerank + parents (before / after) |
|---|---|---|---|---|---|---|
| Q3 | 1 / 1 | 1 / 1 | 5 / 1 | 5 / 1 | 1 / 1 | 1 / 1 |
| Q7 | 1 / 1 | 1 / 1 | 1 / 5 | 1 / 1 | 1 / 1 | 1 / 1 |
| Q12 | 3 / 3 | 3 / 3 | 2 / 2 | 2 / 2 | 4 / 4 | 4 / 4 |
| Q14 | 1 / 1 | 1 / 1 | 3 / 1 | 3 / 1 | 1 / 1 | 1 / 1 |
| Q16 | 1 / 1 | 1 / 1 | 5 / 1 | 5 / 1 | 4 / 4 | 4 / 4 |
| Q18 | 3 / 3 | 3 / 3 | 1 / 1 | 1 / 1 | 1 / 1 | 1 / 1 |

What the two runs show (19 questions, so one question moves MRR by about 0.05):

- **Recall@6 and answer-in-context are 1.00 in every configuration, both
  runs.** The corpus is too small for them to separate anything.
- **The fix made hybrid equal to dense** (MRR 0.85 to 0.93): Q3, Q14 and
  Q16 back to rank 1, while keeping its exact-term win on Q18 ("Zero
  Liability", 3 to 1).
- **Hybrid + parents ranks best** (0.97): merging hits of one parent section
  moves the next section up (Q7).
- **The reranker loses two table rows**: the APY table (Q12) and the
  cushion row (Q16) fall to rank 4. The MS MARCO cross-encoder was trained
  on prose passages; table rows are a known weak spot. It stays the
  provisional default, as specified; Phase 10 should test it on the full
  corpus and consider skipping it for table chunks.

**Where the latency goes** (`python -m eval.latency_breakdown`, branch,
from this laptop):

| Component | Measured |
|---|---|
| Database work per leg (EXPLAIN ANALYZE) | dense 0.7 ms, full-text 1.2 ms, questions 0.8 ms |
| Network round trip (warm `SELECT 1`, us-east-1) | 95 to 102 ms |
| Round trips per search | dense 5 (3 statements + pool pre-ping + COMMIT), hybrid 7 |
| Dense search end to end | 485 ms, about 5 x the round trip |
| Hybrid search end to end | 662 ms, about 7 x the round trip |
| Reranker CPU (this laptop) | about 340 ms for 20 short texts, about 920 ms for 20 real chunks |
| Neon cold start (compute had scaled to zero; up 1 s) | first connection 2,469 ms |
| Fresh connection, compute warm (TLS + auth via Neon's proxy) | 1,486 ms; the pool pays it once |

So the 436 ms of run 1 was network: the search itself is about 1 ms. In
production the backend runs next to the database, where a round trip is
about a millisecond. Collapsing the access-context statements and the legs
into fewer round trips would help callers far from Neon; not done.

**Testing.** CI runs hybrid with rerank **off** (`tests/conftest.py`), so
tests don't load the model; `tests/unit/test_reranker_model.py` runs the
real cross-encoder, with the model cached between CI runs
(`FASTEMBED_CACHE_PATH`, actions/cache). A unit test pins the provisional
defaults. Not done: the Docker image doesn't pre-download the model, so a
container's first reranked query fetches it (Phase 12).

**One env file** (same commit). The Neon strings moved from `backend/.env`
to the repo-root `.env` (`NEON_MIGRATION_DATABASE_URL` and
`NEON_POOLED_DATABASE_URL`, owner; `NEON_APP_DATABASE_URL`, finpilot_app;
`NEON_BRANCH_DATABASE_URL`, the branch's owner); `backend/.env` held only
those and two identical API keys, so it was removed. The backend's config
reads the root file by absolute path. No env file holds `DATABASE_URL` or
`MIGRATION_DATABASE_URL`: Compose and `scripts/neon.py` set them per
process, so a local run or test can't reach Neon by accident. Checked on
Neon: the store refuses the owner URL (`BYPASSRLS`) and accepts
finpilot_app's.

## Phase 5: public sources

| # | Commit | What |
|---|---|---|
| 1 | `9046cf3` | Public sources (SEC 10-K Items 1A and 7, ETF summary prospectuses, CFPB answers): fetched with `SEC_USER_AGENT`, cached outside git, chunked and loaded like the bank's documents; paced embedding in both loaders |
| 2 | `a58e235` | Accounts tools (read-only, parameterized, under the customer's RLS context) with adversarial tests; search legs in one SQL statement |
| fix | `028e2ed` | A hostile-argument test meant to pass validation used `or_1_eq_1`, which the category rule (letters and underscores) rightly refuses; now `or_one_eq_one` (CI caught it) |
| 3 | `0c889ba` | Balances (starting balances in the generator, `get_balance`); market tools (prices, SEC financials, FRED rates) and the calculator; search in one round trip |
| fix | `33a4d1c` | The schema test listed every raw-SQL table by hand and failed on 0013's two new ones (the same trap as Phase 1); it now ignores any table the ORM doesn't define, and still catches drift in the log tables (CI caught it) |
| data | `c83c1eb` | Card autopay from checking, late payers and late fees, card spending inside the budget; receipts re-rendered |

### Notes

- **What's in it** (`data/public/sources.yaml`, the only part committed;
  `data/SOURCES.md` lists every filing and URL): Items 1A and 7 of the
  10-Ks of Apple (FY2026), Microsoft (FY2026) and Coca-Cola (FY2025); the
  summary prospectuses of VTI, BND and SCHD (three of the four demo ETFs);
  three CFPB answers (overdrafts, credit card disputes, unauthorized debit
  transactions).
- **Investor.gov is not in it.** Its CDN (Akamai) answers 403 to any
  non-browser client, the declared SEC User-Agent included, for pages and
  PDFs. FinPilot doesn't impersonate a browser, so it's recorded as blocked.
- **Corpus size for your review.** 9 public documents, **326 chunks, about
  105,000 tokens** (108 under 256 tokens; median 351, maximum 511). With the
  bank's 13 documents and 114 chunks, the knowledge base is **22 documents,
  440 chunks**, about 4x the bank corpus alone; the 10-Ks are 262 of the
  public chunks (Coca-Cola 121, Microsoft 86, Apple 55), the prospectuses
  54, the CFPB answers 10.
- **Extraction.** HTML to Markdown with lxml: headings (h tags, or short
  all-bold lines), paragraphs, tables as rows (`$` cells dropped, `%` and
  `)` joined to their number). For a 10-K, each item runs from its heading to
  the next item's, taking the longest such span so the table of contents
  doesn't win; running page headers ("Item 1A" on every Microsoft page),
  page numbers and "Table of Contents" are dropped. CFPB pages stop before
  their "Browse related questions" link lists. Every document opens with
  its attribution line. Public documents are clearance 0 in the bank's org,
  with doc_type (`sec_10k`, `fund_prospectus`, `consumer_guide`),
  department `public`, version and the filing date as effective date, for
  the filters.
- **Fetching.** SEC: `SEC_USER_AGENT` from `.env` (never printed or
  committed), 4 requests a second (limit 10). CFPB: one a second, plain
  project User-Agent. Raw HTML is cached in `data/.cache/public/`
  (gitignored), so loads don't refetch.
- **Load status: partial.** Main Neon was migrated to 0012 (the hybrid
  default needs the question index) and the bank corpus reloaded with
  question vectors. Chunk metadata for all 326 public chunks is done
  (59 calls, cached; Gemini Flash-Lite answered many first attempts with
  503 "high demand", and one run gave up). Embedding then hit the **daily**
  free-tier cap of `gemini-embedding-2` (1,000 texts a day per project:
  today's reloads, question vectors and experiment queries used most of
  it). Stored so far: the Apple and Microsoft 10-Ks (141 chunks, every one
  with its question vector). **To finish after the reset (midnight Pacific):**
  `scripts/neon.py run -- python -m bank.public load`; metadata comes from
  the cache (0 calls), the stored documents keep their vectors, and the
  other 7 documents need about 370 embeddings. The leakage check on main
  stays clean with the public documents in.
- **Paced embedding.** A loader now gives the store a paced embedder (90
  texts and 27,000 tokens a minute, under the free tier's 100 and 30,000),
  so a large document no longer runs into the per-minute limit; it existed
  for the old loader but wasn't wired into the store path.
- **Not done:** VXUS's prospectus; no Postgres test of the public loader
  (the fetched HTML isn't in CI), only unit tests on inline HTML.

### Notes (commit 2)

- **Accounts tools** (`backend/tools/accounts.py`): `list_accounts`,
  `list_cards`, `list_transactions`, `list_fees`, `list_holdings`, the
  financial-data agent's view of customer data (Phase 6 calls them by
  name). Four layers, each enough alone to stop a cross-customer read: no
  tool takes a customer id (the customer is the authenticated caller;
  staff and anonymous callers are refused before any SQL); strict argument
  models (an unknown argument such as `customer_id` is refused, ids must
  look like the bank's, dates are dates, limits at most 200, ranges
  ordered); parameterized SQL only; and a `READ ONLY` transaction bound to
  the caller, as finpilot_app, which has SELECT only and is under forced
  RLS. Another customer's id returns nothing, exactly like an id that
  doesn't exist, so nothing is revealed. No balances: the data has no
  opening balances, so a balance would be invented; sums are the
  calculator's (next commit).
- **Adversarial tests.** Unit (before any SQL): staff and anonymous
  callers, unknown or write-like tool names, a smuggled `customer_id`,
  quote and comment injection in ids, categories and fee types, oversized
  limits, reversed ranges. Postgres (CI): a customer sees only their own
  rows in every tool; another customer's real account id (checked to exist)
  returns nothing; one tool object serving two callers never mixes them;
  hostile text that passes validation is only data, and table counts are
  unchanged; a write fails in the tools' transaction ("read-only
  transaction") and, without it, for lack of a grant ("permission denied").
  Checked read-only on Neon too: every tool returns only the caller's rows;
  another customer's account gives nothing.
- **Search legs in one statement** (your suggestion). The three legs run as
  one `UNION ALL` query, and the scan setting rides with the access
  context, so a search is 2 statements instead of 5 (hybrid) or 3 (dense).
  The access context stays a statement of its own: RLS reads it, and inside
  one statement Postgres doesn't promise it's set before the policies run.
  The pool's pre-ping stays too (Neon drops idle connections). Same 12
  queries, same fused ranking, old and new. A Postgres test pins a hybrid
  search to 2 statements. Measured from this laptop on the branch
  (`python -m eval.latency_breakdown`):

| Mode | Before: statements | Before: end to end | After: statements | After: end to end |
|---|---|---|---|---|
| dense | 3 | 443 ms | 2 | 388 ms |
| hybrid | 5 | 652 ms | 2 | 422 ms |
| hybrid + rerank | 5 | 1,638 ms | 2 | 1,453 ms |

  Four round trips remain per search (pre-ping, access context, legs,
  COMMIT); next to the database each is about a millisecond.
- **Still open from your decisions.** The public load waits for the
  embedding quota (resets at midnight Pacific); then
  `scripts/neon.py run -- python -m bank.public load` and the leakage
  check on main. Investor.gov: `data/public/manual/` doesn't exist yet;
  it's gitignored now, and loading from it will be added once the pages
  are saved there (with a SOURCES.md note that they were saved by hand
  from a browser).

### Notes (commit 3)

- **Balances.** Each account now has a starting balance on 2025-07-01
  (migration 0013): checking gets the generator's own simulated opening
  balance, so the stored balance matches its simulation; savings and the
  card get amounts drawn from a stream of their own (per customer), so no
  other generated row changed (checked: identical output apart from the
  two new fields, receipts included). `get_balance` (same four layers as
  the other accounts tools) returns starting balance + transactions - fees
  not waived, in integer cents, as of the account's last activity. Across
  the 50 customers: checking -$263 to $10,892 (median $3,236), savings
  $289 to $73,569 (median $10,970), cards owing $304 to $1,801. Known
  gap: the data has no card payments, so card balances only grow;
  adding payments would change checking balances and overdrafts.
  Synthetic data reloaded on Neon (about 30 s).
- **Market tools** (`tools/market.py`, any signed-in caller, read-only):
  `get_price` (on or before a date, with date and source),
  `get_price_history`, `get_company_financials` (AAPL, MSFT, KO: revenue,
  net income, operating income, diluted EPS, operating cash flow, total
  assets, equity), `get_rate` (DPRIME, DFF, DGS10). Every row carries its
  attribution: "Prices: Alpha Vantage" or "Synthetic demo price (not a
  market price)", the 10-K's form, date and accession number, FRED's
  required notice. The tools only read the database;
  `python -m bank.market fetch sec|prices|rates` fills it (as the owner).
  Two new public tables, `company_facts` and `rate_observations`: no
  customer or clearance column, so no RLS; the app role reads them only
  (a test checks writes are refused).
- **What's fetched.** SEC company facts (data.sec.gov, `SEC_USER_AGENT`):
  105 rows on Neon, 3 companies x 7 metrics x 5 fiscal years; annual 10-K
  figures only, the latest filing for each year (later 10-Ks repeat
  earlier years), durations of about a year so quarters don't slip in.
  **Not fetched:** real prices and FRED rates; `.env` has no
  `ALPHA_VANTAGE_API_KEY` or `FRED_API_KEY` (both free). Until then
  `get_price` returns the synthetic prices, labelled as such, and
  `get_rate` returns nothing.
- **Calculator** (`tools/calculator.py`): `afford`, `total`, `interest`
  (simple or daily compounding), `percent_change`, `market_value`, `gain`.
  Decimal, money rounded half-up to the cent, inputs bounded, every result
  with its formula. It reproduces the deposit agreement's worked example
  ($10,000 at 3.54% for 30 days: $29.14). On Neon, customer 1 can spend
  $1,200 and keep $1,614.39 in checking, above a $500 floor.
- **Search in one round trip** (your suggestion). The access context and
  the legs now go as one query string; Postgres runs its statements in
  order in the transaction, so RLS sees the context. It relies on
  psycopg2 filling in parameters client-side; a driver that binds on the
  server refuses several statements (an error, never an unscoped query).
  Checked on the branch: same fused ranking on 12 of 12 queries; a
  customer sees none of the 6 staff-memo chunks staff see; an unscoped
  store sees nothing. A Postgres test pins a hybrid search to one
  statement and checks another org gets nothing. Effective round trips per
  search: about 4.2 before, 2.7 now (the rest are the pool's pre-ping and
  the COMMIT); hybrid end to end 202 ms today (round trip 73 ms; 422 ms
  last time at about 100 ms).
- **Public load finished** after the quota reset: 9 public documents, 326
  chunks, 0 metadata calls (cached); Neon main now holds 22 documents, 440
  chunks, every one with its question vector. The leakage check on main
  is clean.

### Notes (card data, before Phase 6)

- **Autopay** (your call, 2026-11-16; checking balances and overdrafts
  were allowed to change). As the cardmember agreement says: a cycle closes
  at month end, payment is due on the 25th of the next month, and autopay
  pays the statement balance from checking (a debit like any other, so it
  can overdraw). About one customer in seven sometimes misses autopay and
  pays by hand 3 to 12 days late; a late fee follows the day after the due
  date, under the schedule in force, never more than the minimum payment
  (the greater of $25.00 or 1%). The old random 5% late fee, with no
  lateness behind it, is gone. The card's balance is charges minus
  payments (plus its fees), so `get_balance` needed no change.
- **Card use.** Purchases were on 2% of days (about 7 a year), so most
  cards owed nothing; now about 1 day in 7 (about 55 a year), with foreign
  merchants 1 purchase in 20. Card spending comes out of the same budget as
  debit spending: without that, overdrafts became chronic (median 32 a year
  in a test sample).
- **Result** (seed 7, 50 customers): 26,529 transactions, 703 fees, 569 card
  payments (median $288.77). Balances: checking -$966 to $15,424 (median
  $3,452), savings $689 to $96,860 (median $10,876), cards owing $11 to $944
  (median $272). Overdrafts stay occasional: 28 customers, median 3 a year,
  one at 33. **Late fees: 49, for 12 customers, 30 under the 2025 schedule
  and 19 under 2026**; all $25.00, because every statement's minimum payment
  was $25.00, which caps the fee below the schedule's $39 or $40.
- **Receipts** come from these transactions, so the 10 PNGs were rendered
  again, and the synthetic loader now deletes a customer's receipt
  extractions when it replaces their transactions (they described
  transactions that no longer exist). Reloaded on Neon and the vision pass
  rerun (10 calls): merchant 10/10, amount 10/10, card last 4 10/10,
  **date 8/10**. Both misses swap day and month ("01/02/2026" read as
  1 February): the old receipts' dates happened to be unambiguous. See the
  Phase 10 notes.
- **Leakage check on main: clean** after the reload.
- **Not done:** real prices and FRED rates. `ALPHA_VANTAGE_API_KEY` and
  `FRED_API_KEY` aren't in `.env` (it was last saved on 2026-11-14).

## Phase 6: the reasoning engine

| # | Commit | What |
|---|---|---|
| 1 | this commit | Planner, executor, agents and summarizer as a LangGraph (`backend/engine/`), `POST /ask`, `python -m engine.cli` |

### Notes

- **The flow** (`engine/graph.py`): validate (length, PII masking) and
  route in code (small talk answered without a model); the **planner**
  (LLM call 1) writes a typed plan of at most 6 steps, each an agent, a
  tool, its arguments and why; the **executor** (code) runs them as the
  caller; the **summarization agent** (LLM call 2) writes one answer
  citing numbered evidence. Agents: financial data (the accounts and
  market tools), documents (hybrid search under the caller's RLS),
  calculation (the calculator). An out-of-scope plan, or one that found
  nothing, is answered without the second call.
- **The plan is untrusted.** Unknown agents or tools, unreadable arguments
  and steps past 6 are dropped (and listed); every argument still goes
  through the tool's strict model, and every tool runs as the caller. A
  plan that asks for another customer gets a failed step, not their data
  (tests: unit, and `/ask` on Postgres as a customer and as staff).
- **No arithmetic in the model.** A calculation step refers to earlier
  results ("$s1[0].balance_cents", "$s2[*].amount_cents"), resolved in
  code. Amounts are formatted in code too: dollars beside every cents
  field, SEC figures ("$47.94 billion" since Phase 7, "$7.25 per share"), prices to
  the cent.
- **LLM calls per question: 2** (planner, summarizer), under the target of
  3 to 4; Phase 7's validator adds the third. Counted by the engine itself
  (`llm_calls`, `llm_calls_by_role`); tokens per role come along for
  Phase 8.
- **Found while running it, and fixed:** `get_balance` with no arguments
  returned every account and the plan took the first row, the credit card,
  so "can I afford" ran on a card balance. Accounts can now be asked by
  `account_type` ("checking"), and the planner prompt has short recipes
  for common question shapes (explain a charge, affordability, spending,
  companies and funds). Also: the model cites "[1, 2]" as well as "[1]";
  both are read now.
- **`/query` stays** (the documents-only pipeline) until the Phase 9 UI
  moves to `/ask`.
- **Five questions on Neon** (customer cust-0036, real Gemini,
  gemini-3.5-flash-lite for both calls; a few 503 retries along the way):

```
Q: Why was I charged $15 on April 6, 2026?
  s1 financial_data.list_fees {"start": "2026-04-06", "end": "2026-04-06"} -> ok
  s2 financial_data.list_transactions {"start": "2026-04-06", "end": "2026-04-06"} -> ok
  s3 documents.search {"query": "fee 15 dollars", "doc_type": "fee_schedule"} -> ok
A: You were charged a $15.00 overdraft fee on April 6, 2026, because your checking account was overdrawn by more than $50.00 after a card purchase at Corner Grocer [1][2][5].
Citations:
  [1] list_fees
  [2] list_transactions
  [5] fee-schedule-2026.md > FinPilot Bank Schedule of Fees: Personal Accounts > 1. Everyday Checking
LLM calls: 2 {'planner': 1, 'summarizer': 1}; 11.2 s; outcome answered

Q: Can I afford $1,200 this month and stay above $500?
  s1 financial_data.get_balance {"account_type": "checking"} -> ok
  s2 calculation.afford {"balance_cents": "$s1[0].balance_cents", "amount_cents": 120000, "keep_at_least_cents": 50000} -> ok
A: Yes, you can afford $1,200.00 this month and stay above your minimum [2]. Your checking account balance is $2,677.23 as of 2026-06-30 [1]. After the purchase, you will have $1,477.23 remaining [2].
Citations:
  [1] get_balance
  [2] afford
LLM calls: 2 {'planner': 1, 'summarizer': 1}; 3.2 s; outcome answered

Q: How much did I spend on groceries in May 2026?
  s1 financial_data.list_transactions {"category": "groceries", "start": "2026-05-01", "end": "2026-05-31", "limit": 200} -> ok
  s2 calculation.total {"amounts_cents": "$s1[*].amount_cents"} -> ok
A: You spent $171.05 at FreshFields Market on 2026-05-28 and $88.93 at Corner Grocer on 2026-05-13 [1]. Your total spending for these two grocery transactions was $259.98 [2].
Citations:
  [1] list_transactions
  [2] total
LLM calls: 2 {'planner': 1, 'summarizer': 1}; 3.2 s; outcome answered

Q: What were Coca-Cola's revenues in its last two fiscal years, and what does it see as a main risk?
  s1 financial_data.get_company_financials {"ticker": "KO", "metric": "revenue", "years": 2} -> ok
  s2 documents.search {"query": "Coca-Cola risk factors principal risks", "doc_type": "sec_10k"} -> ok
A: Coca-Cola's revenues were $47,941,000,000 for the fiscal year ended December 31, 2025, and $47,061,000,000 for the fiscal year ended December 31, 2024 [1]. The company highlights several risks, including unfavorable general economic and geopolitical conditions (such as inflationary pressures, changing interest rates, and commodity price changes) that could negatively impact its financial results [3]. Other noted risks include climate change and environmental factors affecting agricultural productivity and water availability [2][6], consumer demand shifts driven by ingredients or packaging concerns [4], and risks related to its operations, new product execution, and third-party relationships [3][5][7].
Citations:
  [1] get_company_financials (SEC EDGAR, 10-K filed 2026-02-20 (accession 0001628280-26-010047))
  [2] public/ko-10k-2025.md > The Coca-Cola Company Form 10-K, fiscal year ended December 31, 2025 > ITEM 1A. RISK FACTORS > RISKS RELATED TO ENVIRONMENTAL AND SOCIAL FACTORS
  [3] public/ko-10k-2025.md > The Coca-Cola Company Form 10-K, fiscal year ended December 31, 2025 > ITEM 1A. RISK FACTORS > RISKS RELATED TO OUR OPERATIONS
  [4] public/ko-10k-2025.md > The Coca-Cola Company Form 10-K, fiscal year ended December 31, 2025 > ITEM 1A. RISK FACTORS > RISKS RELATED TO CONSUMER DEMAND FOR OUR PRODUCTS
  [5] public/ko-10k-2025.md > The Coca-Cola Company Form 10-K, fiscal year ended December 31, 2025 > ITEM 1A. RISK FACTORS > RISKS RELATED TO CONSUMER DEMAND FOR OUR PRODUCTS
  [6] public/ko-10k-2025.md > The Coca-Cola Company Form 10-K, fiscal year ended December 31, 2025 > ITEM 1A. RISK FACTORS > RISKS RELATED TO ENVIRONMENTAL AND SOCIAL FACTORS
  [7] public/ko-10k-2025.md > The Coca-Cola Company Form 10-K, fiscal year ended December 31, 2025 > ITEM 1A. RISK FACTORS > RISKS RELATED TO OUR OPERATIONS
LLM calls: 2 {'planner': 1, 'summarizer': 1}; 5.4 s; outcome answered

Q: What is VTI's expense ratio, and what is its latest price?
  s1 documents.search {"query": "VTI expense ratio", "doc_type": "fund_prospectus"} -> ok
  s2 financial_data.get_price {"symbol": "VTI"} -> ok
A: Vanguard Total Stock Market ETF (VTI) has a total annual fund operating expense ratio of 0.03% [1]. Its synthetic demo price is $337.35 as of June 30, 2026 [7].
Citations:
  [1] public/vti-summary-prospectus-2026.md > Vanguard Total Stock Market ETF (VTI) summary prospectus, April 2026 > Fees and Expenses
  [7] get_price (Synthetic demo price (not a market price))
LLM calls: 2 {'planner': 1, 'summarizer': 1}; 4.7 s; outcome answered
```

### Notes (follow-ups, folded into the Phase 7 commit)

- **The documents agent has its own LLM loop.** After a search, code checks
  whether the evidence is weak: nothing found, a best rerank score below 0
  (the cross-encoder's logit), or, without rerank, no passage holding half
  the query's content words. Only then one LLM call (role `query_rewriter`)
  rewrites the query in the words the passage would use, and it searches
  once more, keeping the second result only if it isn't weak too. The step
  says so (`note`).
- **One re-plan when a step fails** (role `replanner`): the planner sees
  each step's status and error and plans replacement steps that can refer
  to the results of the steps that worked. Once only, and not when a tool
  refused the caller outright (`AccessRefused`: staff asking for customer
  data), where another plan can't help. A refused `customer_id` argument
  does get its one re-plan; the re-planner is told not to work around
  refusals, and the tools would refuse again anyway.
- **DEMO_TODAY** (default 2026-06-30, the last day of the data): the
  engine's "today" for the planner ("this month", "this year"), the
  summarizer and the validators, and the as-of date of searches (the
  document versions in force that day), prices and rates when no as_of is
  given. `/ask` returns it as `demo_today`. A question about another date
  gets figures for the nearest data, said plainly; silently answering for
  another date fails the auditor (below).
- **Receipts:** the reader is told these are US receipts, dates MM/DD/YYYY.
  All 10 again (`python -m bank.receipts extract --fresh`): **merchant
  10/10, date 10/10 (was 8/10), amount 10/10, card last 4 10/10.**
- **Large amounts in code:** "$47.94 billion", "$1.25 million".

## Phase 7: the validation nodes

| # | Commit | What |
|---|---|---|
| 1 | this commit | The Phase 6 follow-ups above, and gatekeeper, auditor and strategist after the summarizer |

### Notes

- **Every written answer is checked** (`engine/checks.py`, the `check`
  node). The fixed replies (out of scope, nothing found, small talk) aren't.
  Code first, no LLM: every dollar amount and percentage must match a
  number in the evidence or the question (to the precision the answer
  gives: "$47.94 billion" matches 47,941,000,000), there must be a
  citation, and no "you should buy", "I recommend investing", "I've
  transferred" and no other customer's id. Then **one structured LLM call**
  (role `validator`) judges the three:
  - **gatekeeper:** does it answer the question asked?
  - **auditor:** is every claim, number and date in the cited evidence, with
    the as-of date said when it differs from the date asked about?
  - **strategist:** no investment, tax or legal advice, no money moved or
    promised, nothing about another customer.
- **A failed check regenerates once** with the reason (the summarizer sees
  its previous answer and why it was rejected), and the new answer is
  checked again. A second failure is refused: "I can't give you an answer
  I could check against your accounts and the bank's documents (part of
  it wasn't supported by your data or the documents)…", outcome
  `refused_unverified`. A missing or unreadable verdict counts as a fail.
  `/ask` returns every verdict (`validation`), which validator failed and
  whether code or the LLM caught it.
- **Tests** (unit, fake LLM; CI): a wrong number is caught by the auditor in
  code and regenerated without a validator call; "you should buy VTI" is
  caught by the strategist and, on the second try, refused; an answer to
  another question is caught by the gatekeeper (the fake verdict) and
  regenerated with the reason. Plus the rewrite and the re-plan.
- **The real validator, on planted answers** to "Can I afford $1,200 this
  month and stay above $500?" (cust-0036, real evidence, gemini-3.5-flash-lite;
  code checks and the LLM call each run on their own, to see both):

| Planted answer | Code checks | LLM validator |
|---|---|---|
| Wrong number: balance "$3,677.23", "$2,477.23 left" | auditor fails: neither figure is in the evidence | auditor fails: "the evidence shows it is $2,677.23" |
| Unanswered: only the balance | pass | gatekeeper fails: "fails to answer whether the customer can afford $1,200" |
| Advice: "You should buy VTI with the rest" | strategist fails | strategist fails: "gives investment advice by telling the customer to buy VTI" |
| Wrong date: "today, November 16, 2026, your checking has…" | pass | auditor fails: "the evidence states the date is 2026-06-30" |

- **LLM calls per question: 3.0 on average** over the same five questions
  (planner, summarizer, validator each), in the 3 to 4 target. The rewrite,
  re-plan and regeneration didn't fire on them: every search was strong,
  no step failed, every answer passed the first time. Two more questions
  below (a vague policy question and a date after the data) also took 3.
- **Latency is the model's, today.** 16 to 37 s a question, against 3 to
  11 s in Phase 6. The same call took 0.5 s once and 7 to 9 s the next
  (planner 8.2 s, summarizer 6.5 s, validator 0.9 s on one question; 503
  "high demand" retries throughout). The validator adds one more call of
  that. Phase 8 measures it properly.
- **Seen in the run, fixed:** the planner searched "fee schedule" for the
  $15 charge and cited a section on how fees are shown. Its recipe now asks
  for a query naming the fee and its rule; the planner still wrote "fee
  schedule" once afterwards, but the answer named the purchase that
  caused it. Phase 10's eval will tell whether it needs more.

```
Q: Why was I charged $15 on April 6, 2026?  (today is 2026-06-30)
  s1 financial_data.list_fees {"start": "2026-04-06", "end": "2026-04-06"} -> ok
  s2 financial_data.list_transactions {"start": "2026-04-05", "end": "2026-04-06"} -> ok
  s3 documents.search {"query": "fee schedule", "doc_type": "fee_schedule"} -> ok
A: You were charged a $15.00 overdraft fee on April 6, 2026, for a card purchase of $25.86 at Corner Grocer [1][2]. Every fee appears as its own line on your statement, showing the date and related transaction [3].
Citations:
  [1] list_fees
  [2] list_transactions
  [3] fee-schedule-2026.md > FinPilot Bank Schedule of Fees: Personal Accounts > 6. How fees are shown
Check 1: gatekeeper pass (llm); auditor pass (llm); strategist pass (llm)
LLM calls: 3 {'planner': 1, 'summarizer': 1, 'validator': 1}; 27.1 s; outcome answered

Q: Can I afford $1,200 this month and stay above $500?  (today is 2026-06-30)
  s1 financial_data.get_balance {"account_type": "checking"} -> ok
  s2 calculation.afford {"balance_cents": "$s1[0].balance_cents", "amount_cents": 120000, "keep_at_least_cents": 50000} -> ok
A: Yes, you can afford that amount because your remaining balance after the purchase would be $1,477.23 as of 2026-06-30, which keeps you above your minimum threshold [1][2].
Citations:
  [1] get_balance
  [2] afford
Check 1: gatekeeper pass (llm); auditor pass (llm); strategist pass (llm)
LLM calls: 3 {'planner': 1, 'summarizer': 1, 'validator': 1}; 29.0 s; outcome answered

Q: How much did I spend on groceries in May 2026?  (today is 2026-06-30)
  s1 financial_data.list_transactions {"category": "groceries", "start": "2026-05-01", "end": "2026-05-31", "limit": 200} -> ok
  s2 calculation.total {"amounts_cents": "$s1[*].amount_cents"} -> ok
A: You spent $171.05 at FreshFields Market and $88.93 at Corner Grocer in May 2026, for a total of $259.98 [1][2].
Citations:
  [1] list_transactions
  [2] total
Check 1: gatekeeper pass (llm); auditor pass (llm); strategist pass (llm)
LLM calls: 3 {'planner': 1, 'summarizer': 1, 'validator': 1}; 17.1 s; outcome answered

Q: What were Coca-Cola's revenues in its last two fiscal years, and what does it see as a main risk?  (today is 2026-06-30)
  s1 financial_data.get_company_financials {"ticker": "KO", "metric": "revenue", "years": 2} -> ok
  s2 documents.search {"query": "risk factors Coca-Cola main risk", "doc_type": "sec_10k"} -> ok
A: Coca-Cola reported revenues of $47.94 billion for the 2025 fiscal year and $47.06 billion for the 2024 fiscal year [1]. The company highlights several risk factors in its Form 10-K, including risks related to consumer demand and product preferences [2], unfavorable general economic and geopolitical conditions such as inflationary pressures and recession risks [3], execution and regulatory risks from introducing new products like alcohol beverages [4], environmental factors such as climate change and water scarcity affecting agricultural ingredients [5], and dependencies on the financial strength and profitability of independent bottling partners [6].
Citations:
  [1] get_company_financials (SEC EDGAR, 10-K filed 2026-02-20 (accession 0001628280-26-010047))
  [2] public/ko-10k-2025.md > The Coca-Cola Company Form 10-K, fiscal year ended December 31, 2025 > ITEM 1A. RISK FACTORS > RISKS RELATED TO CONSUMER DEMAND FOR OUR PRODUCTS
  [3] public/ko-10k-2025.md > The Coca-Cola Company Form 10-K, fiscal year ended December 31, 2025 > ITEM 1A. RISK FACTORS > RISKS RELATED TO OUR OPERATIONS
  [4] public/ko-10k-2025.md > The Coca-Cola Company Form 10-K, fiscal year ended December 31, 2025 > ITEM 1A. RISK FACTORS > RISKS RELATED TO CONSUMER DEMAND FOR OUR PRODUCTS
  [5] public/ko-10k-2025.md > The Coca-Cola Company Form 10-K, fiscal year ended December 31, 2025 > ITEM 1A. RISK FACTORS > RISKS RELATED TO ENVIRONMENTAL AND SOCIAL FACTORS
  [6] public/ko-10k-2025.md > The Coca-Cola Company Form 10-K, fiscal year ended December 31, 2025 > ITEM 1A. RISK FACTORS > RISKS RELATED TO THE COCA-COLA SYSTEM
Check 1: gatekeeper pass (llm); auditor pass (llm); strategist pass (llm)
LLM calls: 3 {'planner': 1, 'summarizer': 1, 'validator': 1}; 37.0 s; outcome answered

Q: What is VTI's expense ratio, and what is its latest price?  (today is 2026-06-30)
  s1 documents.search {"query": "VTI expense ratio", "doc_type": "fund_prospectus"} -> ok
  s2 financial_data.get_price {"symbol": "VTI"} -> ok
A: VTI has a total annual fund operating expense ratio of 0.03% [1]. Its latest price is $337.35 as of June 30, 2026, which is a synthetic demo price (not a market price) [7].
Citations:
  [1] public/vti-summary-prospectus-2026.md > Vanguard Total Stock Market ETF (VTI) summary prospectus, April 2026 > Fees and Expenses
  [7] get_price (Synthetic demo price (not a market price))
Check 1: gatekeeper pass (llm); auditor pass (llm); strategist pass (llm)
LLM calls: 3 {'planner': 1, 'summarizer': 1, 'validator': 1}; 22.5 s; outcome answered

Q: How far below zero can my checking go before you charge me?  (today is 2026-06-30)
  s1 documents.search {"query": "overdraft fee cushion threshold checking", "doc_type": "fee_schedule"} -> ok
A: Your available balance can be overdrawn by $50.00 or less after a transaction without incurring an overdraft fee [1]. We charge a $15.00 overdraft fee per item when we pay a transaction that overdraws your available balance by more than $50.00 [1].
Citations:
  [1] fee-schedule-2026.md > FinPilot Bank Schedule of Fees: Personal Accounts > 1. Everyday Checking
Check 1: gatekeeper pass (llm); auditor pass (llm); strategist pass (llm)
LLM calls: 3 {'planner': 1, 'summarizer': 1, 'validator': 1}; 20.1 s; outcome answered

Q: What was my checking balance on December 1, 2026?  (today is 2026-06-30)
  s1 financial_data.get_balance {"account_type": "checking"} -> ok
A: I couldn't work out your checking balance for December 1, 2026, because the available balance is only as of June 30, 2026 [1]. As of June 30, 2026, your checking balance was $2,677.23 [1].
Citations:
  [1] get_balance
Check 1: gatekeeper pass (llm); auditor pass (llm); strategist pass (llm)
LLM calls: 3 {'planner': 1, 'summarizer': 1, 'validator': 1}; 22.1 s; outcome answered
```

## Phase 8: observability

| # | Commit | What |
|---|---|---|
| 1 | this commit | A trace per /ask in Postgres (`tracing.py`, migration 0014), `/admin/traces`, the stage timeline in `/ask`, per-call timeouts |

### Notes

- **One trace per request** (`traces`, migration 0014), written by `/ask`
  and `python -m engine.cli`. Its spans:
  - every engine node;
  - every agent step with its status;
  - every tool call;
  - every search, with its top rerank score, whether it was weak, and the
    sections found;
  - the database legs and the reranker separately;
  - every LLM call: model, role, prompt, output and thinking tokens,
    attempts, each failed attempt's error, and the estimated cost;
  - every embedding call: model, texts (shortened), estimated tokens and
    cost;
  - each validator's verdict and whether code or the LLM decided it.

  The summary columns hold the outcome, total time, LLM calls and estimated
  cost. The question stored is the redacted one the engine saw.
- **Under RLS:** an org sees only its own traces, and a customer's context
  only their own (the leakage check now counts `traces` among the
  customer-owned tables: no leaks on Neon). A trace that fails to save is
  logged, never fails the answer.
- **Costs are estimates** at Gemini's Standard list prices (flash-lite
  $0.30 / $2.50 per million input / output tokens, thinking billed as
  output; gemini-embedding-2 $0.20; checked 2026-11-16 on
  ai.google.dev/gemini-api/docs/pricing). The project runs on the free
  tier, which costs nothing. Embedding tokens are counted with the
  chunker's tokenizer.
- **Per-call timeouts:** `LLM_CALL_TIMEOUT_S` (45 s) caps a whole LLM or
  embedding call, retries and their waits included; each attempt keeps
  its own 60 s cap inside that. A call past it raises `TimeoutError`, and
  `/ask` reports `timeout`. Batch loads can raise it.
- **`GET /admin/traces`** (admins, own org): recent requests with total
  time, LLM calls, estimated cost, retries and outcome.
  **`GET /admin/traces/{id}`**: one trace with every span; another org's
  trace is a 404. A page for these comes with the Phase 9 UI.
- **`/ask` returns `timeline`** (each stage's start and duration, for the
  Phase 9 progress display), `trace_id`, and the trace's totals.

### Latency, from the traces (the five standard questions, cust-0036, Neon)

| Question | Total | Gemini (LLM) | Embeddings | Search: DB legs | Search: rerank | Tools (SQL) | Other |
|---|---|---|---|---|---|---|---|
| $15 charge | 31.2 s | 21.9 s | 0.5 s | 0.7 s | 1.5 s | 1.5 s | 5.2 s (first request: clients, pool, Neon) |
| Afford $1,200 | 27.0 s | 26.3 s (validator 20.5 s, 1 retry) | – | – | – | 0.7 s | 0.0 s |
| Groceries in May | 11.7 s | 11.0 s | – | – | – | 0.7 s | 0.0 s |
| Coca-Cola | 12.5 s | 9.4 s | 0.5 s | 0.7 s | 1.5 s | 0.5 s | 0.0 s |
| VTI | 35.9 s | 29.7 s (planner 18.4 s, 2 retries) | 4.6 s | 0.4 s | 0.9 s | 0.3 s | 0.0 s |
| **All five** | **118.3 s** | **98.3 s (83%)** | **5.6 s (5%)** | **1.7 s** | **3.9 s** | **3.6 s** | **5.2 s** |

- **Gemini is 83% of the time**, and its variance is the latency: the
  same role took 0.8 s in one question and 9.3 s in another, and three
  retries (503 "high demand") cost 13 s and 20 s on two questions.
- **Search is 5.6 s in all, and most of it is the reranker:** the
  cross-encoder runs on this machine's CPU, 0.9 to 1.5 s per search,
  against 0.4 to 0.7 s for the three database legs in one round trip.
  That's the one cost in our hands; Phase 10 weighs it with the
  rerank-on-tables question.
- **Tools** are about 0.3 to 0.7 s each: one Neon round trip each, plus
  the access context.
- **Our own code** is negligible after the first request. The first one
  paid about 5 s to create the Gemini client, the connection pool and
  the store (its startup check of the role connects to Neon).
- **Estimated cost per question** at list prices: $0.0018 to $0.0036 for
  three LLM calls, $0.0051 for the vague question that regenerated.

### Weak-search test

Three deliberately vague questions on Neon (cust-0036), traced:

| Question | What the planner searched | Top rerank | Weak? | Result |
|---|---|---|---|---|
| "What was that fee thing from last spring?" | no search: `list_fees` for March to May | – | – | listed the fees; the auditor (LLM) failed the first answer on its dates, the regeneration passed (5 LLM calls) |
| "Is there some kind of penalty if I pay my card a bit late?" | "late payment fee credit card penalty APR" | 3.59 | no | the card agreement's late fee (up to $40.00; $39.00 before 2026-02-01) |
| "What's the deal with you holding back money when I deposit a check?" | "check deposit hold policy availability funds" | 2.36 | no | the deposit agreement's hold rules, correct |

- **The rewrite didn't fire, and the reason is the planner:** it already
  turns the customer's words into the bank's terms before searching. The
  eight searches of this run scored 2.36 to 6.00 at the top, far above the
  cutoff of 0, and all answered.
- **The cutoff stays at 0 for now: I couldn't tune it from this.** Tuning
  needs scores of genuinely weak searches: the customer's own wording
  searched as is, and questions the corpus can't answer (mortgage
  refinancing, bitcoin). The script for that is ready, but this machine's
  free memory stayed between 0.9 and 2.1 GB, under the 2 GB floor for
  Gemini calls, so it didn't run. One run needs 10 embeddings and 5
  rewrites. (The traced run above started at 1.89 GB, under the floor; I
  noticed only afterwards.)
- **The regeneration did fire live** on the "fee thing" question, the
  first time in a real run: the LLM auditor caught dates it judged
  misstated, and the second answer passed.

## Cleanup after the audit (2026-11-17)

| # | Commit | What |
|---|---|---|
| 1 | this commit | README and plan checklist match the build; DocuMind leftovers removed; every setting in `.env.example`; the deposit agreement as a real PDF |

- **The audit** (read-only, 2026-11-16) found every architecture item but
  evaluation and red-teaming built and tested; the README still said
  "Phase 0" and no checklist box was ticked. The README now describes the
  engine, what's built, how to run it and the API; the plan's boxes are
  ticked to match the audit (24 done, 10 open, the partial ones say why),
  and the gatekeeper and strategist read as built: the gatekeeper checks the
  answer answers the question (scope is the planner's and the router's),
  the strategist refuses advice, money movement and other customers' data.
- **Removed:** DocuMind's eval harness (`run_eval.py`, `dataset.json`,
  `sample.pdf`, `rejudge_faithfulness.py`, and `bench_retrieval.py` and
  `count_grading_tokens.py`, which only served it), its RAGAS requirements
  and CI step, the upload size check in `extract.py` and `MAX_UPLOAD_MB`.
  Phase 10 builds its eval on what stays: `branch_experiment.py` (labelled
  questions, recall@k, MRR), `latency_breakdown.py`, the traces, and
  `firebase_auth.py` for calling a live API.
- **Kept on purpose:** `@documind.test` in the two Firebase scripts: it's
  the domain the existing eval account is checked against.
- **`.env.example`** lists every setting `config.py` reads, with a comment.
- **The deposit agreement is now a real PDF** (`data/bank/deposit-agreement.pdf`,
  11 pages, running header and "Page N of M" footer), built from
  `data/bank/sources/deposit-agreement.md` by
  `data/bank/tools/build_agreement_pdf.py` (reportlab, a build-time tool
  only). The corpus loads the PDF. The PDF parser now recovers structure from
  the layout with pdfplumber (headings by font size, ruled tables as pipe
  tables, paragraph breaks by line gaps, a paragraph joined across a page
  break), then goes through the Markdown parser. A test checks it gets back
  the source exactly: the same 25 sections, 4 tables cell for cell, every
  word, and no header or footer.
- **Neon still holds the Markdown version** of the agreement. Reloading it
  as the PDF costs Gemini calls (metadata and embeddings for its chunks),
  so it waits for the 2 GB memory floor.

## Phase 9: the chat UI

| # | Commit | What |
|---|---|---|
| 1 | 0c8a244 | `/ask/stream` (live stages and steps), the chat on `/ask`, the old `/query` pipeline deleted, friendly retryable errors |
| 2 | 9d371a6 | The "How I got this" panel under each answer |
| 3 | this commit | Walkthrough fixes: accounts sidebar, in-force documents only, readable sources, "why" explains the rule, plain advice and money-movement refusals |

### Notes

- **`POST /ask/stream`** (SSE) runs the same engine as `/ask` and sends:
  - the plan as soon as the planner returns it (again after a re-plan);
  - each stage as it starts and ends: Planning, Re-planning, Writing the
    answer, Checking the answer;
  - each step as it starts and ends, labelled by agent: Searching
    documents, Looking up your accounts, Looking up market data,
    Calculating (with its time, and the documents agent's rewrite note);
  - the answer once, in `done`, after the validators passed it. Nothing
    unchecked is ever shown.

  The events come from the trace: a listener on the request's trace hears
  each node and step span start and end, so there's one source of truth
  for timings. If the browser goes away, the engine task is cancelled.
- **Errors are a message, not a stack trace.** A model timeout, overload
  or rate limit becomes "The model is busy right now…" with
  `retryable: true`, and the chat shows a **Try again** button that resends
  the question. A used-up daily quota says to try tomorrow (no retry
  button). A question that fails validation (too long) is an error event
  with the reason. `/ask` itself returns the same `detail`, `error` and
  `retryable`; provider text goes to admins only (`diagnostic`).
- **`/ask` is now logged** in `query_logs` (route `ask`, the redacted
  question, answer, outcome, whether it regenerated), so feedback links to
  an answer through its `query_id`, and `/admin/logs` and `/admin/stats`
  keep working.
- **Deleted:** `/query`, `/query/stream`, `graph/pipeline.py`,
  `graph/nodes.py` and `graph/state.py` (DocuMind's fixed retrieve, grade,
  generate, groundedness pipeline). Input validation moved into the engine
  (`engine/graph.py validate_node`, same rules: empty, too long, PII
  masked first). PII redaction and the small-talk rule stay in
  `graph/redaction.py` and `graph/routing.py`. One behaviour not carried
  over: the old pipeline also masked PII inside retrieved passages; the
  engine doesn't, because the bank's own documents carry its phone numbers
  and addresses, which answers need.
- **The chat:** it shows the demo date ("Today is June 30, 2026 (demo
  date)", from `/me`), example questions to click (customer or staff),
  the live progress chips, the answer with its sources (document ›
  section, or the tool and its attribution), the outcome when it isn't an
  answer, the time and model calls, and thumbs up/down. The document list
  is now read-only: `/ask` decides what to search, so the old per-document
  scoping went with `/query`.
- **"How I got this"** (`frontend/src/HowIGotThis.tsx`), folded under every
  answer that ran a plan:
  - the plan's intent and each step: agent, tool, purpose, arguments,
    status, the evidence numbers it produced, the rewrite note, a failed
    step's error, re-plan steps marked;
  - the checks the answer passed, in plain words, each marked "checked
    in code" or "checked by the model", with the reason for any failure
    and both attempts when it was rewritten;
  - where the time went: a bar per stage on one time axis, then the split
    between the language model, document search and the customer's data,
    and any retries while the model was busy;
  - the model calls by role, the estimated cost at list price, and the
    trace id (for an admin's `/admin/traces/{id}`).

  Everything comes from the `/ask` result: no extra request.
- **Walkthrough fixes** (commit 3, from a signed-in run as cust-0036):
  - **Accounts sidebar:** `GET /accounts/summary` (customers only, 404 for
    staff) returns checking, savings and card balances and the five latest
    transactions, through the same read-only, RLS-bound tools the engine
    uses, as of the demo date. The document list folds up below it.
  - **Documents in force only:** `/documents` lists the versions in force
    on the demo's today (`list_documents(as_of=...)`, the same rule as
    search), so the 2025 fee schedule and overdraft policy no longer show.
  - **Readable sources:** every evidence item carries a `label` built in
    code from the step's arguments and rows ("Your fees on Apr 6, 2026",
    "Checking balance as of Jun 30, 2026", "Fee Schedule 2026 › 1. Everyday
    Checking"); the chat shows it instead of the tool name.
  - **"Why" explains the rule:** the summarizer is told to give the
    document's rule and its condition, and a code check sends back a "why"
    answer that cites no passage when one was found (regenerated once with
    the reason).
  - **Advice:** the planner gathers holdings, price and expense ratio; the
    answer opens with a plain "can't recommend trades" sentence, with no
    figure or citation in it, then gives the facts.
  - **Moving money** is a code route (`graph/routing.py is_money_movement`,
    no LLM call): "FinPilot is read-only and can't move money…".
  - **The 10-Ks in force on the demo date:** Apple's and Microsoft's FY2026
    10-Ks were filed after 2026-06-30 (Nov 3 and Jul 29), so the version rule
    hid them and no Apple or Microsoft filing could be found. Their FY2025
    10-Ks (filed 2025-10-31 and 2025-07-30) are now in `sources.yaml`, and a
    filing is superseded by the same company's next filing of its kind
    (`bank.public.superseded_dates`), so each day sees the filing that
    existed then. A customer now sees 19 documents in force (24 loaded:
    minus the two 2025 bank versions, the two FY2026 10-Ks not yet filed,
    and the clearance-2 memo, which staff do see).
- **Staff walkthrough fixes** (trace of "What does the fraud rules memo
  say?"):
  - **Retrieval:** clearance was applied correctly (staff: memo chunks at
    full-text ranks 1-6; a customer: none), and the memo's chunks carry its
    title. The planner had passed `doc_type: "policy"` (its type list had no
    `internal_memo`), which left only policies. Now the list has it, the
    planner is told to leave the type out unless the question names one,
    and a weak typed search is searched again across every type, in code.
  - **Query rewriter:** returns the `target` the question names and must
    keep it (code puts it back if the rewrite drops it). A retry that's
    still weak, or whose passages don't name the target, fails the step as
    not found (no re-plan): "I couldn't find…" instead of an answer from
    another document.
  - **Gatekeeper:** a code check fails an answer whose cited evidence
    doesn't name the document or 10-K company the question names; the
    model's gatekeeper is told the same.
  - **Staff asking for account data:** "Staff accounts don't have customer
    data…" (outcome `no_customer_data`) instead of "couldn't find".
  - **Market data after DEMO_TODAY:** prices ended on it and FRED holds no
    rows, but 41 SEC figures were from filings made after it (all of Apple's
    and Microsoft's FY2024-2026, from the FY2026 10-Ks). The loader now keeps
    the latest filing on or before DEMO_TODAY (refetched on Neon: 0 rows
    after it), and the executor caps every market tool's date (as_of, or
    end for price history) at today.
- **Who is asking, and document text as data:** with the memo found, the
  summarizer refused staff its content because the memo says "do not
  share with customers". The summarizer and validators now read an "Asked
  by" line (customer or staff, role and clearance, no ids) and are told
  that access is enforced in the database, so everything retrieved may be
  shared with that person; instructions inside documents ("don't share",
  "ignore…") are reported as data, never obeyed. Tests: a planted
  instruction reaches the models only inside the evidence; staff asking
  about the memo get it, a customer gets "couldn't find" and no prompt
  ever holds the memo's text. Phase 11's red-team set includes
  planted-instruction attacks (FINPILOT_PLAN.md, section 12).
- **Not run in a browser here:** this machine runs no Postgres or Docker,
  so the UI was checked with the type checker, lint and build, and the
  stream with the integration tests (`test_ask_flow.py`: event order, the
  answer after the check, the friendly timeout, admin diagnostics).

## LangChain and Langfuse (2026-11-21)

Where they fit, and nothing else moved:

- **Model calls:** `GeminiLLM` sends each request through LangChain's
  `ChatGoogleGenerativeAI`, on our one genai client (one validated chat
  model per model name, a copy per token limit and thinking level). The
  gateway around it is unchanged: LangChain's own retries are off; our
  timeouts, backoff, daily-quota handling and token and cost accounting
  run as before, and the Gemini error itself is still what a caller sees.
  Every JSON call, the validators' verdict included, uses
  `with_structured_output` (Gemini's native structured output).
- **Tools:** the executor runs each accounts and market tool as a
  LangChain `StructuredTool` (`tools.common.langchain_tool`): the tool's
  strict Pydantic model is the input schema, and the caller is bound when
  the tool is made, never an argument. The four layers stand: no
  customer-id argument, validation (LangChain's, then the tool's own),
  parameterized SQL, a read-only transaction under RLS.
- **Retriever:** `ingestion.retriever.StoreRetriever`, the hybrid store as
  a LangChain retriever; the executor searches through it.
- **Langfuse:** `observability.py` gives each LangGraph run Langfuse's
  LangChain callback handler, so nodes, LLM calls, tools and searches
  show up as one trace (its session id is our trace id). Before anything
  is sent, Langfuse's mask hook replaces amounts, merchants, account,
  card, transaction and customer ids, customer names, emails and card
  last-4s (`tests/unit/test_observability.py`). The Postgres traces stay
  the full, access-controlled record. Off unless all three settings are
  set; tests blank them.
- **Five standard questions on Neon (cust-0036), before and after:** the
  same answers (the $15 one now also states the fee rule), 3 LLM calls
  each, 82.1 s and 81.8 s in all (Gemini 63.0 s and 63.5 s; 2 and 4
  retries, one a 429 whose 26 s retry delay was honoured through
  LangChain). No latency added: the first request's setup was 6.1 s
  before and 6.0 s after.
- **Langfuse rejected the export (403):** the project's API key in .env
  authenticates but lacks the trace permissions (auth check and ingestion
  both 403). A failed export never touches the answer. The masked trace
  in Langfuse can be checked once the key has trace write and read access.

## UI polish (2026-11-21)

- **Customers see answers, not engineering:** latency, model calls,
  outcome and trace id moved into "How I got this" (its first line:
  "Answered in 9.5 s with 3 model calls"); the progress chips show only
  while an answer is being worked on.
- **Header:** the logo mark (a heading needle in a ring), one demo-date
  badge, a theme toggle and "Hi, Amara" with an avatar menu (who you
  are, request traces for admins, sign out). The first name comes from
  `/accounts/summary` (the caller's own customers row, under RLS).
- **Accounts:** checking, savings, then the card (amount owed); checking
  set largest. A card payment shows once, as money leaving checking
  ("paid from checking"): the card's mirror row ("Payment, thank you")
  is left out of recent activity. On a phone the accounts fold to one
  line with the checking balance, so the chat comes first.
- **Chat:** a plain greeting, suggested questions as cards, wider answer
  bubbles, a placeholder that names real questions.
- **Theme:** a light theme (cool paper, slate ink, harbor-teal accent,
  Public Sans) with a dark toggle, remembered on the device and set
  before the first paint. Readable at 375 px with no horizontal scroll.
- **Advice rule:** "I can't recommend trades" opens an answer only when
  the question asks for a recommendation (`graph.routing.asks_for_advice`,
  code); a fact question about VTI gets the facts.

## Langfuse follow-ups (2026-11-21)

- **Who asked, hashed:** the access object (user, Firebase, org, team and
  customer ids) reached Langfuse in the clear. Those fields, under any
  name in any observation, are now salted one-way hashes
  ("user:3f9a...", OBSERVABILITY_SALT in .env; unset, "[id]"), and the
  trace's user is that hash, so a user's traces group without the id.
- **A trace reads as the pipeline:** about 36 observations per question,
  mostly LangChain plumbing. A Langfuse handler subclass
  (`observability.py`) leaves framework runs out (sequences, lambdas,
  parallel maps, output parsers, LangGraph's hidden channel writes) and
  hangs their children on the nearest named step: the nodes (now
  intake, route, plan, execute, replan, summarize, validate: "validate"
  checks the answer, "intake" reads the question), llm:<role>,
  tool:<name> and search.
- **Fee "why" questions search the fee schedule first:** code sets
  doc_type fee_schedule on the plan's first search (or adds one); a weak
  result still widens to every type. The $15 question now cites the
  schedule's "more than $50.00" rule (trace 75b2de2f, 11.3 s, 3 calls).
- **Screenshots** in `docs/screenshots/`, shown in the README.

## Phase 10 notes (from 2026-11-14)

- **Reranker and tables.** The cross-encoder demoted table-row chunks
  (the APY table and the "Overdraft cushion" row fell to rank 4 in the
  branch experiment): test skipping the rerank for table chunks.
- **Ambiguous receipt dates.** "01/02/2026" is 2 January in the US and was
  read as 1 February (2 of 10 dates after the card-data change). Fixed in
  Phase 7 by telling the reader the receipts are US-format (10/10); keep
  dates whose day is 12 or less among the harder receipts.
- **Decide the retrieval defaults on the full corpus:** parent expansion
  (best MRR in the Phase 4 experiment, off for now) and rerank (on, but it
  demotes table rows and costs 0.9 to 1.5 s a search on CPU).
- **Tune the weak-search cutoff** (`RERANK_FLOOR`, 0 now). The planner's
  own queries scored 2.4 to 6.0 at the top and never tripped it; measure
  the customer's wording searched as is, and questions the corpus can't
  answer, then set the cutoff between the two.
- **Self-judging.** The same model (gemini-3.5-flash-lite) writes and
  validates the answer, and a model tends to approve its own output. The
  code checks (figures, citations, advice phrasing) don't depend on it;
  for Phase 10, try a different model as the validator and measure what
  it catches.
- **Reranker cost.** The cross-encoder is 0.9 to 1.5 s a search on this
  CPU (Phase 8 traces), more than the database legs.
- **Harder receipts.** Add 5 receipt images that are blurred, rotated and
  low-contrast, so the vision accuracy claim (10/10 per field) isn't
  measured on clean renders only.
