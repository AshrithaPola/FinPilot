"""Phase 4 retrieval on real Postgres: metadata filters (current version by
default), the hypothetical-question leg, dense-only mode and parent-section
expansion. Skipped without TEST_POSTGRES_URL."""
from datetime import date

import sqlalchemy as sa

from ingestion.chunking import chunk_blocks
from ingestion.parse import parse_markdown
from db.access import Access
from ingestion.store import Filters
from tests.integration.test_corpus_load import ADMIN, CUSTOMER, _one, _store

FEES = "# Fees\n\n## 1. Overdraft\n\nThe overdraft fee is {fee} per item, at most three a day.\n"
CARD = """# Card Agreement

## 1. Rewards

Cash back is 1.5% on every purchase, paid monthly.

## 2. Fees

### 2.1 Late payment

A late payment costs $29 if the minimum payment is not received.

### 2.2 Foreign transactions

Purchases in another currency carry a 3% foreign transaction fee.
"""
ABROAD = "Do I pay extra when I shop abroad?"


async def _load(store):
    docs = [("f25", FEES.format(fee="$35"), {"doc_type": "fee_schedule", "department": "retail_banking",
                                             "effective_date": date(2025, 1, 1), "superseded_on": date(2026, 2, 1)}),
            ("f26", FEES.format(fee="$15"), {"doc_type": "fee_schedule", "department": "retail_banking",
                                             "effective_date": date(2026, 2, 1)}),
            ("card", CARD, {"doc_type": "agreement", "department": "card_services"})]
    for doc_id, text, meta in docs:
        chunks = chunk_blocks(parse_markdown(text), f"{doc_id}.md", doc_id)
        for c in chunks:
            c.questions = [ABROAD] if "Foreign" in c.section else [f"What does {c.section} say?"]
        await store.scoped(ADMIN).add_document(chunks, team_id=None, clearance=0, uploaded_by=None, meta=meta)


async def _docs(store, query, **filters):
    hits = await store.scoped(ADMIN).search(query, filters=Filters(**filters))
    return {h.doc_id for h in hits}


async def test_filters_default_to_the_version_in_force_today(pg_url, app_url):
    store = _store(pg_url, app_url)
    await _load(store)
    assert {"f26"} <= await _docs(store, "overdraft fee") and "f25" not in await _docs(store, "overdraft fee")
    assert "f26" not in await _docs(store, "overdraft fee", as_of=date(2025, 6, 1))
    assert {"f25", "f26"} <= await _docs(store, "overdraft fee", all_versions=True)
    assert await _docs(store, "fee", department="card_services") == {"card"}
    assert await _docs(store, "fee", doc_type="fee_schedule", all_versions=True) == {"f25", "f26"}
    # The documents list (the UI's sidebar) uses the same rule.
    listed = lambda day: {d["doc_id"] for d in store.scoped(ADMIN).list_documents(as_of=day)}  # noqa: E731
    assert listed(date(2026, 6, 30)) == {"f26", "card"} and listed(date(2025, 6, 1)) == {"f25", "card"}


async def test_the_question_leg_finds_a_chunk_by_its_hypothetical_question(pg_url, app_url):
    store = _store(pg_url, app_url)
    await _load(store)
    assert _one(store, ADMIN, "SELECT count(*) FROM chunks WHERE question_embedding IS NULL") == [(0,)]
    hits = await store.scoped(ADMIN).search(ABROAD)
    by_question = min((h for h in hits if h.question_rank), key=lambda h: h.question_rank)
    assert by_question.question_rank == 1 and "Foreign" in by_question.section

    dense = await store.configured(retrieval_mode="dense").scoped(ADMIN).search(ABROAD)
    assert dense and all(h.sparse_rank is None and h.question_rank is None for h in dense)


