"""Tool output projection, sanitization, and deterministic prompt injection detection.

Completely free of regex; uses structural tokenization, Unicode normalization,
and deterministic n-gram matching.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

# Canonical injection phrase n-grams
INJECTION_PHRASES: tuple[tuple[str, ...], ...] = (
    ("ignore", "all", "instructions"),
    ("ignore", "previous", "instructions"),
    ("ignore", "prior", "instructions"),
    ("ignore", "above", "instructions"),
    ("ignore", "all", "rules"),
    ("ignore", "previous", "rules"),
    ("ignore", "prior", "rules"),
    ("ignore", "above", "rules"),
    ("ignore", "all", "directions"),
    ("ignore", "previous", "directions"),
    ("ignore", "prior", "directions"),
    ("ignore", "above", "directions"),
    ("system", "override"),
    ("system", "prompt"),
    ("system", "directive"),
    ("user", "has", "already", "approved"),
    ("user", "has", "approved"),
    ("user", "has", "already", "confirmed"),
    ("user", "has", "confirmed"),
    ("user", "has", "already", "agreed"),
    ("user", "has", "agreed"),
    ("do", "not", "ask", "for", "confirmation"),
    ("do", "not", "ask", "confirmation"),
    ("do", "not", "ask", "for", "approval"),
    ("do", "not", "ask", "approval"),
    ("dont", "ask", "for", "confirmation"),
    ("dont", "ask", "confirmation"),
    ("dont", "ask", "for", "approval"),
    ("dont", "ask", "approval"),
    ("book", "immediately"),
)

INVISIBLE_CODEPOINTS: frozenset[int] = frozenset({0x200B, 0x200C, 0x200D, 0xFEFF})
CODE_INJECTION_FUNCTIONS: tuple[str, ...] = ("eval(", "exec(", "import(", "require(")
ROLE_MARKERS: tuple[str, ...] = ("system:", "assistant:", "admin:")


@dataclass
class SanitizedTextResult:
    """Sanitized text wrapper with injection detection status."""

    clean_text: str
    is_suspicious: bool = False
    flagged_reason: str | None = None


def _tokenize_text(text: str) -> list[str]:
    """Tokenize normalized text into clean lowercase words without punctuation."""
    cleaned = []
    for ch in text.lower():
        if ch.isalnum():
            cleaned.append(ch)
        else:
            cleaned.append(" ")
    return [w for w in "".join(cleaned).split() if w]


def _matches_ngram(tokens: list[str], phrase: tuple[str, ...]) -> bool:
    """Check if tokens contains the exact consecutive n-gram."""
    k = len(phrase)
    if k > len(tokens):
        return False
    return any(tuple(tokens[i : i + k]) == phrase for i in range(len(tokens) - k + 1))


def detect_injection(text: str) -> tuple[bool, str | None]:
    """Check text for prompt injection patterns using structural tokenization and unicode inspection."""
    # 1. Normalize unicode (NFKC)
    normalized = unicodedata.normalize("NFKC", text)

    # 2. Check for invisible / zero-width Unicode characters
    if any(ord(c) in INVISIBLE_CODEPOINTS for c in normalized):
        return True, "Invisible Unicode zero-width characters detected"

    # 3. Check for external URLs
    lower = normalized.lower()
    if "http://" in lower or "https://" in lower:
        return True, "Embedded URL detected in untrusted content"

    # 4. Check for code injection calls
    no_spaces = lower.replace(" ", "")
    for fn in CODE_INJECTION_FUNCTIONS:
        if fn in no_spaces:
            return True, f"Code execution signature detected ({fn})"

    # 5. Check for role impersonation markers
    for marker in ROLE_MARKERS:
        if marker in lower:
            return True, f"Role impersonation marker detected ({marker})"

    # 6. Check for structural injection phrases
    tokens = _tokenize_text(normalized)
    for phrase in INJECTION_PHRASES:
        if _matches_ngram(tokens, phrase):
            matched_str = " ".join(phrase)
            return True, f"Matched injection phrase: '{matched_str}'"

    return False, None


def strip_control_characters(text: str) -> str:
    """Deterministically strip non-printable and control characters without regex."""
    cleaned = []
    for ch in text:
        code = ord(ch)
        # Allow tab, newline, carriage return; strip 0x00-0x1F and 0x7F-0x9F
        if (code < 32 and code not in (9, 10, 13)) or (127 <= code <= 159):
            cleaned.append(" ")
        else:
            cleaned.append(ch)
    return "".join(cleaned).strip()


def sanitize_free_text(text: str, max_length: int = 250) -> SanitizedTextResult:
    """Sanitize free-text fields (reviews, names, notes).

    Strips control characters, caps length, inspects for injection, and wraps in untrusted envelope.
    """
    if not text:
        return SanitizedTextResult(
            clean_text="<untrusted_data></untrusted_data>", is_suspicious=False
        )

    # 1. Strip control characters
    cleaned = strip_control_characters(text)

    # 2. Check for injection before truncation
    is_suspicious, reason = detect_injection(cleaned)

    # 3. Cap length
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length] + "..."

    # 4. If suspicious, redact and wrap
    if is_suspicious:
        safe_body = "[REDACTED_SUSPICIOUS_CONTENT: Potential prompt injection detected]"
    else:
        # Escape any nested XML-like tags to prevent envelope breakout
        safe_body = cleaned.replace("<", "&lt;").replace(">", "&gt;")

    enveloped = f"<untrusted_data>{safe_body}</untrusted_data>"
    return SanitizedTextResult(
        clean_text=enveloped, is_suspicious=is_suspicious, flagged_reason=reason
    )


def sanitize_provider_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """Whitelisted structural projection of provider details for model view."""
    raw_reviews = detail.get("reviews", [])
    sanitized_reviews: list[str] = []
    has_injection = False
    flagged_reasons: list[str] = []

    for r in raw_reviews:
        res = sanitize_free_text(str(r), max_length=200)
        sanitized_reviews.append(res.clean_text)
        if res.is_suspicious:
            has_injection = True
            if res.flagged_reason:
                flagged_reasons.append(res.flagged_reason)

    name_res = sanitize_free_text(str(detail.get("name", "Unknown")), max_length=60)
    if name_res.is_suspicious:
        has_injection = True
        if name_res.flagged_reason:
            flagged_reasons.append(name_res.flagged_reason)

    return {
        "provider_id": str(detail.get("provider_id", "")),
        "name": name_res.clean_text,
        "service": str(detail.get("service", "")),
        "area": str(detail.get("area", "")),
        "price": int(detail.get("price", 0)),
        "rating": float(detail.get("rating", 0.0)),
        "slots": [str(s) for s in detail.get("slots", [])],
        "attributes": [
            sanitize_free_text(str(a), max_length=40).clean_text
            for a in detail.get("attributes", [])
        ],
        "reviews": sanitized_reviews,
        "_metadata": {
            "has_injection": has_injection,
            "flagged_reasons": flagged_reasons,
        },
    }


def sanitize_search_results(results: list[Any]) -> list[dict[str, Any]]:
    """Project list of search results to whitelisted structure."""
    sanitized: list[dict[str, Any]] = []
    for item in results:
        item_dict = item if isinstance(item, dict) else item.__dict__
        sanitized.append(
            {
                "provider_id": str(item_dict.get("provider_id", "")),
                "name": sanitize_free_text(
                    str(item_dict.get("name", "")), max_length=60
                ).clean_text,
                "service": str(item_dict.get("service", "")),
                "area": str(item_dict.get("area", "")),
                "price": int(item_dict.get("price", 0)),
                "rating": float(item_dict.get("rating", 0.0)),
                "slots": [str(s) for s in item_dict.get("slots", [])],
                "attributes": [
                    sanitize_free_text(str(a), max_length=40).clean_text
                    for a in item_dict.get("attributes", [])
                ],
            }
        )
    return sanitized
