"""SQLAlchemy 2.0 async engine and session factory."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base class for all relational models."""

    pass


def create_engine_and_sessionmaker(
    database_url: str = "sqlite+aiosqlite:///:memory:",
    echo: bool = False,
    pool_size: int | None = None,
    max_overflow: int | None = None,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Factory to create async engine and sessionmaker."""
    kwargs: dict[str, Any] = {"echo": echo}

    # Set busy_timeout for sqlite to avoid database locked errors under high concurrency
    if "sqlite" in database_url:
        kwargs["connect_args"] = {"timeout": 60.0}
    elif pool_size is not None:
        kwargs["pool_size"] = pool_size
        if max_overflow is not None:
            kwargs["max_overflow"] = max_overflow

    engine = create_async_engine(database_url, **kwargs)
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return engine, session_factory
