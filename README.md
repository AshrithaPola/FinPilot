# FinPilot

> **Draft README (Phase 12).** Results wait for the ablation runs.

FinPilot is a banking assistant for the customers of a fictional bank. A signed-in customer asks in plain English ("Why was I charged $15 on April 6?", "Can I afford $1,200 this month and stay above $500?"), and FinPilot answers from three kinds of source:

- **their own data:** accounts, balances, cards, transactions, fees and holdings;
- **the bank's documents:** fee schedules, policies, agreements, rate tables and its API spec;
- **public data:** prices, SEC filings, FRED rates and CFPB guides.

Every answer is planned, run through tools as the signed-in customer, checked before it's shown, and traced. Every figure is cited, every calculation is done in code, and FinPilot is read-only: it never moves money or gives investment advice. Bank staff get the same assistant over the bank's internal documents, with no access to customer accounts.

**Status:** Phases 0 to 10 of 12 are built: data, ingestion, Neon, tools, the reasoning engine, validation, tracing, the chat UI and the evaluation harness.

- **Red-teaming (11):** the set's format, its validator and the planted-instruction documents are in place.
- **Deployment (12):** the scripts and cost estimate are ready; nothing is deployed yet.
- **More reading:** the plan and its architecture checklist are in [docs/FINPILOT_PLAN.md](docs/FINPILOT_PLAN.md). What each commit did, with measurements, is in [docs/PROGRESS.md](docs/PROGRESS.md).

FinPilot grew out of an earlier document-QA project (DocuMind), whose retrieval core and access model carried over.

## Screens

A signed-in demo customer (synthetic data) asking why they were charged $15: the answer cites their fee, the transaction that caused it and the fee schedule's rule.

![FinPilot answering a fee question, light theme](docs/screenshots/desktop-light.png)

| Dark theme | "How I got this" (the plan, the checks, where the time went) | Phone, 375 px |
|---|---|---|
| ![Dark theme](docs/screenshots/desktop-dark.png) | ![How I got this](docs/screenshots/desktop-how-i-got-this.png) | ![Phone layout](docs/screenshots/mobile.png) |

## Architecture

```mermaid
flowchart TD
    UI["Chat UI (React)<br/>signs in with Firebase"] -->|"ID token on every call"| WEB["nginx: the app + /api proxy"]
    WEB --> API["FastAPI: /ask/stream"]
    API --> IN["validate input<br/>(PII masked)"]
    IN --> ROUTE{"route (code)"}
    ROUTE -->|"small talk, money movement"| FIXED["fixed reply, no model"]
    ROUTE -->|"a question"| PLAN["plan: LLM call 1<br/>a typed plan, up to 6 steps"]
    PLAN --> EXEC["execute (code)<br/>each step as the caller"]
    EXEC --> FIN["financial-data tools<br/>accounts, market data"]
    EXEC --> DOCS["documents: hybrid search<br/>vectors + full text + question index,<br/>RRF, local cross-encoder rerank"]
    EXEC --> CALC["calculator<br/>Decimal arithmetic"]
    FIN --> DB[("Neon: Postgres + pgvector<br/>row-level security as finpilot_app")]
    DOCS --> DB
    DOCS -.->|"weak search"| RW["rewrite the query once (LLM)"]
    EXEC -.->|"a step failed"| REPLAN["re-plan once (LLM)"]
    REPLAN -.-> EXEC
    EXEC --> SUM["summarize: LLM call 2<br/>one answer citing numbered evidence"]
    SUM --> CHECK["checks: code first (figures, citations, advice),<br/>then LLM call 3: gatekeeper, auditor, strategist"]
    CHECK -.->|"failed: regenerate once with the reason"| SUM
    CHECK -->|"failed twice"| REFUSE["refuse, saying why"]
    CHECK -->|"passed"| ANSWER["answer, citations, plan,<br/>verdicts, trace id"]
    API -.->|"every stage, step, call, cost"| TRACE[("traces, under RLS<br/>+ Langfuse, ids hashed")]
```

The chat streams these stages as they happen. Under each answer, a "How I got this" panel shows:

- the plan and its steps;
- the checks it passed, and whether code or the model decided each one;
- a timeline of where the time went;
- the model calls.

