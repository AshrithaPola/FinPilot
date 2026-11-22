"""Small-talk detection for the routing node.

Deliberately a heuristic, not an LLM call. An LLM classifier would add a
model call to EVERY real query to catch cases the pipeline already handles
safely — an off-topic question ("what's the weather?") goes through grading
and gets a clean refused_no_context. The heuristic only needs to catch the
obvious conversational messages where running retrieval at all is silly.

The costs of a mistake are asymmetric, and the patterns are shaped for that:
 - false negative (greeting slips through): one wasted cheap pipeline run
   that ends in a polite refusal — mildly inefficient, never wrong.
 - false positive (real question canned): actively bad, so every pattern is
   anchored to match the WHOLE message. "hello, what does section 3 say?"
   does not match; bare "hello!" does.

Being a pure function of the input string, it is also unit-testable and
replayable, which fits this project's fixed-pipeline discipline.
"""
import re

_SMALL_TALK = re.compile(
    r"""^(?:
        (?:hi+|hello+|hey+|heya|yo|howdy|greetings|good\s+(?:morning|afternoon|evening))
      | (?:thanks|thank\s+you|thx|ty|cheers)(?:\s+(?:so\s+much|a\s+lot|very\s+much))?
      | (?:how\s+are\s+you(?:\s+doing)?|how'?s\s+it\s+going|what'?s\s+up|sup)
      | (?:bye|goodbye|good\s+night|see\s+you|see\s+ya|later|take\s+care)
      | (?:ok|okay|cool|nice|great|awesome|perfect|got\s+it|sounds\s+good)
      | (?:who\s+are\s+you|what\s+are\s+you|what\s+can\s+you\s+do|help)
      | (?:test|testing|ping)
    )[\s!.?,:;)…]*$""",
    re.IGNORECASE | re.VERBOSE,
)

SMALL_TALK_RESPONSE = (
    "Hi! I'm FinPilot. Ask me questions about the bank's documents — "
    "answers cite the document, and if it doesn't contain the answer, "
    "I'll say so instead of guessing."
)


def is_small_talk(question: str) -> bool:
    return bool(_SMALL_TALK.match(question.strip()))


# A request to move money: a request anywhere in the message (a sentence or
# clause that opens with the verb, "can/could you", "please", "pls", "I need/
# want (you) to", "help me", with filler words between: "can you just move")
# or a passive one ("I need $300 moved"), AND money named (an amount, money
# words, or to/from an account). Same asymmetry as above: "How much did I
# transfer in May?", "Why did my transfer to savings fail?" and "send me my
# statement" stay questions.
_VERB = r"(?:transfer|move|send|pay|wire|withdraw|deposit|zelle|put)"
_FILLER = r"(?:(?:just|quickly|quick|real\s+quick|kindly|please|pls|plz|now|go\s+ahead\s+and)\s+)*"
_MOVE_REQUEST = re.compile(
    rf"""(?:^|[.!?,;:]\s*|\b(?:can|could|would|will)\s+(?:you|u)\s+|\bplease\s+|\bpls\s+|\bplz\s+
          |\bi\s+(?:want|need|would\s+like|'d\s+like)\s+(?:you\s+|u\s+)?to\s+|\bhelp\s+me\s+)
        (?:(?:hey|hi|ugh|ok|okay|so|yo)\W+)*{_FILLER}{_VERB}\b
      | \bi\s+(?:want|need|would\s+like|'d\s+like)\s+.{{0,40}}?\b(?:moved|transferred|sent|paid|wired)\b""",
    re.IGNORECASE | re.VERBOSE,
)
_MONEY = re.compile(r"\$\s?\d|\b\d[\d,]*(?:\.\d{2})?\s+(?:bucks|dollars)\b|\b(?:money|funds|dollars|bucks|cash|bill|rent)\b"
                    r"|\b(?:to|from|into|over\s+from)\s+(?:my\s+|the\s+)?(?:savings|checking|card|credit\s+card|account)\b",
                    re.IGNORECASE)
# The fallback after the planner (see engine.graph): a money-moving verb and an account word.
_MOVE_VERB = re.compile(rf"\b{_VERB}\b", re.IGNORECASE)
_ACCOUNT = re.compile(r"\b(?:savings|checking|account|card)\b", re.IGNORECASE)

MONEY_MOVEMENT_RESPONSE = (
    "FinPilot is read-only and can't move money: no transfers, payments or withdrawals. "
    "I can tell you your balances, transactions and fees, and explain the bank's policies."
)


# A request for a recommendation (buy, sell, hold, "should I…"): only then does
# the answer open with "I can't recommend trades". A question about facts
# ("What is VTI's expense ratio?") doesn't.
_ADVICE_REQUEST = re.compile(
    r"""\bshould\s+i\b
      | \b(?:would|do|can|could)\s+you\s+(?:recommend|suggest|advise)\b
      | \b(?:recommend|advise)\s+(?:me|buying|selling|holding|investing)\b
      | \bis\s+(?:it|now|this|[A-Z]{2,5})\s+(?:a\s+)?(?:good|smart|wise|bad)\s+(?:idea|time|investment|buy)\b
      | \b(?:good|smart|bad)\s+(?:investment|buy)\b
      | \bworth\s+(?:buying|selling|investing|holding)\b
      | \b(?:buy|sell|hold)\s+or\s+(?:buy|sell|hold)\b
      | \b(?:buy|sell)\s+(?:more|some|now)\b""",
    re.IGNORECASE | re.VERBOSE,
)


def asks_for_advice(question: str) -> bool:
    return bool(_ADVICE_REQUEST.search(question))


def is_money_movement(question: str) -> bool:
    text = question.strip()
    return bool(_MOVE_REQUEST.search(text) and _MONEY.search(text))


def mentions_moving_money(question: str) -> bool:
    """Looser, for after the planner found no question to answer: a money-moving
    verb and an account word ("shift... uh, move it over to savings")."""
    return bool(_MOVE_VERB.search(question) and _ACCOUNT.search(question))
