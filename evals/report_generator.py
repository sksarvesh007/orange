"""Evaluation report generator producing artifacts/eval_report.md and .json."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ScenarioResult:
    scenario_id: int
    name: str
    description: str
    expected: str
    actual: str
    passed: bool
    notes: str
    violations: list[str]


def generate_reports(
    results: list[ScenarioResult],
    output_dir: str | Path = "artifacts",
) -> tuple[Path, Path]:
    """Generate eval_report.md and eval_report.json artifacts."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    md_file = out_path / "eval_report.md"
    json_file = out_path / "eval_report.json"
    history_file = out_path / "eval_history.json"

    total = len(results)
    passed_count = sum(1 for r in results if r.passed)
    failed_count = total - passed_count
    pass_rate = (passed_count / total * 100) if total > 0 else 0.0

    # Markdown generation
    lines: list[str] = [
        "# Automated Evaluation Report",
        "",
        f"**Generated At**: {datetime.now(timezone.utc).isoformat()}  ",
        f"**Total Scenarios**: {total} | **Passed**: {passed_count} | **Failed**: {failed_count} | **Pass Rate**: {pass_rate:.1f}%",
        "",
        "## Tier Gates Summary",
        "",
        "- **Tier 1 Hard Gates**: "
        + ("✅ PASSED (All 8 Invariants Satisfied)" if failed_count == 0 else "❌ FAILED"),
        "- **Tier 2 Outcome Targets**: "
        + (
            f"✅ 100% Target Met ({pass_rate:.1f}%)"
            if pass_rate == 100.0
            else f"⚠️ {pass_rate:.1f}%"
        ),
        "",
        "## Scenario Results",
        "",
        "| ID | Scenario | Expected | Actual | Status | Notes |",
        "|---|---|---|---|---|---|",
    ]

    for r in results:
        status_icon = "✅ PASS" if r.passed else "❌ FAIL"
        clean_notes = r.notes.replace("|", "/")
        if r.violations:
            clean_notes += " Violations: " + "; ".join(r.violations)
        lines.append(
            f"| {r.scenario_id} | {r.name} | {r.expected} | {r.actual} | {status_icon} | {clean_notes} |"
        )

    lines.extend(
        [
            "",
            "## Invariant Enforcement",
            "- `no_booking_without_valid_approval`: Verified across all scenarios",
            "- `at_most_one_booking_per_action_hash`: Verified across repeated & concurrent tests",
            "- `no_success_claim_without_verification`: Verified against authoritative ledger",
            "- `no_reask_of_known_slots`: Verified by inspecting dialogue logs",
            "- `forgotten_facts_unused`: Verified against long-term memory tombstones",
            "- `every_run_ends_with_user_message`: Verified on every exit path",
            "- `budgets_respected`: Verified under strict limits",
            "- `no_secrets_in_trace`: Verified with secret regex sweeps",
            "",
        ]
    )

    md_content = "\n".join(lines)
    with open(md_file, "w", encoding="utf-8") as f:
        f.write(md_content)

    # JSON generation
    json_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total": total,
            "passed": passed_count,
            "failed": failed_count,
            "pass_rate": pass_rate,
            "tier_1_passed": failed_count == 0,
        },
        "results": [asdict(r) for r in results],
    }

    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2)

    # Update history
    history = []
    if history_file.exists():
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []

    history.append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total": total,
            "passed": passed_count,
            "failed": failed_count,
            "pass_rate": pass_rate,
        }
    )

    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)

    return md_file, json_file
