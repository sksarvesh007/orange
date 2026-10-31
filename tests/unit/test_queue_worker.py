"""Unit tests for scalable background task queue and worker."""

import pytest

from errand_agent.queue.base import Task
from errand_agent.queue.memory import MemoryTaskQueue
from errand_agent.queue.worker import QueueWorker


@pytest.mark.asyncio
async def test_queue_enqueue_dequeue_ack() -> None:
    q = MemoryTaskQueue()
    task = Task.create("reconcile_booking", {"idempotency_key": "key_123"})

    await q.enqueue(task)
    assert await q.size() == 1

    fetched = await q.dequeue(timeout=0.1)
    assert fetched is not None
    assert fetched.task_id == task.task_id

    await q.ack(fetched.task_id)
    assert q.completed_count == 1
    assert await q.size() == 0


@pytest.mark.asyncio
async def test_queue_nack_and_dead_letter() -> None:
    q = MemoryTaskQueue()
    task = Task.create("export_trace", {"run_id": "r1"}, max_attempts=2)

    await q.enqueue(task)

    # Attempt 1 -> nack requeues
    t1 = await q.dequeue(timeout=0.1)
    assert t1 is not None
    await q.nack(t1, "Network timeout")
    assert len(q.dead_letter_items) == 0
    assert await q.size() == 1

    # Attempt 2 -> nack exceeds max_attempts -> dead letter
    t2 = await q.dequeue(timeout=0.1)
    assert t2 is not None
    await q.nack(t2, "Permanent failure")
    assert len(q.dead_letter_items) == 1
    assert q.dead_letter_items[0][1] == "Permanent failure"
    assert await q.size() == 0


@pytest.mark.asyncio
async def test_queue_worker_execution() -> None:
    q = MemoryTaskQueue()
    worker = QueueWorker(q)

    executed_tasks = []

    async def handle_task(task: Task) -> None:
        executed_tasks.append(task.payload.get("data"))

    worker.register_handler("test_job", handle_task)

    await q.enqueue(Task.create("test_job", {"data": "hello"}))
    await q.enqueue(Task.create("test_job", {"data": "world"}))

    processed1 = await worker.process_one()
    assert processed1 is True
    processed2 = await worker.process_one()
    assert processed2 is True

    assert executed_tasks == ["hello", "world"]
    assert worker.processed_count == 2
    assert worker.failed_count == 0
