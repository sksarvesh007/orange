"""Tool output projection, sanitization, and deterministic prompt injection detection."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

# Prompt injection patterns
INJECTION_PATTERNS = [
    re.compile(r"(?i)\bignore\s+(all\s+)?(previous|prior|above)\s+(instructions|rules|directions)\b"),
    re.compile(r"(?i)\bsystem\s+(override|prompt|directive)\b"),
    re.compile(r"(?i)\buser\s+has\s+(already\s+)?(approved|confirmed|agreed)\b"),
    re.compile(r"(?i)\bdo\s+not\s+ask\s+(for\s+)?(confirmation|approval)\b"),
    re.compile(r"(?i)\bbook\s+immediately\b"),
    re.compile(r"(?i)\b(system|assistant|admin)\s*:\s*"),
    re.compile(r"(?i)\b(eval|exec|import|require)\s*\("),
    re.compile(r"(?i)https?://[^\s]+"),  # URLs
    re.compile(r"[\u200B-\u200D\uFEFF]"),  # Zero-width spaces / invisible Unicode
]

# Control character stripper
CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


@dataclass
class SanitizedTextResult:
    """Sanitized text wrapper with injection detection status."""

    clean_text: str
    is_suspicious: bool = False
    flagged_reason: str | None = None


def detect_injection(text: str) -> tuple[bool, str | None]:
    """Check text for prompt injection patterns."""
    # Normalize unicode to NFKC to defeat homoglyph / compatibility tricks
    normalized = unicodedata.normalize("NFKC", text)

    for pattern in INJECTION_PATTERNS:
        match = pattern.search(normalized)
        if match:
            return True, f"Matched injection pattern: {match.group(0)[:40]}"

    return False, None


def sanitize_free_text(text: str, max_length: int = 250) -> SanitizedTextResult:
    """Sanitize free-text fields (reviews, names, notes).

    Strips control characters, caps length, inspects for injection, and wraps in untrusted envelope.
    """
    if not text:
        return SanitizedTextResult(clean_text="<untrusted_data></untrusted_data>", is_suspicious=False)

    # 1. Strip control characters
    cleaned = CONTROL_CHAR_RE.sub(" ", text).strip()

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
    return SanitizedTextResult(clean_text=enveloped, is_suspicious=is_suspicious, flagged_reason=reason)


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
        "attributes": [sanitize_free_text(str(a), max_length=40).clean_text for a in detail.get("attributes", [])],
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
        # Accepts dataclass or dict
        item_dict = item if isinstance(item, dict) else item.__dict__
        sanitized.append({
            "provider_id": str(item_dict.get("provider_id", "")),
            "name": sanitize_free_text(str(item_dict.get("name", "")), max_length=60).clean_text,
            "service": str(item_dict.get("service", "")),
            "area": str(item_dict.get("area", "")),
            "price": int(item_dict.get("price", 0)),
            "rating": float(item_dict.get("rating", 0.0)),
            "slots": [str(s) for s in item_dict.get("slots", [])],
            "attributes": [sanitize_free_text(str(a), max_length=40).clean_text for a in item_dict.get("attributes", [])],
        })
    return sanitized