A typical question takes three LLM calls; the rewrite, the re-plan and the regeneration fire only when needed. Every request is stored as a trace. A trace records each stage, step, tool call, search, LLM and embedding call, with tokens and estimated cost, plus retries and verdicts, all under row-level security.

## What's built

- **Data, four kinds.**
  - Synthetic customers from a seeded generator: 50 customers, with a year of transactions, card autopay, late payers, overdrafts and fee schedules that change mid-year.
  - The bank's documents: Markdown, a real PDF, CSV and XLSX rate sheets, and an OpenAPI spec.
  - Receipt images rendered from the generator's own transactions.
  - Public sources: 10-K Items 1A and 7 of AAPL, MSFT and KO; the VTI, BND and SCHD prospectuses; CFPB guides. Only the list of public sources is committed, never the fetched text. Licences are in `data/SOURCES.md`.
- **Ingestion.**
  - Parsing follows each format's structure; PDF headings and ruled tables are recovered from the layout.
  - Noise removal strips page headers and footers and navigation lines.
  - Chunks are at most 512 tokens and never cross a section or hold two tables. Tables also become relational rows linked to their chunk.
  - Receipts become fields through one Gemini vision call each, checked against the ground truth (10/10 per field).
  - One LLM call per section adds each chunk's summary, keywords and likely questions, cached by content. Version, effective date, department and type come from the manifest.
- **Storage and retrieval on Neon** (Postgres + pgvector).
  - Hybrid search fuses chunk vectors, full text and a hypothetical-question index with reciprocal rank fusion, in one round trip. A local cross-encoder then reranks the results.
  - Metadata filters run in SQL, and a search sees only the version of each document in force on its as-of date.
- **Tools.**
  - Read-only accounts tools for the signed-in customer. They take no customer id argument, have strict arguments, use parameterized SQL, and run in a read-only transaction under row-level security.
  - Market tools: prices with their date and source, annual figures from SEC filings, FRED rates.
  - A calculator.
- **Evaluation.**
  - 33 questions across 11 types, each with gold sources that a validator runs against the database as the asker.
  - 12 questions have figures taken independently from plain SQL.
  - A judge on a different model from the generator scores facts and faithfulness. Human reviews of its verdicts are kept with each run.
- **Red-team set.** A format and validator for attacks: prompt injection, instructions planted in documents, cross-customer access, advice and money-movement evasion, data exfiltration and biased phrasing. Canaries must be real data. The planted-instruction documents load only into a separate Neon branch.

## Results

Results are pending the ablation runs; the table goes here once there are real numbers. A question passes only if it has the right outcome, every expected number, its expected facts fully covered, a faithful answer and no canary leaks.

## Design decisions

| Decision | Why |
|---|---|
| **Row-level security in Postgres decides what anyone sees**, not Python filters. The app connects as `finpilot_app`, which can't bypass it. | A bug in the app's code can't leak another customer's data, because the database won't return the rows. `scripts/leakage_check.py` proves it on the live database. |
| **Plan first, then execute in code**: one LLM call writes a typed plan; plain code runs its steps. | The model makes about three calls per question (3.0 on average, measured on 5 demo questions) instead of looping, which keeps cost and latency predictable on the free tier. Tool arguments are validated before anything runs. |
| **Arithmetic in code, never in the model** (a Decimal calculator; amounts kept in cents). | Models get sums wrong in plausible ways. The calculator's result is evidence the answer must state and cite. |
| **Code checks before the model's validator**: every figure must be in the evidence, every answer must cite, and advice phrasing is flagged. | They catch the most common errors without a model call, and they don't depend on a model judging its own output. |
| **The three validators in one structured call** (gatekeeper, auditor, strategist). | One call instead of three, within a budget of 3–4 calls per question (a target set in the plan, not a measurement). A failure regenerates the answer once with the reason, then refuses. |
| **Hybrid retrieval with a question index and a reranker** (defaults provisional until the ablations). | Exact terms need full text, and paraphrases need vectors. In the Neon branch experiment (19 questions), hybrid matched dense on MRR (0.93) and won the exact-term question ("Zero Liability", rank 3 to 1). The reranker dropped two table rows to rank 4, which is why the defaults wait for the ablations. |
| **A local ONNX cross-encoder for reranking**, not torch or a hosted reranker. | About 80 MB, runs on CPU, and needs no extra API or key. |
| **A chunk never crosses a section or holds two tables.** | Merging a small section into the next one mixed topics, so retrieval returned the wrong rule. |
| **Version-aware search**: a document version applies from its effective date until it's superseded. | Fee schedules change mid-year: a question about March must see the schedule in force in March. |
| **A fixed demo date** (2026-06-30, `DEMO_TODAY`). | The synthetic data ends there. A moving "today" would make every "this month" question return nothing. |
| **Synthetic customers only.** | The free tier's inputs may be used to improve Google's products, so no real customer data ever goes in. |
| **Read-only by design**: money movement gets a fixed reply. | An assistant that can move money needs confirmation flows and auth beyond a portfolio project's scope. |
| **Neon (serverless Postgres + pgvector)** with branches for experiments. | One database for vectors, full text, relational tables and RLS. A branch gives an experiment, or the red-team corpus, its own copy without touching production. |
| **An eval judge on a different model from the generator**, with human review of its flags. | A model tends to approve its own output. The reviews showed the judge is wrong sometimes too, so its verdicts are inputs, not results. |
| **Cloud Run with min instances 0, max 2.** | An idle demo runs no instances, and the cap bounds the bill. Costs: [docs/DEPLOY.md](docs/DEPLOY.md). |

