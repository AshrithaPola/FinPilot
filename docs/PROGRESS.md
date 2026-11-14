# FinPilot progress log

The plan, the architecture checklist and the commit budget are in
[FINPILOT_PLAN.md](FINPILOT_PLAN.md). DocuMind's own log (Phases 0–2 of the
enterprise build, its eval baselines and decisions) is in git history: see
`docs/PROGRESS.md` at commit 51ba57a.

**Commits: 17 of 25** (budget raised to 24 on 2026-11-14 for Phase 4's two extra commits, then to 25 the same day so the accounts tools and the market tools each get their own commit; commit 17 fixed a test of mine that CI caught, so the plan is now one commit over: 8 left for 9 planned) (budget decided 2026-11-13; the Compose-name chore is outside it).

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
| fix | this commit | A hostile-argument test meant to pass validation used `or_1_eq_1`, which the category rule (letters and underscores) rightly refuses; now `or_one_eq_one` (CI caught it) |

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

## Phase 10 notes (from 2026-11-14)

- **Reranker and tables.** The cross-encoder demoted table-row chunks
  (the APY table and the "Overdraft cushion" row fell to rank 4 in the
  branch experiment): test skipping the rerank for table chunks.
- **Harder receipts.** Add 5 receipt images that are blurred, rotated and
  low-contrast, so the vision accuracy claim (10/10 per field) isn't
  measured on clean renders only.
