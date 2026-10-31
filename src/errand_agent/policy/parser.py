"""Structural, deterministic confirmation and intent parser.

Replaces brittle regex with structural tokenization, canonical token sets,
negation checking, interrogative classification, and unambiguous decision boundaries.
"""

from __future__ import annotations

import string

from errand_agent.domain.enums import ConfirmationDecision

# Exact canonical single tokens
AFFIRMATIVE_EXACT_TOKENS: frozenset[str] = frozenset(
    {
        "yes",
        "y",
        "yep",
        "yeah",
        "sure",
        "confirm",
        "confirmed",
        "proceed",
        "approved",
        "approve",
        "agree",
        "accepted",
    }
)

DENIAL_EXACT_TOKENS: frozenset[str] = frozenset(
    {"no", "n", "nope", "nah", "cancel", "abort", "stop", "nevermind", "reject", "rejected", "dont"}
)

# Canonical n-gram token tuples
AFFIRMATIVE_PHRASES: tuple[tuple[str, ...], ...] = (
    ("go", "ahead"),
    ("book", "it"),
    ("ok", "book", "it"),
    ("please", "book"),
    ("please", "proceed"),
    ("looks", "good"),
    ("that", "works"),
    ("go", "for", "it"),
)

DENIAL_PHRASES: tuple[tuple[str, ...], ...] = (
    ("do", "not", "book"),
    ("dont", "book"),
    ("do", "not", "proceed"),
    ("dont", "proceed"),
    ("cancel", "this"),
    ("cancel", "it"),
    ("abort", "this"),
    ("stop", "this"),
)

CORRECTION_TRIGGERS: tuple[tuple[str, ...], ...] = (
    ("actually",),
    ("make", "it"),
    ("change", "to"),
    ("change", "it", "to"),
    ("instead", "of"),
    ("switch", "to"),
    ("how", "about"),
    ("rather",),
)

NEGATION_TOKENS: frozenset[str] = frozenset(
    {"not", "no", "never", "dont", "do_not", "neither", "nor"}
)

INTERROGATIVE_TOKENS: frozenset[str] = frozenset(
    {"what", "why", "how", "who", "when", "where", "which", "is", "can", "could", "would", "?"}
)


def _tokenize(text: str) -> list[str]:
    """Cleanly tokenize text into normalized lowercase alphanumeric tokens."""
    # Handle common contractions before stripping punctuation
    lowered = (
        text.lower().replace("don't", "dont").replace("won't", "wont").replace("can't", "cant")
    )
    cleaned = []
    for ch in lowered:
        if ch in string.ascii_lowercase or ch in string.digits or ch == "?":
            cleaned.append(ch)
        else:
            cleaned.append(" ")
    tokens = [t for t in "".join(cleaned).split() if t]
    return tokens


def _matches_phrase(tokens: list[str], phrase: tuple[str, ...]) -> bool:
    """Check if token stream contains the consecutive phrase."""
    k = len(phrase)
    if k > len(tokens):
        return False
    return any(tuple(tokens[i : i + k]) == phrase for i in range(len(tokens) - k + 1))


def parse_confirmation(user_message: str) -> tuple[ConfirmationDecision, str | None]:
    """Deterministically parse user input against an active pending action confirmation card.

    Decision boundaries:
    1. Empty / whitespace -> UNKNOWN
    2. Interrogative (contains question mark or leading question words without affirmative clause) -> UNKNOWN
    3. Structural correction phrase detected -> CORRECTION
    4. Negation tokens attached to affirmative words ("not sure", "don't proceed") -> DENY
    5. Pure affirmative token or phrase (without negation or conflict) -> AFFIRM
    6. Pure denial token or phrase -> DENY
    7. Any conflict or ambiguity -> UNKNOWN

    Returns:
        (ConfirmationDecision, optional_correction_text)
    """
    raw_stripped = user_message.strip()
    if not raw_stripped:
        return ConfirmationDecision.UNKNOWN, None

    tokens = _tokenize(raw_stripped)
    if not tokens:
        return ConfirmationDecision.UNKNOWN, None

    token_set = set(tokens)
    has_affirm = bool(token_set & AFFIRMATIVE_EXACT_TOKENS)
    has_deny = bool(token_set & DENIAL_EXACT_TOKENS)
    has_negation = bool(token_set & NEGATION_TOKENS)

    # 1. Mutually conflicting affirmative and denial signals ("yes but no", "approved or cancel")
    if has_affirm and has_deny:
        return ConfirmationDecision.UNKNOWN, None

    # 2. Structural correction triggers take precedence if not conflicting
    for trigger in CORRECTION_TRIGGERS:
        if _matches_phrase(tokens, trigger):
            return ConfirmationDecision.CORRECTION, raw_stripped

    # 2. Check for interrogative inquiry ("what is the price?", "could you check?")
    has_question = "?" in tokens or (tokens and tokens[0] in INTERROGATIVE_TOKENS)
    # If the user is asking a question and not answering yes/no, reject as UNKNOWN
    if has_question and len(tokens) > 1 and tokens[0] in INTERROGATIVE_TOKENS:
        return ConfirmationDecision.UNKNOWN, None

    # 3. Check for multi-token denial phrases
    if len(tokens) >= 2 and tokens[:2] == ["do", "not"]:
        return ConfirmationDecision.DENY, None

    for phrase in DENIAL_PHRASES:
        if _matches_phrase(tokens, phrase):
            return ConfirmationDecision.DENY, None

    # 4. Check for multi-token affirmative phrases
    for phrase in AFFIRMATIVE_PHRASES:
        if _matches_phrase(tokens, phrase):
            # Verify no preceding negation ("do not go ahead")
            idx = 0
            k = len(phrase)
            while idx <= len(tokens) - k:
                if tuple(tokens[idx : idx + k]) == phrase:
                    if idx > 0 and tokens[idx - 1] in NEGATION_TOKENS:
                        return ConfirmationDecision.DENY, None
                    return ConfirmationDecision.AFFIRM, None
                idx += 1

    # 5. Check single tokens
    # If negation is present with affirmative word ("not confirmed", "never book"), treat as DENY or UNKNOWN
    if has_affirm and has_negation:
        return ConfirmationDecision.DENY, None

    # Exact affirmative match
    if has_affirm and not has_question:
        # Check that the message is short / directly responsive (< 8 tokens), not a long rambling sentence
        if len(tokens) <= 7:
            return ConfirmationDecision.AFFIRM, None
        return ConfirmationDecision.UNKNOWN, None

    # Exact denial match
    if has_deny and not has_question:
        if len(tokens) <= 7:
            return ConfirmationDecision.DENY, None
        return ConfirmationDecision.UNKNOWN, None

    return ConfirmationDecision.UNKNOWN, None
