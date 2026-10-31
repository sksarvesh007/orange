"""Structured logging with contextual tracing and correlation IDs."""

from __future__ import annotations

import contextvars
import logging
import sys
from typing import Any

import structlog

# Contextual variables for cross-cutting request / run tracing
ctx_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("ctx_run_id", default=None)
ctx_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("ctx_session_id", default=None)
ctx_user_id_hash: contextvars.ContextVar[str | None] = contextvars.ContextVar("ctx_user_id_hash", default=None)


def bind_context(
    run_id: str | None = None,
    session_id: str | None = None,
    user_id_hash: str | None = None,
) -> None:
    """Bind correlation identifiers into current async context."""
    if run_id is not None:
        ctx_run_id.set(run_id)
    if session_id is not None:
        ctx_session_id.set(session_id)
    if user_id_hash is not None:
        ctx_user_id_hash.set(user_id_hash)


def clear_context() -> None:
    """Clear correlation identifiers from current async context."""
    ctx_run_id.set(None)
    ctx_session_id.set(None)
    ctx_user_id_hash.set(None)


def inject_context_vars(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Structlog processor injecting correlation identifiers from contextvars."""
    run_id = ctx_run_id.get()
    session_id = ctx_session_id.get()
    user_id_hash = ctx_user_id_hash.get()

    if run_id:
        event_dict["run_id"] = run_id
    if session_id:
        event_dict["session_id"] = session_id
    if user_id_hash:
        event_dict["user_id_hash"] = user_id_hash

    return event_dict


def configure_logging(level: str = "INFO", json_format: bool = False) -> None:
    """Configure structlog and standard logging pipeline."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        inject_context_vars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_format:
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Retrieve structured bound logger."""
    return structlog.get_logger(name)  # type: ignore[no-any-return]