## Setup

Every setting, with its default and a one-line comment, is in `.env.example`.


You need Docker and a Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey); the free tier is enough, no billing required. The key powers all LLM reasoning (the planner, summarizer, validators and query rewriter, all on `gemini-3.5-flash-lite` by default) and the default embeddings (`gemini-embedding-2`). With `EMBEDDING_PROVIDER=local`, embeddings need no key: the sentence-transformers model (~90 MB) is downloaded from HuggingFace on first startup and cached in a Docker volume after that. Changing the embedding provider, model or dimension needs a fresh database and a reload of the documents.

```bash
cp .env.example .env
# edit .env and set GEMINI_API_KEY - the LLM calls need it
docker compose up --build
```

LLM settings in `.env`:

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | `gemini` or `anthropic` |
| `GEMINI_API_KEY` | — | Required when the provider is `gemini` |
| `ANTHROPIC_API_KEY` | — | Required when the provider is `anthropic` |
| `GENERATION_MODEL` | provider default | `gemini-3.5-flash-lite` / `claude-opus-4-8`; `gemini-3.6-flash` for demos (free tier: 20 requests/day) |
| `GRADING_MODEL` | provider default | `gemini-3.5-flash-lite` / `claude-haiku-4-5` |
| `GRADING_THINKING_LEVEL` | `minimal` | Gemini thinking level for the checker calls; empty = model default |
| `LLM_MAX_ATTEMPTS` | `5` | Attempts per call on 429/overloaded before the error surfaces |
| `LLM_REQUEST_TIMEOUT_S` | `60` | Seconds before a single Gemini request is abandoned; counts as one retryable attempt |
| `LLM_CALL_TIMEOUT_S` | `45` | Seconds for a whole call, retries included, so one slow model can't hang a request |
| `EMBEDDING_PROVIDER` | `gemini` | `gemini` (`gemini-embedding-2`) or `local` (`all-MiniLM-L6-v2`) |
| `EMBEDDING_MODEL` | provider default | Override the embedding model |
| `EMBEDDING_DIMENSIONS` | model default | 1536 for `gemini-embedding-2`, 384 for MiniLM; must match the database's vector column |
| `EMBEDDING_USE_VERTEXAI` | `false` | Embed through Vertex AI instead of the API key (needs billing; untested here) |

- Web UI: http://localhost:3000 (set `FRONTEND_PORT` in `.env` if 3000 is taken)
- API docs: http://localhost:8000/docs (set `BACKEND_PORT` in `.env` if 8000 is taken)
- Health check: `GET http://localhost:8000/health`

Every endpoint except `/me` and `/health` requires a Firebase ID token (`Authorization: Bearer ...`) for a user set up in Postgres, in the project named by `FIREBASE_PROJECT_ID`. The web UI signs in with Firebase email/password (the `VITE_FIREBASE_*` web config in `.env`), sends the ID token on every call, and shows a "not set up yet" screen, with the reason from `/me`, to users who aren't registered, have no org, or were deactivated.

