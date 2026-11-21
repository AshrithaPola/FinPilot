"""Query routing classification: small talk vs. real document questions."""
import pytest

from graph.routing import asks_for_advice, is_money_movement, is_small_talk

SMALL_TALK = [
    "hi",
    "Hello!",
    "hey",
    "Good morning",
    "thanks!",
    "Thank you so much",
    "thx",
    "how are you?",
    "what's up",
    "ok",
    "cool",
    "got it",
    "bye",
    "goodbye!",
    "who are you?",
    "What can you do?",
    "help",
    "testing",
    "  Hi!!  ",
]

REAL_QUESTIONS = [
    "hello, what does section 3 say?",       # greeting + real question
    "What is the vacation policy?",
    "hi level threshold for the alarm?",      # starts with 'hi' but is a question
    "thanks to what mechanism does the pump cool?",
    "What's up with the Q3 budget numbers?",
    "help me understand the refund clause",
    "Who are you required to notify after an incident?",
    "summarize the document",
    "test coverage requirements in the spec?",
    "ok so what does the contract say about renewal?",
]


@pytest.mark.parametrize("question", SMALL_TALK)
def test_small_talk_detected(question):
    assert is_small_talk(question)


@pytest.mark.parametrize("question", REAL_QUESTIONS)
def test_document_questions_pass_through(question):
    # False positives are the costly error here — a real question must never
    # be answered with the canned small-talk response.
    assert not is_small_talk(question)


MOVE_MONEY = [
    "Transfer $500 to my savings",
    "Can you send $40 to my credit card?",
    "please pay my rent",
    "I want to move money from my checking to my savings",
    "Could you wire $2,000 to my brother?",
]
ABOUT_MONEY = [
    "How much did I transfer in May 2026?",  # a question about past transfers
    "send me my statement",  # a request, but not to move money
    "Why was I charged $15 on April 6, 2026?",
    "Can I afford $1,200 this month and stay above $500?",
    "pay attention to the overdraft policy: what does it say?",
]


@pytest.mark.parametrize("question", MOVE_MONEY)
def test_requests_to_move_money_get_the_read_only_reply(question):
    assert is_money_movement(question)


@pytest.mark.parametrize("question", ABOUT_MONEY)
def test_questions_about_money_are_not_refused(question):
    assert not is_money_movement(question)


WANTS_ADVICE = [
    "Should I buy more VTI?",
    "Should I sell my BND?",
    "Would you recommend SCHD for me?",
    "Is VTI a good investment right now?",
    "Is it a good time to buy?",
    "Buy or sell VTI?",
    "Is SCHD worth buying?",
]
WANTS_FACTS = [
    "What is VTI's expense ratio, and what is its latest price?",
    "How many VTI shares do I hold?",
    "What did Coca-Cola say about its main risks in its last 10-K?",
    "What does the prospectus say about how VTI is managed?",
    "Can I afford $1,200 this month and stay above $500?",
]


@pytest.mark.parametrize("question", WANTS_ADVICE)
def test_requests_for_a_recommendation_are_recognised(question):
    assert asks_for_advice(question)


@pytest.mark.parametrize("question", WANTS_FACTS)
def test_questions_about_facts_are_not_treated_as_advice(question):
    assert not asks_for_advice(question)
