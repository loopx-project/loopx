#!/usr/bin/env python3
"""Regression for status/diagnose after consecutive status-neutral runs."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from loopx.diagnose import collect_diagnosis  # noqa: E402
from loopx.control_plane.scheduler.execution_context import (  # noqa: E402
    GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
)
from loopx.quota import (  # noqa: E402
    QUOTA_MONITOR_POLL_CLASSIFICATION,
    QUOTA_SLOT_SPENT_CLASSIFICATION,
    QUOTA_SLOT_VOIDED_CLASSIFICATION,
    build_quota_should_run,
)
from loopx.status import collect_status  # noqa: E402


GOAL_ID = "neutral-window-connected-delivery"
AGENT_LANE_GOAL_ID = "neutral-window-agent-lane-recommendation"
AGENT_ID = "codex-main-control"
REAL_CLASSIFICATION = "autonomous_replan_recorded_stable_monitor"
REAL_ACTION = "Quiet monitor only until a material stable monitor transition appears."
DISPLAY_ACTION = (
    "No immediate agent work; keep the monitor quiet until a material stable monitor transition appears."
)
AGENT_TODO = (
    "[P1] Monitor the stable signal; write back only a material transition, regression, or blocker."
)
STALE_GOAL_ACTION = "Stale docs-governance cap action."
AGENT_LANE_ACTION = (
    "Closed stale docs-governance cap; continue with next projected runnable todo."
)
AGENT_LANE_TODO = "[P2-repair] Align quota/status recommended-action projection."


def write_registry(root: Path) -> Path:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"

    (project / Path(state_file).parent).mkdir(parents=True, exist_ok=True)
    (project / state_file).write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Neutral Window Fixture\n\n"
        "## Agent Todo\n\n"
        f"- [ ] {AGENT_TODO}\n"
        "  <!-- loopx:todo todo_id=todo_monitor_fixture status=open "
        "task_class=continuous_monitor action_kind=stable_signal_monitor -->\n",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "status-neutral-window-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {
                            "kind": "fixture_connected_delivery_v0",
                            "status": "connected-delivery",
                        },
                        "quota": {
                            "compute": 1.0,
                            "window_hours": 24,
                        },
                        "coordination": {
                            "write_scope": ["docs/**"],
                        },
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path


def write_agent_lane_registry(root: Path) -> Path:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{AGENT_LANE_GOAL_ID}/ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"

    (project / Path(state_file).parent).mkdir(parents=True, exist_ok=True)
    (project / state_file).write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:03:00+00:00\n"
        "---\n\n"
        "# Agent Lane Recommendation Fixture\n\n"
        "## Next Action\n\n"
        "Implement broad primary app-server guard.\n\n"
        "## Agent Todo\n\n"
        f"- [ ] {AGENT_LANE_TODO}\n"
        "  <!-- loopx:todo todo_id=todo_agent_lane_repair status=open "
        "task_class=advancement_task action_kind=repair_recommended_action_projection "
        f"claimed_by={AGENT_ID} -->\n",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": AGENT_LANE_GOAL_ID,
                        "domain": "status-neutral-window-agent-lane-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {
                            "kind": "fixture_connected_delivery_v0",
                            "status": "connected-delivery",
                        },
                        "quota": {
                            "compute": 1.0,
                            "window_hours": 24,
                        },
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
                            "write_scope": ["loopx/**", "examples/**"],
                        },
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path


def append_run(
    root: Path,
    *,
    generated_at: str,
    classification: str,
    action: str,
    goal_id: str = GOAL_ID,
    **extra: object,
) -> None:
    run_dir = root / "runtime" / "goals" / goal_id / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    compact_time = generated_at.replace("-", "").replace(":", "")
    json_path = run_dir / f"{compact_time}-{classification}.json"
    markdown_path = run_dir / f"{compact_time}-{classification}.md"
    record = {
        "generated_at": generated_at,
        "goal_id": goal_id,
        "classification": classification,
        "recommended_action": action,
        "health_check": "status-neutral-window fixture run",
        "delivery_batch_scale": "single_surface",
        "delivery_outcome": "outcome_progress",
    }
    record.update(extra)
    json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(f"# {classification}\n", encoding="utf-8")
    with (run_dir / "index.jsonl").open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    **record,
                    "json_path": str(json_path),
                    "markdown_path": str(markdown_path),
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def write_runs(root: Path) -> None:
    append_run(
        root,
        generated_at="2026-01-01T00:00:00+00:00",
        classification=REAL_CLASSIFICATION,
        action=REAL_ACTION,
    )
    for index in range(1, 7):
        append_run(
            root,
            generated_at=f"2026-01-01T00:0{index}:00+00:00",
            classification=QUOTA_MONITOR_POLL_CLASSIFICATION,
            action="monitor poll only; no status transition",
        )
    append_run(
        root,
        generated_at="2026-01-01T00:07:00+00:00",
        classification=QUOTA_SLOT_SPENT_CLASSIFICATION,
        action="quota accounting only; no status transition",
    )
    append_run(
        root,
        generated_at="2026-01-01T00:08:00+00:00",
        classification=QUOTA_SLOT_VOIDED_CLASSIFICATION,
        action="quota void accounting only; no status transition",
    )


def write_agent_lane_runs(root: Path) -> None:
    append_run(
        root,
        goal_id=AGENT_LANE_GOAL_ID,
        generated_at="2026-01-01T00:01:00+00:00",
        classification="stale_goal_status",
        action=STALE_GOAL_ACTION,
    )
    append_run(
        root,
        goal_id=AGENT_LANE_GOAL_ID,
        generated_at="2026-01-01T00:04:00+00:00",
        classification="stale_projected_todo_closed",
        action=AGENT_LANE_ACTION,
        progress_scope="agent_lane",
        agent_id=AGENT_ID,
        delivery_batch_scale="test_only",
    )
    append_run(
        root,
        goal_id=AGENT_LANE_GOAL_ID,
        generated_at="2026-01-01T00:05:00+00:00",
        classification=QUOTA_SLOT_SPENT_CLASSIFICATION,
        action="quota accounting only; no status transition",
        agent_id=AGENT_ID,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-status-neutral-window-") as tmp:
        root = Path(tmp)
        registry_path = write_registry(root)
        write_runs(root)

        status = collect_status(
            registry_path=registry_path,
            runtime_root_override=str(root / "runtime"),
            limit=5,
        )
        item = status["attention_queue"]["items"][0]
        assert item["goal_id"] == GOAL_ID, item
        assert item["status"] == REAL_CLASSIFICATION, item
        assert item["waiting_on"] == "monitor_signal", item
        assert item["severity"] == "watch", item
        assert item["execution_waiting_on"] == "codex", item
        assert item["source"] == "latest_run", item
        assert item["recommended_action"] == DISPLAY_ACTION, item
        assert item["monitor_display"]["no_immediate_agent_work"] is True, item
        project_asset = item["project_asset"]
        assert project_asset["owner"] == "monitor_signal", project_asset
        assert project_asset["support_mode"] == "read_only_observer", project_asset
        assert status["attention_queue"]["needs_codex"] == 0, status["attention_queue"]
        assert status["attention_queue"]["watching_monitor"] == 1, status["attention_queue"]
        assert status["attention_queue"]["autonomous_monitor_candidates"]["open_count"] == 1, (
            status["attention_queue"]
        )
        assert "connect an adapter" not in item["recommended_action"], item

        run_goal = status["run_history"]["goals"][0]
        assert run_goal["latest_status_run"]["classification"] == REAL_CLASSIFICATION, run_goal
        assert len(run_goal["latest_runs"]) == 5, run_goal
        latest_classifications = {run["classification"] for run in run_goal["latest_runs"]}
        assert QUOTA_MONITOR_POLL_CLASSIFICATION in latest_classifications, run_goal
        assert QUOTA_SLOT_SPENT_CLASSIFICATION in latest_classifications, run_goal
        assert QUOTA_SLOT_VOIDED_CLASSIFICATION in latest_classifications, run_goal

        readiness = item["handoff_readiness"]
        assert readiness["post_handoff_latest_run"]["classification"] == REAL_CLASSIFICATION, readiness

        quota = build_quota_should_run(
            status,
            goal_id=GOAL_ID,
            scheduler_execution_context=(
                GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT
            ),
        )
        assert quota["state"] != "operator_gate", quota
        assert quota["effective_action"] == "normal_run", quota
        assert quota["actionable_by_codex"] is True, quota
        lane = quota["work_lane_contract"]
        assert lane["lane"] == "advancement_task", lane
        assert lane["next_lane"] == "continuous_monitor", lane
        assert lane["obligation"] == "repair_monitor_schedule_metadata", lane
        assert lane["monitor_policy"] == "repair_schedule_metadata_before_quiet_wait", lane
        assert lane["monitor_schedule_gap_count"] == 1, lane
        assert quota["requires_user_action"] is False, quota
        user_todo_summary = quota.get("user_todo_summary") if isinstance(quota.get("user_todo_summary"), dict) else {}
        assert user_todo_summary.get("open_count", 0) == 0, quota
        assert quota["status"] == REAL_CLASSIFICATION, quota
        assert quota["waiting_on"] == "monitor_signal", quota
        assert "connect an adapter" not in quota["recommended_action"], quota
        assert quota["recommended_action"] == DISPLAY_ACTION, quota
        assert quota["interaction_contract"]["agent_channel"]["must_attempt"] is True, quota
        assert quota["interaction_contract"]["agent_channel"]["quiet_noop_allowed"] is False, quota
        assert (
            quota["handoff_readiness"]["post_handoff_latest_run"]["classification"]
            == REAL_CLASSIFICATION
        ), quota

        diagnosis = collect_diagnosis(
            registry_path=registry_path,
            runtime_root_override=str(root / "runtime"),
            limit=5,
            goal_id=GOAL_ID,
        )
        selected = diagnosis["selected"]
        assert selected["status"] == REAL_CLASSIFICATION, selected
        assert selected["waiting_on"] == "monitor_signal", selected
        assert selected["severity"] == "watch", selected
        assert selected["machine_signal"] == "agent_work_attention", selected
        assert selected["quota_signals"]["effective_action"] == "normal_run", selected
        assert selected["work_lane_contract"]["obligation"] == "repair_monitor_schedule_metadata", selected
        assert selected["todo_evidence"]["user_open_count"] == 0, selected

    with tempfile.TemporaryDirectory(prefix="loopx-status-neutral-agent-lane-") as tmp:
        root = Path(tmp)
        registry_path = write_agent_lane_registry(root)
        write_agent_lane_runs(root)

        status = collect_status(
            registry_path=registry_path,
            runtime_root_override=str(root / "runtime"),
            limit=5,
        )
        item = status["attention_queue"]["items"][0]
        assert item["goal_id"] == AGENT_LANE_GOAL_ID, item
        assert item["status"] == "stale_goal_status", item
        assert item["latest_run_recommended_action"] == STALE_GOAL_ACTION, item
        assert item["latest_run_recommended_action_source"] == "latest_status_run", item
        assert item["agent_lane_recommendation"]["agent_id"] == AGENT_ID, item
        assert item["agent_lane_recommendation"]["recommended_action"] == AGENT_LANE_ACTION, item

        quota = build_quota_should_run(
            status,
            goal_id=AGENT_LANE_GOAL_ID,
            agent_id=AGENT_ID,
            scheduler_execution_context=(
                GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT
            ),
        )
        assert quota["recommended_action"] == AGENT_LANE_TODO, quota
        assert quota["latest_run_recommended_action"] == AGENT_LANE_ACTION, quota
        assert quota["agent_lane_next_action"]["todo_id"] == "todo_agent_lane_repair", quota
        assert quota["next_action_projection_warning"]["severity"] == "info", quota
        assert quota["interaction_contract"]["agent_channel"]["must_attempt"] is True, quota

    print("status-neutral-run-window-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
