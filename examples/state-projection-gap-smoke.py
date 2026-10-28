#!/usr/bin/env python3
"""Smoke-test active-state Next Action vs Todo projection consistency."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.quota import build_quota_should_run, render_quota_should_run_markdown  # noqa: E402
from loopx.state_projection import state_projection_gap_warning  # noqa: E402
from loopx.state_refresh import refresh_state_run, render_state_refresh_markdown  # noqa: E402


GOAL_ID = "state-projection-gap-fixture"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_registry(root: Path, state_text: str) -> tuple[Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = Path(".codex/goals") / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    (project / state_file).parent.mkdir(parents=True, exist_ok=True)
    (project / state_file).write_text(state_text, encoding="utf-8")
    registry_path = project / ".loopx" / "registry.json"
    write_json(
        registry_path,
        {
            "schema_version": 1,
            "common_runtime_root": str(runtime),
            "goals": [
                {
                    "id": GOAL_ID,
                    "domain": "projection-fixture",
                    "status": "active",
                    "repo": str(project),
                    "state_file": str(state_file),
                    "adapter": {"kind": "harness_self_improvement", "status": "connected"},
                    "quota": {"compute": 1.0, "window_hours": 24},
                }
            ],
        },
    )
    from loopx.control_plane.testing.canary_harness import import_fixture_authority
    import_fixture_authority(registry_path, GOAL_ID, provider="sqlite")
    return registry_path, runtime


def executable_gap_state() -> str:
    return (
        "---\n"
        "status: active\n"
        "updated_at: 2026-06-16T00:00:00+00:00\n"
        "---\n\n"
        "# Projection Gap Fixture\n\n"
        "## Agent Todo\n\n"
        "- [x] Finish the previous bounded implementation slice.\n\n"
        "## Next Action\n\n"
        "- Run the trace reducer backfill and validate the benchmark compact output.\n"
    )


def test_projection_gap_warning() -> dict:
    gap = state_projection_gap_warning(executable_gap_state())
    assert gap is not None, gap
    assert gap["kind"] == "state_projection_gap", gap
    assert gap["requires_todo_expansion"] is True, gap
    assert gap["agent_open_count"] == 0, gap
    assert gap["user_open_count"] == 0, gap
    assert gap["target_roles"] == ["agent"], gap

    user_gap = state_projection_gap_warning(
        "## Next Action\n\n- Wait for owner approval before uploading anything.\n"
    )
    assert user_gap is not None, user_gap
    assert user_gap["target_roles"] == ["user"], user_gap

    zh_user_gap = state_projection_gap_warning(
        "## Next Action\n\n- 等待用户确认后再继续外部发布。\n"
    )
    assert zh_user_gap is not None, zh_user_gap
    assert zh_user_gap["target_roles"] == ["user"], zh_user_gap

    zh_approval_gap = state_projection_gap_warning(
        "## Next Action\n\n- 待审批后执行公开发布。\n"
    )
    assert zh_approval_gap is not None, zh_approval_gap
    assert zh_approval_gap["target_roles"] == ["user"], zh_approval_gap

    no_gap = state_projection_gap_warning(
        "## Agent Todo\n\n"
        "- [ ] Run the trace reducer backfill.\n\n"
        "## Next Action\n\n"
        "- Run the trace reducer backfill.\n"
    )
    assert no_gap is None, no_gap

    technical_gate_no_gap = state_projection_gap_warning(
        "## Agent Todo\n\n"
        "- [ ] Advance ALE split-control provider/task-data readiness.\n\n"
        "## Next Action\n\n"
        "- Agent: choose exactly one compact no-upload next gate among "
        "baked-task-input scan, task-material readiness, or remote Docker "
        "capacity proof. Keep credentials local; avoid submit and leaderboard "
        "paths.\n"
    )
    assert technical_gate_no_gap is None, technical_gate_no_gap

    benchmark_acceptance_no_gap = state_projection_gap_warning(
        "## Agent Todo\n\n"
        "- [ ] Run the second matched LoopX Turn versus Codex Goal pair.\n\n"
        "## Next Action\n\n"
        "- Run the second matched pair; require both arms to be official and "
        "verify committed receipts leave the next-case gate open.\n"
    )
    assert benchmark_acceptance_no_gap is None, benchmark_acceptance_no_gap

    decision_result_input_no_gap = state_projection_gap_warning(
        "## Agent Todo\n\n"
        "- [ ] Build a read-only projection adapter from compact session facts.\n\n"
        "## Next Action\n\n"
        "- Acceptance: input compact session, event, outcome, decision-result, "
        "and artifact summaries.\n"
    )
    assert decision_result_input_no_gap is None, decision_result_input_no_gap

    chinese_substring_no_gap = state_projection_gap_warning(
        "## Agent Todo\n\n"
        "- [ ] 整理需求文档并确认用户体验字段。\n\n"
        "## Next Action\n\n"
        "- 整理需求文档并确认用户体验字段。\n"
    )
    assert chinese_substring_no_gap is None, chinese_substring_no_gap

    chinese_todo_projection_no_gap = state_projection_gap_warning(
        "## Agent Todo\n\n"
        "- [ ] 同步待办投影并确认 user channel action_required=false。\n\n"
        "## Next Action\n\n"
        "- 同步待办投影并确认 user channel action_required=false。\n"
    )
    assert chinese_todo_projection_no_gap is None, chinese_todo_projection_no_gap

    chinese_agent_instruction_no_gap = state_projection_gap_warning(
        "## Agent Todo\n\n"
        "- [ ] 请检查用户态字段是否被误投影。\n\n"
        "## Next Action\n\n"
        "- 请检查用户态字段是否被误投影。\n"
    )
    assert chinese_agent_instruction_no_gap is None, chinese_agent_instruction_no_gap

    for artifact_text in (
        "Keep/suppress decision packet rows in the runner sidecar.",
        "Validate the policy decision row emitted by the dry-run adapter.",
        "Record the runner decision sidecar as compact technical evidence.",
    ):
        technical_decision_artifact_no_gap = state_projection_gap_warning(
            "## Agent Todo\n\n"
            f"- [ ] {artifact_text}\n\n"
            "## Next Action\n\n"
            f"- {artifact_text}\n"
        )
        assert technical_decision_artifact_no_gap is None, (
            artifact_text,
            technical_decision_artifact_no_gap,
        )
    return gap


def test_refresh_state_warns() -> None:
    with tempfile.TemporaryDirectory(prefix="state-projection-gap-refresh-") as tmp:
        registry_path, _runtime = write_registry(Path(tmp), executable_gap_state())
        payload = refresh_state_run(
            registry_path=registry_path,
            runtime_root_override=None,
            goal_id=GOAL_ID,
            project=None,
            state_file=None,
            classification="state_refreshed",
            recommended_action=None,
            dry_run=True,
            sync_global=False,
        )
        gap = payload.get("state_projection_gap")
        assert isinstance(gap, dict), payload
        assert gap["target_roles"] == ["agent"], payload
        markdown = render_state_refresh_markdown(payload)
        assert "state_projection_gap" in markdown, markdown
        assert "requires_todo_expansion=True" in markdown, markdown


def test_quota_routes_gap_to_projection_repair(gap: dict) -> None:
    status_payload = {
        "ok": True,
        "run_history": {
            "goals": [
                {
                    "id": GOAL_ID,
                    "registry_member": True,
                    "status": "active",
                    "adapter_kind": "harness_self_improvement",
                    "adapter_status": "connected",
                    "quota": {"compute": 1.0, "window_hours": 24},
                    "latest_runs": [
                        {
                            "generated_at": "2026-06-16T00:00:00+00:00",
                            "classification": "state_refreshed",
                            "recommended_action": (
                                "Run the trace reducer backfill and validate the benchmark compact output."
                            ),
                        }
                    ],
                }
            ]
        },
        "attention_queue": {
            "items": [
                {
                    "goal_id": GOAL_ID,
                    "status": "state_refreshed",
                    "waiting_on": "codex",
                    "severity": "action",
                    "source": "latest_run",
                    "recommended_action": (
                        "Run the trace reducer backfill and validate the benchmark compact output."
                    ),
                    "quota": {
                        "compute": 1.0,
                        "slot_minutes": 1,
                        "allowed_slots": 1440,
                        "spent_slots": 0,
                        "state": "eligible",
                        "reason": "eligible fixture",
                    },
                    "project_asset": {
                        "owner": "codex",
                        "next_action": (
                            "Run the trace reducer backfill and validate the benchmark compact output."
                        ),
                        "stop_condition": "stop on fixture boundary",
                        "state_projection_gap": gap,
                        "quota": {
                            "compute": 1.0,
                            "slot_minutes": 1,
                            "allowed_slots": 1440,
                            "spent_slots": 0,
                            "state": "eligible",
                            "reason": "eligible fixture",
                        },
                    },
                    "state_projection_gap": gap,
                }
            ]
        },
    }
    decision = build_quota_should_run(status_payload, goal_id=GOAL_ID)
    assert decision["should_run"] is True, decision
    assert decision["normal_delivery_allowed"] is False, decision
    assert decision["self_repair_allowed"] is True, decision
    assert decision["effective_action"] == "state_projection_gap_repair", decision
    assert decision["heartbeat_recommendation"]["recommended_mode"] == (
        "repair_state_projection_gap"
    ), decision
    assert decision["execution_obligation"]["kind"] == "state_projection_gap_repair", decision
    assert decision["execution_obligation"]["delivery_allowed"] is False, decision
    markdown = render_quota_should_run_markdown(decision)
    assert "state_projection_gap" in markdown, markdown
    assert "effective_action: `state_projection_gap_repair`" in markdown, markdown


def test_quota_revalidates_stale_user_wait_gap_with_current_parser() -> None:
    stale_technical_gap = {
        "schema_version": "state_projection_gap_v0",
        "kind": "state_projection_gap",
        "severity": "warning",
        "requires_todo_expansion": True,
        "agent_open_count": 1,
        "user_open_count": 0,
        "target_roles": ["user"],
        "evidence_count": 1,
        "first_evidence": [
            {
                "kind": "next_action_waits_without_user_todo",
                "target_role": "user",
                "section": "Next Action",
                "text": "Keep/suppress decision packet rows in the runner sidecar.",
            }
        ],
        "recommended_action": "fixture should be suppressed when agent work is open",
    }
    open_agent_todos = {
        "schema_version": "todo_summary_v0",
        "open_count": 1,
        "items": [
            {
                "index": 1,
                "text": "Keep/suppress decision packet rows in the runner sidecar.",
                "status": "open",
                "priority": "P0",
                "task_class": "advancement_task",
            }
        ],
    }
    status_payload = {
        "ok": True,
        "run_history": {
            "goals": [
                {
                    "id": GOAL_ID,
                    "registry_member": True,
                    "status": "active",
                    "adapter_kind": "harness_self_improvement",
                    "adapter_status": "connected",
                    "quota": {"compute": 1.0, "window_hours": 24},
                }
            ]
        },
        "attention_queue": {
            "items": [
                {
                    "goal_id": GOAL_ID,
                    "status": "state_refreshed",
                    "waiting_on": "codex",
                    "severity": "action",
                    "source": "latest_run",
                    "recommended_action": (
                        "Keep/suppress decision packet rows in the runner sidecar."
                    ),
                    "quota": {
                        "compute": 1.0,
                        "slot_minutes": 1,
                        "allowed_slots": 1440,
                        "spent_slots": 0,
                        "state": "eligible",
                        "reason": "eligible fixture",
                    },
                    "project_asset": {
                        "owner": "codex",
                        "next_action": (
                            "Keep/suppress decision packet rows in the runner sidecar."
                        ),
                        "stop_condition": "stop on fixture boundary",
                        "state_projection_gap": stale_technical_gap,
                        "user_todos": {"open_count": 0, "items": []},
                        "agent_todos": open_agent_todos,
                        "quota": {
                            "compute": 1.0,
                            "slot_minutes": 1,
                            "allowed_slots": 1440,
                            "spent_slots": 0,
                            "state": "eligible",
                            "reason": "eligible fixture",
                        },
                    },
                    "state_projection_gap": stale_technical_gap,
                }
            ]
        },
    }
    decision = build_quota_should_run(status_payload, goal_id=GOAL_ID)
    assert decision["should_run"] is True, decision
    assert decision["normal_delivery_allowed"] is True, decision
    assert decision["self_repair_allowed"] is False, decision
    assert "state_projection_gap" not in decision, decision
    assert decision["interaction_contract"]["user_channel"]["action_required"] is False
    assert decision["interaction_contract"]["agent_channel"]["must_attempt"] is True


def main() -> int:
    gap = test_projection_gap_warning()
    test_refresh_state_warns()
    test_quota_routes_gap_to_projection_repair(gap)
    test_quota_revalidates_stale_user_wait_gap_with_current_parser()
    print("state-projection-gap-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
