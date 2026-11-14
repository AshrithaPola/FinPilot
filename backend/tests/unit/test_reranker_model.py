"""The real cross-encoder (fastembed, ONNX), not a stand-in. Downloads the
model (about 80 MB) on first use; CI keeps it between runs in a cache
(FASTEMBED_CACHE_PATH, .github/workflows/tests.yml)."""
from config import Settings
from ingestion.rerank import Reranker


def test_the_cross_encoder_prefers_the_relevant_passage():
    reranker = Reranker(Settings(_env_file=None).rerank_model)
    relevant, unrelated = reranker.scores("What is the overdraft fee?", [
        "The overdraft fee is $15.00 per item, at most two a day.",
        "Branches open at 9 a.m. on weekdays and 10 a.m. on Saturdays.",
    ])
    assert relevant > unrelated