### Running without Docker

```bash
# backend - put GEMINI_API_KEY in the repo-root .env or the environment
cd backend
python -m venv .venv
.venv/Scripts/activate          # source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
uvicorn main:app --reload
```

```bash
# frontend
cd frontend
npm install
npm run dev                     # Vite proxies /api to localhost:8000
```

Outside Docker the backend still needs Postgres with pgvector, because the chunks live there: set `DATABASE_URL` to the `finpilot_app` role (e.g. `postgresql+psycopg2://finpilot_app:change-me-too@localhost:5432/finpilot`) run `docker compose up postgres`, then run the migrate step once with the owner's URL given only to it: `MIGRATION_DATABASE_URL=postgresql+psycopg2://finpilot:change-me@localhost:5432/finpilot python migrate.py` (from `backend/`). The backend connects only as `finpilot_app`, which row-level security applies to; it refuses to start on a superuser or table-owner connection, or with `MIGRATION_DATABASE_URL` set. In Docker, the one-shot `migrate` service does this before the backend starts.

### Running on Neon

FinPilot's hosted database is Neon (serverless Postgres with pgvector). Put the two URLs from the Neon console in the repo-root `.env` (gitignored; the one env file, which the backend's config and docker-compose both read), never in the repo: `NEON_MIGRATION_DATABASE_URL` (the direct URL of the owner role) and `NEON_POOLED_DATABASE_URL` (the pooled host). Neon's `postgresql://` and `postgresql+psycopg://` schemes are accepted; the backend rewrites them for its psycopg2 driver (`config.sqlalchemy_url`). Then, from the repo root:

```bash
P=backend/.venv/Scripts/python                              # backend/.venv/bin/python on macOS/Linux
$P scripts/neon.py setup                                    # app role URL, migrations, finpilot_app's password
$P scripts/neon.py run -- python -m bank.synthetic          # synthetic customers (~30 s)
$P scripts/neon.py run -- python -m bank.corpus load        # the bank's documents: metadata + embeddings (Gemini)
$P scripts/neon.py run -- python -m bank.public load        # public sources (fetch first: python -m bank.public fetch)
$P scripts/neon.py run -- python -m bank.market fetch sec   # SEC company facts (also: prices, rates; need free keys)
$P scripts/neon.py run -- python -m bank.receipts extract   # receipt fields: 10 Gemini vision calls
$P scripts/neon.py run -- python ../scripts/leakage_check.py
```

`setup` writes `NEON_APP_DATABASE_URL` to `.env` on its first run: `finpilot_app` on the pooled host, with a random password. Then it runs the migrate step as the owner, which creates `finpilot_app` (not a superuser, no `BYPASSRLS`, owns no table) and sets that password. `run` gives a command the owner URL as `MIGRATION_DATABASE_URL` and the app URL as `DATABASE_URL`. No script prints a URL. `.env` never holds `DATABASE_URL` or `MIGRATION_DATABASE_URL` themselves, so nothing that reads it, a local test run included, can reach Neon by accident; and the backend refuses to start on an owner connection (superuser, `BYPASSRLS` or table owner).

Reloading is incremental. Chunk metadata is cached by chunk text, and a replaced document keeps the stored vectors of chunks whose text didn't change, so only changed chunks cost Gemini calls.

`scripts/leakage_check.py` is read-only, so it is safe on a live database. It connects as `finpilot_app` and checks four things: every table with a `customer_id` or `clearance` column is under forced row-level security; with no access context, every one of those tables shows zero rows; each synthetic customer sees only their own rows; and a customer sees no staff-only (clearance above 0) chunks, documents or table rows. It exits 1 on any leak. Never point the pytest fixtures at Neon: they create and drop databases and reset `finpilot_app`'s password.

## Authentication and access control

**Firebase identifies the user; Postgres decides what they may see.** The web UI signs in with Firebase email/password and send the ID token as `Authorization: Bearer ...` on every call. The backend checks the token with the Firebase Admin SDK (allowing 5 seconds of clock skew, `FIREBASE_CLOCK_SKEW_SECONDS`), then loads the user's row from Postgres on every request: org, teams, role (`viewer`, `editor`, `admin`), integer clearance (higher sees more) and an `active` flag. Nothing about access lives in Firebase custom claims, so a change applies on the user's next request, not at their next token refresh.

