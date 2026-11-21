# FinPilot

A banking assistant for the customers of a fictional bank. It answers from the customer's own data (accounts, balances, cards, transactions, fees, holdings), the bank's documents (fee schedules, policies, agreements, rate tables, its API spec) and public market and regulatory data. Every answer is planned, run through tools as the signed-in customer, checked, and traced.

**Status: Phases 0 to 9 of 12 are built** (data, ingestion, Neon, tools, the reasoning engine, validation, tracing, the chat UI). Next: evaluation (10), red-teaming (11), deployment (12). The plan and its architecture checklist are in [docs/FINPILOT_PLAN.md](docs/FINPILOT_PLAN.md); what each commit did, with measurements and design decisions, is in [docs/PROGRESS.md](docs/PROGRESS.md). FinPilot grew out of an earlier document-QA project; its retrieval core and access model carried over.

## Screens

A signed-in demo customer (synthetic data) asking why they were charged $15: the answer cites their fee, the transaction that caused it and the fee schedule's rule.

![FinPilot answering a fee question, light theme](docs/screenshots/desktop-light.png)

| Dark theme | "How I got this" (the plan, the checks, where the time went) | Phone, 375 px |
|---|---|---|
| ![Dark theme](docs/screenshots/desktop-dark.png) | ![How I got this](docs/screenshots/desktop-how-i-got-this.png) | ![Phone layout](docs/screenshots/mobile.png) |

Before the UI polish, for comparison: [desktop](docs/screenshots/before-desktop.png), [phone](docs/screenshots/before-mobile.png).

## How a question is answered

```
POST /ask ── validate (PII masked) ── route (small talk: no model)
                │
              plan          LLM 1: a typed plan of up to 6 steps
                │
             execute        code: each step through its agent, as the caller
                │             financial data: accounts tools (RLS) + market tools
                │             documents: hybrid search; a weak search is rewritten once (LLM)
                │             calculation: Decimal arithmetic, never the model
                ├─ a step failed → one re-plan (LLM)
                │
            summarize       LLM 2: one answer citing numbered evidence
                │
              check         code checks (every figure in the evidence, a citation,
                │           no advice), then LLM 3: gatekeeper, auditor, strategist
                │           failed → regenerate once with the reason → else refuse
               END          the answer, plan, steps, citations, verdicts, LLM calls, trace id
```

The chat (`frontend/`) streams these stages as they happen and folds a "How I got this" panel under each answer: the plan and its steps, the checks it passed and who decided (code or the model), a timeline of where the time went, and the model calls. A typical question takes three LLM calls. The rewrite, the re-plan and the regeneration fire only when needed. Every request is stored as a trace (each stage, step, tool call, search, LLM and embedding call with tokens and estimated cost, retries, verdicts) under row-level security.

## What's built

- **Data, four kinds.** Synthetic customers from a seeded generator (50 customers, a year of transactions with card autopay, late payers, overdrafts and fee schedules that change mid-year), the bank's documents (Markdown, a real PDF, CSV and XLSX rate sheets, an OpenAPI spec), receipt images rendered from the generator's own transactions, and public sources (10-K Items 1A and 7 of AAPL, MSFT and KO; VTI, BND and SCHD prospectuses; CFPB guides). Only the list of public sources is committed, never the fetched text. Licences are in `data/SOURCES.md`.
- **Ingestion.** Structure-aware parsing per format (PDF headings and ruled tables are recovered from the layout), noise removal (page headers and footers, navigation lines), and chunks of at most 512 tokens that never cross a section or hold two tables. Tables also become relational rows linked to their chunk. Receipts become fields through one Gemini vision call each, checked against the ground truth (10/10 per field). Per chunk, one LLM call per section adds a summary, keywords and likely questions, cached by content; version, effective date, department and type come from the manifest.
- **Storage and retrieval on Neon** (Postgres + pgvector). Hybrid search fuses chunk vectors, full text and a hypothetical-question index with reciprocal rank fusion in one round trip, then a local cross-encoder (ONNX, no torch) reranks. Metadata filters run in SQL, and a search sees only the version of each document in force on its as-of date. Parent-section expansion is available. A Neon branch held the retrieval experiment (`backend/eval/branch_experiment.py`). The defaults (hybrid + rerank, no expansion) are provisional until Phase 10.
- **Tools.** Read-only accounts tools for the signed-in customer (no customer id argument, strict arguments, parameterized SQL, a read-only transaction under row-level security), market tools (prices with their date and source, annual figures from SEC filings, FRED rates) and a calculator.
- **Access.** Firebase identifies the user; Postgres decides what they see. The app connects as `finpilot_app`, which can't bypass row-level security; each transaction sets the caller's org, clearance, teams and customer. `scripts/leakage_check.py` proves it on a live database.
- **Demo date.** The synthetic data ends on 2026-06-30, so that's the engine's "today" (`DEMO_TODAY`), returned by `/ask`. An answer about another date says which date its figures are for.

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

### Known limitations of the access model

- Clearance is a single ordered level with no compartments: a user at level N sees everything at N or below in their org and teams.
- Firebase's revoked-token check is a network call, so only `/admin/*` makes it. Elsewhere a revoked token keeps working until it expires (at most an hour); setting `users.active=false` is the immediate off switch everywhere.
- The embedding-model check in the migrate step reads every org's chunks, so the migration role must be able to bypass RLS (true for the Docker Postgres owner, a superuser).

## Testing

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt -c constraints.txt
python -m pytest tests
```

No test calls Gemini or Neon: the LLM gateway and the embedder are replaced by deterministic fakes, and the Postgres tests create a fresh local database each. `tests/unit/` covers parsing and chunking per format (the PDF agreement must parse back to its Markdown source word for word), the synthetic data, the tools' argument validation, the calculator, the engine with a fake LLM (plans, references between steps, the re-plan, the query rewrite, and each validator catching a planted bad answer: a wrong number, an unanswered question, investment advice), tracing and per-call timeouts, and the LLM gateway against stubbed SDK clients. `tests/integration/` runs everything that touches Postgres + pgvector: row-level security per customer and per org, the accounts tools under adversarial arguments, the leakage check (and that it catches a table with RLS switched off), `/ask` end to end as a customer and as staff, and the traces under RLS.

CI (`.github/workflows/tests.yml`) runs on every push against a `pgvector/pgvector:0.8.7-pg16` service container and fails if the Postgres tests would skip; it needs no secrets and caches the reranker model. Locally, set `TEST_POSTGRES_URL` to a pgvector database.

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

## Free-tier limits

All reasoning and embeddings run on the Gemini free tier. Limits are per project and per model and reset at midnight Pacific; failed attempts count against them. Measured so far: `gemini-3.6-flash` 20 requests/day (why it isn't the default), `gemini-3.5-flash-lite` 15 requests/minute, embeddings about 100 texts and 30,000 tokens a minute. Latency varies widely and 503 "high demand" responses are common; backoff makes this survivable, not fast. Free-tier usage may be used to improve Google's products, so no real customer data ever goes in: FinPilot's customers are synthetic.
