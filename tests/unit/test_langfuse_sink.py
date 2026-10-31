"""Unit tests for Langfuse exporter sink (Section 7A).

Verifies:
1. Off-by-default behavior when environment keys are absent.
2. Ingestion payload mapping (traceId, sessionId, generation vs span).
3. Redaction guarantee: PII (phone, email) is redacted BEFORE sending to Langfuse.
4. Failure isolation: network failure does not crash agent.
"""

import json
from datetime import datetime, timezone

import httpx
import pytest

from errand_agent.trace.langfuse import LangfuseSink
from errand_agent.trace.models import TraceEvent


@pytest.fixture
def sample_event() -> TraceEvent:
    return TraceEvent(
        event_id="ev_langfuse_1",
        run_id="run_langfuse_1",
        session_id="sess_langfuse_1",
        user_id_hash="user_abc_hash",
        timestamp=datetime.now(timezone.utc),
        event_type="user_message_received",
        data={"message": "Please call me at 9876543210 regarding my leak at test@example.com"},
    )


@pytest.mark.asyncio
async def test_langfuse_sink_disabled_by_default(sample_event: TraceEvent) -> None:
    """Without keys, Langfuse sink is disabled and does nothing."""
    sink = LangfuseSink(public_key="", secret_key="")
    assert sink.enabled is False
    await sink.emit(sample_event)
    assert len(sink._buffer) == 0


@pytest.mark.asyncio
async def test_langfuse_sink_redacts_pii_before_export(sample_event: TraceEvent) -> None:
    """Phone number and email must be redacted before sending in the batch."""
    captured_payloads: list[dict] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        captured_payloads.append(data)
        return httpx.Response(200, json={"success": True})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        sink = LangfuseSink(
            public_key="pk-lf-test",
            secret_key="sk-lf-test",
            host="http://langfuse.local",
            http_client=http_client,
        )
        assert sink.enabled is True

        await sink.emit(sample_event)
        await sink.flush()

    assert len(captured_payloads) == 1
    batch = captured_payloads[0]["batch"]
    assert len(batch) == 1
    item = batch[0]

    assert item["type"] == "span-create"
    assert item["body"]["traceId"] == "run_langfuse_1"
    meta = item["body"]["metadata"]
    msg_sent = meta["data"]["message"]

    # Redaction assertions
    assert "9876543210" not in msg_sent
    assert "[REDACTED_PHONE]" in msg_sent
    assert "test@example.com" not in msg_sent
    assert "[REDACTED_EMAIL]" in msg_sent


@pytest.mark.asyncio
async def test_langfuse_sink_generation_mapping() -> None:
    """model_invocation events are correctly mapped to generation-create with token usage."""
    captured_payloads: list[dict] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        captured_payloads.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"success": True})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        sink = LangfuseSink(
            public_key="pk-lf-test",
            secret_key="sk-lf-test",
            host="http://langfuse.local",
            http_client=http_client,
        )

        gen_event = TraceEvent(
            event_id="ev_gen_1",
            run_id="run_gen_1",
            session_id="sess_gen_1",
            user_id_hash="user_hash",
            timestamp=datetime.now(timezone.utc),
            event_type="model_invocation",
            data={
                "model": "gpt-4o-mini",
                "prompt_tokens": 120,
                "completion_tokens": 30,
            },
        )
        await sink.emit(gen_event)
        await sink.flush()

    assert len(captured_payloads) == 1
    item = captured_payloads[0]["batch"][0]
    assert item["type"] == "generation-create"
    assert item["body"]["model"] == "gpt-4o-mini"
    assert item["body"]["usage"]["totalTokens"] == 150


@pytest.mark.asyncio
async def test_langfuse_sink_failure_isolation(sample_event: TraceEvent) -> None:
    """Network connection errors to Langfuse must be suppressed and not crash."""

    def error_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Failed to reach Langfuse")

    transport = httpx.MockTransport(error_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        sink = LangfuseSink(
            public_key="pk-lf-test",
            secret_key="sk-lf-test",
            host="http://down.langfuse.local",
            http_client=http_client,
        )
        await sink.emit(sample_event)
        # Flush must complete silently without raising
        await sink.flush()