- **Deny by default.** No valid token: 401. A valid token whose user has no row, no org, or `active=false`: 403 on everything except `GET /me`, which always answers and says why (`unknown_user`, `no_org`, `inactive`). `/health` is open and reports nothing about the data.
- **Roles.** Viewers ask questions and give feedback. There is no upload or access-change API: documents come only from the loader, which writes as an editor or admin of the bank's org. `/admin/*` is admin-only, also checks with Firebase that the token wasn't revoked, and shows only the admin's own org. LLM diagnostics (per-model request counts, token usage, raw provider errors) are returned only to admins.
- **Row-level security does the filtering, not Python.** The app connects as `finpilot_app`, a role that isn't the table owner, isn't a superuser and can't bypass RLS; the backend refuses to start otherwise. Every transaction first sets `app.org_id`, `app.clearance` and `app.team_ids` for that transaction only (`set_config(..., true)`), from the user just loaded. `chunks` and `documents` (and the query and feedback logs, by org) are under `FORCE ROW LEVEL SECURITY` with one policy: same org, clearance at or below the user's, and the team empty (org-wide) or one of the user's. Both retrieval legs (the HNSW vector search with iterative scans, and the full-text search) run under it. A transaction with no context sees no rows.
- **The `documents` table is the source of truth for access.** Loading a document writes its org, team and clearance (required, no default) and its chunks in one transaction, the chunks copying those values from the document row, so they can't drift apart.
- **Owner credentials never reach the app.** Migrations, setting `finpilot_app`'s password and the embedding-model check run as a separate one-shot step (`backend/migrate.py`; the `migrate` service in docker-compose, which the backend waits for). The backend gets only `DATABASE_URL` and refuses to start if `MIGRATION_DATABASE_URL` is in its environment.

### Setting up the first admin

1. In the Firebase console, create the project with email/password sign-in enabled, and create the user.
2. Put the web config (`VITE_FIREBASE_*`), `FIREBASE_PROJECT_ID` and `GOOGLE_APPLICATION_CREDENTIALS` (the path to a service account key file kept outside the repo) in `.env`. docker-compose mounts that file read-only into the backend; it never goes into an image. Without these the backend refuses to start and says what is missing. CI fails if a key file is ever committed.
3. Run the migrate step, then map the user to an admin with the owner's database URL:
   `python scripts/setup_eval_admin.py --database-url OWNER_URL` (it looks the user's uid up by `EVAL_ADMIN_EMAIL`, creates org `eval`, and is safe to re-run; it accepts only `@documind.test` accounts, the test-account domain from the project's earlier name, which the eval account still uses). Further users and teams are rows in `users` and `team_memberships`, written as the owner; there's no admin page yet.

