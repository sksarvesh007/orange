"""Transactional repository implementations for PostgreSQL and SQLite."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from errand_agent.domain.enums import ActionType, BookingStatus, Provenance
from errand_agent.domain.models import (
    ApprovalRecord,
    BookingAttemptRecord,
    MemoryFactRecord,
    SessionRecord,
)
from errand_agent.domain.state import WorkflowState
from errand_agent.store.schema import (
    ApprovalModel,
    AuditLogModel,
    BookingAttemptModel,
    MemoryFactModel,
    MemoryTombstoneModel,
    SessionModel,
    TraceRecordModel,
    WorkflowStateModel,
)


class ConcurrencyError(Exception):
    """Raised when an optimistic lock version conflict occurs."""

    pass


class SessionRepository:
    """Session management with optimistic locking."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, session_id: str) -> SessionRecord | None:
        stmt = select(SessionModel).where(SessionModel.id == session_id)
        res = await self.session.execute(stmt)
        model = res.scalar_one_or_none()
        if not model:
            return None
        return SessionRecord(
            id=model.id,
            user_id=model.user_id,
            status=model.status,
            version=model.version,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def create(self, session_id: str, user_id: str) -> SessionRecord:
        now = datetime.now(timezone.utc)
        model = SessionModel(
            id=session_id,
            user_id=user_id,
            status="active",
            version=1,
            created_at=now,
            updated_at=now,
        )
        self.session.add(model)
        await self.session.flush()
        return SessionRecord(
            id=model.id,
            user_id=model.user_id,
            status=model.status,
            version=model.version,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def increment_version(self, session_id: str, expected_version: int) -> int:
        now = datetime.now(timezone.utc)
        stmt = (
            update(SessionModel)
            .where(SessionModel.id == session_id, SessionModel.version == expected_version)
            .values(version=expected_version + 1, updated_at=now)
        )
        res = cast(CursorResult[Any], await self.session.execute(stmt))
        if res.rowcount == 0:
            raise ConcurrencyError(
                f"Session {session_id} concurrency conflict: expected version {expected_version}"
            )
        return expected_version + 1


class WorkflowStateRepository:
    """Persistence for workflow state machine snapshots."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, session_id: str) -> WorkflowState | None:
        stmt = select(WorkflowStateModel).where(WorkflowStateModel.session_id == session_id)
        res = await self.session.execute(stmt)
        model = res.scalar_one_or_none()
        if not model:
            return None
        return WorkflowState.model_validate(model.state_data)

    async def save(self, state: WorkflowState) -> None:
        stmt = select(WorkflowStateModel).where(WorkflowStateModel.session_id == state.session_id)
        res = await self.session.execute(stmt)
        model = res.scalar_one_or_none()

        state_dict = state.model_dump(mode="json")
        now = datetime.now(timezone.utc)

        if model:
            model.intent = state.intent.value
            model.phase = state.phase.value
            model.state_data = state_dict
            model.version = state.version
            model.updated_at = now
        else:
            new_model = WorkflowStateModel(
                session_id=state.session_id,
                intent=state.intent.value,
                phase=state.phase.value,
                state_data=state_dict,
                version=state.version,
                updated_at=now,
            )
            self.session.add(new_model)
        await self.session.flush()


class ApprovalRepository:
    """Approval token management."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, approval: ApprovalRecord) -> None:
        model = ApprovalModel(
            approval_id=approval.approval_id,
            user_id=approval.user_id,
            session_id=approval.session_id,
            action_type=approval.action_type.value,
            provider_id=approval.provider_id,
            slot=approval.slot,
            price=approval.price,
            booking_id=approval.booking_id,
            new_slot=approval.new_slot,
            hold_id=approval.hold_id,
            action_hash=approval.action_hash,
            created_at=approval.created_at,
            expires_at=approval.expires_at,
            consumed_at=approval.consumed_at,
            voided_at=approval.voided_at,
            void_reason=approval.void_reason,
        )
        self.session.add(model)
        await self.session.flush()

    async def get(self, approval_id: str) -> ApprovalRecord | None:
        stmt = select(ApprovalModel).where(ApprovalModel.approval_id == approval_id)
        res = await self.session.execute(stmt)
        model = res.scalar_one_or_none()
        if not model:
            return None
        from errand_agent.utils.clock import ensure_utc

        created_at = ensure_utc(model.created_at)
        expires_at = ensure_utc(model.expires_at)
        assert created_at is not None and expires_at is not None
        return ApprovalRecord(
            approval_id=model.approval_id,
            user_id=model.user_id,
            session_id=model.session_id,
            action_type=ActionType(model.action_type),
            provider_id=model.provider_id,
            slot=model.slot,
            price=model.price,
            booking_id=model.booking_id,
            new_slot=model.new_slot,
            hold_id=model.hold_id,
            action_hash=model.action_hash,
            created_at=created_at,
            expires_at=expires_at,
            consumed_at=ensure_utc(model.consumed_at),
            voided_at=ensure_utc(model.voided_at),
            void_reason=model.void_reason,
        )

    async def find_active_by_hash(
        self, user_id: str, action_hash: str, now: datetime | None = None
    ) -> ApprovalRecord | None:
        from errand_agent.utils.clock import ensure_utc

        check_time = ensure_utc(now) or datetime.now(timezone.utc)
        stmt = (
            select(ApprovalModel)
            .where(
                ApprovalModel.user_id == user_id,
                ApprovalModel.action_hash == action_hash,
                ApprovalModel.consumed_at.is_(None),
                ApprovalModel.voided_at.is_(None),
            )
            .order_by(ApprovalModel.created_at.desc())
        )
        res = await self.session.execute(stmt)
        for model in res.scalars().all():
            expires_at = ensure_utc(model.expires_at)
            assert expires_at is not None
            if expires_at > check_time:
                created_at = ensure_utc(model.created_at)
                assert created_at is not None
                return ApprovalRecord(
                    approval_id=model.approval_id,
                    user_id=model.user_id,
                    session_id=model.session_id,
                    action_type=ActionType(model.action_type),
                    provider_id=model.provider_id,
                    slot=model.slot,
                    price=model.price,
                    booking_id=model.booking_id,
                    new_slot=model.new_slot,
                    hold_id=model.hold_id,
                    action_hash=model.action_hash,
                    created_at=created_at,
                    expires_at=expires_at,
                    consumed_at=ensure_utc(model.consumed_at),
                    voided_at=ensure_utc(model.voided_at),
                    void_reason=model.void_reason,
                )
        return None

    async def consume_atomically_with_booking_attempt(
        self,
        approval_id: str,
        attempt: BookingAttemptRecord,
    ) -> None:
        """Atomically mark approval consumed and persist ledger BookingAttempt (same transaction)."""
        now = datetime.now(timezone.utc)

        # 1. Update approval consumed_at
        stmt = (
            update(ApprovalModel)
            .where(
                ApprovalModel.approval_id == approval_id,
                ApprovalModel.consumed_at.is_(None),
                ApprovalModel.voided_at.is_(None),
            )
            .values(consumed_at=now)
        )
        res = cast(CursorResult[Any], await self.session.execute(stmt))
        if res.rowcount == 0:
            raise ValueError(f"Approval {approval_id} is already consumed, voided, or not found")

        # 2. Insert ledger attempt
        model = BookingAttemptModel(
            idempotency_key=attempt.idempotency_key,
            action_hash=attempt.action_hash,
            user_id=attempt.user_id,
            session_id=attempt.session_id,
            action_type=attempt.action_type.value,
            provider_id=attempt.provider_id,
            slot=attempt.slot,
            hold_id=attempt.hold_id,
            booking_id=attempt.booking_id,
            status=attempt.status.value,
            attempt_count=attempt.attempt_count,
            last_error=attempt.last_error,
            created_at=attempt.created_at,
            updated_at=attempt.updated_at,
        )
        self.session.add(model)
        await self.session.flush()

    async def void(self, approval_id: str, reason: str) -> None:
        now = datetime.now(timezone.utc)
        stmt = (
            update(ApprovalModel)
            .where(ApprovalModel.approval_id == approval_id, ApprovalModel.voided_at.is_(None))
            .values(voided_at=now, void_reason=reason)
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def void_all_for_session(self, session_id: str, reason: str) -> int:
        now = datetime.now(timezone.utc)
        stmt = (
            update(ApprovalModel)
            .where(
                ApprovalModel.session_id == session_id,
                ApprovalModel.consumed_at.is_(None),
                ApprovalModel.voided_at.is_(None),
            )
            .values(voided_at=now, void_reason=reason)
        )
        res = cast(CursorResult[Any], await self.session.execute(stmt))
        await self.session.flush()
        return int(res.rowcount or 0)


class BookingLedgerRepository:
    """Authoritative booking ledger and idempotency key repository."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_idempotency_key(self, idempotency_key: str) -> BookingAttemptRecord | None:
        stmt = select(BookingAttemptModel).where(
            BookingAttemptModel.idempotency_key == idempotency_key
        )
        res = await self.session.execute(stmt)
        model = res.scalar_one_or_none()
        if not model:
            return None
        return BookingAttemptRecord(
            idempotency_key=model.idempotency_key,
            action_hash=model.action_hash,
            user_id=model.user_id,
            session_id=model.session_id,
            action_type=ActionType(model.action_type),
            provider_id=model.provider_id,
            slot=model.slot,
            hold_id=model.hold_id,
            booking_id=model.booking_id,
            status=BookingStatus(model.status),
            attempt_count=model.attempt_count,
            last_error=model.last_error,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def get_by_action_hash(self, action_hash: str) -> list[BookingAttemptRecord]:
        stmt = select(BookingAttemptModel).where(BookingAttemptModel.action_hash == action_hash)
        res = await self.session.execute(stmt)
        models = res.scalars().all()
        return [
            BookingAttemptRecord(
                idempotency_key=m.idempotency_key,
                action_hash=m.action_hash,
                user_id=m.user_id,
                session_id=m.session_id,
                action_type=ActionType(m.action_type),
                provider_id=m.provider_id,
                slot=m.slot,
                hold_id=m.hold_id,
                booking_id=m.booking_id,
                status=BookingStatus(m.status),
                attempt_count=m.attempt_count,
                last_error=m.last_error,
                created_at=m.created_at,
                updated_at=m.updated_at,
            )
            for m in models
        ]

    async def find_pending_or_executing(self) -> list[BookingAttemptRecord]:
        """Find attempts in INTENT, EXECUTING, or TIMEOUT for recovery."""
        stmt = select(BookingAttemptModel).where(
            BookingAttemptModel.status.in_(
                [
                    BookingStatus.INTENT.value,
                    BookingStatus.EXECUTING.value,
                    BookingStatus.TIMEOUT.value,
                ]
            )
        )
        res = await self.session.execute(stmt)
        models = res.scalars().all()
        return [
            BookingAttemptRecord(
                idempotency_key=m.idempotency_key,
                action_hash=m.action_hash,
                user_id=m.user_id,
                session_id=m.session_id,
                action_type=ActionType(m.action_type),
                provider_id=m.provider_id,
                slot=m.slot,
                hold_id=m.hold_id,
                booking_id=m.booking_id,
                status=BookingStatus(m.status),
                attempt_count=m.attempt_count,
                last_error=m.last_error,
                created_at=m.created_at,
                updated_at=m.updated_at,
            )
            for m in models
        ]

    async def save(self, attempt: BookingAttemptRecord) -> None:
        model = BookingAttemptModel(
            idempotency_key=attempt.idempotency_key,
            action_hash=attempt.action_hash,
            user_id=attempt.user_id,
            session_id=attempt.session_id,
            action_type=attempt.action_type.value,
            provider_id=attempt.provider_id,
            slot=attempt.slot,
            hold_id=attempt.hold_id,
            booking_id=attempt.booking_id,
            status=attempt.status.value,
            attempt_count=attempt.attempt_count,
            last_error=attempt.last_error,
            created_at=attempt.created_at,
            updated_at=attempt.updated_at,
        )
        await self.session.merge(model)
        await self.session.flush()

    async def update_status(
        self,
        idempotency_key: str,
        status: BookingStatus,
        booking_id: str | None = None,
        error: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        values: dict[str, Any] = {
            "status": status.value,
            "updated_at": now,
        }
        if booking_id is not None:
            values["booking_id"] = booking_id
        if error is not None:
            values["last_error"] = error

        stmt = (
            update(BookingAttemptModel)
            .where(BookingAttemptModel.idempotency_key == idempotency_key)
            .values(**values)
        )
        await self.session.execute(stmt)
        await self.session.flush()


class MemoryRepository:
    """Long-term memory repository with stated/inferred facts and tombstones."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_facts(
        self, user_id: str, include_deleted: bool = False
    ) -> list[MemoryFactRecord]:
        query = select(MemoryFactModel).where(MemoryFactModel.user_id == user_id)
        if not include_deleted:
            query = query.where(MemoryFactModel.deleted_at.is_(None))
        res = await self.session.execute(query)
        models = res.scalars().all()
        return [
            MemoryFactRecord(
                id=m.id,
                user_id=m.user_id,
                key=m.key,
                value=m.value,
                provenance=Provenance(m.provenance),
                created_at=m.created_at,
                updated_at=m.updated_at,
                last_confirmed_at=m.last_confirmed_at,
                deleted_at=m.deleted_at,
                source_run_id=m.source_run_id,
            )
            for m in models
        ]

    async def get_fact(self, user_id: str, key: str) -> MemoryFactRecord | None:
        stmt = select(MemoryFactModel).where(
            MemoryFactModel.user_id == user_id,
            MemoryFactModel.key == key,
            MemoryFactModel.deleted_at.is_(None),
        )
        res = await self.session.execute(stmt)
        m = res.scalar_one_or_none()
        if not m:
            return None
        return MemoryFactRecord(
            id=m.id,
            user_id=m.user_id,
            key=m.key,
            value=m.value,
            provenance=Provenance(m.provenance),
            created_at=m.created_at,
            updated_at=m.updated_at,
            last_confirmed_at=m.last_confirmed_at,
            deleted_at=m.deleted_at,
            source_run_id=m.source_run_id,
        )

    async def save_fact(self, fact: MemoryFactRecord) -> None:
        # Check if already exists for user and key
        stmt = select(MemoryFactModel).where(
            MemoryFactModel.user_id == fact.user_id,
            MemoryFactModel.key == fact.key,
        )
        res = await self.session.execute(stmt)
        existing = res.scalar_one_or_none()
        now = datetime.now(timezone.utc)

        if existing:
            existing.value = fact.value
            existing.provenance = fact.provenance.value
            existing.updated_at = now
            existing.last_confirmed_at = fact.last_confirmed_at
            existing.deleted_at = None
            existing.source_run_id = fact.source_run_id
        else:
            model = MemoryFactModel(
                id=fact.id,
                user_id=fact.user_id,
                key=fact.key,
                value=fact.value,
                provenance=fact.provenance.value,
                created_at=fact.created_at,
                updated_at=now,
                last_confirmed_at=fact.last_confirmed_at,
                deleted_at=None,
                source_run_id=fact.source_run_id,
            )
            self.session.add(model)
        await self.session.flush()

    async def delete_fact(self, user_id: str, key: str) -> bool:
        """Soft delete fact and insert tombstone."""
        now = datetime.now(timezone.utc)
        stmt = (
            update(MemoryFactModel)
            .where(
                MemoryFactModel.user_id == user_id,
                MemoryFactModel.key == key,
                MemoryFactModel.deleted_at.is_(None),
            )
            .values(deleted_at=now)
        )
        res = cast(CursorResult[Any], await self.session.execute(stmt))
        # Add tombstone
        tombstone = MemoryTombstoneModel(user_id=user_id, key=key, tombstoned_at=now)
        await self.session.merge(tombstone)
        await self.session.flush()
        return bool((res.rowcount or 0) > 0)

    async def forget_all(self, user_id: str) -> int:
        now = datetime.now(timezone.utc)
        # Retrieve all active keys
        stmt_keys = select(MemoryFactModel.key).where(
            MemoryFactModel.user_id == user_id, MemoryFactModel.deleted_at.is_(None)
        )
        res = await self.session.execute(stmt_keys)
        keys = res.scalars().all()

        stmt = (
            update(MemoryFactModel)
            .where(MemoryFactModel.user_id == user_id, MemoryFactModel.deleted_at.is_(None))
            .values(deleted_at=now)
        )
        res_del = cast(CursorResult[Any], await self.session.execute(stmt))

        for k in keys:
            tomb = MemoryTombstoneModel(user_id=user_id, key=k, tombstoned_at=now)
            await self.session.merge(tomb)

        await self.session.flush()
        return int(res_del.rowcount or 0)

    async def is_tombstoned(self, user_id: str, key: str) -> bool:
        stmt = select(MemoryTombstoneModel).where(
            MemoryTombstoneModel.user_id == user_id,
            MemoryTombstoneModel.key == key,
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none() is not None


class AuditLogRepository:
    """Append-only audit log repository."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def log(
        self,
        session_id: str,
        user_id: str,
        event_type: str,
        payload: dict[str, Any],
        action_hash: str | None = None,
        idempotency_key: str | None = None,
        actor: str = "orchestrator",
    ) -> None:
        model = AuditLogModel(
            id=str(uuid.uuid4()),
            ts=datetime.now(timezone.utc),
            session_id=session_id,
            user_id=user_id,
            event_type=event_type,
            action_hash=action_hash,
            idempotency_key=idempotency_key,
            payload=payload,
            actor=actor,
        )
        self.session.add(model)
        await self.session.flush()


class TraceIndexRepository:
    """Index of execution traces."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save(
        self,
        run_id: str,
        session_id: str,
        user_id_hash: str,
        start_time: datetime,
        outcome: str,
        file_path: str,
        end_time: datetime | None = None,
        event_count: int = 0,
    ) -> None:
        model = TraceRecordModel(
            run_id=run_id,
            session_id=session_id,
            user_id_hash=user_id_hash,
            start_time=start_time,
            end_time=end_time,
            outcome=outcome,
            event_count=event_count,
            file_path=file_path,
        )
        await self.session.merge(model)
        await self.session.flush()

    async def get(self, run_id: str) -> TraceRecordModel | None:
        stmt = select(TraceRecordModel).where(TraceRecordModel.run_id == run_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()
