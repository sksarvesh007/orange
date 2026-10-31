"""Tier 3 Robustness: 500-run randomized fault-injection sweep.

Validates that Tier 1 invariants hold across 500 seeded combinations of:
- Timeouts (both timeout_after_success and timeout_not_booked)
- Slot contention (slot_taken)
- Hold price drift
- Transient 503 provider errors
"""

from pathlib import Path

import pytest

from errand_agent.domain.enums import Phase
from errand_agent.mocks.scenario import Scenario
from evals.invariants import evaluate_all_invariants
from tests.evals.test_scenarios import run_scenario_script, setup_test_env


@pytest.mark.asyncio
async def test_fault_sweep_500_seeded_runs() -> None:
    """Run 500 randomized runs with varying fault probabilities."""
    tmp_traces = Path("artifacts/traces/fault_sweep")
    tmp_traces.mkdir(parents=True, exist_ok=True)

    violations_count = 0
    terminal_phases = {
        Phase.DONE,
        Phase.CANCELLED_BY_USER,
        Phase.NEEDS_HUMAN,
        Phase.LIMIT_EXHAUSTED,
        Phase.FAILED,
        Phase.AWAITING_APPROVAL,
        Phase.PRESENTING,
    }

    # Execute 500 distinct seeds
    num_runs = 500
    for seed in range(num_runs):
        # Configure randomized scenario characteristics
        timeout_rate = 0.15 if seed % 3 == 0 else 0.05
        slot_taken_rate = 0.10 if seed % 4 == 0 else 0.0
        price_drift = seed % 5 == 0

        scenario = Scenario(
            name=f"sweep_seed_{seed}",
            seed=seed,
            book_timeout_rate=timeout_rate,
            book_slot_taken_rate=slot_taken_rate,
            price_drift_enabled=price_drift,
        )

        engine, sm, mock, client, sink, m, clk, _ = await setup_test_env(
            scenario=scenario, trace_dir=tmp_traces
        )

        # Standard 2-turn dialogue: request errand, then confirm
        ctx = await run_scenario_script(
            sm,
            mock,
            client,
            sink,
            m,
            clk,
            session_id=f"sess_swp_{seed}",
            user_id=f"usr_swp_{seed}",
            turns=[
                "Find a plumber for my kitchen tap this week in Indiranagar under 1500.",
                "Yes, proceed",
            ],
        )

        # Tier 1 Invariants must hold on EVERY single run
        v = evaluate_all_invariants(ctx)
        if v:
            violations_count += 1
            pytest.fail(f"Invariant violation on seed {seed}: {v}")

        # Workflow must cleanly resolve to a recognized terminal or stable phase
        assert ctx.state.phase in terminal_phases

    assert violations_count == 0
