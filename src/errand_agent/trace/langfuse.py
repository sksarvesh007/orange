"""Langfuse trace exporter sink implementing TraceSink port (Section 7A).

Exports sanitized, redacted spans to self-hosted or cloud Langfuse instance.
Uses standard Langfuse REST ingestion API v2:
- Grouped by session_id
- Spans for user input, model generation, tool calls, policy gate evaluations, and side effects
- PII is strictly redacted BEFORE payload serialization
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from errand_agent.trace.models import TraceEvent
from errand_agent.trace.redact import redact_event
from errand_agent.trace.sink import TraceSink


class LangfuseSink(TraceSink):
    """Langfuse HTTP ingestion sink with pre-export redaction."""

    def __init__(
        self,
        public_key: str | None = None,
        secret_key: str | None = None,
        host: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        enabled: bool = True,
    ) -> None:
        self.public_key: str = public_key or os.getenv("LANGFUSE_PUBLIC_KEY") or ""
        self.secret_key: str = secret_key or os.getenv("LANGFUSE_SECRET_KEY") or ""
        raw_host: str = host or os.getenv("LANGFUSE_HOST") or "http://localhost:3000"
        self.host: str = raw_host.rstrip("/")
        self._http_client = http_client
        self.enabled: bool = enabled and bool(self.public_key and self.secret_key)
        self._buffer: list[dict[str, Any]] = []

    async def emit(self, event: TraceEvent) -> None:
        """Format and buffer redacted trace event as Langfuse span/event."""
        if not self.enabled:
            return

        # 1. Mandatory Redaction Step (Must-Have #8)
        redacted = redact_event(event)

        timestamp_iso = redacted.timestamp.isoformat()
        body_data = redacted.model_dump(mode="json")

        # Map TraceEvent into Langfuse ingestion item
        item: dict[str, Any] = {
            "id": redacted.event_id,
            "type": "span-create",
            "body": {
                "id": redacted.event_id,
                "traceId": redacted.run_id,
                "name": redacted.event_type,
                "startTime": timestamp_iso,
                "metadata": {
                    "session_id": redacted.session_id,
                    "user_id_hash": redacted.user_id_hash,
                    "data": body_data.get("data", {}),
                },
            },
        }

        # If model invocation, format as generation-create
        if redacted.event_type == "model_invocation":
            d = body_data.get("data", {})
            item["type"] = "generation-create"
            item["body"]["model"] = d.get("model", "scripted")
            item["body"]["usage"] = {
                "promptTokens": d.get("prompt_tokens", 0),
                "completionTokens": d.get("completion_tokens", 0),
                "totalTokens": d.get("prompt_tokens", 0) + d.get("completion_tokens", 0),
            }

        self._buffer.append(item)
        if len(self._buffer) >= 20:
            await self.flush()

    async def flush(self) -> None:
        """Flush buffered spans to Langfuse API."""
        if not self.enabled or not self._buffer:
            return

        batch = list(self._buffer)
        self._buffer.clear()

        payload = {"batch": batch}
        auth = (self.public_key, self.secret_key)

        try:
            if self._http_client:
                await self._http_client.post(
                    f"{self.host}/api/public/ingestion",
                    json=payload,
                    auth=auth,
                    timeout=5.0,
                )
            else:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"{self.host}/api/public/ingestion",
                        json=payload,
                        auth=auth,
                        timeout=5.0,
                    )
        except Exception:
            # Observability failures must never crash the operational agent workflow
            pass

    async def close(self) -> None:
        await self.flush()
