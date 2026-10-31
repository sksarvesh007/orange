"""Unit tests for MemoryService: provenance, tombstones, and real commands."""

import pytest
from datetime import datetime, timezone

from errand_agent.domain.enums import Provenance
from errand_agent.memory.service import MemoryService
from errand_agent.store.base import Base, create_engine_and_sessionmaker
from errand_agent.store.repositories import MemoryRepository
from errand_agent.utils.clock import FrozenClock


@pytest.fixture
async def memory_fixture():
    clock = FrozenClock(datetime(2026, 10, 31, 10, 0, 0, tzinfo=timezone.utc))
    engine, sessionmaker = create_engine_and_sessionmaker("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sessionmaker() as session:
        repo = MemoryRepository(session)
        service = MemoryService(repo, clock=clock)
        yield service, session

    await engine.dispose()


@pytest.mark.asyncio
async def test_stated_vs_inferred_provenance(memory_fixture) -> None:
    service, _ = memory_fixture
    u = "user_mem_1"

    # Stored stated fact
    f_stated = await service.store_stated_fact(u, "area", "Indiranagar")
    assert f_stated.provenance == Provenance.STATED
    assert f_stated.last_confirmed_at is not None

    # Stored inferred fact
    f_inferred = await service.record_inferred_fact(u, "time_preference", "mornings")
    assert f_inferred is not None
    assert f_inferred.provenance == Provenance.INFERRED
    assert f_inferred.last_confirmed_at is None

    # Confirm inferred fact
    confirmed = await service.confirm_inferred_fact(u, "time_preference")
    assert confirmed is not None
    assert confirmed.provenance == Provenance.STATED
    assert confirmed.last_confirmed_at is not None


@pytest.mark.asyncio
async def test_forget_creates_tombstone_blocking_reinference(memory_fixture) -> None:
    service, _ = memory_fixture
    u = "user_mem_2"

    await service.store_stated_fact(u, "area", "Koramangala")
    assert await service.get_fact(u, "area") is not None

    # User commands: "forget area"
    reply = await service.handle_user_command(u, "forget area")
    assert "forgotten your preference for 'area'" in reply

    # Fact is now soft-deleted
    assert await service.get_fact(u, "area") is None

    # Attempting to re-infer the forgotten fact is BLOCKED by tombstone!
    blocked = await service.record_inferred_fact(u, "area", "Koramangala")
    assert blocked is None


@pytest.mark.asyncio
async def test_user_memory_commands(memory_fixture) -> None:
    service, _ = memory_fixture
    u = "user_mem_3"

    await service.store_stated_fact(u, "area", "Indiranagar")
    await service.store_stated_fact(u, "preferred_service", "plumber")

    # What do you remember
    reply = await service.handle_user_command(u, "what do you remember about me")
    assert "Indiranagar" in reply
    assert "plumber" in reply

    # Correct X to Y
    corr_reply = await service.handle_user_command(u, "correct area to Whitefield")
    assert "updated your 'area' preference to 'Whitefield'" in corr_reply
    updated = await service.get_fact(u, "area")
    assert updated is not None
    assert updated.value == "Whitefield"

    # Forget everything
    forget_reply = await service.handle_user_command(u, "forget everything")
    assert "forgotten all" in forget_reply

    facts_remaining = await service.get_active_facts(u)
    assert len(facts_remaining) == 0
