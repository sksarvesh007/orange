"""Trace sink port and concrete adapters."""

from __future__ import annotations

import contextlib
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Sequence

from errand_agent.trace.models import TraceEvent
from errand_agent.trace.redact import redact_event


class TraceSink(ABC):
    """Abstract port for trace sinks."""

    @abstractmethod
    async def emit(self, event: TraceEvent) -> None:
        """Process and store a trace event."""
        pass

    @abstractmethod
    async def flush(self) -> None:
        """Flush any buffered events."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close sink resources."""
        pass


class JsonlSink(TraceSink):
    """Authoritative local JSONL trace sink."""

    def __init__(self, trace_dir: str | Path = "artifacts/traces") -> None:
        self.trace_dir = Path(trace_dir)
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self._open_files: dict[str, Any] = {}

    def _get_file_path(self, run_id: str) -> Path:
        return self.trace_dir / f"{run_id}.jsonl"

    async def emit(self, event: TraceEvent) -> None:
        # Redaction runs BEFORE any sink persistence
        redacted = redact_event(event)
        file_path = self._get_file_path(redacted.run_id)

        line = json.dumps(redacted.model_dump(mode="json")) + "\n"
        # Append-only synchronous file write (fast, local)
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(line)

    async def flush(self) -> None:
        # File writes are direct appends
        pass

    async def close(self) -> None:
        pass


class CompositeSink(TraceSink):
    """Broadcasts events to multiple sinks with failure isolation."""

    def __init__(self, sinks: Sequence[TraceSink]) -> None:
        self.sinks = list(sinks)

    async def emit(self, event: TraceEvent) -> None:
        for sink in self.sinks:
            with contextlib.suppress(Exception):
                await sink.emit(event)

    async def flush(self) -> None:
        for sink in self.sinks:
            with contextlib.suppress(Exception):
                await sink.flush()

    async def close(self) -> None:
        for sink in self.sinks:
            with contextlib.suppress(Exception):
                await sink.close()


class NullSink(TraceSink):
    """No-op sink for tests and benchmarks."""

    async def emit(self, event: TraceEvent) -> None:
        pass

    async def flush(self) -> None:
        pass

    async def close(self) -> None:
        pass
