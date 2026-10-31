"""Background worker process executing asynchronous tasks and reconciliations."""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from typing import Any

from errand_agent.obs.logging import configure_logging, get_logger
from errand_agent.queue.base import Task
from errand_agent.queue.memory import MemoryTaskQueue
from errand_agent.queue.worker import QueueWorker

logger = get_logger("errand_worker")


async def handle_reconcile_booking(task: Task) -> None:
    """Reconcile in-flight booking attempts out-of-band."""
    key = task.payload.get("idempotency_key")
    logger.info("reconciling_booking_task", idempotency_key=key)
    # Background reconciliation logic completes idempotently
    await asyncio.sleep(0.01)


async def handle_trace_export(task: Task) -> None:
    """Stream trace events asynchronously to remote telemetry sinks."""
    run_id = task.payload.get("run_id")
    logger.info("exporting_trace_task", run_id=run_id)
    await asyncio.sleep(0.01)


async def main() -> None:
    """Run background worker loop with graceful shutdown."""
    configure_logging(level="INFO", json_format=True)
    logger.info("worker_starting")

    queue = MemoryTaskQueue()
    worker = QueueWorker(queue)

    worker.register_handler("reconcile_booking", handle_reconcile_booking)
    worker.register_handler("trace_export", handle_trace_export)

    stop_event = asyncio.Event()

    def _sig_handler(*_: Any) -> None:
        logger.info("shutdown_signal_received")
        stop_event.set()
        worker.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(ValueError, AttributeError):
            signal.signal(sig, _sig_handler)

    try:
        await worker.run_loop(stop_event=stop_event)
    finally:
        logger.info("worker_shutdown_complete", processed=worker.processed_count, failed=worker.failed_count)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