## Testing

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt -c constraints.txt
python -m pytest tests
```

No test calls Gemini or Neon: the LLM gateway and the embedder are replaced by deterministic fakes, and the Postgres tests create a fresh local database each. `tests/unit/` covers parsing and chunking per format (the PDF agreement must parse back to its Markdown source word for word), the synthetic data, the tools' argument validation, the calculator, the engine with a fake LLM (plans, references between steps, the re-plan, the query rewrite, and each validator catching a planted bad answer: a wrong number, an unanswered question, investment advice), tracing and per-call timeouts, and the LLM gateway against stubbed SDK clients. `tests/integration/` runs everything that touches Postgres + pgvector: row-level security per customer and per org, the accounts tools under adversarial arguments, the leakage check (and that it catches a table with RLS switched off), `/ask` end to end as a customer and as staff, and the traces under RLS.

CI (`.github/workflows/tests.yml`) runs on every push against a `pgvector/pgvector:0.8.7-pg16` service container and fails if the Postgres tests would skip; it needs no secrets and caches the reranker model. Locally, set `TEST_POSTGRES_URL` to a pgvector database.

CI also builds both Docker images, without pushing them, and checks the frontend's nginx template with `nginx -t`. The red-team set is checked by `python -m eval.redteam`, and its tests run in the same suite.

## Deploying

`scripts/gcp_deploy.sh` deploys the backend and frontend to Cloud Run (min instances 0, max 2); Neon and Firebase stay as they are. It prints its commands unless given `--apply`, never turns billing on, keeps secrets in Secret Manager, keeps the latest 3 images and sets a budget alert. `scripts/gcp_teardown.sh` removes everything it created. The monthly cost estimate and what it covers are in [docs/DEPLOY.md](docs/DEPLOY.md).

## API

| Endpoint | Purpose |
|---|---|
| `POST /ask` | The reasoning engine: `{question}` → the answer, its plan and steps, citations, the validators' verdicts, `demo_today`, the stage `timeline`, LLM calls and `trace_id` |
| `GET /documents` | Documents the caller can see, in the version in force on the demo date, newest first |
| `GET /accounts/summary` | A customer's balances and five latest transactions, as of the demo date (404 for staff) |
| `POST /ask/stream` | `/ask` over server-sent events, for the chat: the plan, each stage and step as it starts and ends, then the answer once the validators passed it (`done`), or a friendly `error` with whether retrying can help |
| `POST /feedback` | `{query_id, thumbs_up, comment?}`: upserts the verdict on an answer (`/ask` returns its `query_id`) |
| `GET /admin/traces`, `GET /admin/traces/{id}` | Recent `/ask` traces of the admin's org (latency, LLM calls, estimated cost, outcome), and one with every span |
| `GET /admin/logs`, `GET /admin/stats` | The query log and its aggregates |
| `GET /me` | Who the token belongs to and whether they're set up |
| `GET /health` | Liveness |

From the command line, as a synthetic customer: `python -m engine.cli --customer cust-0036 "Can I afford $1,200 this month and stay above $500?"` (through `scripts/neon.py run`) prints the plan, steps, answer, citations, verdicts and where the time went.

## Known limitations

- **A small eval set.** 33 questions, about 3 per type, so one question moves a type's score a lot. The results above are measurements on this set, not general accuracy claims.
- **The same model writes and validates the answer.** The code checks don't depend on it, but the LLM validator (gatekeeper, auditor, strategist) runs on the generator's model, which tends to approve its own output.
- **The eval judge makes mistakes.** Its flags are reviewed by hand, and each run's `NOTES.md` records which verdicts were overridden.
- **False refusals.** A strict figures check refuses an answer whose number isn't in the evidence. That's right when the number was garbled, but it means the customer gets no answer at all.
- **Latency and availability depend on the Gemini free tier.** All reasoning and embeddings run on the Gemini free tier. Limits are per project and per model and reset at midnight Pacific; failed attempts count against them. Measured so far: `gemini-3.6-flash` 20 requests/day (why it isn't the default), `gemini-3.5-flash-lite` 15 requests/minute, embeddings about 100 texts and 30,000 tokens a minute. Latency varies widely and 503 "high demand" responses are common; backoff makes this survivable, not fast. Free-tier usage may be used to improve Google's products, so no real customer data ever goes in: FinPilot's customers are synthetic.
- **The retrieval numbers are likely optimistic.** The MRR of 0.93 on 19 questions in the branch experiment was measured after a fix made while looking at those same questions.
- **The reranker is slow on CPU and weak on table rows.** It takes 0.9 to 1.5 s a search on a laptop CPU, and it ranked two table rows down in the branch experiment.
- **Receipts are clean renders.** The 10/10 field accuracy is on rendered images, not blurred, rotated or low-contrast ones. There are no statement images yet.
- **Partial data sources.** Investor.gov blocks automated clients, so its pages aren't loaded. Prices are real month-end closes (Alpha Vantage's free monthly series), so a price "as of" a mid-month date is the previous month-end's, and there are none until the Alpha Vantage key is set; FRED rates are empty until the FRED key is set.
- **Access model:**
  - Clearance is a single ordered level with no compartments: a user at level N sees everything at N or below in their org and teams.
  - Firebase's revoked-token check is a network call, so only `/admin/*` makes it. Elsewhere a revoked token keeps working until it expires (at most an hour); setting `users.active=false` is the immediate off switch everywhere.
  - The embedding-model check in the migrate step reads every org's chunks, so the migration role must be able to bypass RLS (true for the Docker Postgres owner, a superuser).
- **Not deployed yet.** The Docker images are built in CI, but the deploy scripts haven't been run against GCP, and the backend's memory on Cloud Run isn't measured.
