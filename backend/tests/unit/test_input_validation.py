"""The engine's input validation: empty / oversized / normal, and PII redaction there."""
import pytest

import config
from engine.graph import validate_node as input_validation_node


@pytest.fixture(autouse=True)
def fresh_settings(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


async def test_empty_question_rejected():
    update = await input_validation_node({"question": "   "})
    assert update["outcome"] == "rejected_input"
    assert update["rejection"] == "Question is empty."


async def test_missing_question_rejected():
    update = await input_validation_node({})
    assert update["outcome"] == "rejected_input"


async def test_oversized_question_rejected():
    update = await input_validation_node({"question": "x" * 2001})
    assert update["outcome"] == "rejected_input"
    assert "2001 characters" in update["rejection"]
    assert "limit is 2000" in update["rejection"]


async def test_normal_question_passes_to_routing():
    update = await input_validation_node({"question": "  What is the policy?  "})
    assert "rejection" not in update
    assert update["question"] == "What is the policy?"
    assert update["redaction_categories"] == []


async def test_question_with_pii_is_redacted_before_anything_else():
    update = await input_validation_node(
        {"question": "Does the doc mention jane@corp.com or 555-123-4567?"}
    )
    assert update["redaction_categories"] == ["email", "phone"]
    assert "jane@corp.com" not in update["question"]
    assert "[EMAIL_REDACTED]" in update["question"]
    assert "[PHONE_REDACTED]" in update["question"]
