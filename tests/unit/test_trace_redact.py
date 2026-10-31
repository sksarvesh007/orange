"""Unit tests for trace event redaction and JsonlSink."""

import json
from pathlib import Path

import pytest

from errand_agent.trace.models import TraceEvent, hash_user_id
from errand_agent.trace.redact import redact_event
from errand_agent.trace.sink import JsonlSink


def test_redact_secrets_and_sentinels() -> None:
    event = TraceEvent(
        event_id="e1",
        run_id="run_101",
        session_id="sess_101",
        user_id_hash=hash_user_id("user_secret_test"),
        event_type="tool_call",
        data={
            "api_key": "sk-1234567890abcdef1234567890",
            "notes": "Here is a secret: SENTINEL_SECRET_TOKEN_9999",
            "nested": {
                "auth_token": "bearer xyz789secret",
                "normal": "clean value",
            },
        },
    )

    redacted = redact_event(event)

    assert "sk-1234567890abcdef1234567890" not in str(redacted.data)
    assert "SENTINEL_SECRET_TOKEN_9999" not in str(redacted.data)
    assert redacted.data["api_key"] == "[REDACTED_SECRET]"
    assert redacted.data["nested"]["auth_token"] == "[REDACTED_SECRET]"
    assert redacted.data["nested"]["normal"] == "clean value"


def test_redact_raw_reviews_to_hash_and_length() -> None:
    review_text = "The technician was amazing and arrived on time."
    event = TraceEvent(
        event_id="e2",
        run_id="run_102",
        session_id="sess_102",
        user_id_hash=hash_user_id("u1"),
        event_type="tool_result",
        data={
            "provider_id": "P-101",
            "reviews": [review_text],
        },
    )

    redacted = redact_event(event)
    redacted_reviews = redacted.data["reviews"]
    assert len(redacted_reviews) == 1
    assert review_text not in redacted_reviews[0]
    assert "[REDACTED_REVIEW: hash=" in redacted_reviews[0]
    assert f"len={len(review_text)}" in redacted_reviews[0]


@pytest.mark.asyncio
async def test_jsonl_sink_writes_redacted_events(tmp_path: Path) -> None:
    sink = JsonlSink(trace_dir=tmp_path)
    sentinel = "SENTINEL_SECRET_DO_NOT_LEAK"

    event = TraceEvent(
        event_id="e3",
        run_id="run_103",
        session_id="sess_103",
        user_id_hash=hash_user_id("u2"),
        event_type="policy_decision",
        data={
            "token": sentinel,
            "decision": "allow",
        },
    )

    await sink.emit(event)
    await sink.flush()

    trace_file = tmp_path / "run_103.jsonl"
    assert trace_file.exists()

    content = trace_file.read_text(encoding="utf-8")
    assert sentinel not in content
    assert "[REDACTED_SECRET]" in content

    parsed = json.loads(content.strip())
    assert parsed["run_id"] == "run_103"
    assert parsed["event_type"] == "policy_decision"
