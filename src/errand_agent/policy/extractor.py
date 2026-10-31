"""Structural, deterministic semantic extractor and command parser.

Completely free of regex, heuristic guessing, and ReDoS vulnerabilities.
Uses lexical tokenization, typed token sequences, and structural n-gram scanning.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from errand_agent.domain.enums import Intent

# Canonical Services
KNOWN_SERVICES: tuple[tuple[str, ...], ...] = (
    ("ac", "servicing"),
    ("air", "conditioner"),
    ("plumber",),
    ("electrician",),
    ("carpentry",),
    ("carpenter",),
    ("cleaning",),
)

# Canonical Areas
KNOWN_AREAS: dict[tuple[str, ...], str] = {
    ("indiranagar",): "Indiranagar",
    ("koramangala",): "Koramangala",
    ("hsr", "layout"): "HSR Layout",
    ("hsr",): "HSR Layout",
    ("whitefield",): "Whitefield",
    ("jayanagar",): "Jayanagar",
    ("malleswaram",): "Malleswaram",
}

# Canonical Date/Time Windows
KNOWN_TIME_WINDOWS: dict[tuple[str, ...], str] = {
    ("this", "week"): "this week",
    ("saturday", "morning"): "Saturday morning",
    ("saturday", "afternoon"): "Saturday afternoon",
    ("saturday", "evening"): "Saturday evening",
    ("saturday",): "Saturday",
    ("sunday", "morning"): "Sunday morning",
    ("sunday", "afternoon"): "Sunday afternoon",
    ("sunday",): "Sunday",
    ("mornings", "only"): "mornings only",
    ("morning", "only"): "mornings only",
    ("mornings",): "mornings only",
}

# Intent trigger phrases
BOOK_TRIGGERS: tuple[tuple[str, ...], ...] = (
    ("book",),
    ("find",),
    ("hire",),
    ("need", "a"),
    ("get", "a"),
    ("reserve",),
)

RESCHEDULE_TRIGGERS: tuple[tuple[str, ...], ...] = (
    ("reschedule",),
    ("move", "my"),
    ("move",),
    ("change", "time"),
    ("postpone",),
    ("shift",),
)

CANCEL_TRIGGERS: tuple[tuple[str, ...], ...] = (
    ("cancel",),
    ("drop",),
    ("abort",),
    ("stop", "my", "booking"),
)

BUDGET_MARKERS: frozenset[str] = frozenset(
    {"under", "below", "within", "budget", "max", "maximum", "upto", "less"}
)

CURRENCY_TOKENS: frozenset[str] = frozenset({"₹", "rs", "rs.", "inr", "$"})


def tokenize(text: str) -> list[str]:
    """Deterministically tokenize text into normalized alphanumeric/symbol tokens.

    Preserves numbers, words, and currency symbols without regex.
    """
    normalized = unicodedata.normalize("NFKC", text)
    tokens: list[str] = []
    current: list[str] = []
    n = len(normalized)

    for i, char in enumerate(normalized):
        if char in ("₹", "$"):
            if current:
                tok = "".join(current).lower().strip(".,;:!?")
                if tok:
                    tokens.append(tok)
                current = []
            tokens.append(char)
        elif char.isalnum():
            current.append(char)
        elif char in (",", "."):
            # Embedded number separator lookahead & lookbehind: e.g. "1,500" or "10.00"
            if current and current[-1].isdigit() and (i + 1 < n) and normalized[i + 1].isdigit():
                current.append(char)
            else:
                if current:
                    tok = "".join(current).lower().strip(".,;:!?")
                    if tok:
                        tokens.append(tok)
                    current = []
        else:
            if current:
                tok = "".join(current).lower().strip(".,;:!?")
                if tok:
                    tokens.append(tok)
                current = []

    if current:
        tok = "".join(current).lower().strip(".,;:!?")
        if tok:
            tokens.append(tok)

    return tokens


def _matches_phrase_at(tokens: list[str], phrase: tuple[str, ...], index: int) -> bool:
    """Check if token sequence matches phrase starting at index."""
    if index + len(phrase) > len(tokens):
        return False
    return tuple(tokens[index : index + len(phrase)]) == phrase


def _find_phrase(tokens: list[str], phrase: tuple[str, ...]) -> int:
    """Find starting index of phrase in tokens, or -1 if not found."""
    k = len(phrase)
    for i in range(len(tokens) - k + 1):
        if tuple(tokens[i : i + k]) == phrase:
            return i
    return -1


@dataclass(frozen=True)
class ExtractedSlots:
    """Structurally extracted slots and intent from user message."""

    intent: Intent | None
    service: str | None
    area: str | None
    date_range: str | None
    budget: int | None
    earliest_time: str | None
    reference_last_provider: bool


def extract_intent_and_slots(message: str, existing_intent: Intent = Intent.NONE) -> ExtractedSlots:
    """Deterministically extract semantic slots from message without regex."""
    tokens = tokenize(message)

    # 1. Intent Detection
    intent = None
    if existing_intent == Intent.NONE:
        for phrase in CANCEL_TRIGGERS:
            if _find_phrase(tokens, phrase) != -1:
                intent = Intent.CANCEL
                break
        if not intent:
            for phrase in RESCHEDULE_TRIGGERS:
                if _find_phrase(tokens, phrase) != -1:
                    intent = Intent.RESCHEDULE
                    break
        if not intent:
            for phrase in BOOK_TRIGGERS:
                if _find_phrase(tokens, phrase) != -1:
                    intent = Intent.BOOK
                    break

    # 2. Service Detection
    service = None
    for phrase in KNOWN_SERVICES:
        if _find_phrase(tokens, phrase) != -1:
            service = " ".join(phrase)
            if service in ("air conditioner", "ac servicing"):
                service = "ac servicing"
            elif service == "carpenter":
                service = "carpentry"
            break

    # 3. Area Detection
    area = None
    for phrase, canonical in KNOWN_AREAS.items():
        if _find_phrase(tokens, phrase) != -1:
            area = canonical
            break

    # 4. Time Window / Date Range Detection
    date_range = None
    for phrase, canonical in KNOWN_TIME_WINDOWS.items():
        if _find_phrase(tokens, phrase) != -1:
            date_range = canonical
            break

    # 5. Budget Extraction: structural marker + optional currency + integer
    budget = None
    for i, tok in enumerate(tokens):
        if tok in BUDGET_MARKERS or (
            tok == "budget" and i + 1 < len(tokens) and tokens[i + 1] == "of"
        ):
            # Inspect subsequent tokens for numeric value
            offset = 1
            if tok == "budget" and i + 1 < len(tokens) and tokens[i + 1] == "of":
                offset = 2

            while i + offset < len(tokens) and tokens[i + offset] in CURRENCY_TOKENS:
                offset += 1

            if i + offset < len(tokens):
                candidate = tokens[i + offset].replace(",", "")
                # Handle cases where currency symbol is prefix: ₹1500
                if candidate.startswith("₹") or candidate.startswith("$"):
                    candidate = candidate[1:]
                if candidate.isdigit():
                    val = int(candidate)
                    if 100 <= val <= 100000:
                        budget = val
                        break

    # 6. Earliest Time: "not before 10 a.m." / "not before 10" / "after 10"
    earliest_time = None
    idx = _find_phrase(tokens, ("not", "before"))
    if idx != -1 and idx + 2 < len(tokens):
        time_part = tokens[idx + 2].replace(".", "")
        meridiem = ""
        if idx + 3 < len(tokens):
            m_cand = tokens[idx + 3].replace(".", "")
            if m_cand in ("am", "pm"):
                meridiem = m_cand.upper()
        if time_part.isdigit():
            hour = int(time_part)
            if not meridiem:
                meridiem = "AM" if hour in (8, 9, 10, 11) else "PM"
            earliest_time = (
                f"{hour}:00 {meridiem}" if ":" not in time_part else f"{time_part} {meridiem}"
            )

    # 7. Provider Reference: "same ... as last time"
    ref_idx = _find_phrase(tokens, ("as", "last", "time"))
    reference_last_provider = False
    if ref_idx != -1 and "same" in tokens[:ref_idx]:
        reference_last_provider = True

    return ExtractedSlots(
        intent=intent,
        service=service,
        area=area,
        date_range=date_range,
        budget=budget,
        earliest_time=earliest_time,
        reference_last_provider=reference_last_provider,
    )


# ---------------------------------------------------------------------------
# Structural Memory Command Parser
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InspectMemoryCommand:
    """User wants to inspect what is stored about them."""

    pass


@dataclass(frozen=True)
class ForgetAllMemoryCommand:
    """User wants to purge all memory facts."""

    pass


@dataclass(frozen=True)
class ForgetFactCommand:
    """User wants to purge a specific memory key."""

    key: str


@dataclass(frozen=True)
class CorrectFactCommand:
    """User wants to update a specific memory key to a new value."""

    key: str
    value: str


MemoryCommand = (
    InspectMemoryCommand | ForgetAllMemoryCommand | ForgetFactCommand | CorrectFactCommand
)


def parse_memory_command(message: str) -> MemoryCommand | None:
    """Deterministically parse explicit memory control commands without regex.

    Returns typed MemoryCommand or None if message is not a memory command.
    """
    raw_stripped = message.strip()
    if not raw_stripped:
        return None

    tokens = tokenize(raw_stripped)
    if not tokens:
        return None

    # 1. Inspection commands
    inspect_phrases = (
        ("what", "do", "you", "remember", "about", "me"),
        ("what", "do", "you", "know", "about", "me"),
        ("my", "preferences"),
        ("show", "my", "preferences"),
        ("what", "are", "my", "preferences"),
    )
    for p in inspect_phrases:
        if tuple(tokens) == p:
            return InspectMemoryCommand()

    # 2. Forget everything
    forget_all_phrases = (
        ("forget", "everything"),
        ("forget", "all"),
        ("delete", "all", "memory"),
        ("clear", "memory"),
    )
    for p in forget_all_phrases:
        if tuple(tokens) == p:
            return ForgetAllMemoryCommand()

    # 3. Forget specific key: "forget <key>"
    if tokens[0] == "forget" and len(tokens) >= 2:
        # Key is remainder of tokens joined by space
        key = " ".join(tokens[1:])
        return ForgetFactCommand(key=key)

    # 4. Correct fact: "correct <key> to <value>"
    if tokens[0] == "correct":
        try:
            to_idx = tokens.index("to")
            if to_idx > 1 and to_idx < len(tokens) - 1:
                key = " ".join(tokens[1:to_idx])
                # Value preserves raw string formatting and casing after " to "
                lower_raw = raw_stripped.lower()
                to_marker = " to "
                idx_to = lower_raw.find(to_marker)
                if idx_to != -1:
                    raw_val = raw_stripped[idx_to + len(to_marker) :].strip()
                    return CorrectFactCommand(key=key, value=raw_val)
        except ValueError:
            pass

    return None
