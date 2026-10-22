#!/usr/bin/env python3
"""Smoke-test the long-task cadence hint contract."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx.long_task_cadence import (  # noqa: E402
    LONG_TASK_CADENCE_HINT_SCHEMA_VERSION,
    build_long_task_cadence_hint,
    reconcile_long_task_cadence_hint,
)
from loopx.quota import build_quota_should_run  # noqa: E402

POLICY_PATH = REPO_ROOT / "docs/operations/long-task-cadence-policy.md"
DOCS_INDEX = REPO_ROOT / "docs/README.md"
OPERATIONS_INDEX = REPO_ROOT / "docs/operations/README.md"
GETTING_STARTED = REPO_ROOT / "docs/guides/getting-started.md"
INTERACTION_PATTERN = REPO_ROOT / "docs/concepts/interaction-pattern-catalog.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def compact(text: str) -> str:
    return " ".join(text.split())


def assert_contains(text: str, needle: str, label: str) -> None:
    if needle not in text and needle not in compact(text):
        raise AssertionError(f"{label} missing {needle!r}")


def extract_json_block(text: str) -> dict:
    start = text.index("```json", text.index("## Public Fields"))
    body_start = text.index("\n", start) + 1
    end = text.index("```", body_start)
    return json.loads(text[body_start:end])


def single_surface_run() -> dict:
    return {
        "generated_at": "2026-06-26T00:02:00Z",
        "delivery_batch_scale": "single_surface",
        "delivery_outcome": "surface_only",
        "delivery_turn_kind": "contract_only_preparation",
    }


def assert_runtime_hint() -> None:
    recent_runs = [single_surface_run(), single_surface_run()]
    hint = build_long_task_cadence_hint(
        latest_runs=recent_runs,
        quota_state="eligible",
        user_todo_open_count=1,
    )
    assert hint == {
        "schema_version": LONG_TASK_CADENCE_HINT_SCHEMA_VERSION,
        "signal": "thin_progress",
        "recommendation": "widen",
        "reason_codes": ["repeated_surface_only"],
    }, hint

    gated = build_long_task_cadence_hint(
        latest_runs=recent_runs,
        quota_state="operator_gate",
        user_todo_open_count=1,
    )
    assert gated == {
        "schema_version": LONG_TASK_CADENCE_HINT_SCHEMA_VERSION,
        "signal": "blocked",
        "recommendation": "wait",
        "reason_codes": ["quota_state_operator_gate", "open_user_todos_visible"],
    }, gated

    custom_profile = {"degradation_policy": {"small_scale_streak_threshold": 3}}
    below_threshold = build_long_task_cadence_hint(
        execution_profile=custom_profile,
        latest_runs=recent_runs,
        quota_state="eligible",
    )
    assert below_threshold == {
        "schema_version": LONG_TASK_CADENCE_HINT_SCHEMA_VERSION,
        "signal": "thin_progress",
        "recommendation": "keep",
        "reason_codes": ["single_surface_latest_turn"],
    }, below_threshold

    material = build_long_task_cadence_hint(
        latest_runs=[
            {
                "delivery_batch_scale": "implementation",
                "delivery_outcome": "primary_goal_outcome",
                "delivery_turn_kind": "product_path_execution",
            }
        ],
        quota_state="eligible",
    )
    assert material == {
        "schema_version": LONG_TASK_CADENCE_HINT_SCHEMA_VERSION,
        "signal": "material_progress",
        "recommendation": "keep",
        "reason_codes": ["milestone_latest_turn"],
    }, material

    unknown = build_long_task_cadence_hint(latest_runs=[], quota_state="eligible")
    assert unknown == {
        "schema_version": LONG_TASK_CADENCE_HINT_SCHEMA_VERSION,
        "signal": "unknown",
        "recommendation": "keep",
        "reason_codes": ["missing_recent_runs"],
    }, unknown

    reconciled = reconcile_long_task_cadence_hint(
        gated,
        interaction_contract={
            "agent_channel": {
                "must_attempt": True,
                "quiet_noop_allowed": False,
            }
        },
        scheduler_hint={"action": "run_now", "cadence_class": "active_work"},
    )
    assert reconciled == {
        "schema_version": LONG_TASK_CADENCE_HINT_SCHEMA_VERSION,
        "signal": "active_work",
        "recommendation": "keep",
        "reason_codes": ["final_agent_scoped_active_work"],
        "authority": "final_agent_scoped_interaction_and_scheduler",
        "superseded": {
            "signal": "blocked",
            "recommendation": "wait",
            "reason_codes": [
                "quota_state_operator_gate",
                "open_user_todos_visible",
            ],
        },
    }, reconciled

    still_gated = reconcile_long_task_cadence_hint(
        gated,
        interaction_contract={
            "agent_channel": {
                "must_attempt": False,
                "quiet_noop_allowed": False,
            }
        },
        scheduler_hint={
            "action": "backoff_waiting_for_user",
            "cadence_class": "human_gate",
        },
    )
    assert still_gated == gated, still_gated


def assert_status_and_quota_projection() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-cadence-hint-") as tmp:
        root = Path(tmp)
        runtime = root / "runtime"
        project = root / "project"
        registry = project / ".loopx" / "registry.json"
        state = project / ".codex" / "goals" / "cadence" / "ACTIVE_GOAL_STATE.md"
        state.parent.mkdir(parents=True)
        registry.parent.mkdir(parents=True)
        state.write_text(
            "\n".join(
                [
                    "# Active Goal State",
                    "",
                    "- Goal ID: cadence",
                    "- Status: active",
                    "- Agent ID: codex-cadence",
                    "",
                    "## Objective",
                    "Validate cadence hint projection.",
                    "",
                    "## Agent Todo",
                    "",
                    "## Latest Run",
                    "- Run ID: run_thin_2",
                    "- Agent: codex-cadence",
                    "- Generated At: 2026-06-26T00:02:00Z",
                    "- Classification: heartbeat_refresh",
                    "- Delivery Outcome: surface_only",
                    "- Delivery Turn Kind: contract_only_preparation",
                    "- Delivery Batch Scale: single_surface",
                    "",
                    "## Run History",
                    "| run_id | agent | generated_at | classification | delivery_outcome | delivery_turn_kind | delivery_batch_scale | notes |",
                    "| --- | --- | --- | --- | --- | --- | --- | --- |",
                    "| run_thin_2 | codex-cadence | 2026-06-26T00:02:00Z | heartbeat_refresh | surface_only | contract_only_preparation | single_surface | state writeback |",
                    "| run_thin_1 | codex-cadence | 2026-06-26T00:01:00Z | heartbeat_refresh | surface_only | contract_only_preparation | single_surface | state writeback |",
                ]
            ),
            encoding="utf-8",
        )
        registry.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "updated_at": "2026-06-26T00:03:00Z",
                    "common_runtime_root": str(runtime),
                    "goals": [
                        {
                            "id": "cadence",
                            "domain": "cadence-smoke",
                            "status": "active",
                            "repo": str(project),
                            "state_file": ".codex/goals/cadence/ACTIVE_GOAL_STATE.md",
                            "agent_id": "codex-cadence",
                            "coordination": {
                                "agent_model": "peer_v1",
                                "registered_agents": ["codex-cadence"],
                            },
                            "adapter": {
                                "kind": "cadence_hint_fixture_v0",
                                "status": "active",
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        status = subprocess.check_output(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "--registry",
                str(registry),
                "status",
            ],
            cwd=REPO_ROOT,
            text=True,
        )
        status_payload = json.loads(status)
        item = status_payload["attention_queue"]["items"][0]
        hint = item["project_asset"]["long_task_cadence_hint"]
        assert hint["schema_version"] == "cadence_hint_v0", hint
        assert hint["signal"] == "blocked", hint
        assert hint["recommendation"] == "wait", hint
        assert hint["reason_codes"] == ["quota_state_operator_gate"], hint

        quota_payload = build_quota_should_run(
            status_payload,
            goal_id="cadence",
            agent_id="codex-cadence",
        )
        assert quota_payload["long_task_cadence_hint"] == hint, quota_payload


def main() -> int:
    policy = read(POLICY_PATH)
    docs_index = read(DOCS_INDEX)
    operations_index = read(OPERATIONS_INDEX)
    getting_started = read(GETTING_STARTED)
    interaction = read(INTERACTION_PATTERN)

    for required in [
        "Long-Task Cadence Hint",
        "not a scheduler policy",
        "`cadence_hint_v0`",
        "`blocked`, `active_work`, `thin_progress`, `material_progress`, `unknown`",
        "`wait`, `widen`, `keep`",
        "final agent channel",
        "`superseded`",
        "not a perfect measure of actual agent-loop runtime",
        "conversation transcripts",
        "raw local logs",
        "credentials",
        "local absolute paths",
    ]:
        assert_contains(policy, required, "policy")

    projection = extract_json_block(policy)
    hint = projection["long_task_cadence_hint"]
    assert hint == {
        "schema_version": "cadence_hint_v0",
        "signal": "thin_progress",
        "recommendation": "widen",
        "reason_codes": ["repeated_surface_only"],
    }, hint

    assert_contains(docs_index, "operations/README.md", "docs index")
    assert_contains(
        operations_index,
        "Long-task cadence policy",
        "operations index",
    )
    assert_contains(getting_started, "Long-task cadence hint", "getting started")
    assert_contains(interaction, "IP-010 Cadence Hint", "interaction pattern")
    assert_contains(interaction, "thin-progress streak", "interaction pattern")
    assert_runtime_hint()
    assert_status_and_quota_projection()

    print("long-task-cadence-policy-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
