#!/usr/bin/env python3
"""Smoke-test refresh-state goal vision budget enforcement."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GOAL_ID = "goal-vision-budget-fixture"
AGENT_ID = "research-curator"


def write_fixture(root: Path) -> tuple[Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_file
    registry_path = project / ".loopx" / "registry.json"

    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Goal Vision Budget Fixture\n\n"
        "## Next Action\n\n"
        "- Refresh the compact goal vision packet.\n\n"
        "## Agent Todo\n\n"
        "- [ ] Monitor the bounded fixture without replacing advancement.\n"
        "  <!-- loopx:todo todo_id=todo_123456789abc status=open "
        "task_class=continuous_monitor action_kind=monitor claimed_by=research-curator "
        "target_key=fixture-monitor cadence=1h next_due_at=2099-01-01T00:00:00Z -->\n",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "goal-vision-budget-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {"kind": "fixture", "status": "connected-read-only"},
                        "authority_sources": [],
                        "coordination": {
                            "registered_agents": [AGENT_ID],
                            "agent_model": "peer_v1",
                        },
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path, runtime, project


def run_cli(
    registry_path: Path,
    runtime: Path,
    *,
    output_format: str = "json",
    vision_path: Path | None = None,
    inline_vision_args: list[str] | None = None,
    check: bool,
    extra_args: list[str] | None = None,
    dry_run: bool = True,
    include_agent_id: bool = True,
    autonomous_replan_recorded: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime),
        "--format",
        output_format,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "goal_vision_patch_recorded",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "outcome_progress",
        "--no-global-sync",
    ]
    if autonomous_replan_recorded:
        command.append("--autonomous-replan-recorded")
    if include_agent_id:
        command.extend(["--agent-id", AGENT_ID])
    if vision_path is not None:
        command.extend(["--agent-vision-json", str(vision_path)])
    if inline_vision_args:
        command.extend(inline_vision_args)
    if extra_args:
        command.extend(extra_args)
    if dry_run:
        command.append("--dry-run")
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=check,
        text=True,
        capture_output=True,
    )


def run_status(registry_path: Path, runtime: Path) -> dict:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            "status",
            "--goal-id",
            GOAL_ID,
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return payload(result)


def run_quota(registry_path: Path, runtime: Path) -> dict:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return payload(result)


def payload(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-goal-vision-budget-") as tmp:
        root = Path(tmp)
        registry_path, runtime, project = write_fixture(root)
        valid_path = root / "valid-vision.json"
        invalid_path = root / "invalid-vision.json"
        drift_path = root / "drift-vision.json"
        explained_drift_path = root / "explained-drift-vision.json"

        write_json(
            valid_path,
            {
                "schema_version": "goal_vision_replan_contract_v0",
                "goal_id": GOAL_ID,
                "agent_id": AGENT_ID,
                "state": "vision_patch_proposed",
                "vision_patch": {
                    "vision_summary": "Map the next evidence frontier and hand off one runnable claim.",
                    "role_scope": "Owns framing; does not run evaluation.",
                    "acceptance_summary": "One successor todo plus evidence references.",
                    "advancement_policy": "repeat_until_closed",
                    "replan_trigger_summary": "Frontier exhausted while acceptance remains open.",
                },
                "path_delta": {
                    "schema_version": "goal_path_delta_v0",
                    "outcome": "continue",
                    "prior_assumption": "The current frontier can satisfy the acceptance summary.",
                    "observed_reality": "A successor todo plus evidence references is now routable.",
                    "retained": ["Keep the successor route and evidence boundary."],
                    "evidence_refs": ["evidence:successor-route-01"],
                },
                "todo_delta": ["create_successor"],
                "validation": {"write_correctness_checked": True},
            },
        )
        missing_baseline = payload(
            run_cli(
                registry_path,
                runtime,
                inline_vision_args=[
                    "--vision-unchanged-reason",
                    "A reason cannot stand in for a first per-agent vision baseline.",
                ],
                check=True,
                dry_run=False,
                autonomous_replan_recorded=False,
            )
        )
        missing_baseline_checkpoint = missing_baseline["vision_checkpoint"]
        assert missing_baseline_checkpoint["required"] is True, missing_baseline
        assert missing_baseline_checkpoint["satisfied"] is False, missing_baseline
        assert missing_baseline_checkpoint["decision"] == "missing_required", (
            missing_baseline
        )
        assert missing_baseline_checkpoint["missing_baseline"] is True, (
            missing_baseline
        )
        assert missing_baseline_checkpoint["required_resolution"] == [
            "write_vision_patch",
        ], missing_baseline
        missing_baseline_quota = run_quota(registry_path, runtime)
        assert missing_baseline_quota["effective_action"] == (
            "autonomous_replan_required"
        ), missing_baseline_quota
        assert missing_baseline_quota["interaction_contract"]["agent_channel"][
            "must_attempt"
        ] is True, missing_baseline_quota
        assert missing_baseline_quota["goal_frontier_projection"]["acceptance_gaps"][
            0
        ]["kind"] == "vision_checkpoint_missing", missing_baseline_quota
        missing_baseline_gap = missing_baseline_quota["goal_frontier_projection"][
            "acceptance_gaps"
        ][0]
        assert missing_baseline_gap["missing_baseline"] is True, missing_baseline_gap
        assert "record an unchanged reason" not in missing_baseline_gap[
            "acceptance_summary"
        ], missing_baseline_gap

        valid = payload(
            run_cli(registry_path, runtime, vision_path=valid_path, check=True)
        )
        assert valid["ok"] is True, valid
        assert valid["dry_run"] is True, valid
        assert valid["autonomous_replan_recorded"] is True, valid
        assert valid["repair_delta_contract"]["delta_present"] is True, valid
        assert "goal_vision_patch" in valid["repair_delta_contract"]["delta_kinds"], valid
        assert valid["agent_vision"]["vision_budget"]["status"] == "ok", valid
        assert valid["agent_vision"]["validation"]["budget_checked"] is True, valid
        assert valid["agent_vision"]["schema_version"] == "goal_vision_replan_contract_v0", valid
        assert valid["vision_checkpoint"]["agent_id"] == AGENT_ID, valid
        assert valid["vision_checkpoint"]["required"] is True, valid
        assert valid["vision_checkpoint"]["satisfied"] is True, valid
        assert valid["vision_checkpoint"]["decision"] == "patched", valid

        written = payload(
            run_cli(
                registry_path,
                runtime,
                vision_path=valid_path,
                check=True,
                dry_run=False,
            )
        )
        assert written["ok"] is True, written
        index_path = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
        index_rows = [
            json.loads(line)
            for line in index_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert index_rows, written
        indexed_vision = index_rows[-1]["agent_vision"]
        indexed_checkpoint = index_rows[-1]["vision_checkpoint"]
        assert indexed_vision["vision_patch"]["acceptance_summary"] == (
            "One successor todo plus evidence references."
        ), indexed_vision
        assert indexed_vision["vision_patch"]["replan_trigger_summary"] == (
            "Frontier exhausted while acceptance remains open."
        ), indexed_vision
        assert indexed_vision["vision_patch"]["advancement_policy"] == (
            "repeat_until_closed"
        ), indexed_vision
        assert indexed_vision["todo_delta"] == ["create_successor"], indexed_vision
        assert indexed_checkpoint["agent_id"] == AGENT_ID, indexed_checkpoint
        assert indexed_checkpoint["decision"] == "patched", indexed_checkpoint
        status = run_status(registry_path, runtime)
        status_goal = next(
            goal
            for goal in status["run_history"]["goals"]
            if goal["id"] == GOAL_ID
        )
        latest_status_vision = status_goal["latest_runs"][0]["agent_vision"]
        latest_status_checkpoint = status_goal["latest_runs"][0]["vision_checkpoint"]
        assert latest_status_vision["vision_patch"]["acceptance_summary"] == (
            "One successor todo plus evidence references."
        ), latest_status_vision
        assert latest_status_vision["todo_delta"] == ["create_successor"], latest_status_vision
        assert "field_limits" not in latest_status_vision["vision_budget"], latest_status_vision
        assert latest_status_vision["vision_budget"]["field_usage"] == {
            "vision_summary": 63,
            "role_scope": 38,
            "acceptance_summary": 44,
            "advancement_policy": 19,
            "replan_trigger_summary": 49,
            "path_delta.outcome": 8,
            "path_delta.prior_assumption": 56,
            "path_delta.observed_reality": 58,
            "path_delta.retained[0]": 47,
            "path_delta.evidence_refs[0]": 27,
        }, latest_status_vision
        assert latest_status_checkpoint["agent_id"] == AGENT_ID, latest_status_checkpoint
        assert latest_status_checkpoint["decision"] == "patched", latest_status_checkpoint
        assert latest_status_checkpoint["triggers"][0]["kind"] == (
            "material_delivery_outcome"
        ), latest_status_checkpoint

        partial_inline = payload(
            run_cli(
                registry_path,
                runtime,
                inline_vision_args=[
                    "--vision-replan-trigger",
                    "The current dependency remains blocked; preserve the research mainline.",
                ],
                check=True,
            )
        )
        partial_patch = partial_inline["agent_vision"]["vision_patch"]
        assert partial_patch["vision_summary"] == (
            "Map the next evidence frontier and hand off one runnable claim."
        ), partial_inline
        assert partial_patch["acceptance_summary"] == (
            "One successor todo plus evidence references."
        ), partial_inline
        assert partial_patch["advancement_policy"] == (
            "repeat_until_closed"
        ), partial_inline
        assert partial_patch["replan_trigger_summary"] == (
            "The current dependency remains blocked; preserve the research mainline."
        ), partial_inline

        unexplained_inline_drift = run_cli(
            registry_path,
            runtime,
            inline_vision_args=[
                "--vision-summary",
                "Wait for one supporting pull request.",
            ],
            check=False,
        )
        assert unexplained_inline_drift.returncode == 1, unexplained_inline_drift
        assert "provide path_delta with schema_version=goal_path_delta_v0 and outcome=replan" in payload(
            unexplained_inline_drift
        )["error"], unexplained_inline_drift.stdout

        drift_packet = json.loads(valid_path.read_text(encoding="utf-8"))
        drift_packet["vision_patch"]["vision_summary"] = (
            "Wait for one supporting pull request."
        )
        write_json(drift_path, drift_packet)
        unexplained_drift = run_cli(
            registry_path,
            runtime,
            vision_path=drift_path,
            check=False,
        )
        assert unexplained_drift.returncode == 1, unexplained_drift
        assert "provide path_delta with schema_version=goal_path_delta_v0 and outcome=replan" in payload(
            unexplained_drift
        )["error"], unexplained_drift.stdout

        explained_drift_packet = dict(drift_packet)
        explained_drift_packet["path_delta"] = {
            "schema_version": "goal_path_delta_v0",
            "outcome": "replan",
            "prior_assumption": "The research mainline remained directly runnable.",
            "observed_reality": "A required dependency now blocks the next experiment.",
            "retained": ["Keep the experiment acceptance and evidence boundary."],
            "changed": ["Wait on the dependency before the next experiment."],
        }
        write_json(explained_drift_path, explained_drift_packet)
        explained_drift = payload(
            run_cli(
                registry_path,
                runtime,
                vision_path=explained_drift_path,
                check=True,
            )
        )
        assert explained_drift["agent_vision"]["path_delta"]["outcome"] == (
            "replan"
        ), explained_drift

        inline = payload(
            run_cli(
                registry_path,
                runtime,
                inline_vision_args=[
                    "--vision-state",
                    "vision_drift_detected",
                    "--vision-summary",
                    "Keep the KNN research frontier tied to visible role-authored improvements.",
                    "--vision-role-scope",
                    "Owns research framing and successor routing.",
                    "--vision-acceptance",
                    "At least two visible research passes produce evidence-backed next steps.",
                    "--vision-advancement-policy",
                    "repeat_until_closed",
                    "--vision-replan-trigger",
                    "The current frontier explains mechanism work but not multi-round research uplift.",
                    "--vision-todo-delta",
                    "create_successor",
                ],
                check=True,
                autonomous_replan_recorded=False,
            )
        )
        assert inline["ok"] is True, inline
        assert inline["agent_vision"]["state"] == "vision_drift_detected", inline
        assert inline["agent_vision"]["vision_patch"]["replan_trigger_summary"] == (
            "The current frontier explains mechanism work but not multi-round research uplift."
        ), inline
        assert inline["agent_vision"]["vision_patch"]["advancement_policy"] == (
            "repeat_until_closed"
        ), inline
        assert inline["agent_vision"]["todo_delta"] == ["create_successor"], inline
        assert inline["vision_checkpoint"]["agent_id"] == AGENT_ID, inline
        assert inline["vision_checkpoint"]["decision"] == "patched", inline

        closed_alias = payload(
            run_cli(
                registry_path,
                runtime,
                inline_vision_args=[
                    "--vision-state",
                    "vision_satisfied",
                    "--vision-summary",
                    "Close the bounded research route after authoritative validation.",
                ],
                check=True,
                autonomous_replan_recorded=False,
            )
        )
        assert closed_alias["agent_vision"]["state"] == "vision_closed", (
            closed_alias
        )

        invalid_state_result = run_cli(
            registry_path,
            runtime,
            inline_vision_args=[
                "--vision-state",
                "vision is done",
                "--vision-summary",
                "Invalid lifecycle prose should fail.",
            ],
            check=False,
        )
        assert invalid_state_result.returncode == 1, invalid_state_result
        invalid_state = payload(invalid_state_result)
        assert "lower snake_case lifecycle token" in invalid_state["error"], (
            invalid_state
        )

        unchanged = payload(
            run_cli(
                registry_path,
                runtime,
                inline_vision_args=[
                    "--vision-unchanged-reason",
                    "Current per-agent vision still matches this bounded material closeout.",
                ],
                check=True,
                autonomous_replan_recorded=False,
            )
        )
        assert unchanged["ok"] is True, unchanged
        assert unchanged["agent_vision"] is None, unchanged
        assert unchanged["vision_checkpoint"]["agent_id"] == AGENT_ID, unchanged
        assert unchanged["vision_checkpoint"]["decision"] == "unchanged_with_reason", unchanged

        state_path = (
            project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
        )
        state_before_rejected_refresh = state_path.read_text(encoding="utf-8")
        oversized_unchanged_reason_result = run_cli(
            registry_path,
            runtime,
            inline_vision_args=["--vision-unchanged-reason", "x" * 241],
            dry_run=False,
            check=False,
        )
        assert oversized_unchanged_reason_result.returncode == 1, (
            oversized_unchanged_reason_result
        )
        oversized_unchanged_reason = payload(oversized_unchanged_reason_result)
        assert (
            "vision_unchanged_reason exceeds 240 chars"
            in oversized_unchanged_reason["error"]
        ), oversized_unchanged_reason
        assert state_path.read_text(encoding="utf-8") == state_before_rejected_refresh

        missing_checkpoint = payload(
            run_cli(
                registry_path,
                runtime,
                check=True,
            )
        )
        assert missing_checkpoint["vision_checkpoint"]["required"] is True, (
            missing_checkpoint
        )
        assert missing_checkpoint["vision_checkpoint"]["satisfied"] is False, (
            missing_checkpoint
        )
        assert missing_checkpoint["vision_checkpoint"]["decision"] == "missing_required", (
            missing_checkpoint
        )
        assert missing_checkpoint["vision_checkpoint"]["required_resolution"] == [
            "write_vision_patch",
            "record_unchanged_reason",
        ], missing_checkpoint
        missing_markdown = run_cli(
            registry_path,
            runtime,
            output_format="markdown",
            check=True,
        ).stdout
        assert "vision_checkpoint_required_resolution:" in missing_markdown, (
            missing_markdown
        )
        assert "write_vision_patch,record_unchanged_reason" in missing_markdown, (
            missing_markdown
        )

        no_agent_inline_result = run_cli(
            registry_path,
            runtime,
            inline_vision_args=["--vision-summary", "Missing agent id should fail."],
            check=False,
            include_agent_id=False,
        )
        assert no_agent_inline_result.returncode == 1, no_agent_inline_result
        no_agent_inline = payload(no_agent_inline_result)
        assert "inline agent vision requires --agent-id" in no_agent_inline["error"], no_agent_inline

        mixed_result = run_cli(
            registry_path,
            runtime,
            vision_path=valid_path,
            inline_vision_args=["--vision-summary", "Conflicting inline patch."],
            check=False,
        )
        assert mixed_result.returncode == 1, mixed_result
        mixed = payload(mixed_result)
        assert "--agent-vision-json cannot be combined" in mixed["error"], mixed

        long_todo_delta = (
            "create a successor todo that references the public research frontier, "
            "acceptance gap, validation check, owner handoff, and next compact action"
        )
        long_todo_result = run_cli(
            registry_path,
            runtime,
            inline_vision_args=[
                "--vision-summary",
                "Keep the next research frontier bounded and public-safe.",
                "--vision-todo-delta",
                long_todo_delta,
            ],
            check=False,
        )
        assert long_todo_result.returncode == 1, long_todo_result
        long_todo = payload(long_todo_result)
        assert "vision_budget_exceeded" in long_todo["error"], long_todo
        assert "todo_delta uses" in long_todo["error"], long_todo
        assert "limit is 80" in long_todo["error"], long_todo
        assert "suggested compact value" in long_todo["error"], long_todo

        from loopx.todos import add_goal_todo
        add_goal_todo(registry_path=registry_path, goal_id=GOAL_ID, role="agent",
            text="Validate the scoped delivery evidence.", task_class="advancement_task",
            claimed_by=AGENT_ID)
        local_next_action = "/Users/example/private/raw-task-note"
        private_next_action_result = payload(
            run_cli(
                registry_path,
                runtime,
                extra_args=[
                    "--next-action",
                    local_next_action,
                    "--progress-scope",
                    "goal",
                ],
                check=True,
            )
        )
        assert private_next_action_result["ok"] is True, private_next_action_result
        assert private_next_action_result["recommended_action_resolution"][
            "recommended_action"
        ] == local_next_action, private_next_action_result
        assert private_next_action_result["recommended_action_source"] == "agent_lane_step"
        assert "active_state_next_action_update" not in private_next_action_result

        secret_next_action_result = run_cli(
            registry_path,
            runtime,
            extra_args=[
                "--next-action",
                "Continue with access_" + "key=" + "AKIA" + "1234567890ABCDEF",
                "--progress-scope",
                "goal",
            ],
            check=False,
        )
        assert secret_next_action_result.returncode == 1, secret_next_action_result
        secret_next_action = payload(secret_next_action_result)
        assert "active_state_next_action" in secret_next_action["error"], secret_next_action
        assert "secret-looking value" in secret_next_action["error"], secret_next_action
        assert "AK/SK" in secret_next_action["error"], secret_next_action

        write_json(
            invalid_path,
            {
                "goal_id": GOAL_ID,
                "agent_id": AGENT_ID,
                "vision_patch": {
                    "vision_summary": "Invalid advancement policy should fail.",
                    "advancement_policy": "always_loop",
                },
            },
        )
        invalid_policy_result = run_cli(
            registry_path,
            runtime,
            vision_path=invalid_path,
            check=False,
        )
        assert invalid_policy_result.returncode == 1, invalid_policy_result
        invalid_policy = payload(invalid_policy_result)
        assert "advancement_policy must be one of" in invalid_policy["error"], (
            invalid_policy
        )

        write_json(
            invalid_path,
            {
                "goal_id": GOAL_ID,
                "agent_id": AGENT_ID,
                "vision_patch": {"vision_summary": "x" * 421},
            },
        )
        invalid_result = run_cli(
            registry_path,
            runtime,
            vision_path=invalid_path,
            check=False,
        )
        assert invalid_result.returncode == 1, invalid_result
        invalid = payload(invalid_result)
        assert invalid["ok"] is False, invalid
        assert "vision_budget_exceeded" in invalid["error"], invalid
        assert "vision_summary" in invalid["error"], invalid
        assert "suggested compact value" in invalid["error"], invalid

    print("goal-vision-refresh-state-budget-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
