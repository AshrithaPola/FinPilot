## FinPilot Phases 3–11: ingestion to red-team set

Everything since PR #5: the corpus pipeline, Neon storage and retrieval, the tools, the reasoning
engine with validation, tracing, the chat UI, the evaluation and the red-team set. It also has the
Cloud Run deploy scripts, which have not been run.

### What's in it, by phase

- **3 Ingestion.**
  - Structure-aware parsing (Markdown, PDF, CSV, XLSX, OpenAPI) and noise removal.
  - A chunk never crosses a section or holds two tables, and tables also become relational rows.
  - Receipt fields come from one vision call each.
  - Chunk metadata (summary, keywords, likely questions) is cached by content.
- **4 Neon storage and retrieval.**
  - Postgres + pgvector on Neon, with a setup script that never prints a URL.
  - Hybrid search (vectors, full text, a question index, RRF) in one round trip, with
    version-aware filters and an optional local reranker.
  - A Neon branch experiment picked the retrieval defaults.
  - `scripts/leakage_check.py` proves row-level security on the live database.
- **5 Tools and public data.**
  - Read-only accounts tools: fixed parameterised SQL with no customer-id argument, run under the
    caller's RLS context in a read-only transaction.
  - Market tools: SEC company facts, real Alpha Vantage month-end closes.
  - A Decimal calculator.
  - Public sources: 10-K Items 1A and 7, ETF prospectuses, CFPB answers. The text is never committed.
- **6–7 Reasoning engine and validation.**
  - LangGraph: intake → route → plan → execute → summarize → validate, with one re-plan and one
    query rewrite.
  - Code checks (figures, citations, advice, other customers' ids) run before one structured
    model check.
  - A fixed demo date, 2026-06-30.
  - Document text is treated as data, never as instructions.
- **8 Observability.** A trace per question in Postgres under RLS, plus Langfuse with hashed user
  ids, per-call timeouts and admin trace views.
- **9 Chat UI.** Live stages, a "How I got this" panel (plan, checks, timeline), light and dark
  themes, demo sign-in accounts.
- **10 Evaluation.**
  - 33 questions; 12 have figures taken independently from plain SQL. Gold sources are validated
    against the database as the asker.
  - The judge runs on a different model, with every flag reviewed by hand.
  - Eval freeze (`eval-freeze` = `c3c5dfa`), a retrieval replay, validation ablations, and
    `docs/RESULTS.md` written from committed runs only.
- **11 Red-team set.**
  - 41 attacks: 38 drafted by Claude and reviewed by the author, 3 written by the author.
  - A validator: every canary must be real data, a planted document, staff-only text, prompt text
    or a secret prefix.
  - Two planted-instruction documents, loaded only into the Neon branch `redteam`.
  - A runner that refuses to start anywhere else.
- **12 prep (not deployed).** `scripts/gcp_deploy.sh` and `scripts/gcp_teardown.sh` (dry run unless
  `--apply`), the cost estimate in `docs/DEPLOY.md`, and Docker images built in CI.

### Key results ([docs/RESULTS.md](docs/RESULTS.md))

- **28 / 33 strict passes** at eval-freeze with the app's defaults.
- 0 canary leaks, 0 false refusals.
- About $0.003, 2.8 model calls and 11.5 s per question.
- **Reranker off:** in the replay, it added no recall@5 (0.929 either way) and lowered MRR (0.714 to
  0.651), on 14 document questions.
- **Validation:** code checks cut wrong-but-shown answers from 5 to 3, at the cost of more refusals.
  The model validator showed no measurable gain, possibly because it uses the generator's model.
- **Red team:** _results pending (the run is in progress on the `redteam` branch)._

### Decisions

- Row-level security decides what anyone sees, not Python filters. The app connects as
  `finpilot_app`, which can't bypass it, and owner credentials go only to the migrate step.
- Fixed parameterised tools instead of text-to-SQL.
- Arithmetic in code; every figure in an answer must appear in the evidence.
- Code checks before the model validator.
- The reranker turned off after the replay (provisional: 14 questions).
- A fixed demo date, so results repeat.
- An eval freeze before the final runs, and human review of every judge flag, recorded in each
  run's `NOTES.md`.
- Planted red-team documents only ever on a separate Neon branch. The leakage check fails any
  other database that holds one.

### Testing

- `pytest` runs the unit suite and the Postgres integration suite against pgvector in CI. CI also
  builds both Docker images.
- `python -m eval.redteam` validates the attack set.
- No test calls Gemini or Neon.

### Not in this PR

- The red-team results, and any fixes they lead to (next commits on this branch).
- The deployment itself (Phase 12).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
