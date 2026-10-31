"""Unit tests for FastAPI transport endpoints and CLI commands."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from httpx import ASGITransport, AsyncClient

from errand_agent.api.app import app, app_context
from errand_agent.cli import cli
from errand_agent.mocks.provider import MockProviderService
from errand_agent.store.base import Base, create_engine_and_sessionmaker
from errand_agent.tools.provider_client import ProviderClient
from errand_agent.trace.sink import JsonlSink
from errand_agent.utils.clock import FrozenClock


@pytest.fixture(autouse=True)
async def init_app_context(tmp_path: Path):
    engine, sessionmaker = create_engine_and_sessionmaker("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    clock = FrozenClock()
    mock_prov = MockProviderService(clock=clock)
    client = ProviderClient(mock_prov, clock=clock)
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_sink = JsonlSink(trace_dir=trace_dir)

    app_context.session_factory = sessionmaker
    app_context.clock = clock
    app_context.mock_provider = mock_prov
    app_context.provider_client = client
    app_context.trace_dir = trace_dir
    app_context.trace_sink = trace_sink

    yield

    await engine.dispose()


@pytest.mark.asyncio
async def test_healthz_and_readyz() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res_h = await client.get("/healthz")
        assert res_h.status_code == 200
        assert res_h.json() == {"status": "ok"}

        res_r = await client.get("/readyz")
        assert res_r.status_code == 200
        assert res_r.json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_chat_and_trace_viewer_flow() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Chat turn
        res = await client.post(
            "/chat",
            json={
                "session_id": "sess_api_test",
                "user_id": "user_api_test",
                "message": "Find a plumber in Indiranagar this week under 1500.",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["phase"] == "AWAITING_APPROVAL"
        run_id = data["run_id"]

        # 2. Trace JSON endpoint
        res_trace = await client.get(f"/api/trace/{run_id}")
        assert res_trace.status_code == 200
        trace_data = res_trace.json()
        assert trace_data["run_id"] == run_id
        assert len(trace_data["events"]) > 0

        # 3. HTML Trace Viewer endpoint
        res_html = await client.get(f"/trace/{run_id}")
        assert res_html.status_code == 200
        assert "Execution Trace:" in res_html.text
        assert run_id in res_html.text


def test_cli_trace_commands(tmp_path: Path) -> None:
    runner = CliRunner()

    # List empty
    res = runner.invoke(cli, ["trace", "list", "--trace-dir", str(tmp_path)])
    assert res.exit_code == 0

    # Write a sample trace
    sample_trace = tmp_path / "run_sample_123.jsonl"
    ev = {
        "event_id": "e1",
        "run_id": "run_sample_123",
        "session_id": "s1",
        "user_id_hash": "u1_hash",
        "timestamp": "2026-10-31T10:00:00Z",
        "event_type": "state_transition",
        "data": {"from": "COLLECTING", "to": "SEARCHING"},
    }
    sample_trace.write_text(json.dumps(ev) + "\n", encoding="utf-8")

    # List now shows it
    res_list = runner.invoke(cli, ["trace", "list", "--trace-dir", str(tmp_path)])
    assert res_list.exit_code == 0
    assert "run_sample_123" in res_list.output

    # Pretty print
    res_pretty = runner.invoke(
        cli, ["trace", "pretty", "run_sample_123", "--trace-dir", str(tmp_path)]
    )
    assert res_pretty.exit_code == 0
    assert "STATE_TRANSITION" in res_pretty.output
    assert "COLLECTING" in res_pretty.output
