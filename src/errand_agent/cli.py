"""Command-line interface for interactive chatting and trace inspection."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import click
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient

from errand_agent.api.app import app

load_dotenv()


@click.group()
def cli() -> None:
    """Errand Agent CLI: interactive chat and trace viewer."""
    pass


@cli.command("chat")
@click.option("--user-id", default="cli_user", help="User ID for the session")
@click.option("--session-id", default=None, help="Existing session ID to resume")
@click.option(
    "--use-llm",
    is_flag=True,
    default=False,
    help="Use external LLM instead of default deterministic scripted model",
)
def chat(user_id: str, session_id: str | None, use_llm: bool) -> None:
    """Start an interactive chat session with the Errand Agent."""
    import os

    if use_llm:
        os.environ["USE_LLM"] = "true"
    sid = session_id or f"sess_{uuid.uuid4().hex[:8]}"
    click.echo(
        click.style(
            f"=== Errand Agent Chat (Session: {sid}, User: {user_id}) ===", fg="cyan", bold=True
        )
    )
    click.echo("Type 'exit' or 'quit' to end.\n")

    async def _loop() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            while True:
                try:
                    user_msg = click.prompt(click.style("You", fg="green", bold=True))
                except (EOFError, KeyboardInterrupt):
                    break

                if user_msg.strip().lower() in ("exit", "quit"):
                    break

                res = await client.post(
                    "/chat",
                    json={"session_id": sid, "user_id": user_id, "message": user_msg},
                )
                if res.status_code == 200:
                    data = res.json()
                    reply = data["reply"]
                    phase = data["phase"]
                    run_id = data["run_id"]
                    click.echo(
                        click.style(f"Agent [{phase}]:", fg="yellow", bold=True) + f" {reply}"
                    )
                    click.echo(click.style(f"  (Trace run_id: {run_id})\n", fg="bright_black"))
                else:
                    click.echo(click.style(f"Error {res.status_code}: {res.text}", fg="red"))

    asyncio.run(_loop())


@cli.group("trace")
def trace_group() -> None:
    """Trace management commands."""
    pass


@trace_group.command("list")
@click.option("--trace-dir", default="artifacts/traces", help="Trace directory path")
def list_traces(trace_dir: str) -> None:
    """List recent execution traces."""
    p = Path(trace_dir)
    if not p.exists():
        click.echo("No traces found.")
        return

    traces = list(p.glob("*.jsonl"))
    if not traces:
        click.echo("No trace files found.")
        return

    click.echo(f"Found {len(traces)} trace files in {trace_dir}:")
    for t in sorted(traces, key=lambda f: f.stat().st_mtime, reverse=True)[:20]:
        click.echo(f" - {t.stem} ({t.stat().st_size} bytes)")


@trace_group.command("pretty")
@click.argument("run_id")
@click.option("--trace-dir", default="artifacts/traces", help="Trace directory path")
def pretty_trace(run_id: str, trace_dir: str) -> None:
    """Pretty-print an execution trace."""
    _print_trace(run_id, trace_dir)


@trace_group.command("view")
@click.argument("run_id")
@click.option("--trace-dir", default="artifacts/traces", help="Trace directory path")
def view_trace(run_id: str, trace_dir: str) -> None:
    """View an execution trace."""
    _print_trace(run_id, trace_dir)


def _print_trace(run_id: str, trace_dir: str) -> None:
    p = Path(trace_dir) / f"{run_id}.jsonl"
    if not p.exists():
        click.echo(click.style(f"Trace {run_id} not found in {trace_dir}", fg="red"))
        return

    click.echo(
        click.style(f"\n================ Trace: {run_id} ================", fg="cyan", bold=True)
    )
    with open(p, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f, 1):
            if not line.strip():
                continue
            ev = json.loads(line)
            ev_type = ev.get("event_type", "unknown")
            ts = ev.get("timestamp", "")
            data = ev.get("data", {})

            color = "white"
            if ev_type == "state_transition":
                color = "blue"
            elif ev_type == "policy_decision":
                color = "green"
            elif ev_type == "approval_event":
                color = "yellow"
            elif ev_type in ("injection_flag", "unknown_error"):
                color = "red"

            click.echo(
                click.style(f"[{idx:02d}] {ts} ", fg="bright_black")
                + click.style(f"[{ev_type.upper()}]", fg=color, bold=True)
            )
            click.echo(json.dumps(data, indent=2))
            click.echo("-" * 50)


if __name__ == "__main__":
    cli()
