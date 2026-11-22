"""The eval runner's scoring, without the engine: numbers as written, retrieval
ranks, gold citations, outcome groups and the failure diagnosis."""
from eval import questions as ev
from eval import run


def test_expected_numbers_are_found_only_as_written():
    answer = "You were charged $15.00 on 2026-04-06; the cushion is $50 and you have 60 days. Ratio 0.03%."
    assert run.number_found(15.0, answer) and run.number_found(50, answer) and run.number_found(60, answer)
    assert run.number_found(0.03, answer)
    assert not run.number_found(34.0, answer)
    assert not run.number_found(2, answer)  # "2026" doesn't hold a 2 of its own
    assert not run.number_found(15.75, "a $15.00 fee")
    assert run.number_found(1200, "spend $1,200 this month") and run.number_found(259.98, "you spent $259.98")
    assert run.number_found(25.86, "a -$25.86 purchase")  # the sign is the answer's wording


def _q(**kw):
    base = {"id": "q-x", "question": "What is the fee?", "asked_as": "cust-0036", "type": "exact-term", "notes": "a test question",
            "expected_answer": {"outcome": "answered", "facts": ["x"]},
            "gold_sources": [{"id": "s1", "kind": "document", "file": "fee-schedule-2026.md",
                              "section": "1. Everyday Checking"},
                             {"id": "s2", "kind": "tool", "tool": "list_fees", "arguments": {}}]}
    return ev.Question.model_validate({**base, **kw})


def test_retrieval_is_scored_on_the_apps_own_searches():
    hit = {"filename": "fee-schedule-2026.md", "section": "Fees > 1. Everyday Checking"}
    other = {"filename": "deposit-agreement.md", "section": "11. Fees"}
    scores = run.retrieval_scores(_q(), [[other, other, hit], [other]])
    assert scores["recall_at_5"] == 1.0 and scores["mrr"] == round(1 / 3, 3) and scores["searches"] == 2
    assert run.retrieval_scores(_q(), [[other] * 5 + [hit]])["recall_at_5"] == 0.0  # 6th place is outside the top 5
    assert run.retrieval_scores(_q(), [])["mrr"] == 0.0  # no search planned
    assert run.retrieval_scores(_q(gold_sources=[]), [[hit]]) is None  # no gold documents: not scored


def test_gold_citations_match_documents_by_section_and_tools_by_name():
    citations = [{"kind": "document", "filename": "fee-schedule-2026.md", "section": "Fees > 1. Everyday Checking"},
                 {"kind": "tool", "tool": "get_balance"}]
    cited = run.gold_cited(_q(), citations)
    assert (cited["cited"], cited["of"]) == (1, 2)


def test_declines_are_one_group_and_failures_name_their_stage():
    assert run.OUTCOME_GROUP["nothing_found"] == "not_found" and run.OUTCOME_GROUP["out_of_scope"] == "refused"
    record = {"outcome_ok": False, "expected_outcome": "answered", "outcome_group": "not_found",
              "outcome": "nothing_found", "plan": [], "numbers_ok": False,
              "numbers": [{"value": 289.42, "found": False}], "facts": [], "facts_covered": None,
              "retrieval": None, "faithfulness": None, "canary_leaks": 0}
    causes = run._diagnose(record)
    assert causes[0].startswith("false refusal") and "no calculation step planned" in causes[1]


async def test_a_timeout_is_retried_once_and_both_attempts_are_kept(monkeypatch):
    calls = []

    async def flaky(q, judge=True):
        calls.append(q.id)
        if len(calls) == 1:
            raise TimeoutError("gemini-3.5-flash-lite gave no answer within 45 s")
        return {"id": q.id, "outcome": "answered"}

    monkeypatch.setattr(run, "run_question", flaky)
    record = await run.attempt(_q())
    assert len(calls) == 2 and record["retried"] and record["failed_attempts"][0]["kind"] == "timeout"

    async def broken(q, judge=True):
        raise ValueError("a bug, not capacity")

    monkeypatch.setattr(run, "run_question", broken)
    failed = await run.attempt(_q())
    assert "error" in failed and len(failed["failed_attempts"]) == 1  # not retried


def test_an_alternative_document_counts_for_retrieval_and_citation():
    q = _q(gold_sources=[{"id": "s1", "kind": "document", "file": "cardmember-agreement.md", "section": "5. Fees",
                          "alternatives": [{"file": "fee-schedule-2026.md", "section": "5. Cash Back Card (credit card)"}]}])
    hit = {"filename": "fee-schedule-2026.md", "section": "Fees > 5. Cash Back Card (credit card)"}
    assert run.retrieval_scores(q, [[hit]])["recall_at_5"] == 1.0
    assert run.gold_cited(q, [{"kind": "document", **hit}])["cited"] == 1


async def test_a_rescore_changes_only_what_the_corrected_set_changes():
    from eval import rescore

    q = _q(gold_sources=[{"id": "s1", "kind": "document", "file": "cardmember-agreement.md", "section": "5. Fees",
                          "alternatives": [{"file": "fee-schedule-2026.md", "section": "5. Cash Back Card (credit card)"}]}],
           expected_answer={"outcome": "answered", "facts": []})
    record = {"id": "q-x", "outcome": "answered", "answer": "It's 3% of each transaction [1].", "canary": [],
              "plan": [{"tool": "documents.search"}], "facts": [{"fact": "old fact", "covered": False}],
              "gold_cited": {"cited": 0, "of": 1, "detail": [{"source": "s1", "cited": False}]},
              "retrieval": {"recall_at_5": 0.0, "mrr": 0.0, "searches": 1,
                            "top5": [["fee-schedule-2026.md > 5. Cash Back Card (credit card)"]]},
              "faithfulness": {"faithful": False, "unsupported": ["x"]}}
    r = await rescore.rescore_record(record, q, {"q-x": "the evidence states it"})
    assert r["gold_cited"]["cited"] == 1 and r["retrieval"]["recall_at_5"] == 1.0  # the alternative counts
    assert r["facts_covered"] is None and r["facts"] == []  # the fact list is empty now: no judge call
    assert r["faithfulness"]["faithful"] and r["faithfulness"]["judge_error"]["reason"] == "the evidence states it"
