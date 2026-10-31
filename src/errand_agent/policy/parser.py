"""Deterministic confirmation parser for user messages."""

from __future__ import annotations

import re

from errand_agent.domain.enums import ConfirmationDecision

# Strict normalized regexes for confirmation decisions
AFFIRM_PATTERNS = [
    re.compile(r"^\s*(yes|y|yep|yeah|sure|confirm|confirmed|proceed|go ahead|approved|approve|book it|ok book it)\b", re.I),
    re.compile(r"\b(please book|please proceed|go for it|looks good|that works)\b", re.I),
]

DENY_PATTERNS = [
    re.compile(r"^\s*(no|n|nope|nah|cancel|abort|stop|don't|do not|never mind|nevermind)\b", re.I),
    re.compile(r"\b(don't book|do not book|cancel this|abort this)\b", re.I),
]

CORRECTION_PATTERNS = [
    re.compile(r"\b(actually|make it|change to|instead of|rather|how about|switch to)\b", re.I),
]


def parse_confirmation(user_message: str) -> tuple[ConfirmationDecision, str | None]:
    """Deterministically parse user input against an active pending action confirmation card.

    Returns:
        (ConfirmationDecision, optional_correction_text)
    """
    cleaned = user_message.strip()
    if not cleaned:
        return ConfirmationDecision.UNKNOWN, None

    # Check for explicit correction first
    for pattern in CORRECTION_PATTERNS:
        if pattern.search(cleaned):
            return ConfirmationDecision.CORRECTION, cleaned

    # Check affirmations
    for pattern in AFFIRM_PATTERNS:
        if pattern.search(cleaned):
            return ConfirmationDecision.AFFIRM, None

    # Check denials
    for pattern in DENY_PATTERNS:
        if pattern.search(cleaned):
            return ConfirmationDecision.DENY, None

    return ConfirmationDecision.UNKNOWN, None
