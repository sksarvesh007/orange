"""Deterministic redaction for traces and logs."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from errand_agent.trace.models import TraceEvent

# Patterns identifying potential secrets and tokens
SECRET_PATTERNS = [
    re.compile(
        r"(?i)(api[_-]?key|secret|token|password|auth|bearer)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-\.]{8,})['\"]?"
    ),
    re.compile(r"(?i)SENTINEL[_-]SECRET[_-]?[a-zA-Z0-9_]*"),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"ghp_[a-zA-Z0-9]{20,}"),
]

# Sensitive keys whose values should be redacted entirely
SENSITIVE_KEY_NAMES = {
    "password",
    "secret",
    "api_key",
    "apikey",
    "auth_token",
    "authorization",
    "access_token",
}


def _redact_string(text: str) -> str:
    """Scrub secrets from text."""
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED_SECRET]", redacted)
    return redacted


def _redact_review(review_text: str) -> str:
    """Redact raw review text to hash and length."""
    h = hashlib.sha256(review_text.encode("utf-8")).hexdigest()[:12]
    return f"[REDACTED_REVIEW: hash={h}, len={len(review_text)}]"


def _sanitize_data(data: Any, key_name: str = "") -> Any:
    """Recursively sanitize data structures."""
    if isinstance(data, dict):
        sanitized: dict[str, Any] = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(sens in k_lower for sens in SENSITIVE_KEY_NAMES):
                sanitized[k] = "[REDACTED_SECRET]"
            elif k_lower == "reviews" and isinstance(v, list):
                # Specific redaction for raw review lists
                sanitized[k] = [
                    _redact_review(str(r)) if isinstance(r, str) else _sanitize_data(r, k)
                    for r in v
                ]
            elif "review" in k_lower and isinstance(v, str):
                sanitized[k] = _redact_review(v)
            else:
                sanitized[k] = _sanitize_data(v, k)
        return sanitized
    elif isinstance(data, list):
        return [_sanitize_data(item, key_name) for item in data]
    elif isinstance(data, str):
        # If this is specifically marked as review content
        if "review" in key_name.lower():
            return _redact_review(data)
        return _redact_string(data)
    else:
        return data


def redact_event(event: TraceEvent) -> TraceEvent:
    """Pure redaction function executed before writing to any sink."""
    sanitized_data = _sanitize_data(event.data)
    return TraceEvent(
        event_id=event.event_id,
        run_id=event.run_id,
        session_id=event.session_id,
        user_id_hash=event.user_id_hash,
        timestamp=event.timestamp,
        event_type=event.event_type,
        data=sanitized_data,
    )
