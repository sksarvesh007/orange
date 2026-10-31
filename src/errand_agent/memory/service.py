"""Memory service managing user preferences, provenance, tombstones, and user controls."""

from __future__ import annotations

import uuid

from errand_agent.domain.enums import Provenance
from errand_agent.domain.models import MemoryFactRecord
from errand_agent.policy.extractor import (
    CorrectFactCommand,
    ForgetAllMemoryCommand,
    ForgetFactCommand,
    InspectMemoryCommand,
    parse_memory_command,
)
from errand_agent.store.repositories import MemoryRepository
from errand_agent.utils.clock import Clock, FrozenClock


class MemoryService:
    """Service managing long-term user memory facts with strict provenance."""

    def __init__(self, memory_repo: MemoryRepository, clock: Clock | None = None) -> None:
        self.memory_repo = memory_repo
        self.clock = clock or FrozenClock()

    async def store_stated_fact(
        self,
        user_id: str,
        key: str,
        value: str,
        source_run_id: str | None = None,
    ) -> MemoryFactRecord:
        """Store an explicitly user-stated preference or fact."""
        now = self.clock.now()
        fact = MemoryFactRecord(
            id=f"mem_{uuid.uuid4().hex[:10]}",
            user_id=user_id,
            key=key.strip().lower(),
            value=value.strip(),
            provenance=Provenance.STATED,
            created_at=now,
            updated_at=now,
            last_confirmed_at=now,
            source_run_id=source_run_id,
        )
        await self.memory_repo.save_fact(fact)
        return fact

    async def record_inferred_fact(
        self,
        user_id: str,
        key: str,
        value: str,
        source_run_id: str | None = None,
    ) -> MemoryFactRecord | None:
        """Record an inferred fact. Blocked if a tombstone exists for (user_id, key)."""
        clean_key = key.strip().lower()
        if await self.memory_repo.is_tombstoned(user_id, clean_key):
            # Blocked: user previously asked to forget this fact!
            return None

        now = self.clock.now()
        fact = MemoryFactRecord(
            id=f"mem_{uuid.uuid4().hex[:10]}",
            user_id=user_id,
            key=clean_key,
            value=value.strip(),
            provenance=Provenance.INFERRED,
            created_at=now,
            updated_at=now,
            last_confirmed_at=None,
            source_run_id=source_run_id,
        )
        await self.memory_repo.save_fact(fact)
        return fact

    async def confirm_inferred_fact(self, user_id: str, key: str) -> MemoryFactRecord | None:
        """Promote an inferred fact to confirmed stated status upon explicit user confirmation."""
        clean_key = key.strip().lower()
        fact = await self.memory_repo.get_fact(user_id, clean_key)
        if not fact:
            return None

        fact.provenance = Provenance.STATED
        fact.last_confirmed_at = self.clock.now()
        await self.memory_repo.save_fact(fact)
        return fact

    async def get_active_facts(self, user_id: str) -> list[MemoryFactRecord]:
        """Retrieve all active (non-deleted) facts."""
        return await self.memory_repo.get_facts(user_id, include_deleted=False)

    async def get_fact(self, user_id: str, key: str) -> MemoryFactRecord | None:
        return await self.memory_repo.get_fact(user_id, key.strip().lower())

    async def forget_fact(self, user_id: str, key: str) -> bool:
        """Forget a fact: soft delete and place tombstone preventing re-inference."""
        clean_key = key.strip().lower()
        return await self.memory_repo.delete_fact(user_id, clean_key)

    async def forget_all(self, user_id: str) -> int:
        """Forget everything: tombstones all existing keys."""
        return await self.memory_repo.forget_all(user_id)

    async def handle_user_command(self, user_id: str, utterance: str) -> str | None:
        """Process explicit memory commands deterministically without regex."""
        cmd = parse_memory_command(utterance)
        if not cmd:
            return None

        if isinstance(cmd, InspectMemoryCommand):
            facts = await self.get_active_facts(user_id)
            if not facts:
                return "I do not have any saved preferences or remembered facts about you."
            items = [f"- {f.key}: {f.value} ({f.provenance.value})" for f in facts]
            return "Here is what I remember about you:\n" + "\n".join(items)

        elif isinstance(cmd, ForgetAllMemoryCommand):
            count = await self.forget_all(user_id)
            return f"I have forgotten all {count} saved facts and preferences about you."

        elif isinstance(cmd, ForgetFactCommand):
            deleted = await self.forget_fact(user_id, cmd.key)
            if deleted:
                return f"I have forgotten your preference for '{cmd.key}'."
            return f"I didn't have any saved preference for '{cmd.key}'."

        elif isinstance(cmd, CorrectFactCommand):
            await self.store_stated_fact(user_id, cmd.key, cmd.value)
            return f"I have updated your '{cmd.key}' preference to '{cmd.value}'."

        return None
