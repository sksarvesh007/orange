"""Scalable background worker processing asynchronous tasks from queue."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine

from errand_agent.obs.logging import get_logger
from errand_agent.queue.base import Task, TaskQueue

logger = get_logger("queue_worker")

TaskHandler = Callable[[Task], Coroutine[Any, Any, None]]


class QueueWorker:
    """Consumes tasks from a TaskQueue and dispatches to registered async handlers."""

    def __init__(self, queue: TaskQueue) -> None:
        self.queue = queue
        self._handlers: dict[str, TaskHandler] = {}
        self._running = False
        self._processed_count = 0
        self._failed_count = 0

    def register_handler(self, task_type: str, handler: TaskHandler) -> None:
        """Register an async handler function for a specific task type."""
        self._handlers[task_type] = handler

    async def process_one(self, timeout: float = 0.5) -> bool:
        """Fetch and process a single task from queue. Returns True if task was processed."""
        task = await self.queue.dequeue(timeout=timeout)
        if not task:
            return False

        handler = self._handlers.get(task.task_type)
        if not handler:
            logger.warning("unhandled_task_type", task_id=task.task_id, task_type=task.task_type)
            await self.queue.nack(task, error=f"No handler registered for {task.task_type}")
            self._failed_count += 1
            return True

        try:
            await handler(task)
            await self.queue.ack(task.task_id)
            self._processed_count += 1
            return True
        except Exception as exc:
            logger.error("task_execution_failed", task_id=task.task_id, error=str(exc))
            await self.queue.nack(task, error=str(exc))
            self._failed_count += 1
            return True

    async def run_loop(self, stop_event: asyncio.Event | None = None) -> None:
        """Continually process tasks until stop_event is set."""
        self._running = True
        while self._running:
            if stop_event and stop_event.is_set():
                break
            processed = await self.process_one(timeout=1.0)
            if not processed:
                await asyncio.sleep(0.05)

    def stop(self) -> None:
        """Signal the worker to halt."""
        self._running = False

    @property
    def processed_count(self) -> int:
        return self._processed_count

    @property
    def failed_count(self) -> int:
        return self._failed_count
