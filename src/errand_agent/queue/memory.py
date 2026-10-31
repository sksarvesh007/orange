"""In-memory async task queue for local testing and deterministic evaluation."""

from __future__ import annotations

import asyncio

from errand_agent.queue.base import Task, TaskQueue


class MemoryTaskQueue(TaskQueue):
    """Deterministic in-memory asyncio queue supporting ack and dead-letter handling."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Task] = asyncio.Queue()
        self._in_flight: dict[str, Task] = {}
        self._dead_letter: list[tuple[Task, str]] = []
        self._completed: list[str] = []

    async def enqueue(self, task: Task) -> None:
        await self._queue.put(task)

    async def dequeue(self, timeout: float = 1.0) -> Task | None:
        try:
            task = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            self._in_flight[task.task_id] = task
            return task
        except (asyncio.TimeoutError, TimeoutError):
            return None

    async def ack(self, task_id: str) -> None:
        if task_id in self._in_flight:
            del self._in_flight[task_id]
            self._completed.append(task_id)

    async def nack(self, task: Task, error: str) -> None:
        if task.task_id in self._in_flight:
            del self._in_flight[task.task_id]

        task.attempts += 1
        if task.attempts < task.max_attempts:
            # Requeue for retry
            await self._queue.put(task)
        else:
            # Route to dead letter queue
            self._dead_letter.append((task, error))

    async def size(self) -> int:
        return self._queue.qsize()

    @property
    def dead_letter_items(self) -> list[tuple[Task, str]]:
        return list(self._dead_letter)

    @property
    def completed_count(self) -> int:
        return len(self._completed)
