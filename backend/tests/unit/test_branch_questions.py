"""The branching experiment's questions still match the corpus: every gold
section exists, every answer snippet is in its gold chunks, and version
questions' answers never appear in a superseded document."""
from eval.branch_experiment import check


def test_branch_questions_match_the_corpus():
    assert check() == []
