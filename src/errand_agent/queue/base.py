"""Scalable asynchronous task queue interfaces and task models."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class Task:
    """Asynchronous background work item."""

    task_id: str
    task_type: str
    payload: dict[str, Any]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    attempts: int = 0
    max_attempts: int = 3

    @classmethod
    def create(cls, task_type: str, payload: dict[str, Any], max_attempts: int = 3) -> Task:
        return cls(
            task_id=f"tsk_{uuid.uuid4().hex[:12]}",
            task_type=task_type,
            payload=payload,
            max_attempts=max_attempts,
        )


class TaskQueue(ABC):
    """Abstract port for asynchronous background task dispatch and consumption."""

    @abstractmethod
    async def enqueue(self, task: Task) -> None:
        """Push a new task to the queue."""
        pass

    @abstractmethod
    async def dequeue(self, timeout: float = 1.0) -> Task | None:
        """Pull the next available task from the queue."""
        pass

    @abstractmethod
    async def ack(self, task_id: str) -> None:
        """Acknowledge successful completion of a task."""
        pass

    @abstractmethod
    async def nack(self, task: Task, error: str) -> None:
        """Negative acknowledge: requeue or send to dead-letter queue."""
        pass

    @abstractmethod
    async def size(self) -> int:
        """Return count of pending tasks in queue."""
        pass