async def test_parent_expansion_gives_the_whole_top_level_section(pg_url, app_url):
    store = _store(pg_url, app_url)
    await _load(store)
    expanded = store.configured(parent_expansion=True, top_k=10).scoped(ADMIN)
    hits = await expanded.search("foreign transaction fee currency", filters=Filters(department="card_services"))
    [fees] = [h for h in hits if "2. Fees" in h.section]  # its sibling's hit isn't repeated
    with store.scoped(ADMIN)._begin() as conn:
        sections = dict(conn.execute(sa.text(
            "SELECT chunk_id, section FROM chunks WHERE chunk_id = ANY(:ids)"),
            {"ids": fees.context_chunk_ids}).all())
    assert set(sections.values()) == {"Card Agreement > 2. Fees > 2.1 Late payment",
                                      "Card Agreement > 2. Fees > 2.2 Foreign transactions"}
    assert "late payment costs $29" in fees.text and "3% foreign" in fees.text
    assert "Cash back" not in fees.text


async def test_full_text_ranks_the_overdraft_cushion_row_first(pg_url, app_url):
    """Distinct query terms, then adjacent pairs, then ts_rank_cd: the row named
    "Overdraft cushion" beats sections that only repeat "overdraft"."""
    from bank.corpus import load, load_corpus
    from config import Settings
    from tests.integration.test_corpus_load import CUSTOMER

    store = _store(pg_url, app_url)
    await load(store.scoped(ADMIN), load_corpus(Settings(_env_file=None, anthropic_api_key="x")))
    query = "What is the overdraft cushion?"
    ranking = await store.scoped(CUSTOMER).fused_ranking(query, await store.embed_query(query))
    first = min((c for c in ranking if c.sparse_rank), key=lambda c: c.sparse_rank)
    assert first.filename == "fee-schedule-2026.md" and "overdraft cushion" in first.text.lower()


async def test_a_hybrid_search_is_one_round_trip_under_rls(pg_url, app_url):
    """The access context (with the scan setting) and all three legs go as one
    query string: from far away every round trip costs about 100 ms. RLS
    still applies: another org's caller gets nothing."""
    store = _store(pg_url, app_url)
    await _load(store)
    sent = []
    sa.event.listen(store._engine, "before_cursor_execute",
                    lambda conn, cursor, statement, *args: sent.append(statement))
    hits = await store.scoped(ADMIN).search(ABROAD)
    assert hits and len(sent) == 1
    other_org = Access(org_id="another-org", clearance=2)  # sees none of this org's documents
    assert await store.scoped(other_org).search(ABROAD, filters=Filters(all_versions=True)) == []


MEMO = """# INTERNAL: Card Fraud Rules, Q2 2026

**Memo reference:** FO-2026-03-RULES

## 1. Purpose

This memo sets the thresholds the real-time card fraud engine uses.

## 2. Real-time rules

| Rule | Trigger |
|---|---|
| R-101 Velocity | 6 card-not-present purchases within 10 minutes |
"""
DISPUTES = "".join(f"## {i}. Part {i}\n\nYou can dispute a charge within 60 days of statement {i}.\n\n"
                   for i in range(1, 6))


async def test_staff_find_the_fraud_memo_by_name_and_customers_never_see_it(pg_url, app_url):
    store = _store(pg_url, app_url)
    for doc_id, text, clearance, doc_type in (("memo", MEMO, 2, "internal_memo"),
                                              ("disputes", f"# Dispute and Error Resolution Policy\n\n{DISPUTES}", 0,
                                               "policy")):
        chunks = chunk_blocks(parse_markdown(text), f"{doc_id}.md", doc_id)
        await store.scoped(ADMIN).add_document(chunks, team_id=None, clearance=clearance, uploaded_by=None,
                                               meta={"doc_type": doc_type, "effective_date": date(2026, 3, 1)})
    staff = await store.scoped(ADMIN).search("fraud rules memo", filters=Filters(as_of=date(2026, 6, 30)))
    assert "memo" in {h.doc_id for h in staff[:3]}  # clearance 2 sees it, and its title is in its chunks
    customer = await store.scoped(CUSTOMER).search("fraud rules memo", filters=Filters(as_of=date(2026, 6, 30)))
    assert customer and "memo" not in {h.doc_id for h in customer}
