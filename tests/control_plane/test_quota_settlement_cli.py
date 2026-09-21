from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from loopx.bootstrap_command_pack import build_start_goal_guided_packet
from loopx.control_plane.work_items.delivery_outcome import (
    PROGRESS_DELIVERY_OUTCOMES,
    DeliveryOutcome,
)
from loopx.control_plane.status.autonomous_replan_projection import (
    AUTONOMOUS_REPLAN_PERIODIC_RUN_THRESHOLD,
)
from loopx.heartbeat_prompt import build_heartbeat_prompt

REPO_ROOT = Path(__file__).resolve().parents[2]
GOAL_ID = "settlement-cli-fixture"
AGENT_ID = "codex-settlement-cli"
TODO_ID = "todo_fixture_settlement"
ALTERNATIVE_TODO_ID = "todo_fixture_alternative"
SECOND_ALTERNATIVE_TODO_ID = "todo_fixture_second_alternative"
OUTSIDE_BOUNDED_PORTFOLIO_TODO_ID = "todo_fixture_outside_portfolio"
DEEP_ALTERNATIVE_TODO_ID = "todo_fixture_deep_alternative"
REENTRY_TODO_ID = "todo_fixture_network_reentry"
DUE_MONITOR_TODO_ID = "todo_fixture_due_monitor"
TURN_ID = "turn-settlement-cli-1"
SELECTED_REPLAN_TODO_ID = "todo_chain_000000000000"


def _write_fixture(
    root: Path,
    *,
    required_capability: str | None = None,
) -> tuple[Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_file
    registry_path = project / ".loopx" / "registry.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    capability_metadata = (
        f" required_capabilities={required_capability}" if required_capability else ""
    )
    state_path.write_text(
        "---\n"
        "status: active-read-only\n"
        "owner_mode: goal\n"
        'objective: "Settle one standard Codex App delivery."\n'
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Settlement CLI Fixture\n\n"
        "## Objective\n\n"
        "Settle one standard Codex App delivery.\n\n"
        "## Next Action\n\n"
        "- Validate and settle the selected delivery.\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P1] Validate and settle the selected delivery.\n"
        f"  <!-- loopx:todo todo_id={TODO_ID} status=open "
        "task_class=advancement_task action_kind=validate"
        f"{capability_metadata} -->\n",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "settlement-cli-fixture",
                        "status": "active-read-only",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {
                            "kind": "read_only_project_map_v0",
                            "status": "connected-read-only",
                        },
                        "coordination": {
                            "registered_agents": [AGENT_ID],
                            "agent_model": "peer_v1",
                        },
                        "authority_sources": [],
                        "quota": {
                            "compute": 1.0,
                            "window_hours": 24,
                            "allowed_slots": 2,
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
    return project, runtime, registry_path


def _run_cli(
    registry_path: Path,
    runtime: Path,
    *args: str,
    cwd: Path = REPO_ROOT,
) -> tuple[int, dict[str, Any]]:
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
            *args,
        ],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": str(REPO_ROOT),
        },
    )
    return result.returncode, json.loads(result.stdout)


def _run_generated_cli(
    command: str,
    *,
    registry_path: Path,
) -> tuple[int, dict[str, Any]]:
    argv = shlex.split(command)
    assert argv[0] == "loopx"
    if "--registry" not in argv:
        argv[1:1] = ["--registry", str(registry_path)]
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", *argv[1:]],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    return result.returncode, json.loads(result.stdout)


def _spend_run_count(runtime: Path) -> int:
    index_path = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    if not index_path.exists():
        return 0
    return sum(
        1
        for line in index_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("classification") == "quota_slot_spent"
    )


def _classification_count(runtime: Path, classification: str) -> int:
    index_path = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    if not index_path.exists():
        return 0
    return sum(
        1
        for line in index_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("classification") == classification
    )


def _append_surface_only_runs(runtime: Path, *, count: int) -> None:
    index_path = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("a", encoding="utf-8") as handle:
        for index in range(count):
            handle.write(
                json.dumps(
                    {
                        "generated_at": (
                            f"2026-08-13T00:{index:02d}:00+00:00"
                        ),
                        "goal_id": GOAL_ID,
                        "classification": "surface_only_progress",
                        "agent_id": AGENT_ID,
                        "progress_scope": "agent_lane",
                        "delivery_outcome": "surface_only",
                    }
                )
                + "\n"
            )


def _heartbeat_receipt_count(runtime: Path, turn_instance_id: str) -> int:
    log_path = runtime / "goals" / GOAL_ID / "rollout-event-log.jsonl"
    if not log_path.exists():
        return 0
    return sum(
        1
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if (
            (event := json.loads(line)).get("event_kind") == "quota_should_run"
            and event.get("run_id") == turn_instance_id
            and event.get("agent_id") == AGENT_ID
        )
    )


def _configure_read_only_todo(project: Path) -> Path:
    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_text = state_path.read_text(encoding="utf-8")
    state_path.write_text(
        state_text.replace(
            "action_kind=validate -->",
            "action_kind=validate "
            "continuation_policy=same_agent_non_delivery "
            "required_capabilities=shell%2Cfilesystem_read -->",
        ),
        encoding="utf-8",
    )
    return state_path


def _configure_repository_write_todo(project: Path) -> Path:
    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_text = state_path.read_text(encoding="utf-8")
    state_path.write_text(
        state_text.replace(
            "action_kind=validate -->",
            "action_kind=validate "
            "task_repository=git:github.com/example/read-only-settlement-fixture "
            "required_capabilities=filesystem_write -->",
        ),
        encoding="utf-8",
    )
    return state_path


def _configure_boundary_blocked_primary(project: Path) -> Path:
    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_text = state_path.read_text(encoding="utf-8")
    state_path.write_text(
        state_text.replace(
            "action_kind=validate -->",
            "action_kind=validate required_write_scopes=private/** -->",
        ),
        encoding="utf-8",
    )
    return state_path


def _configure_completion_validation_todo(project: Path) -> Path:
    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_text = state_path.read_text(encoding="utf-8")
    state_path.write_text(
        state_text.replace(
            "action_kind=validate -->",
            "action_kind=validate validation_command=pytest -->",
        ),
        encoding="utf-8",
    )
    return state_path


def _configure_selectable_alternative(
    project: Path,
    *,
    required_capability: str | None = None,
) -> None:
    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_text = state_path.read_text(encoding="utf-8")
    capability_metadata = (
        f" required_capabilities={required_capability}"
        if required_capability
        else ""
    )
    state_path.write_text(
        state_text.rstrip()
        + "\n- [ ] [P1] Advance the independent alternative delivery.\n"
        + "  <!-- loopx:todo "
        + f"todo_id={ALTERNATIVE_TODO_ID} status=open "
        + "task_class=advancement_task action_kind=implement"
        + capability_metadata
        + " -->\n"
        + "- [ ] [P1] Advance the second independent alternative.\n"
        + "  <!-- loopx:todo "
        + f"todo_id={SECOND_ALTERNATIVE_TODO_ID} status=open "
        + "task_class=advancement_task action_kind=implement"
        + capability_metadata
        + " -->\n"
        + "- [ ] [P1] Advance the fourth eligible action.\n"
        + "  <!-- loopx:todo "
        + f"todo_id={OUTSIDE_BOUNDED_PORTFOLIO_TODO_ID} status=open "
        + "task_class=advancement_task action_kind=implement"
        + capability_metadata
        + " -->\n",
        encoding="utf-8",
    )


def _configure_runtime_capability_reentry_fixture(project: Path) -> None:
    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_text = state_path.read_text(encoding="utf-8")
    state_path.write_text(
        state_text.replace(
            "## Agent Todo\n\n",
            "## Agent Todo\n\n"
            "- [ ] [P0] Inspect the network target.\n"
            f"  <!-- loopx:todo todo_id={REENTRY_TODO_ID} status=open "
            "task_class=advancement_task action_kind=inspect_target "
            "required_capabilities=network -->\n",
        ),
        encoding="utf-8",
    )


def _append_newly_due_monitor(
    project: Path,
    *,
    priority: str = "P0-monitor",
    watch_only: bool = False,
) -> None:
    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_text = state_path.read_text(encoding="utf-8")
    watch_only_field = "watch_only=true " if watch_only else ""
    state_path.write_text(
        state_text.replace(
            "## Agent Todo\n\n",
            "## Agent Todo\n\n"
            f"- [ ] [{priority}] Observe the newly due public target.\n"
            f"  <!-- loopx:todo todo_id={DUE_MONITOR_TODO_ID} status=open "
            "task_class=continuous_monitor action_kind=observe "
            f"claimed_by={AGENT_ID} target_key=due-monitor-fixture "
            "required_capabilities=network%2Cexternal_evidence_poll "
            f"{watch_only_field}"
            "cadence=1m next_due_at=2000-01-01T00%3A00%3A00Z -->\n",
        ),
        encoding="utf-8",
    )


def _append_due_monitors(
    project: Path,
    monitors: list[tuple[str, str]],
) -> None:
    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_text = state_path.read_text(encoding="utf-8")
    rows = "".join(
        "- [ ] [P2-monitor] Observe an independent due target.\n"
        f"  <!-- loopx:todo todo_id={todo_id} status=open "
        "task_class=continuous_monitor action_kind=observe "
        f"claimed_by={AGENT_ID} target_key={target_key} "
        "required_capabilities=network%2Cexternal_evidence_poll "
        "cadence=1m next_due_at=2000-01-01T00%3A00%3A00Z -->\n"
        for todo_id, target_key in monitors
    )
    state_path.write_text(
        state_text.replace("## Agent Todo\n\n", f"## Agent Todo\n\n{rows}"),
        encoding="utf-8",
    )


def _append_blocking_user_gate(project: Path) -> None:
    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_text = state_path.read_text(encoding="utf-8")
    state_path.write_text(
        state_text.rstrip()
        + "\n\n## User Todo\n\n"
        + "- [ ] [P0-user] Approve the newly introduced delivery gate.\n"
        + "  <!-- loopx:todo todo_id=todo_fixture_user_gate status=open "
        + "task_class=user_gate action_kind=approve_delivery "
        + f"blocks_agent={AGENT_ID} "
        + "decision_scope=delivery:action:fixture priority=P0-USER -->\n",
        encoding="utf-8",
    )


def _heartbeat_receipt_events(
    runtime: Path, turn_instance_id: str
) -> list[dict[str, Any]]:
    log_path = runtime / "goals" / GOAL_ID / "rollout-event-log.jsonl"
    if not log_path.exists():
        return []
    return [
        event
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if (
            (event := json.loads(line)).get("event_kind") == "quota_should_run"
            and event.get("run_id") == turn_instance_id
            and event.get("agent_id") == AGENT_ID
        )
    ]


def _configure_autonomous_replan_fixture(
    project: Path,
    runtime: Path,
    registry_path: Path,
) -> None:
    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path.write_text(
        "---\n"
        "status: active\n"
        "owner_mode: goal\n"
        'objective: "Settle one Todo-less autonomous replan."\n'
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Autonomous Replan Settlement Fixture\n\n"
        "## Objective\n\n"
        "Settle one Todo-less autonomous replan.\n\n"
        "## Next Action\n\n"
        "- Replan the repeated monitor observation.\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P1-monitor] Observe the stable public fixture.\n"
        "  <!-- loopx:todo todo_id=todo_replan_monitor status=open "
        "task_class=continuous_monitor action_kind=observe "
        f"claimed_by={AGENT_ID} target_key=replan-settlement-fixture "
        "cadence=1d next_due_at=2999-01-01T00%3A00%3A00Z -->\n",
        encoding="utf-8",
    )
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["goals"][0]["status"] = "active"
    registry_path.write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    runs_dir = runtime / "goals" / GOAL_ID / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []
    for minute in (1, 2):
        generated_at = f"2026-01-01T00:0{minute}:00+00:00"
        run = {
            "generated_at": generated_at,
            "goal_id": GOAL_ID,
            "agent_id": AGENT_ID,
            "classification": "bounded_fixture_probe",
            "delivery_batch_scale": "single_surface",
            "delivery_outcome": "surface_only",
            "progress_observation": {
                "schema_version": "typed_progress_observation_v0",
                "result_class": "unchanged",
                "work_item_id": "todo_replan_monitor",
                "surface_id": "surface-existing",
                "hypothesis_id": "hypothesis-existing",
                "probe_kind": "probe-existing",
                "evidence_ids": ["evidence-existing"],
            },
        }
        json_path = runs_dir / f"replan-{minute}.json"
        markdown_path = runs_dir / f"replan-{minute}.md"
        json_path.write_text(json.dumps(run) + "\n", encoding="utf-8")
        markdown_path.write_text("# Replan fixture\n", encoding="utf-8")
        runs.append(
            {
                **run,
                "json_path": str(json_path),
                "markdown_path": str(markdown_path),
            }
        )
    (runs_dir / "index.jsonl").write_text(
        "".join(json.dumps(run) + "\n" for run in runs),
        encoding="utf-8",
    )


def _configure_selected_todo_replan_fixture(
    project: Path,
    registry_path: Path,
) -> None:
    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    todo_rows = "\n".join(
        (
            f"- [ ] [P1] Validate bounded slice {index}.\n"
            "  <!-- loopx:todo "
            f"todo_id=todo_chain_{index:012d} status=open "
            "task_class=advancement_task action_kind=validate "
            f"claimed_by={AGENT_ID} -->"
        )
        for index in range(15)
    )
    state_path.write_text(
        "---\n"
        "status: active\n"
        "owner_mode: goal\n"
        'objective: "Settle one Todo-bound autonomous replan."\n'
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Todo-bound Replan Settlement Fixture\n\n"
        "## Objective\n\n"
        "Settle one Todo-bound autonomous replan.\n\n"
        "## Next Action\n\n"
        "- Validate bounded slice 0.\n\n"
        "## Agent Todo\n\n"
        f"{todo_rows}\n",
        encoding="utf-8",
    )
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["goals"][0]["status"] = "active"
    registry_path.write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _projected_cli_args(command: str, *, turn_instance_id: str) -> tuple[str, ...]:
    tokens = shlex.split(command)
    command_names = {"refresh-state", "quota"}
    command_index = next(
        index for index, token in enumerate(tokens) if token in command_names
    )
    return tuple(
        turn_instance_id if token == "${LOOPX_TURN:?}" else token
        for token in tokens[command_index:]
    )


def _initialize_git_checkout(project: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "remote",
            "add",
            "origin",
            "https://github.com/example/read-only-settlement-fixture.git",
        ],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )


def _strip_heartbeat_workspace_causality(runtime: Path) -> None:
    log_path = runtime / "goals" / GOAL_ID / "rollout-event-log.jsonl"
    events = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for event in events:
        if (
            event.get("event_kind") == "quota_should_run"
            and event.get("run_id") == TURN_ID
            and isinstance(event.get("details"), dict)
        ):
            event["details"].pop("delivery_workspace_causality", None)
            for key in tuple(event["details"]):
                if key.startswith("delivery_workspace_"):
                    event["details"].pop(key)
    log_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )


def test_gitless_goal_refresh_and_quota_spend_settle_end_to_end(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(
        tmp_path,
        required_capability="filesystem_write",
    )
    turn_id = "turn-gitless-settlement"
    binding = (
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        turn_id,
    )

    guard_rc, guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        *binding,
        "--scan-path",
        str(project),
        cwd=project,
    )
    assert guard_rc == 0, guard
    assert guard["should_run"] is True
    assert (
        guard["heartbeat_receipt"]["delivery_workspace_causality"]["requirement"]
        == "required"
    )

    refresh_rc, refresh = _run_cli(
        registry_path,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "gitless_validated_progress",
        "--delivery-batch-scale",
        "implementation",
        "--delivery-outcome",
        "outcome_progress",
        *binding,
        "--no-global-sync",
        "--suppress-external-sinks",
        cwd=project,
    )
    assert refresh_rc == 0, refresh
    assert refresh["delivery_workspace"] == {
        "schema_version": "delivery_workspace_v1",
        "workspace_identity": f"loopx:{GOAL_ID}",
        "identity_kind": "local_goal",
        "task_repository": None,
        "repository_source": "goal_id_fallback",
        "workspace_kind": "local_goal_workspace",
        "peer_independent_worktree_required": False,
    }
    assert str(project) not in json.dumps(refresh["delivery_workspace"])

    spend_rc, spend = _run_cli(
        registry_path,
        runtime,
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
        *binding,
        "--scan-path",
        str(project),
        cwd=project,
    )
    assert spend_rc == 0, spend
    assert spend["appended"] is True
    assert spend["delivery_workspace_validated"] is True
    assert spend["delivery_workspace"]["workspace_identity"] == f"loopx:{GOAL_ID}"
    assert _spend_run_count(runtime) == 1


def test_codex_app_refresh_stages_validated_memory_and_spend_finalizes_hook(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    validator = project / "validate_reward_memory.py"
    validator.write_text(
        "import json, sys\n"
        "text = sys.stdin.read()\n"
        "if text:\n"
        "  request = json.loads(text)\n"
        "  print(json.dumps({\n"
        "    'schema_version': 'reward_memory_reflection_validation_v0',\n"
        "    'status': 'validated',\n"
        "    'reflection_digest': request['reflection_digest'],\n"
        "    'evidence_refs': request['reflection']['evidence_refs'],\n"
        "  }))\n",
        encoding="utf-8",
    )
    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_text = state_path.read_text(encoding="utf-8")
    validation_argv = quote(
        json.dumps([sys.executable, str(validator)], separators=(",", ":")),
        safe="",
    )
    state_path.write_text(
        state_text.replace(
            "action_kind=validate -->",
            "action_kind=validate "
            f"validation_command_argv={validation_argv} "
            "validation_label=reward-memory-app-fixture -->",
        ),
        encoding="utf-8",
    )
    turn_id = "turn-reward-memory-app"
    binding = (
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        turn_id,
    )
    reflection = json.dumps(
        {
            "schema_version": "turn_reward_memory_reflection_v0",
            "status": "eligible",
            "surface_id": "agent_workflow.turn_admission",
            "outcome_kind": "engineering",
            "content_summary": "Reuse the exact settlement identity on retries.",
            "reasoning_summary": "The declared validator covered the bound outcome.",
            "confidence": "high",
            "evidence_refs": ["artifact:app-settlement", "receipt:validator"],
        },
        separators=(",", ":"),
    )

    guard_rc, guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        *binding,
        "--scan-path",
        str(project),
        cwd=project,
    )
    assert guard_rc == 0, guard

    complete_rc, complete = _run_cli(
        registry_path,
        runtime,
        "todo",
        "complete",
        "--goal-id",
        GOAL_ID,
        *binding,
        "--claimed-by",
        AGENT_ID,
        "--evidence",
        "reward memory app fixture validated",
        "--next-agent-todo",
        "Continue after the reward memory fixture.",
        "--next-claimed-by",
        AGENT_ID,
        "--next-action-kind",
        "implement",
        cwd=project,
    )
    assert complete_rc == 0, complete

    refresh_rc, refresh = _run_cli(
        registry_path,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "reward_memory_app_progress",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "outcome_progress",
        "--reward-memory-reflection-json",
        reflection,
        *binding,
        "--no-global-sync",
        "--suppress-external-sinks",
        cwd=project,
    )
    assert refresh_rc == 0, refresh.get("error") or refresh
    candidate = refresh["reward_memory_outcome_candidate"]
    assert candidate["status"] == "validation_bound"
    assert candidate["validation_bound"] is True
    assert candidate["raw_content_projected"] is False
    assert "content_summary" not in json.dumps(candidate)

    spend_rc, spend = _run_cli(
        registry_path,
        runtime,
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
        *binding,
        "--scan-path",
        str(project),
        cwd=project,
    )
    assert spend_rc == 0, spend
    assert spend["settlement_result"]["ok"] is True
    assert spend["reward_memory_ingest"]["host_wiring"] == (
        "codex_app_refresh_spend_post_settlement"
    )
    assert spend["reward_memory_ingest"]["external_writes_performed"] is False


def test_typed_outcome_gap_settles_exact_turn_without_becoming_progress(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(
        tmp_path,
        required_capability="filesystem_write",
    )
    turn_id = "turn-typed-blocker-settlement"
    binding = (
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        turn_id,
    )
    guard_rc, guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        *binding,
        "--scan-path",
        str(project),
        cwd=project,
    )
    assert guard_rc == 0, guard
    assert guard["heartbeat_receipt"]["settlement_identity"]["todo_id"] == TODO_ID
    assert DeliveryOutcome.OUTCOME_GAP not in PROGRESS_DELIVERY_OUTCOMES

    common_refresh_args = (
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "typed_blocker_writeback",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "outcome_gap",
        *binding,
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    bare_rc, bare = _run_cli(
        registry_path,
        runtime,
        *common_refresh_args,
        cwd=project,
    )
    assert bare_rc == 1, bare
    assert "typed blocked outcome_gap settlement" in bare["error"]
    assert _classification_count(runtime, "typed_blocker_writeback") == 0

    surface_args = list(common_refresh_args)
    outcome_index = surface_args.index("outcome_gap")
    surface_args[outcome_index] = "surface_only"
    surface_rc, surface = _run_cli(
        registry_path,
        runtime,
        *surface_args,
        "--progress-result-class",
        "blocked",
        "--progress-blocker-id",
        "blocker:runtime-boundary",
        "--progress-evidence-id",
        "evidence:runtime-boundary",
        cwd=project,
    )
    assert surface_rc == 1, surface
    assert "typed blocked outcome_gap settlement" in surface["error"]

    mismatch_args = list(common_refresh_args)
    todo_index = mismatch_args.index(TODO_ID)
    mismatch_args[todo_index] = ALTERNATIVE_TODO_ID
    mismatch_rc, mismatch = _run_cli(
        registry_path,
        runtime,
        *mismatch_args,
        "--progress-result-class",
        "blocked",
        "--progress-blocker-id",
        "blocker:runtime-boundary",
        "--progress-evidence-id",
        "evidence:runtime-boundary",
        cwd=project,
    )
    assert mismatch_rc == 1, mismatch
    assert "settlement binding does not match" in mismatch["error"]

    refresh_rc, refresh = _run_cli(
        registry_path,
        runtime,
        *common_refresh_args,
        "--progress-result-class",
        "blocked",
        "--progress-blocker-id",
        "blocker:runtime-boundary",
        "--progress-evidence-id",
        "evidence:runtime-boundary",
        cwd=project,
    )
    assert refresh_rc == 0, refresh
    assert refresh["delivery_outcome"] == "outcome_gap"
    assert refresh["progress_observation"]["result_class"] == "blocked"
    assert refresh["progress_observation"]["work_item_id"] == TODO_ID
    assert [
        receipt["step_kind"]
        for receipt in refresh["settlement_result"]["receipts"]
    ] == ["validation", "durable_writeback"]

    spend_rc, spend = _run_cli(
        registry_path,
        runtime,
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
        *binding,
        "--scan-path",
        str(project),
        cwd=project,
    )
    assert spend_rc == 0, spend
    assert [
        receipt["step_kind"]
        for receipt in spend["settlement_result"]["receipts"]
    ] == ["validation", "durable_writeback", "quota_spend"]
    assert _spend_run_count(runtime) == 1


def test_in_flight_progress_preserves_todo_across_heartbeat_settlements(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    first_turn_id = "turn-settlement-continuation-1"
    guard_rc, guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        first_turn_id,
        "--scan-path",
        str(project),
    )
    assert guard_rc == 0, guard
    assert guard["selected_todo"]["todo_id"] == TODO_ID
    assert guard["selected_todo"]["delivery_boundary"] == ("in_flight_continuation")
    first_writeback = guard["interaction_contract"]["cli_channel"]["next_cli_actions"][
        0
    ]
    assert "--delivery-boundary in_flight_continuation" in first_writeback

    refresh_rc, refresh = _run_cli(
        registry_path,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "validated_progress",
        "--delivery-batch-scale",
        "implementation",
        "--delivery-outcome",
        "outcome_progress",
        "--delivery-boundary",
        "in_flight_continuation",
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        first_turn_id,
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    assert refresh_rc == 0, refresh
    persisted = json.loads(Path(refresh["json_path"]).read_text(encoding="utf-8"))
    assert persisted["vision_checkpoint"]["decision"] == "not_required"
    assert persisted["vision_checkpoint"]["delivery_boundary"] == (
        "in_flight_continuation"
    )

    spend_rc, spend = _run_cli(
        registry_path,
        runtime,
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        first_turn_id,
    )
    assert spend_rc == 0, spend

    second_guard_rc, second_guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        "turn-settlement-continuation-2",
        "--scan-path",
        str(project),
    )
    assert second_guard_rc == 0, second_guard
    assert second_guard["selected_todo"]["todo_id"] == TODO_ID
    assert second_guard["selected_todo"]["selected_by"] == "in_flight_todo"
    assert second_guard["selected_todo"]["delivery_boundary"] == (
        "in_flight_continuation"
    )
    writeback = second_guard["interaction_contract"]["cli_channel"]["next_cli_actions"][
        0
    ]
    assert "--delivery-boundary in_flight_continuation" in writeback

    second_refresh_rc, second_refresh = _run_cli(
        registry_path,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "validated_progress",
        "--delivery-batch-scale",
        "implementation",
        "--delivery-outcome",
        "outcome_progress",
        "--delivery-boundary",
        "in_flight_continuation",
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        "turn-settlement-continuation-2",
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    assert second_refresh_rc == 0, second_refresh
    persisted_second = json.loads(
        Path(second_refresh["json_path"]).read_text(encoding="utf-8")
    )
    assert persisted_second["vision_checkpoint"] == {
        "schema_version": "vision_checkpoint_v0",
        "agent_id": AGENT_ID,
        "required": False,
        "satisfied": True,
        "decision": "not_required",
        "triggers": [{"kind": "in_flight_continuation", "todo_id": TODO_ID}],
        "delivery_boundary": "in_flight_continuation",
    }


def test_in_flight_progress_settles_while_completion_validation_todo_is_open(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_completion_validation_todo(project)
    turn_id = "turn-settlement-open-validation"
    guard_rc, guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_id,
        "--scan-path",
        str(project),
    )
    assert guard_rc == 0, guard

    refresh_rc, refresh = _run_cli(
        registry_path,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "validated_intermediate_progress",
        "--delivery-batch-scale",
        "implementation",
        "--delivery-outcome",
        "outcome_progress",
        "--delivery-boundary",
        "in_flight_continuation",
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        turn_id,
        "--no-global-sync",
        "--suppress-external-sinks",
    )

    assert refresh_rc == 0, refresh
    assert refresh["settlement_result"]["ok"] is True
    assert refresh["vision_checkpoint"]["delivery_boundary"] == (
        "in_flight_continuation"
    )


def test_recovery_does_not_bind_current_replan_and_reenters_same_turn(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    _append_newly_due_monitor(project)
    prior_turn_id = "turn-unsettled-prior"

    prior_rc, prior = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        prior_turn_id,
        "--todo-id",
        TODO_ID,
        "--scan-path",
        str(project),
    )
    assert prior_rc == 0, prior
    assert prior["heartbeat_receipt"]["closeout_required"] is True

    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_before_replan = state_path.read_text(encoding="utf-8")
    replan_backlog = "\n".join(
        f"- [ ] [P1] Review recovery breadth {index}.\n"
        "  <!-- loopx:todo "
        f"todo_id=todo_recovery_replan_{index:012d} status=open "
        "task_class=advancement_task action_kind=validate "
        f"claimed_by={AGENT_ID} -->"
        for index in range(15)
    )
    state_path.write_text(
        state_before_replan.rstrip() + "\n\n" + replan_backlog + "\n",
        encoding="utf-8",
    )

    recovery_rc, recovery = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--begin-turn",
        "--scan-path",
        str(project),
    )
    assert recovery_rc == 0, recovery
    assert recovery["effective_action"] == "unsettled_host_turn_recovery"
    replan_obligation_id = recovery["replan_action_packet"]["obligation_id"]
    packet = recovery["unsettled_host_turn_recovery"]
    assert packet["prior_turn_instance_id"] == prior_turn_id
    assert packet["binding_id"] == TODO_ID
    assert packet["missing_receipts"] == [
        "durable_writeback_receipt",
        "quota_spend_receipt",
    ]
    assert "selected_todo" not in recovery
    contract = recovery["interaction_contract"]
    assert contract["mode"] == "unsettled_host_turn_recovery"
    assert contract["agent_channel"]["must_attempt"] is True
    assert contract["agent_channel"]["delivery_allowed"] is False
    assert contract["agent_channel"]["quiet_noop_allowed"] is False
    assert contract["agent_channel"]["primary_action"] == recovery[
        "recommended_action"
    ]
    assert contract["agent_channel"]["recovery_ref"] == (
        "$.unsettled_host_turn_recovery"
    )
    assert contract["cli_channel"]["spend_after_validation"] is False
    assert "monitor_changed:<monitor-todo-id>" in contract["cli_channel"][
        "next_cli_actions"
    ][1]
    assert "settlement_identity" not in recovery["heartbeat_receipt"]
    assert recovery["heartbeat_receipt"]["semantic_replan_obligation_id"] == (
        replan_obligation_id
    )

    # Model the separately verified replan settlement without touching the
    # append-only heartbeat receipt.  The same recovery Turn must then be able
    # to bind an independent successor instead of preserving an obsolete replan.
    state_path.write_text(state_before_replan, encoding="utf-8")

    wait_rc, wait = _run_cli(
        registry_path,
        runtime,
        "todo",
        "update",
        "--goal-id",
        GOAL_ID,
        "--todo-id",
        TODO_ID,
        "--agent-id",
        AGENT_ID,
        "--status",
        "open",
        "--resume-when",
        f"monitor_changed:{DUE_MONITOR_TODO_ID}",
        "--successor-todo-id",
        ALTERNATIVE_TODO_ID,
    )
    assert wait_rc == 0, wait
    assert wait["external_wait_transition"]["successor_todo_ids"] == [
        ALTERNATIVE_TODO_ID
    ]

    current_turn_id = recovery["heartbeat_receipt"]["turn_instance_id"]
    resumed_rc, resumed = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        current_turn_id,
        "--todo-id",
        ALTERNATIVE_TODO_ID,
        "--scan-path",
        str(project),
    )
    assert resumed_rc == 0, resumed
    assert resumed["effective_action"] != "unsettled_host_turn_recovery"
    assert resumed.get("autonomous_replan_obligation") is None
    assert resumed["selected_todo"]["todo_id"] == ALTERNATIVE_TODO_ID
    assert resumed["heartbeat_receipt"]["status"] == "upgraded"
    assert resumed["heartbeat_receipt"]["settlement_identity"]["todo_id"] == (
        ALTERNATIVE_TODO_ID
    )


def test_recovery_guard_accepts_the_todo_it_must_settle(
    tmp_path: Path,
) -> None:
    """A recovery guard bound to the Todo it recovers is not a selection conflict.

    The recovery decision carries no top-level ``selected_todo``: its
    qualification names the Todo the prior unsettled Turn has to settle. Binding
    the guard to that same Todo is the documented way to close out, so the
    preflight must accept it instead of failing as a projection conflict.
    """

    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    _append_newly_due_monitor(project)
    prior_turn_id = "turn-unsettled-bound-prior"
    recovery_turn_id = "turn-unsettled-bound-recovery"

    prior_rc, prior = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        prior_turn_id,
        "--todo-id",
        TODO_ID,
        "--scan-path",
        str(project),
    )
    assert prior_rc == 0, prior
    assert prior["heartbeat_receipt"]["closeout_required"] is True

    recovery_rc, recovery = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        recovery_turn_id,
        "--todo-id",
        TODO_ID,
        "--scan-path",
        str(project),
    )

    assert recovery_rc == 0, recovery
    assert recovery["effective_action"] == "unsettled_host_turn_recovery"
    packet = recovery["unsettled_host_turn_recovery"]
    assert packet["prior_turn_instance_id"] == prior_turn_id
    assert packet["binding_id"] == TODO_ID
    assert recovery.get("error_code") != "quota_unexpected_collection_error"


def test_prior_turn_with_several_receipts_recovers_once(tmp_path: Path) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    prior_turn_id = "turn-multi-receipt-prior"
    prior_rc, prior = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        prior_turn_id,
        "--todo-id",
        TODO_ID,
        "--scan-path",
        str(project),
    )
    assert prior_rc == 0, prior
    assert prior["heartbeat_receipt"]["closeout_required"] is True

    # A real goal persists more than one guard per Turn: an identity upgrade
    # append and an older guard that never carried a binding.  The newest bound
    # receipt decides the Turn, and the unbound one can neither outrank it nor
    # turn the Turn into an identity conflict.
    log_path = runtime / "goals" / GOAL_ID / "rollout-event-log.jsonl"
    with log_path.open("a", encoding="utf-8") as stream:
        for event_id, details in (
            (
                "multi-receipt-identity-upgrade",
                {
                    "todo_id": TODO_ID,
                    "settlement_effect_id": (
                        f"{GOAL_ID}:{AGENT_ID}:{TODO_ID}:{prior_turn_id}"
                    ),
                    "closeout_required": True,
                },
            ),
            ("multi-receipt-stale-unbound", {"stall_observation": "not_applicable"}),
        ):
            stream.write(
                json.dumps(
                    {
                        "schema_version": "loopx_rollout_event_v0",
                        "event_id": event_id,
                        "event_kind": "quota_should_run",
                        "goal_id": GOAL_ID,
                        "agent_id": AGENT_ID,
                        "run_id": prior_turn_id,
                        "status": "turn_run_once",
                        "summary": "prior Turn guard",
                        "details": details,
                    },
                    sort_keys=True,
                )
                + "\n"
            )

    recovery_rc, recovery = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--begin-turn",
        "--scan-path",
        str(project),
    )
    assert recovery_rc == 0, recovery
    assert recovery["effective_action"] == "unsettled_host_turn_recovery"
    packet = recovery["unsettled_host_turn_recovery"]
    assert packet["prior_turn_instance_id"] == prior_turn_id
    assert packet["binding_kind"] == "todo"
    assert packet["binding_id"] == TODO_ID
    assert packet["prior_event_id"] == "multi-receipt-identity-upgrade"


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
@pytest.mark.parametrize("hidden_count", [0, 6])
def test_prior_host_closeout_survives_hidden_todo_lifecycle(
    tmp_path: Path,
    hidden_count: int,
    provider: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from canonical_authority_fixture import (
        initialize_canonical_authority,
        isolate_sqlite_runtime,
    )
    from loopx.control_plane.coordination.runtime_shadow import (
        build_todo_runtime_shadow_projection,
    )

    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    guard = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--scan-path",
        str(project),
    )
    rc, prior = _run_cli(
        registry_path,
        runtime,
        *guard,
        "--turn-instance-id",
        "turn-hidden-closeout-prior",
        "--todo-id",
        TODO_ID,
    )
    assert rc == 0, prior
    assert prior["heartbeat_receipt"]["closeout_required"] is True
    rc, recovery = _run_cli(registry_path, runtime, *guard, "--begin-turn")
    assert rc == 0, recovery
    assert recovery["effective_action"] == "unsettled_host_turn_recovery"
    state = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    with state.open("a") as stream:
        for index in range(hidden_count):
            stream.write(
                f"\n- [ ] [P0] Wait for validation {index}.\n"
                f"  <!-- loopx:todo todo_id=todo_hidden_{index} status=blocked "
                "task_class=advancement_task -->\n"
            )
    if provider != "legacy":
        rc, listed = _run_cli(
            registry_path, runtime, "todo", "list", "--goal-id", GOAL_ID
        )
        assert rc == 0, listed
        projection = build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID,
            handoff_mode="soft_claim",
            todos=listed["todos"],
        )
        initialize_canonical_authority(
            runtime, GOAL_ID, projection, state_path=state, provider=provider
        )
    rc, update = _run_cli(
        registry_path,
        runtime,
        "todo",
        "update",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--status",
        "blocked",
        "--reason",
        "Waiting on independent validation.",
        "--successor-todo-id",
        ALTERNATIVE_TODO_ID,
    )
    assert rc == 0, json.dumps(update, indent=2)
    turn_id = recovery["heartbeat_receipt"]["turn_instance_id"]
    rc, observed = _run_cli(
        registry_path, runtime, *guard, "--turn-instance-id", turn_id
    )
    assert observed["effective_action"] != "unsettled_host_turn_recovery", observed.get(
        "unsettled_host_turn_recovery"
    )
    assert rc == 0, observed
    rc, resumed = _run_cli(
        registry_path,
        runtime,
        *guard,
        "--turn-instance-id",
        turn_id,
        "--todo-id",
        ALTERNATIVE_TODO_ID,
    )
    assert rc == 0, resumed
    assert resumed["selected_todo"]["todo_id"] == ALTERNATIVE_TODO_ID
    assert resumed["heartbeat_receipt"]["status"] == "upgraded"
    assert resumed["quota"]["spent_slots"] == prior["quota"]["spent_slots"]


def test_standard_codex_app_settlement_is_receipted_and_idempotent(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_read_only_todo(project)
    binding = (
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        TURN_ID,
    )

    guard_rc, guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        TURN_ID,
        "--scan-path",
        str(project),
    )

    assert guard_rc == 0, guard
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    assert identity["todo_id"] == TODO_ID
    assert identity["effect_id"] == (f"{GOAL_ID}:{AGENT_ID}:{TODO_ID}:{TURN_ID}")

    complete_args = (
        "todo",
        "complete",
        "--goal-id",
        GOAL_ID,
        *binding,
        "--claimed-by",
        AGENT_ID,
        "--evidence",
        "original delivery validated",
        "--next-agent-todo",
        "Continue the explicit successor delivery.",
        "--next-claimed-by",
        AGENT_ID,
        "--next-action-kind",
        "implement",
    )
    complete_rc, complete = _run_cli(
        registry_path,
        runtime,
        *complete_args,
    )

    assert complete_rc == 0, complete
    assert complete["settlement_result"]["ok"] is True
    assert [
        receipt["step_kind"] for receipt in complete["settlement_result"]["receipts"]
    ] == ["validation"]
    successor_id = complete["next_todos"][0]["todo_id"]
    assert successor_id != TODO_ID
    complete_replay_rc, complete_replay = _run_cli(
        registry_path,
        runtime,
        *complete_args,
    )
    assert complete_replay_rc == 0, complete_replay
    assert complete_replay["idempotent_replay"] is True
    assert complete_replay["settlement_result"]["ok"] is True

    refresh_args = (
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "validated_progress",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "outcome_progress",
        *binding,
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    refresh_rc, refresh = _run_cli(
        registry_path,
        runtime,
        *refresh_args,
    )

    assert refresh_rc == 0, refresh
    assert refresh["settlement_result"]["ok"] is True
    assert [
        receipt["step_kind"] for receipt in refresh["settlement_result"]["receipts"]
    ] == ["validation", "durable_writeback"]
    refresh_replay_rc, refresh_replay = _run_cli(
        registry_path,
        runtime,
        *refresh_args,
    )
    assert refresh_replay_rc == 0, refresh_replay
    assert refresh_replay["idempotent_replay"] is True
    assert _classification_count(runtime, "validated_progress") == 1

    fresh_guard_rc, fresh_guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--scan-path",
        str(project),
    )
    assert fresh_guard_rc == 0, fresh_guard
    assert fresh_guard["selected_todo"]["todo_id"] == successor_id

    spend_args = (
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
        *binding,
        "--scan-path",
        str(project),
    )
    spend_rc, spend = _run_cli(registry_path, runtime, *spend_args)
    replay_rc, replay = _run_cli(registry_path, runtime, *spend_args)

    assert spend_rc == 0, spend
    assert spend["settlement_result"]["ok"] is True
    assert [
        receipt["step_kind"] for receipt in spend["settlement_result"]["receipts"]
    ] == ["validation", "durable_writeback", "quota_spend"]
    assert replay_rc == 0, replay
    assert replay["idempotent_replay"] is True
    assert replay["appended"] is False
    assert _spend_run_count(runtime) == 1

    settled_replay_rc, settled_replay = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        TURN_ID,
        "--scan-path",
        str(project),
    )
    assert settled_replay_rc == 0, settled_replay
    assert settled_replay["decision"] == "skip"
    assert settled_replay["effective_action"] == "heartbeat_settled_skip"
    assert settled_replay["execution_obligation"]["must_attempt_work"] is False
    assert settled_replay.get("selected_todo") is None
    assert settled_replay["heartbeat_receipt"]["status"] == "replayed"
    assert (
        settled_replay["heartbeat_receipt"]["settlement_identity"]["todo_id"] == TODO_ID
    )
    assert _spend_run_count(runtime) == 1

    settled_ack_hint = settled_replay["scheduler_hint"]["codex_app"]["ack_hint"]
    assert settled_ack_hint["args"]["turn_instance_id"] == TURN_ID
    assert settled_ack_hint["cli_args"][-3:] == [
        "--turn-instance-id",
        TURN_ID,
        "--execute",
    ]
    ack_rc, ack = _run_cli(
        registry_path,
        runtime,
        *settled_ack_hint["cli_args"],
    )
    assert ack_rc == 0, ack
    assert ack["scheduler_state_mutated"] is True
    assert ack["already_applied"] is False

    fresh_turn_rc, fresh_turn = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        "turn-settlement-cli-2",
        "--scan-path",
        str(project),
    )
    assert fresh_turn_rc == 0, fresh_turn
    assert fresh_turn["selected_todo"]["todo_id"] == successor_id


def _assert_material_monitor_writeback_can_add_workspace_before_spend(
    tmp_path: Path,
    *,
    repository_write: bool,
    expected_requirement: str,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    if repository_write:
        _configure_repository_write_todo(project)
    _initialize_git_checkout(project)
    binding = (
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        TURN_ID,
    )

    guard_rc, guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        TURN_ID,
        "--scan-path",
        str(project),
    )
    assert guard_rc == 0, guard
    assert (
        guard["heartbeat_receipt"]["delivery_workspace_causality"]["requirement"]
        == expected_requirement
    )

    runs_dir = runtime / "goals" / GOAL_ID / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    material_monitor = {
        "generated_at": "2026-01-01T00:01:00+00:00",
        "goal_id": GOAL_ID,
        "classification": "quota_monitor_poll",
        "delivery_batch_scale": "single_surface",
        "delivery_outcome": "outcome_progress",
        "material_change": True,
        "agent_id": AGENT_ID,
        "todo_id": TODO_ID,
        "turn_instance_id": TURN_ID,
        "settlement_identity": guard["heartbeat_receipt"]["settlement_identity"],
    }
    (runs_dir / "index.jsonl").write_text(
        json.dumps(material_monitor, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    repair_rc, repair = _run_cli(
        registry_path,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "material_monitor_receipt_repair",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "outcome_progress",
        *binding,
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    assert repair_rc == 0, repair
    assert repair["receipt_repaired"] is True
    assert repair["appended"] is False

    supplement_args = (
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "material_monitor_workspace_supplemented",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "outcome_progress",
        "--delivery-workspace-path",
        str(project),
        *binding,
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    supplement_rc, supplement = _run_cli(
        registry_path,
        runtime,
        *supplement_args,
    )
    replay_rc, replay = _run_cli(
        registry_path,
        runtime,
        *supplement_args,
    )

    assert supplement_rc == 0, supplement
    assert supplement["appended"] is True
    assert supplement["delivery_workspace"] == {
        "schema_version": "delivery_workspace_v1",
        "workspace_identity": "git:github.com/example/read-only-settlement-fixture",
        "identity_kind": "git_repository",
        "task_repository": "git:github.com/example/read-only-settlement-fixture",
        "repository_source": "refresh_state.delivery_workspace_path",
        "workspace_kind": "canonical_checkout",
        "peer_independent_worktree_required": False,
    }
    assert replay_rc == 0, replay
    assert replay["idempotent_replay"] is True
    assert (
        _classification_count(runtime, "material_monitor_workspace_supplemented") == 1
    )

    spend_rc, spend = _run_cli(
        registry_path,
        runtime,
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
        *binding,
        "--scan-path",
        str(project),
        cwd=project,
    )
    assert spend_rc == 0, spend
    assert spend["delivery_workspace_validated"] is True
    assert spend["settlement_result"]["ok"] is True


def test_material_monitor_writeback_can_add_required_workspace_before_spend(
    tmp_path: Path,
) -> None:
    _assert_material_monitor_writeback_can_add_workspace_before_spend(
        tmp_path,
        repository_write=True,
        expected_requirement="required",
    )


def test_material_monitor_writeback_can_add_unknown_workspace_before_spend(
    tmp_path: Path,
) -> None:
    _assert_material_monitor_writeback_can_add_workspace_before_spend(
        tmp_path,
        repository_write=False,
        expected_requirement="unknown",
    )


def test_same_turn_identityless_guard_upgrades_and_settles_full_chain(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(
        tmp_path,
        required_capability="network",
    )
    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path.write_text(
        state_path.read_text(encoding="utf-8").replace(
            "action_kind=validate ",
            "action_kind=validate continuation_policy=same_agent_non_delivery ",
        ),
        encoding="utf-8",
    )
    binding = (
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        TURN_ID,
    )
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        TURN_ID,
        "--scan-path",
        str(project),
    )

    first_rc, first = _run_cli(registry_path, runtime, *guard_args)
    upgraded_rc, upgraded = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--available-capability",
        "network",
    )
    replay_rc, replay = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--available-capability",
        "network",
    )

    assert first_rc == 0, first
    assert "settlement_identity" not in first["heartbeat_receipt"]
    # The documented wake order commits this receipt before a work item exists,
    # so the guard has to say the turn still owes its binding instead of letting
    # the caller discover it when the closeout cannot settle.
    assert first["heartbeat_receipt"]["settlement_binding_owed"] is True
    assert upgraded_rc == 0, upgraded
    assert upgraded["heartbeat_receipt"]["status"] == "upgraded"
    assert "settlement_binding_owed" not in upgraded["heartbeat_receipt"]
    identity = upgraded["heartbeat_receipt"]["settlement_identity"]
    assert identity["todo_id"] == TODO_ID
    assert identity["effect_id"] == f"{GOAL_ID}:{AGENT_ID}:{TODO_ID}:{TURN_ID}"
    assert replay_rc == 0, replay
    assert replay["heartbeat_receipt"]["status"] == "replayed"
    assert (
        replay["heartbeat_receipt"]["event_id"]
        == upgraded["heartbeat_receipt"]["event_id"]
    )
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 2

    complete_rc, complete = _run_cli(
        registry_path,
        runtime,
        "todo",
        "complete",
        "--goal-id",
        GOAL_ID,
        *binding,
        "--claimed-by",
        AGENT_ID,
        "--evidence",
        "identity upgrade delivery validated",
        "--next-agent-todo",
        "Continue after the identity upgrade delivery.",
        "--next-claimed-by",
        AGENT_ID,
        "--next-action-kind",
        "implement",
    )
    assert complete_rc == 0, complete
    successor_id = complete["next_todos"][0]["todo_id"]
    assert successor_id != TODO_ID

    refresh_rc, refresh = _run_cli(
        registry_path,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "identity_upgrade_validated",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "outcome_progress",
        *binding,
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    assert refresh_rc == 0, refresh

    successor_guard_rc, successor_guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--available-capability",
        "network",
        "--scan-path",
        str(project),
    )
    assert successor_guard_rc == 0, successor_guard
    assert successor_guard["selected_todo"]["todo_id"] == successor_id

    spend_args = (
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
        *binding,
        "--scan-path",
        str(project),
    )
    spend_rc, spend = _run_cli(registry_path, runtime, *spend_args)
    spend_replay_rc, spend_replay = _run_cli(registry_path, runtime, *spend_args)

    assert spend_rc == 0, spend
    assert spend["settlement_result"]["ok"] is True
    assert spend_replay_rc == 0, spend_replay
    assert spend_replay["idempotent_replay"] is True
    assert spend_replay["appended"] is False
    assert _spend_run_count(runtime) == 1
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 2


def test_agent_selects_one_bounded_action_before_delivery_receipt_binding(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    turn_instance_id = "turn-agent-selection-1"
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
    )

    first_rc, first = _run_cli(registry_path, runtime, *guard_args)

    assert first_rc == 0, first
    assert (
        first["action_portfolio"]["selection_policy"]["requires_explicit_turn_binding"]
        is True
    )
    assert first["interaction_contract"]["agent_channel"]["selection_required"] is True
    assert first["interaction_contract"]["agent_channel"]["delivery_allowed"] is False
    assert "settlement_identity" not in first["heartbeat_receipt"]
    cli_channel = first["interaction_contract"]["cli_channel"]
    assert cli_channel["next_cli_actions"] == []
    selection_command = cli_channel["selection_command"]
    assert "--todo-id '{todo_id}'" in selection_command["command_args_template"]
    assert "--registry" in selection_command["route_prefix"]
    assert "--runtime-root" in selection_command["route_prefix"]

    selected_rc, selected = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--todo-id",
        ALTERNATIVE_TODO_ID,
    )

    assert selected_rc == 0, selected
    assert selected["selected_todo"]["todo_id"] == ALTERNATIVE_TODO_ID
    assert selected["selected_todo"]["selection_binding"] == "heartbeat_receipt"
    assert "action_portfolio" not in selected
    assert (
        selected["interaction_contract"]["agent_channel"].get("selection_required")
        is None
    )
    assert selected["interaction_contract"]["agent_channel"]["delivery_allowed"] is True
    assert selected["heartbeat_receipt"]["status"] == "upgraded"
    assert (
        selected["heartbeat_receipt"]["settlement_identity"]["todo_id"]
        == ALTERNATIVE_TODO_ID
    )
    plan_identity = selected["interaction_contract"]["cli_channel"]["settlement_plan"][
        "identity"
    ]
    assert plan_identity["todo_id"] == ALTERNATIVE_TODO_ID
    assert _heartbeat_receipt_count(runtime, turn_instance_id) == 2


@pytest.mark.parametrize("host_surface", ["codex-app", "codex-app-ssh"])
def test_guided_start_begins_one_turn_and_executes_returned_selection(
    tmp_path: Path,
    host_surface: str,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    packet = build_start_goal_guided_packet(
        project=project,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        cli_bin="loopx",
        host_surface=host_surface,
        goal_text="Start one accountable delivery turn.",
    )
    guard_command = next(
        step["command"]
        for step in packet["guided_transaction"]["ordered_steps"]
        if step["id"] == "quota_guard"
    )

    assert "--begin-turn" in guard_command
    assert "--turn-instance-id" not in guard_command
    first_rc, first = _run_generated_cli(
        guard_command,
        registry_path=registry_path,
    )

    assert first_rc == 0, first
    assert first["interaction_contract"]["cli_channel"]["selection_required"] is True
    turn_instance_id = first["heartbeat_receipt"]["turn_instance_id"]
    assert turn_instance_id.startswith("guided-start:")
    selection = first["interaction_contract"]["cli_channel"]["selection_command"]
    assert (
        f"--turn-instance-id {turn_instance_id}" in selection["command_args_template"]
    )
    selection_command = f"{selection['route_prefix']} " + selection[
        "command_args_template"
    ].replace("{todo_id}", ALTERNATIVE_TODO_ID)

    selected_rc, selected = _run_generated_cli(
        selection_command,
        registry_path=registry_path,
    )

    assert selected_rc == 0, selected
    assert selected["selected_todo"]["todo_id"] == ALTERNATIVE_TODO_ID
    assert selected["selected_todo"]["selection_binding"] == "heartbeat_receipt"
    receipt_identity = selected["heartbeat_receipt"]["settlement_identity"]
    assert receipt_identity["turn_instance_id"] == turn_instance_id
    cli_channel = selected["interaction_contract"]["cli_channel"]
    settlement_plan = cli_channel["settlement_plan"]
    assert settlement_plan["identity"] == receipt_identity
    assert f"--turn-instance-id {turn_instance_id}" in json.dumps(settlement_plan)
    expected_source = "heartbeat" if host_surface == "codex-app" else "visible-goal"
    assert any(
        f"--source {expected_source}" in action
        for action in cli_channel["next_cli_actions"]
    )
    assert _heartbeat_receipt_count(runtime, turn_instance_id) == 2


def test_visible_goal_continuation_begins_turn_and_executes_returned_selection(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    prompt = build_heartbeat_prompt(
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        runtime_profile="codex_app_ssh_goal",
        thin=True,
    )
    guard_command = prompt["quota_guard_command"].replace(
        "$HOME/.codex/loopx/registry.global.json",
        str(registry_path),
    )

    assert "--begin-turn" in guard_command
    assert "--turn-instance-id" not in guard_command
    first_rc, first = _run_generated_cli(
        guard_command,
        registry_path=registry_path,
    )

    assert first_rc == 0, first
    assert first["interaction_contract"]["cli_channel"]["selection_required"] is True
    turn_instance_id = first["heartbeat_receipt"]["turn_instance_id"]
    assert turn_instance_id.startswith("guided-start:")
    selection = first["interaction_contract"]["cli_channel"]["selection_command"]
    assert (
        f"--turn-instance-id {turn_instance_id}" in selection["command_args_template"]
    )
    selection_command = f"{selection['route_prefix']} " + selection[
        "command_args_template"
    ].replace("{todo_id}", ALTERNATIVE_TODO_ID)

    selected_rc, selected = _run_generated_cli(
        selection_command,
        registry_path=registry_path,
    )

    assert selected_rc == 0, selected
    assert selected["selected_todo"]["todo_id"] == ALTERNATIVE_TODO_ID
    assert selected["selected_todo"]["selection_binding"] == "heartbeat_receipt"
    assert (
        selected["heartbeat_receipt"]["settlement_identity"]["turn_instance_id"]
        == turn_instance_id
    )
    cli_channel = selected["interaction_contract"]["cli_channel"]
    settlement_plan = cli_channel["settlement_plan"]
    assert settlement_plan["identity"] == selected["heartbeat_receipt"][
        "settlement_identity"
    ]
    assert any(
        "--source visible-goal" in action for action in cli_channel["next_cli_actions"]
    )
    assert _heartbeat_receipt_count(runtime, turn_instance_id) == 2


@pytest.mark.parametrize("fallback_available", [False, True])
def test_visible_goal_capability_reentry_preserves_turn_through_selection(
    tmp_path: Path, fallback_available: bool,
) -> None:
    project, runtime, registry_path = _write_fixture(
        tmp_path,
        required_capability=None if fallback_available else "network",
    )
    _configure_runtime_capability_reentry_fixture(project)
    _configure_selectable_alternative(project, required_capability=None if fallback_available else "network")
    prompt = build_heartbeat_prompt(
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        runtime_profile="codex_app_ssh_goal",
        thin=True,
    )
    guard_command = prompt["quota_guard_command"].replace(
        "$HOME/.codex/loopx/registry.global.json",
        str(registry_path),
    )

    first_rc, first = _run_generated_cli(
        guard_command,
        registry_path=registry_path,
    )

    assert first_rc == 0, first
    turn_instance_id = first["heartbeat_receipt"]["turn_instance_id"]
    if fallback_available:
        assert first["interaction_contract"]["cli_channel"]["selection_required"] is True
        assert first["selected_todo"]["todo_id"] == TODO_ID
        assert first["interaction_contract"]["agent_channel"]["next_task_action"]["kind"] == "capability_verification"
        assert first["interaction_contract"]["cli_channel"]["next_cli_actions"][0] == first["runtime_capability_reentry"]["candidates"][0]["command"]
    reentry_command = first["runtime_capability_reentry"]["candidates"][0][
        "command"
    ]
    assert f"--turn-instance-id {turn_instance_id}" in reentry_command

    reentry_rc, reentry = _run_generated_cli(
        reentry_command,
        registry_path=registry_path,
    )

    assert reentry_rc == 0, reentry
    cli_channel = reentry["interaction_contract"]["cli_channel"]
    assert cli_channel["selection_required"] is True
    selection = cli_channel["selection_command"]
    assert f"--turn-instance-id {turn_instance_id}" in selection[
        "command_args_template"
    ]
    selection_command = f"{selection['route_prefix']} " + selection[
        "command_args_template"
    ].replace("{todo_id}", REENTRY_TODO_ID)

    selected_rc, selected = _run_generated_cli(
        selection_command,
        registry_path=registry_path,
    )

    assert selected_rc == 0, selected
    assert selected["selected_todo"]["todo_id"] == REENTRY_TODO_ID
    receipt_identity = selected["heartbeat_receipt"]["settlement_identity"]
    assert receipt_identity["turn_instance_id"] == turn_instance_id
    assert receipt_identity["todo_id"] == REENTRY_TODO_ID
    assert _heartbeat_receipt_count(runtime, turn_instance_id) == 2


@pytest.mark.parametrize("host_surface", ["codex-app", "codex-app-ssh"])
def test_single_todo_guided_start_keeps_direct_delivery_semantics(
    tmp_path: Path,
    host_surface: str,
) -> None:
    project, _runtime, registry_path = _write_fixture(tmp_path)
    packet = build_start_goal_guided_packet(
        project=project,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        cli_bin="loopx",
        host_surface=host_surface,
        goal_text="Start one accountable delivery turn.",
    )
    guard_command = next(
        step["command"]
        for step in packet["guided_transaction"]["ordered_steps"]
        if step["id"] == "quota_guard"
    )

    guard_rc, guard = _run_generated_cli(
        guard_command,
        registry_path=registry_path,
    )

    assert guard_rc == 0, guard
    assert guard["selected_todo"]["todo_id"] == TODO_ID
    assert (
        guard["interaction_contract"]["cli_channel"].get("selection_required") is None
    )
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    assert identity["todo_id"] == TODO_ID
    assert identity["turn_instance_id"].startswith("guided-start:")
    settlement_plan = guard["interaction_contract"]["cli_channel"][
        "settlement_plan"
    ]
    assert settlement_plan["identity"] == identity
    expected_source = "heartbeat" if host_surface == "codex-app" else "visible-goal"
    assert expected_source in next(
        step["command_template"]
        for step in settlement_plan["ordered_steps"]
        if step["kind"] == "quota_spend"
    )


def test_visible_goal_refresh_and_spend_preserve_selected_todo_causality(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_read_only_todo(project)

    guard_rc, guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--runtime-profile",
        "codex_app_ssh_goal",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--begin-turn",
        "--scan-path",
        str(project),
    )

    assert guard_rc == 0, guard
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    turn_instance_id = identity["turn_instance_id"]
    binding = (
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        turn_instance_id,
    )
    plan = guard["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert plan["identity"] == identity
    assert "--source visible-goal" in next(
        step["command_template"]
        for step in plan["ordered_steps"]
        if step["kind"] == "quota_spend"
    )

    refresh_rc, refresh = _run_cli(
        registry_path,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "visible_goal_delivery_validated",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "outcome_progress",
        *binding,
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    assert refresh_rc == 0, refresh
    assert refresh["todo_id"] == TODO_ID
    assert refresh["turn_instance_id"] == turn_instance_id
    assert refresh["delivery_workspace_causality"]["todo_id"] == TODO_ID
    assert refresh["delivery_workspace_causality"]["requirement"] == "not_required"

    spend_rc, spend = _run_cli(
        registry_path,
        runtime,
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--slots",
        "1",
        "--source",
        "visible-goal",
        "--execute",
        *binding,
        "--scan-path",
        str(project),
    )
    assert spend_rc == 0, spend
    assert spend["todo_id"] == TODO_ID
    assert spend["turn_instance_id"] == turn_instance_id
    assert spend["settlement_identity"] == identity
    assert spend["delivery_workspace_causality"]["todo_id"] == TODO_ID
    assert spend["delivery_workspace_causality"]["requirement"] == "not_required"
    assert _spend_run_count(runtime) == 1


def test_todo_guard_defers_replan_obligation_created_after_admission(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_read_only_todo(project)

    guard_rc, guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--runtime-profile",
        "codex_app_ssh_goal",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--begin-turn",
        "--scan-path",
        str(project),
    )

    assert guard_rc == 0, guard
    receipt = guard["heartbeat_receipt"]
    assert "semantic_replan_obligation_id" not in receipt
    identity = receipt["settlement_identity"]

    _append_surface_only_runs(
        runtime,
        count=AUTONOMOUS_REPLAN_PERIODIC_RUN_THRESHOLD,
    )

    refresh_rc, refresh = _run_cli(
        registry_path,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "turn_admitted_progress",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "outcome_progress",
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        identity["turn_instance_id"],
        "--no-global-sync",
        "--suppress-external-sinks",
    )

    assert refresh_rc == 0, refresh
    assert refresh["todo_id"] == TODO_ID
    assert refresh["turn_instance_id"] == identity["turn_instance_id"]


def test_legacy_todo_guard_keeps_current_replan_gate_strict(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry_path)
    _initialize_git_checkout(project)
    turn_instance_id = "turn-legacy-replan-guard"

    guard_rc, guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
    )
    assert guard_rc == 0, guard
    assert guard["decision"] == "autonomous_replan_required", guard

    log_path = runtime / "goals" / GOAL_ID / "rollout-event-log.jsonl"
    events = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for event in events:
        if event.get("event_kind") == "quota_should_run":
            event["details"].pop("semantic_replan_obligation_id", None)
    log_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    refresh_rc, refresh = _run_cli(
        registry_path,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--todo-id",
        SELECTED_REPLAN_TODO_ID,
        "--delivery-workspace-path",
        str(project),
        "--delivery-outcome",
        "outcome_progress",
        "--classification",
        "validated_progress",
        "--progress-result-class",
        "advanced",
        "--progress-evidence-id",
        "evidence:legacy-guard",
    )

    assert refresh_rc == 1, refresh
    assert "requires a typed semantic delta" in refresh["error"]


def test_visible_goal_unbound_spend_recovers_delivery_after_capability_replan(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    state_path = _configure_repository_write_todo(project)
    state_path.write_text(
        state_path.read_text(encoding="utf-8").replace(
            "required_capabilities=filesystem_write",
            "required_capabilities=filesystem_write%2Cnetwork",
        ),
        encoding="utf-8",
    )
    _initialize_git_checkout(project)

    guard_rc, guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--runtime-profile",
        "codex_app_ssh_goal",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--available-capability",
        "network",
        "--begin-turn",
        "--scan-path",
        str(project),
        cwd=project,
    )

    assert guard_rc == 0, guard
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    turn_instance_id = identity["turn_instance_id"]
    binding = (
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        turn_instance_id,
    )
    refresh_rc, refresh = _run_cli(
        registry_path,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "capability_replan_delivery_validated",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "outcome_progress",
        "--delivery-workspace-path",
        str(project),
        *binding,
        "--no-global-sync",
        "--suppress-external-sinks",
        cwd=project,
    )

    assert refresh_rc == 0, refresh
    assert refresh["settlement_identity"] == identity
    assert refresh["delivery_workspace_causality"]["requirement"] == "required"

    spend_args = (
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--slots",
        "1",
        "--source",
        "visible-goal",
        "--execute",
        "--agent-id",
        AGENT_ID,
        "--scan-path",
        str(project),
    )
    spend_rc, spend = _run_cli(
        registry_path,
        runtime,
        *spend_args,
        cwd=project,
    )
    replay_rc, replay = _run_cli(
        registry_path,
        runtime,
        *spend_args,
        cwd=project,
    )

    assert spend_rc == 0, spend
    assert spend["todo_id"] == TODO_ID
    assert spend["turn_instance_id"] == turn_instance_id
    assert spend["settlement_identity"] == identity
    assert spend["delivery_completion_spend"] is True
    assert spend["capability_repair_spend"] is False
    assert spend["delivery_workspace_validated"] is True
    assert spend["delivery_workspace_causality"]["todo_id"] == TODO_ID
    assert spend["delivery_workspace_causality"]["requirement"] == "required"
    assert replay_rc == 0, replay
    assert replay.get("idempotent_replay") is True, replay
    assert replay["appended"] is False
    assert replay["settlement_identity"] == identity
    assert _spend_run_count(runtime) == 1


@pytest.mark.parametrize("profile", ["codex_app_ssh_goal", "codex_cli", "ark_managed_agent_goal"])
def test_unbound_visible_goal_spend_returns_typed_mismatch_without_receipt(
    tmp_path: Path, profile: str,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)

    guard_rc, guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--runtime-profile",
        profile,
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--scan-path",
        str(project),
    )
    assert guard_rc == 0, guard
    actions = guard["interaction_contract"]["cli_channel"]["next_cli_actions"]
    assert len(actions) == 1
    if profile == "codex_app_ssh_goal":
        assert actions[0].endswith("--begin-turn")
    else:
        assert "--turn-instance-id" in actions[0]
        assert "--begin-turn" not in actions[0]
    assert all("spend-slot" not in action for action in actions)

    spend_rc, spend = _run_cli(
        registry_path,
        runtime,
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--slots",
        "1",
        "--source",
        "visible-goal",
        "--execute",
        "--agent-id",
        AGENT_ID,
        "--scan-path",
        str(project),
    )
    assert spend_rc == 1, spend
    assert spend["appended"] is False
    assert spend["settlement_result"]["failure"]["kind"] == "identity_mismatch"
    assert spend["settlement_result"]["failure"]["step_kind"] == "validation"
    assert spend["delivery_workspace_causality"] is None
    assert _spend_run_count(runtime) == 0

    # Execute the projected re-entry, filling only the host-owned identity.
    command = actions[0].replace(
        "<unique-work-iteration-id-reuse-on-retry>", TURN_ID,
    )
    bound_rc, bound = _run_generated_cli(command, registry_path=registry_path)
    assert bound_rc == 0, bound
    plan = bound["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert plan["identity"]["todo_id"] == TODO_ID
    assert plan["identity"]["agent_id"] == AGENT_ID
    if profile != "codex_app_ssh_goal":
        assert plan["identity"]["turn_instance_id"] == TURN_ID
    assert [step["kind"] for step in plan["ordered_steps"]] == [
        "validation", "durable_writeback", "quota_spend", "terminal_closeout",
    ]
    assert _spend_run_count(runtime) == 0


def test_begin_turn_rejects_a_non_receipt_runtime_profile(tmp_path: Path) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)

    guard_rc, guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--runtime-profile",
        "generic_cli",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--begin-turn",
        "--scan-path",
        str(project),
    )

    assert guard_rc == 1, guard
    assert guard["error_code"] == "QUOTA_VALIDATION_FAILED"
    # The rejection must also tell an agent-CLI host how to start its turn:
    # Kiro CLI, ZCode, agy, Gemini CLI, Cursor and custom runners all land on
    # generic_cli and mint their own identity instead.
    assert guard["reason"] == (
        "--begin-turn requires runtime-profile codex_app_heartbeat, "
        "trae_app, or codex_app_ssh_goal; every other host starts its turn by "
        "passing its own --turn-instance-id"
    )


def test_agent_can_select_eligible_todo_outside_bounded_suggestions(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    turn_instance_id = "turn-agent-selection-invalid"
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
    )
    first_rc, first = _run_cli(registry_path, runtime, *guard_args)
    selected_rc, selected = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--todo-id",
        OUTSIDE_BOUNDED_PORTFOLIO_TODO_ID,
    )

    assert first_rc == 0, first
    assert OUTSIDE_BOUNDED_PORTFOLIO_TODO_ID not in {
        item["todo_id"] for item in first["action_portfolio"]["suggested_actions"]
    }
    assert selected_rc == 0, selected
    assert selected["selected_todo"]["todo_id"] == (OUTSIDE_BOUNDED_PORTFOLIO_TODO_ID)
    assert selected["selected_todo"]["selection_binding"] == ("heartbeat_receipt")
    assert selected["heartbeat_receipt"]["status"] == "upgraded"
    assert selected["heartbeat_receipt"]["settlement_identity"]["todo_id"] == (
        OUTSIDE_BOUNDED_PORTFOLIO_TODO_ID
    )
    assert _heartbeat_receipt_count(runtime, turn_instance_id) == 2


def _configure_deep_alternative(project: Path, *, fillers: int = 9) -> None:
    """Add one owned advancement Todo beyond every bounded suggestion lane."""

    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    filler_rows = "".join(
        f"- [ ] [P1] Advance filler delivery {index}.\n"
        f"  <!-- loopx:todo todo_id=todo_fixture_filler_{index} status=open "
        "task_class=advancement_task action_kind=implement -->\n"
        for index in range(fillers)
    )
    state_path.write_text(
        state_path.read_text(encoding="utf-8").rstrip()
        + "\n"
        + filler_rows
        + "- [ ] [P1] Advance the deep alternative delivery.\n"
        + "  <!-- loopx:todo "
        + f"todo_id={DEEP_ALTERNATIVE_TODO_ID} status=open "
        + "task_class=advancement_task action_kind=implement -->\n",
        encoding="utf-8",
    )


def test_agent_can_select_an_owned_todo_outside_every_bounded_lane(
    tmp_path: Path,
) -> None:
    """The display lanes are a presentation budget, not the eligible Todo set."""

    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_deep_alternative(project)
    turn_instance_id = "turn-agent-selection-beyond-lanes"
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
    )
    first_rc, first = _run_cli(registry_path, runtime, *guard_args)
    selected_rc, selected = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--todo-id",
        DEEP_ALTERNATIVE_TODO_ID,
    )

    assert first_rc == 0, first
    # The row is a real, open, typed advancement Todo that this Agent owns, and
    # no bounded presentation lane lists it.
    assert DEEP_ALTERNATIVE_TODO_ID not in {
        item["todo_id"] for item in first["action_portfolio"]["suggested_actions"]
    }
    # Every bounded lane this payload publishes must not list it either; the
    # count guard keeps the check from passing vacuously on an empty payload.
    bounded_lanes = [
        (key, items)
        for key, items in first["agent_todo_summary"].items()
        if key.endswith("executable_items") and isinstance(items, list)
    ]
    assert bounded_lanes, sorted(first["agent_todo_summary"])
    for key, items in bounded_lanes:
        assert DEEP_ALTERNATIVE_TODO_ID not in {
            item["todo_id"] for item in items
        }, key
    assert selected_rc == 0, selected
    assert selected["ok"] is True
    assert selected["selected_todo"]["todo_id"] == DEEP_ALTERNATIVE_TODO_ID
    assert selected["selected_todo"]["selection_binding"] == "heartbeat_receipt"
    assert selected["heartbeat_receipt"]["status"] == "upgraded"
    assert selected["heartbeat_receipt"]["settlement_identity"]["todo_id"] == (
        DEEP_ALTERNATIVE_TODO_ID
    )


def test_same_turn_can_select_eligible_todo_created_after_unbound_receipt(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    turn_instance_id = "turn-agent-selection-after-todo-create"
    late_todo_id = "todo_fixture_late_alternative"
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
    )
    first_rc, first = _run_cli(registry_path, runtime, *guard_args)
    assert first_rc == 0, first
    assert "settlement_identity" not in first["heartbeat_receipt"]

    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_text = state_path.read_text(encoding="utf-8")
    state_path.write_text(
        state_text.replace(
            "## Agent Todo\n\n",
            "## Agent Todo\n\n"
            "- [ ] [P1] Advance the newly created same-Turn alternative.\n"
            f"  <!-- loopx:todo todo_id={late_todo_id} status=open "
            "task_class=advancement_task action_kind=research "
            f"claimed_by={AGENT_ID} -->\n",
        ),
        encoding="utf-8",
    )
    reentry_rc, reentry = _run_cli(registry_path, runtime, *guard_args)
    assert reentry_rc == 0, reentry
    assert late_todo_id in {
        item["todo_id"]
        for item in reentry["action_portfolio"]["suggested_actions"]
    }

    selected_rc, selected = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--todo-id",
        late_todo_id,
    )
    assert selected_rc == 0, selected
    assert selected["action_selection_qualification"]["state"] == "qualified"
    assert selected["selected_todo"]["todo_id"] == late_todo_id
    assert selected["selected_todo"]["selection_binding"] == (
        "heartbeat_receipt"
    )
    assert selected["heartbeat_receipt"]["status"] == "upgraded"
    assert selected["heartbeat_receipt"]["settlement_identity"]["todo_id"] == (
        late_todo_id
    )
    assert _heartbeat_receipt_count(runtime, turn_instance_id) == 2


def test_agent_selection_rejects_unprojected_todo(tmp_path: Path) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    turn_instance_id = "turn-agent-selection-unprojected"
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
    )
    first_rc, first = _run_cli(registry_path, runtime, *guard_args)
    invalid_rc, invalid = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--todo-id",
        "todo_not_projected",
    )

    assert first_rc == 0, first
    assert invalid_rc != 0, invalid
    assert invalid["ok"] is False
    assert invalid["error_code"] == "quota_action_selection_rejected"
    assert invalid["effective_action"] == "quota_skip"
    assert invalid["should_run"] is False
    assert invalid["action_selection_qualification"] == {
        "schema_version": "action_selection_qualification_v0",
        "state": "rejected",
        "recovery_action": "reenter_guard_without_selection",
        "requested_todo_id": "todo_not_projected",
        "reason": "candidate_not_currently_eligible",
    }
    assert invalid["heartbeat_receipt"]["status"] == "replayed"
    assert invalid["rollout_event"]["appended"] is False
    assert _heartbeat_receipt_count(runtime, turn_instance_id) == 1


def test_unsuggested_selection_revalidates_current_capability_readiness(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_text = state_path.read_text(encoding="utf-8")
    state_path.write_text(
        state_text.replace(
            f"todo_id={OUTSIDE_BOUNDED_PORTFOLIO_TODO_ID} status=open ",
            f"todo_id={OUTSIDE_BOUNDED_PORTFOLIO_TODO_ID} status=open "
            "required_capabilities=network ",
        ),
        encoding="utf-8",
    )
    turn_instance_id = "turn-agent-selection-capability-blocked"
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
    )

    first_rc, first = _run_cli(registry_path, runtime, *guard_args)
    blocked_rc, blocked = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--todo-id",
        OUTSIDE_BOUNDED_PORTFOLIO_TODO_ID,
    )

    assert first_rc == 0, first
    assert blocked_rc != 0, blocked
    assert blocked["error_code"] == "quota_action_selection_rejected"
    assert blocked["action_selection_qualification"]["reason"] == (
        "candidate_not_currently_eligible"
    )
    assert blocked["heartbeat_receipt"]["status"] == "replayed"
    assert _heartbeat_receipt_count(runtime, turn_instance_id) == 1


def test_first_call_rejected_selection_does_not_commit_a_false_receipt(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    turn_instance_id = "turn-agent-selection-first-call-rejected"

    rejected_rc, rejected = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
        "--todo-id",
        "todo_not_projected",
    )

    assert rejected_rc == 1, rejected
    assert rejected["error_code"] == "quota_action_selection_rejected"
    assert rejected["heartbeat_receipt"] == {
        "schema_version": "heartbeat_quota_receipt_v0",
        "turn_instance_id": turn_instance_id,
        "status": "not_committed",
        "stall_observation": "not_evaluated",
        "reason_code": "quota_action_selection_rejected",
    }
    assert _heartbeat_receipt_count(runtime, turn_instance_id) == 0


def test_first_call_agent_selection_is_qualified_before_receipt_commit(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    turn_instance_id = "turn-agent-selection-without-portfolio"

    selected_rc, selected = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
        "--todo-id",
        ALTERNATIVE_TODO_ID,
    )

    assert selected_rc == 0, selected
    assert selected["selected_todo"]["todo_id"] == ALTERNATIVE_TODO_ID
    assert selected["selected_todo"]["selection_binding"] == "heartbeat_receipt"
    assert selected["heartbeat_receipt"]["status"] == "committed"
    assert selected["heartbeat_receipt"]["settlement_identity"]["todo_id"] == (
        ALTERNATIVE_TODO_ID
    )
    assert _heartbeat_receipt_count(runtime, turn_instance_id) == 1


def test_pending_selection_preserves_workspace_repair_then_reenters_same_turn(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["goals"][0]["coordination"]["registered_agents"].append(
        "codex-settlement-peer"
    )
    registry_path.write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _initialize_git_checkout(project)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=LoopX Test",
            "-c",
            "user.email=loopx-test@example.invalid",
            "commit",
            "--quiet",
            "--allow-empty",
            "-m",
            "fixture",
        ],
        cwd=project,
        check=True,
    )
    turn_instance_id = "turn-pending-selection-workspace-repair"
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
    )

    first_rc, first = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        cwd=project,
    )
    repair_rc, repair = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--todo-id",
        ALTERNATIVE_TODO_ID,
        cwd=project,
    )

    assert first_rc == 0, first
    assert "settlement_identity" not in first["heartbeat_receipt"]
    assert repair_rc == 0, repair
    assert repair.get("error_code") != "heartbeat_receipt_identity_conflict"
    assert repair["effective_action"] == "agent_workspace_repair"
    assert repair["workspace_repair_allowed"] is True
    assert repair["selected_todo"]["todo_id"] == ALTERNATIVE_TODO_ID
    assert repair["selected_todo"]["selection_binding"] == "heartbeat_receipt"
    assert repair["execution_obligation"]["kind"] == "agent_workspace_repair"
    assert repair["interaction_contract"]["agent_channel"]["primary_action"] == (
        "create or switch to an independent worktree/branch, then rerun quota "
        "guard before file edits"
    )
    assert repair["heartbeat_receipt"]["status"] == "upgraded"
    assert repair["heartbeat_receipt"]["settlement_identity"]["todo_id"] == (
        ALTERNATIVE_TODO_ID
    )

    linked_worktree = tmp_path / "linked-worktree"
    subprocess.run(
        ["git", "worktree", "add", "--quiet", "--detach", str(linked_worktree)],
        cwd=project,
        check=True,
    )
    resumed_rc, resumed = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--todo-id",
        ALTERNATIVE_TODO_ID,
        cwd=linked_worktree,
    )

    assert resumed_rc == 0, resumed
    assert resumed["effective_action"] == "normal_run"
    assert resumed["normal_delivery_allowed"] is True
    assert resumed["workspace_repair_allowed"] is False
    assert resumed["selected_todo"]["todo_id"] == ALTERNATIVE_TODO_ID
    assert resumed["selected_todo"]["selection_binding"] == "heartbeat_receipt"
    assert resumed["heartbeat_receipt"]["status"] == "replayed"
    assert _heartbeat_receipt_count(runtime, turn_instance_id) == 2


def test_boundary_projection_repair_keeps_same_turn_alternative_selectable(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_boundary_blocked_primary(project)
    _configure_selectable_alternative(project)
    turn_instance_id = "turn-boundary-repair-alternative-selection"
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
    )

    first_rc, first = _run_cli(registry_path, runtime, *guard_args)
    selected_rc, selected = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--todo-id",
        ALTERNATIVE_TODO_ID,
    )

    assert first_rc == 0, first
    assert first["effective_action"] == "boundary_projection_repair"
    assert first["selected_todo"]["todo_id"] == TODO_ID
    assert first["action_portfolio"]["selection_policy"][
        "requires_explicit_turn_binding"
    ] is True
    assert "settlement_identity" not in first["heartbeat_receipt"]
    assert selected_rc == 0, selected
    assert selected.get("error_code") != "heartbeat_receipt_identity_conflict"
    assert selected["effective_action"] == "normal_run"
    assert selected["normal_delivery_allowed"] is True
    assert selected["selected_todo"]["todo_id"] == ALTERNATIVE_TODO_ID
    assert selected["selected_todo"]["selection_binding"] == "heartbeat_receipt"
    assert selected["heartbeat_receipt"]["status"] == "upgraded"
    assert selected["heartbeat_receipt"]["settlement_identity"]["todo_id"] == (
        ALTERNATIVE_TODO_ID
    )
    assert _heartbeat_receipt_count(runtime, turn_instance_id) == 2


def test_selection_added_after_pending_guard_reports_final_boundary(
    tmp_path: Path,
) -> None:
    """A late Todo must re-enter the final scope guard without a false receipt."""

    from canonical_authority_fixture import initialize_canonical_authority
    from loopx.control_plane.coordination.runtime_shadow import (
        build_todo_runtime_shadow_projection,
    )
    from loopx.todos import list_goal_todos

    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    items = list_goal_todos(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        role="agent",
    )["todos"]
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID,
        todos=items,
        leases=[],
        handoff_mode="soft_claim",
    )
    initialize_canonical_authority(
        runtime,
        GOAL_ID,
        projection,
        state_path=state_path,
    )

    turn_instance_id = "turn-selection-added-after-guard"
    guard_args = (
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--codex-app",
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
    )
    first_rc, first = _run_cli(registry_path, runtime, *guard_args)
    assert first_rc == 0, first
    assert first["interaction_contract"]["agent_channel"]["selection_required"] is True
    assert "settlement_identity" not in first["heartbeat_receipt"]
    first_receipt = next(
        event
        for event in _heartbeat_receipt_events(runtime, turn_instance_id)
    )

    added_rc, added = _run_cli(
        registry_path,
        runtime,
        "todo",
        "add",
        "--goal-id",
        GOAL_ID,
        "--role",
        "agent",
        "--task-class",
        "advancement_task",
        "--action-kind",
        "implement",
        "--claimed-by",
        AGENT_ID,
        "--text",
        "Validate the new canonical task",
        "--task-repository",
        "git:github.com/example/read-only-settlement-fixture",
        "--required-write-scope",
        "loopx/**",
    )
    assert added_rc == 0, added
    selection_args = (*guard_args, "--todo-id", added["todo_id"])
    rejected_rc, rejected = _run_cli(
        registry_path,
        runtime,
        *selection_args,
    )

    assert rejected_rc == 1, rejected
    assert rejected["error_code"] == "quota_action_selection_deferred"
    assert rejected["effective_action"] == "quota_skip"
    assert rejected["should_run"] is False
    assert rejected["action_selection_qualification"]["reason"] == "control_repair"
    assert rejected["action_selection_qualification"]["requested_todo_id"] == (
        added["todo_id"]
    )
    assert rejected["normal_delivery_allowed"] is False
    assert rejected["heartbeat_receipt"]["status"] == "selection_retained"
    assert rejected["heartbeat_receipt"]["pending_action_selection"]["todo_id"] == (
        added["todo_id"]
    )
    retained_events = _heartbeat_receipt_events(runtime, turn_instance_id)
    assert retained_events[0] == first_receipt
    assert len(retained_events) == 2

    repeated_rc, repeated = _run_cli(
        registry_path,
        runtime,
        *selection_args,
    )
    assert repeated_rc == 1, repeated
    assert repeated["action_selection_qualification"] == (
        rejected["action_selection_qualification"]
    )
    assert "rerun quota should-run" in repeated["recommended_action"]
    retry_rc, retry = _run_cli(
        registry_path,
        runtime,
        *guard_args,
    )
    assert retry_rc == 0, retry
    assert retry["normal_delivery_allowed"] is False
    assert retry["workspace_repair_allowed"] or retry["self_repair_allowed"]


def test_pending_action_selection_does_not_preempt_newly_due_monitor(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    turn_instance_id = "turn-pending-selection-monitor-preemption"
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
    )
    first_rc, first = _run_cli(registry_path, runtime, *guard_args)
    assert first_rc == 0, first
    assert "settlement_identity" not in first["heartbeat_receipt"]

    _append_newly_due_monitor(project)
    selected_rc, selected = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--todo-id",
        ALTERNATIVE_TODO_ID,
        "--available-capability",
        "network",
        "--available-capability",
        "external_evidence_poll",
    )

    assert selected_rc == 1, selected
    assert selected["error_code"] == "quota_action_selection_deferred"
    assert selected["action_selection_qualification"]["reason"] == (
        "blocking_work_lane"
    )
    assert selected["heartbeat_receipt"]["status"] == "selection_retained"
    assert selected["heartbeat_receipt"]["pending_action_selection"]["todo_id"] == (
        ALTERNATIVE_TODO_ID
    )
    assert selected["rollout_event"]["appended"] is True
    events = _heartbeat_receipt_events(runtime, turn_instance_id)
    assert len(events) == 2
    assert all(not event["details"].get("todo_id") for event in events)
    assert all(not event["details"].get("settlement_effect_id") for event in events)


def test_pending_action_selection_reports_autonomous_replan_preemption(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    turn_instance_id = "turn-pending-selection-replan-preemption"
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
    )
    first_rc, first = _run_cli(registry_path, runtime, *guard_args)
    assert first_rc == 0, first
    assert first["decision"] == "run"
    assert "settlement_identity" not in first["heartbeat_receipt"]

    _append_surface_only_runs(
        runtime,
        count=AUTONOMOUS_REPLAN_PERIODIC_RUN_THRESHOLD,
    )
    selected_rc, selected = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--todo-id",
        ALTERNATIVE_TODO_ID,
    )

    assert selected_rc == 1, selected
    assert selected["error_code"] == "quota_action_selection_deferred"
    assert selected["action_selection_qualification"] == {
        "schema_version": "action_selection_qualification_v0",
        "state": "deferred",
        "recovery_action": "reenter_guard_without_selection",
        "requested_todo_id": ALTERNATIVE_TODO_ID,
        "reason": "autonomous_replan",
        "delivery_preemptions": ["autonomous_replan", "delivery_not_allowed"],
    }
    assert selected["heartbeat_receipt"]["status"] == "selection_retained"
    assert selected["heartbeat_receipt"]["pending_action_selection"]["todo_id"] == (
        ALTERNATIVE_TODO_ID
    )
    assert selected["rollout_event"]["appended"] is True
    assert _heartbeat_receipt_count(runtime, turn_instance_id) == 2


def test_due_monitor_auxiliary_context_has_typed_selection_rejection(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    _append_newly_due_monitor(project, priority="P2-monitor")
    turn_instance_id = "turn-auxiliary-due-monitor-selection"
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
        "--available-capability",
        "network",
        "--available-capability",
        "external_evidence_poll",
    )
    first_rc, first = _run_cli(registry_path, runtime, *guard_args)
    assert first_rc == 0, first
    assert first["selected_todo"]["todo_id"] == TODO_ID
    assert first["work_lane_contract"]["lane"] == "advancement_task"
    assert first["work_lane_contract"]["monitor_policy"] == (
        "material_transition_only"
    )
    assert "settlement_identity" not in first["heartbeat_receipt"]

    selected_rc, selected = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--todo-id",
        DUE_MONITOR_TODO_ID,
    )

    assert selected_rc == 1, selected
    assert selected["error_code"] == "quota_action_selection_rejected"
    assert selected["action_selection_qualification"]["state"] == "rejected"
    assert selected["action_selection_qualification"]["reason"] == (
        "auxiliary_monitor_not_selectable_in_advancement_lane"
    )
    assert selected["recommended_action"].startswith(
        "the due monitor is visible as auxiliary context"
    )
    assert selected["heartbeat_receipt"]["status"] == "replayed"
    assert selected["rollout_event"]["appended"] is False
    assert _heartbeat_receipt_count(runtime, turn_instance_id) == 1


def test_pending_action_selection_can_bind_exact_newly_due_monitor(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    turn_instance_id = "turn-pending-selection-exact-due-monitor"
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
    )
    first_rc, first = _run_cli(registry_path, runtime, *guard_args)
    assert first_rc == 0, first
    assert "settlement_identity" not in first["heartbeat_receipt"]

    _append_newly_due_monitor(project)
    selected_rc, selected = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--todo-id",
        DUE_MONITOR_TODO_ID,
        "--available-capability",
        "network",
        "--available-capability",
        "external_evidence_poll",
    )

    assert selected_rc == 0, selected
    assert selected["selected_todo"]["todo_id"] == DUE_MONITOR_TODO_ID
    assert selected["selected_todo"]["selection_binding"] == "heartbeat_receipt"
    assert selected["heartbeat_receipt"]["status"] == "upgraded"
    assert (
        selected["heartbeat_receipt"]["settlement_identity"]["todo_id"]
        == DUE_MONITOR_TODO_ID
    )
    assert _heartbeat_receipt_count(runtime, turn_instance_id) == 2

    poll_args = (
        "quota",
        "monitor-poll",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--todo-id",
        DUE_MONITOR_TODO_ID,
        "--target-key",
        "due-monitor-fixture",
        "--result-hash",
        "unchanged-due-monitor",
        "--available-capability",
        "network",
        "--available-capability",
        "external_evidence_poll",
        "--execute",
        "--scan-path",
        str(project),
    )
    poll_rc, poll = _run_cli(registry_path, runtime, *poll_args)
    poll_replay_rc, poll_replay = _run_cli(
        registry_path,
        runtime,
        *poll_args,
    )

    assert poll_rc == 0, poll
    assert poll["material_change"] is False
    assert poll["replayed"] is False
    assert poll_replay_rc == 0, poll_replay
    assert poll_replay["replayed"] is True
    assert poll_replay["appended"] is False
    assert _classification_count(runtime, "quota_monitor_poll") == 1
    assert _spend_run_count(runtime) == 0

    settled_rc, settled = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--available-capability",
        "network",
        "--available-capability",
        "external_evidence_poll",
    )
    assert settled_rc == 0, settled
    assert settled["effective_action"] == "heartbeat_settled_skip"
    assert settled["execution_obligation"]["must_attempt_work"] is False
    assert settled["heartbeat_receipt"]["status"] == "replayed"
    assert _spend_run_count(runtime) == 0

def test_prior_monitor_turn_accepts_exact_committed_receipt(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _append_newly_due_monitor(project)
    prior_turn_id = "turn-monitor-exact-closeout"
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--scan-path",
        str(project),
        "--available-capability",
        "network",
        "--available-capability",
        "external_evidence_poll",
    )
    prior_rc, prior = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--turn-instance-id",
        prior_turn_id,
        "--todo-id",
        DUE_MONITOR_TODO_ID,
    )
    assert prior_rc == 0, prior
    assert prior["selected_todo"]["todo_id"] == DUE_MONITOR_TODO_ID

    poll_rc, poll = _run_cli(
        registry_path,
        runtime,
        "quota",
        "monitor-poll",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        prior_turn_id,
        "--todo-id",
        DUE_MONITOR_TODO_ID,
        "--target-key",
        "due-monitor-fixture",
        "--result-hash",
        "unchanged-exact-closeout",
        "--available-capability",
        "network",
        "--available-capability",
        "external_evidence_poll",
        "--execute",
        "--scan-path",
        str(project),
    )
    assert poll_rc == 0, poll
    assert poll["material_change"] is False
    assert poll["appended"] is True

    next_turn_rc, next_turn = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--turn-instance-id",
        "turn-after-exact-monitor-closeout",
    )
    assert next_turn_rc == 0, next_turn
    assert next_turn["effective_action"] != "unsettled_host_turn_recovery"
    assert next_turn["selected_todo"]["todo_id"] == TODO_ID
    assert _spend_run_count(runtime) == 0


def test_prior_monitor_turn_recovery_rejects_wrong_identity_receipt(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _append_newly_due_monitor(project)
    prior_turn_id = "turn-monitor-wrong-identity"
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--scan-path",
        str(project),
        "--available-capability",
        "network",
        "--available-capability",
        "external_evidence_poll",
    )
    prior_rc, prior = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--turn-instance-id",
        prior_turn_id,
        "--todo-id",
        DUE_MONITOR_TODO_ID,
    )
    assert prior_rc == 0, prior
    assert prior["selected_todo"]["todo_id"] == DUE_MONITOR_TODO_ID

    preview_rc, preview = _run_cli(
        registry_path,
        runtime,
        "quota",
        "monitor-poll",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        prior_turn_id,
        "--todo-id",
        DUE_MONITOR_TODO_ID,
        "--target-key",
        "due-monitor-fixture",
        "--result-hash",
        "preview-only",
        "--available-capability",
        "network",
        "--available-capability",
        "external_evidence_poll",
        "--scan-path",
        str(project),
    )
    assert preview_rc == 0, preview
    assert preview["dry_run"] is True
    assert preview["appended"] is False
    assert _classification_count(runtime, "quota_monitor_poll") == 0

    index_path = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "classification": "quota_monitor_poll",
                    "goal_id": GOAL_ID,
                    "agent_id": "different-agent",
                    "todo_id": DUE_MONITOR_TODO_ID,
                    "turn_instance_id": prior_turn_id,
                    "quota_monitor_poll_commit": {
                        "schema_version": "quota_monitor_poll_commit_receipt_v0",
                        "effect_id": "quota-monitor-poll:wrong-agent-fixture",
                        "request_digest": "sha256:wrong-agent-fixture",
                    },
                }
            )
            + "\n"
        )

    recovery_rc, recovery = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--turn-instance-id",
        "turn-after-wrong-monitor-receipt",
    )
    assert recovery_rc == 0, recovery
    assert recovery["effective_action"] == "unsettled_host_turn_recovery"
    packet = recovery["unsettled_host_turn_recovery"]
    assert packet["binding_task_class"] == "continuous_monitor"
    actions = recovery["interaction_contract"]["cli_channel"]["next_cli_actions"]
    assert "quota monitor-poll" in actions[1]
    assert f"--turn-instance-id {prior_turn_id}" in actions[1]
    assert "--resume-when" not in "\n".join(actions)


def test_receipt_bound_advancement_turn_records_multiple_due_monitors(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    monitors = [
        ("todo_fixture_monitor_alpha", "monitor-alpha"),
        ("todo_fixture_monitor_beta", "monitor-beta"),
        ("todo_fixture_monitor_gamma", "monitor-gamma"),
    ]
    _append_due_monitors(project, monitors)
    turn_instance_id = "turn-multiple-auxiliary-monitors"
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
        "--available-capability",
        "network",
        "--available-capability",
        "external_evidence_poll",
    )
    first_rc, first = _run_cli(registry_path, runtime, *guard_args)

    assert first_rc == 0, first
    assert first["selected_todo"]["todo_id"] == TODO_ID
    assert first["heartbeat_receipt"]["settlement_identity"]["todo_id"] == (
        TODO_ID
    )
    projected_due = [
        *(first["work_lane_contract"].get("monitor_due_items") or []),
        *(first["agent_todo_summary"].get("monitor_due_items") or []),
    ]
    projected_due_ids = {
        item["todo_id"] for item in projected_due if item.get("todo_id")
    }
    assert projected_due_ids < {todo_id for todo_id, _ in monitors}

    poll_args: list[tuple[str, ...]] = []
    for index, (todo_id, target_key) in enumerate(monitors):
        args = (
            "quota",
            "monitor-poll",
            "--codex-app",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--turn-instance-id",
            turn_instance_id,
            "--todo-id",
            todo_id,
            *(() if index == 2 else ("--target-key", target_key)),
            "--result-hash",
            f"unchanged-{target_key}",
            "--available-capability",
            "network",
            "--available-capability",
            "external_evidence_poll",
            "--execute",
            "--scan-path",
            str(project),
        )
        poll_args.append(args)
        poll_rc, poll = _run_cli(registry_path, runtime, *args)
        assert poll_rc == 0, poll
        assert poll["settlement_todo_id"] == TODO_ID
        assert poll["todo_id"] == todo_id
        assert poll["target_key"] == target_key
        assert poll["material_change"] is False
        assert poll["replayed"] is False

    for args in poll_args:
        replay_rc, replay = _run_cli(registry_path, runtime, *args)
        assert replay_rc == 0, replay
        assert replay["settlement_todo_id"] == TODO_ID
        assert replay["replayed"] is True
        assert replay["appended"] is False

    changed_args = list(poll_args[1])
    changed_args[changed_args.index("unchanged-monitor-beta")] = (
        "changed-monitor-beta"
    )
    conflict_rc, conflict = _run_cli(
        registry_path,
        runtime,
        *changed_args,
    )
    assert conflict_rc == 1, conflict
    assert conflict["error_code"] == "heartbeat_receipt_identity_conflict"
    assert conflict["conflict_fields"] == ["result_hash"]

    index_path = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    observation_rows = [
        json.loads(line)
        for line in index_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("classification") == "quota_monitor_poll"
    ]
    effect_ids = {
        row["quota_monitor_poll_commit"]["effect_id"]
        for row in observation_rows
    }
    assert len(observation_rows) == 3
    assert effect_ids == {
        f"quota-monitor-poll:{GOAL_ID}:{AGENT_ID}:{turn_instance_id}:todo:{todo_id}"
        for todo_id, _ in monitors
    }
    assert {
        row["todo_id"] for row in observation_rows
    } == {todo_id for todo_id, _ in monitors}
    assert all(
        row["settlement_todo_id"] == TODO_ID for row in observation_rows
    )
    assert _spend_run_count(runtime) == 0

    settled_rc, settled = _run_cli(registry_path, runtime, *guard_args)
    assert settled_rc == 0, settled
    assert settled["selected_todo"]["todo_id"] == TODO_ID
    assert settled["heartbeat_receipt"]["settlement_identity"]["todo_id"] == (
        TODO_ID
    )


def test_pending_action_selection_does_not_commit_after_new_user_gate(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    turn_instance_id = "turn-pending-selection-user-gate"
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
    )
    first_rc, first = _run_cli(registry_path, runtime, *guard_args)
    assert first_rc == 0, first
    assert "settlement_identity" not in first["heartbeat_receipt"]

    _append_blocking_user_gate(project)
    selected_rc, selected = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--todo-id",
        ALTERNATIVE_TODO_ID,
    )

    assert selected_rc == 1, selected
    assert selected["error_code"] == "quota_action_selection_deferred"
    assert selected["action_selection_qualification"]["reason"] == (
        "delivery_not_allowed"
    )
    assert selected["heartbeat_receipt"]["status"] == "selection_retained"
    assert selected["heartbeat_receipt"]["pending_action_selection"]["todo_id"] == (
        ALTERNATIVE_TODO_ID
    )
    assert selected["rollout_event"]["appended"] is True
    events = _heartbeat_receipt_events(runtime, turn_instance_id)
    assert len(events) == 2
    assert all(not event["details"].get("todo_id") for event in events)
    assert all(not event["details"].get("settlement_effect_id") for event in events)


def test_todoless_autonomous_replan_settles_quota_refresh_spend_chain(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_autonomous_replan_fixture(project, runtime, registry_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["goals"][0]["control_plane"] = {
        "periodic_report": {
            "enabled": True,
            "profile_preset": "weekly",
            "route_ref": "project-room",
        }
    }
    registry_path.write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    turn_instance_id = "turn-autonomous-replan-settlement-1"

    guard_rc, guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
    )

    assert guard_rc == 0, guard
    assert guard["decision"] == "autonomous_replan_required", guard
    assert guard.get("selected_todo") is None, guard
    obligation_id = guard["replan_action_packet"]["obligation_id"]
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    assert identity["binding_kind"] == "autonomous_replan"
    assert identity["replan_obligation_id"] == obligation_id
    assert "todo_id" not in identity
    cli_channel = guard["interaction_contract"]["cli_channel"]
    plan_identity = cli_channel["settlement_plan"]["identity"]
    assert plan_identity["binding_kind"] == identity["binding_kind"]
    assert plan_identity["binding_id"] == identity["binding_id"]
    assert plan_identity["replan_obligation_id"] == obligation_id
    assert plan_identity["turn_instance_id"] == turn_instance_id
    original_scheduler_ack_args = guard["scheduler_hint"]["codex_app"][
        "ack_hint"
    ]["cli_args"]
    original_scheduler_ack_args = original_scheduler_ack_args[
        original_scheduler_ack_args.index("quota"):
    ]
    assert original_scheduler_ack_args[:2] == ["quota", "scheduler-ack-current"]
    assert "--turn-instance-id" in original_scheduler_ack_args
    assert turn_instance_id in original_scheduler_ack_args
    actions = cli_channel["next_cli_actions"]
    refresh_command = next(action for action in actions if "refresh-state" in action)
    spend_command = next(action for action in actions if "spend-slot" in action)
    for command in (refresh_command, spend_command):
        assert f"--replan-obligation-id {obligation_id}" in command
        assert f"--turn-instance-id {turn_instance_id}" in command
        assert "--todo-id" not in command

    refresh_command = (
        refresh_command.replace(
            "<advanced|blocked|exploration_exhausted|no_followup>",
            "advanced",
        )
        .replace("<surface-id>", "surface-new")
        .replace("<hypothesis-id>", "hypothesis-new")
        .replace("<probe-kind>", "probe-new")
        .replace("<evidence-id>", "evidence-new")
    )
    refresh_rc, refresh = _run_cli(
        registry_path,
        runtime,
        *_projected_cli_args(
            refresh_command,
            turn_instance_id=turn_instance_id,
        ),
    )

    assert refresh_rc == 0, refresh.get("error") or refresh
    assert refresh["settlement_result"]["ok"] is True
    assert [
        receipt["step_kind"] for receipt in refresh["settlement_result"]["receipts"]
    ] == ["validation", "durable_writeback"]
    assert refresh["post_writeback_hooks"]["invoked_count"] == 1
    assert refresh["post_writeback_hooks"]["intent_count"] == 0
    assert refresh["post_writeback_hooks"]["failures"] == []

    spend_args = _projected_cli_args(
        spend_command,
        turn_instance_id=turn_instance_id,
    )
    spend_rc, spend = _run_cli(
        registry_path,
        runtime,
        *spend_args,
        "--scan-path",
        str(project),
    )
    replay_rc, replay = _run_cli(
        registry_path,
        runtime,
        *spend_args,
        "--scan-path",
        str(project),
    )

    assert spend_rc == 0, spend
    assert spend["settlement_result"]["ok"] is True
    assert [
        receipt["step_kind"] for receipt in spend["settlement_result"]["receipts"]
    ] == ["validation", "durable_writeback", "quota_spend"]
    assert replay_rc == 0, replay
    assert replay["idempotent_replay"] is True
    assert replay["appended"] is False
    assert _spend_run_count(runtime) == 1

    settled_rc, settled = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
    )

    assert settled_rc == 0, settled
    assert settled["decision"] == "skip", settled
    assert settled["effective_action"] == "heartbeat_settled_skip"
    assert settled["execution_obligation"]["must_attempt_work"] is False
    assert settled.get("autonomous_replan_obligation") is None
    assert settled.get("replan_action_packet") is None
    assert settled["heartbeat_receipt"]["status"] == "replayed"
    assert settled["heartbeat_receipt"]["settlement_identity"][
        "binding_kind"
    ] == "autonomous_replan"
    assert _spend_run_count(runtime) == 1

    ack_rc, ack = _run_cli(
        registry_path,
        runtime,
        *original_scheduler_ack_args,
    )
    assert ack_rc == 0, ack
    assert ack["ok"] is True
    assert ack["mode"] == "scheduler-ack-current"
    assert ack["status"] == "heartbeat_settled_skip"
    assert ack["idempotent_replay"] is True
    assert ack["write_performed"] is False
    assert ack["scheduler_state_mutated"] is False
    assert ack["quota_spend_performed"] is False
    assert ack["appended"] is False
    assert _spend_run_count(runtime) == 1

    fresh_turn_id = "turn-autonomous-replan-settlement-2"
    fresh_rc, fresh = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        fresh_turn_id,
        "--scan-path",
        str(project),
    )

    assert fresh_rc == 0, fresh
    assert fresh["decision"] == "skip", fresh
    assert fresh["effective_action"] == "monitor_quiet_skip"
    assert fresh["execution_obligation"]["must_attempt_work"] is False
    assert fresh.get("autonomous_replan_obligation") is None
    assert fresh.get("replan_action_packet") is None
    assert fresh["heartbeat_receipt"]["turn_instance_id"] == fresh_turn_id
    assert _spend_run_count(runtime) == 1


def test_todoless_blocked_replan_settles_read_only_external_evidence_without_worktree(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_autonomous_replan_fixture(project, runtime, registry_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["goals"][0]["coordination"]["registered_agents"].append(
        "codex-settlement-peer"
    )
    registry_path.write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    external_evidence_dir = tmp_path / "external-review"
    external_evidence_dir.mkdir()
    turn_instance_id = "turn-autonomous-replan-blocked-external-evidence"

    guard_rc, guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
    )

    assert guard_rc == 0, guard
    assert guard["decision"] == "autonomous_replan_required", guard
    obligation_id = guard["replan_action_packet"]["obligation_id"]
    binding = (
        "--agent-id",
        AGENT_ID,
        "--replan-obligation-id",
        obligation_id,
        "--turn-instance-id",
        turn_instance_id,
    )
    refresh_rc, refresh = _run_cli(
        registry_path,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--progress-scope",
        "agent_lane",
        "--classification",
        "external_evidence_replan_blocked",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "outcome_gap",
        "--progress-result-class",
        "blocked",
        "--progress-blocker-id",
        "public-head-validation-failed",
        "--progress-evidence-id",
        "public-review-readback",
        *binding,
        "--no-global-sync",
        "--suppress-external-sinks",
        cwd=external_evidence_dir,
    )

    assert refresh_rc == 0, refresh.get("error") or refresh
    assert refresh["delivery_outcome"] == "outcome_gap"
    assert refresh["progress_observation"]["work_item_id"] == obligation_id
    assert refresh["settlement_workspace_requirement"] == {
        "schema_version": "settlement_workspace_requirement_v0",
        "settlement_binding_kind": "autonomous_replan",
        "requirement": "not_required",
        "source": "typed_settlement_identity",
        "reason": "autonomous_replan_is_non_repository_control_plane_work",
    }
    assert "delivery_workspace" not in refresh
    assert refresh["settlement_result"]["ok"] is True

    spend_rc, spend = _run_cli(
        registry_path,
        runtime,
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
        *binding,
        "--scan-path",
        str(project),
        cwd=external_evidence_dir,
    )

    assert spend_rc == 0, spend
    assert spend["settlement_workspace_requirement"]["requirement"] == (
        "not_required"
    )
    assert spend["delivery_workspace_validated"] is False
    assert spend["settlement_result"]["ok"] is True
    assert [
        receipt["step_kind"] for receipt in spend["settlement_result"]["receipts"]
    ] == ["validation", "durable_writeback", "quota_spend"]
    assert _spend_run_count(runtime) == 1

    replay_rc, replay = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--todo-id",
        SELECTED_REPLAN_TODO_ID,
        "--scan-path",
        str(project),
    )

    assert replay_rc == 0, replay
    assert replay["effective_action"] == "heartbeat_settled_skip"
    assert replay["should_run"] is False
    assert replay.get("selected_todo") is None
    assert replay.get("unsettled_host_turn_recovery") is None


def test_unbound_visible_goal_todoless_replan_reenters_through_guided_turn(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_autonomous_replan_fixture(project, runtime, registry_path)

    unbound_rc, unbound = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--runtime-profile",
        "codex_app_ssh_goal",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--scan-path",
        str(project),
    )

    assert unbound_rc == 0, unbound
    assert unbound["decision"] == "autonomous_replan_required", unbound
    assert unbound.get("selected_todo") is None, unbound
    actions = unbound["interaction_contract"]["cli_channel"]["next_cli_actions"]
    assert len(actions) == 1
    assert actions[0].endswith("--begin-turn")
    assert "refresh-state" not in actions[0]
    assert "spend-slot" not in actions[0]

    bound_rc, bound = _run_generated_cli(
        actions[0],
        registry_path=registry_path,
    )

    assert bound_rc == 0, bound
    assert bound["decision"] == "autonomous_replan_required", bound
    assert bound.get("selected_todo") is None, bound
    obligation_id = bound["replan_action_packet"]["obligation_id"]
    identity = bound["heartbeat_receipt"]["settlement_identity"]
    assert identity["binding_kind"] == "autonomous_replan"
    assert identity["replan_obligation_id"] == obligation_id
    assert identity["turn_instance_id"].startswith("guided-start:")
    cli_channel = bound["interaction_contract"]["cli_channel"]
    assert cli_channel["settlement_plan"]["identity"] == identity
    assert len(cli_channel["next_cli_actions"]) == 2
    for command in cli_channel["next_cli_actions"]:
        assert f"--replan-obligation-id {obligation_id}" in command
        assert f"--turn-instance-id {identity['turn_instance_id']}" in command


def test_autonomous_replan_semantic_delta_keeps_accountable_receipt_chain(
    tmp_path: Path,
) -> None:
    """Regression for #3528's real Todo-bound replan path.

    A stale repair-delta label does not itself prove a successor, but a fresh
    typed progress observation can still satisfy the open obligation. The
    accepted semantic ACK must remain in the accountable refresh/receipt chain
    instead of being mislabeled as a noop after it discharges the obligation.
    """

    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry_path)
    _initialize_git_checkout(project)
    turn_instance_id = "turn-autonomous-replan-semantic-1"

    guard_rc, guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
    )
    assert guard_rc == 0, guard
    assert guard["decision"] == "autonomous_replan_required", guard
    assert guard["selected_todo"]["todo_id"] == SELECTED_REPLAN_TODO_ID
    obligation_id = guard["replan_action_packet"]["obligation_id"]

    refresh_args = (
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--todo-id",
        SELECTED_REPLAN_TODO_ID,
        "--delivery-workspace-path",
        str(project),
        "--autonomous-replan-recorded",
        "--repair-delta-kind",
        "successor_or_supersede",
        "--delivery-outcome",
        "outcome_progress",
        "--classification",
        "validated_progress",
        "--progress-result-class",
        "advanced",
        "--progress-surface-id",
        "surface:periodic-review",
        "--progress-hypothesis-id",
        "hypothesis:periodic-review",
        "--progress-probe-kind",
        "probe:periodic-review",
        "--progress-evidence-id",
        "evidence:periodic-review",
    )
    mismatched_args = list(refresh_args)
    todo_flag_index = mismatched_args.index("--todo-id")
    mismatched_args[todo_flag_index : todo_flag_index + 2] = [
        "--replan-obligation-id",
        obligation_id,
    ]
    mismatch_rc, mismatch = _run_cli(
        registry_path,
        runtime,
        *mismatched_args,
    )
    assert mismatch_rc == 1, mismatch
    assert mismatch["ok"] is False
    assert mismatch["appended"] is False
    assert "settlement binding does not match" in mismatch["error"]

    refresh_rc, refresh = _run_cli(
        registry_path,
        runtime,
        *refresh_args,
    )

    assert refresh_rc == 0, refresh.get("error") or refresh
    assert refresh["ok"] is True
    assert refresh["appended"] is True
    assert refresh["classification"] == "validated_progress"
    assert refresh["delivery_outcome"] == "outcome_progress"
    assert refresh["autonomous_replan_recorded"] is True
    assert refresh["autonomous_replan_ack"]["semantic_delta"]["accepted"] is True
    assert refresh["settlement_result"]["ok"] is True
    assert [
        receipt["step_kind"] for receipt in refresh["settlement_result"]["receipts"]
    ] == ["validation", "durable_writeback"]

    replay_rc, replay = _run_cli(
        registry_path,
        runtime,
        *refresh_args,
    )
    assert replay_rc == 0, replay
    assert replay["idempotent_replay"] is True
    assert replay["appended"] is False
    assert _classification_count(runtime, "validated_progress") == 1

    next_guard_rc, next_guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        "turn-after-autonomous-replan-semantic-1",
        "--scan-path",
        str(project),
    )
    assert next_guard_rc == 0, next_guard
    assert next_guard.get("autonomous_replan_obligation") is None
    assert next_guard.get("decision") != "autonomous_replan_required"
    assert obligation_id not in json.dumps(next_guard, sort_keys=True)


def test_open_replan_rejects_missing_semantic_delta_before_durable_write(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry_path)
    _initialize_git_checkout(project)
    turn_instance_id = "turn-autonomous-replan-noop-1"

    guard_rc, guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
    )
    assert guard_rc == 0, guard
    assert guard["decision"] == "autonomous_replan_required", guard
    assert guard["selected_todo"]["todo_id"] == SELECTED_REPLAN_TODO_ID

    refresh_rc, refresh = _run_cli(
        registry_path,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--todo-id",
        SELECTED_REPLAN_TODO_ID,
        "--delivery-workspace-path",
        str(project),
        "--autonomous-replan-recorded",
        "--repair-delta-kind",
        "successor_or_supersede",
        "--delivery-outcome",
        "outcome_progress",
        "--classification",
        "validated_progress",
        "--progress-result-class",
        "advanced",
        "--progress-evidence-id",
        "evidence:periodic-review",
    )

    assert refresh_rc == 1, refresh
    assert refresh["ok"] is False
    assert refresh["appended"] is False
    assert "requires a typed semantic delta" in refresh["error"]
    assert _classification_count(runtime, "validated_progress") == 0
    assert _classification_count(runtime, "replan_noop") == 0


def test_runtime_capability_reentry_preserves_receipt_bound_todo_and_rejects_explicit_conflict(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_runtime_capability_reentry_fixture(project)
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        TURN_ID,
        "--scan-path",
        str(project),
    )

    first_rc, first = _run_cli(registry_path, runtime, *guard_args)
    replay_rc, replay = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--available-capability",
        "network",
    )

    assert first_rc == 0, first
    assert first["selected_todo"]["todo_id"] == TODO_ID
    assert first["heartbeat_receipt"]["settlement_identity"]["todo_id"] == TODO_ID
    assert "runtime_capability_reentry" not in first
    assert replay_rc == 0, replay
    assert replay["selected_todo"]["todo_id"] == TODO_ID
    assert replay["selected_todo"]["selection_binding"] == "heartbeat_receipt"
    assert replay["agent_lane_next_action"]["todo_id"] == TODO_ID
    assert replay["heartbeat_receipt"]["status"] == "replayed"
    assert replay["heartbeat_receipt"]["settlement_identity"]["todo_id"] == TODO_ID
    assert (
        replay["interaction_contract"]["cli_channel"]["settlement_plan"]["identity"][
            "todo_id"
        ]
        == TODO_ID
    )
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 1

    conflict_rc, conflict = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--available-capability",
        "network",
        "--todo-id",
        REENTRY_TODO_ID,
    )

    assert conflict_rc == 1, conflict
    assert conflict["error_code"] == "heartbeat_receipt_identity_conflict"
    assert "explicitly requested Todo" in conflict["reason"]
    assert conflict["heartbeat_receipt"]["status"] == "write_failed"
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 1


def test_runtime_capability_reentry_preserves_receipt_bound_autonomous_replan(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_autonomous_replan_fixture(project, runtime, registry_path)
    _configure_runtime_capability_reentry_fixture(project)
    turn_instance_id = "turn-autonomous-replan-capability-reentry"
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
    )

    first_rc, first = _run_cli(registry_path, runtime, *guard_args)
    replay_rc, replay = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--available-capability",
        "network",
    )

    assert first_rc == 0, first
    assert first["decision"] == "autonomous_replan_required", first
    obligation_id = first["replan_action_packet"]["obligation_id"]
    assert (
        first["heartbeat_receipt"]["settlement_identity"]["replan_obligation_id"]
        == obligation_id
    )
    assert replay_rc == 0, replay
    assert replay["decision"] == "autonomous_replan_required", replay
    assert replay["selected_todo"] is None
    assert replay["autonomous_replan_obligation"]["selection_binding"] == (
        "heartbeat_receipt"
    )
    assert replay["replan_action_packet"]["obligation_id"] == obligation_id
    assert replay["heartbeat_receipt"]["status"] == "replayed"
    assert (
        replay["heartbeat_receipt"]["settlement_identity"]["replan_obligation_id"]
        == obligation_id
    )
    assert (
        replay["interaction_contract"]["cli_channel"]["settlement_plan"]["identity"][
            "replan_obligation_id"
        ]
        == obligation_id
    )
    assert _heartbeat_receipt_count(runtime, turn_instance_id) == 1

    conflict_rc, conflict = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        "--available-capability",
        "network",
        "--todo-id",
        REENTRY_TODO_ID,
    )

    assert conflict_rc == 1, conflict
    assert conflict["error_code"] == "heartbeat_receipt_identity_conflict"
    assert _heartbeat_receipt_count(runtime, turn_instance_id) == 1


def test_peer_refresh_rejects_implicit_canonical_workspace_before_writeback(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _initialize_git_checkout(project)
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=LoopX Test",
            "-c",
            "user.email=loopx-test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
        cwd=project,
        check=True,
    )
    binding = (
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        TURN_ID,
    )
    _guard_rc, guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        TURN_ID,
        "--scan-path",
        str(project),
    )
    assert guard["heartbeat_receipt"]["settlement_identity"]["todo_id"] == TODO_ID

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["goals"][0]["coordination"]["registered_agents"].append(
        "codex-settlement-peer"
    )
    registry_path.write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path.write_text(
        state_path.read_text(encoding="utf-8").replace(
            "action_kind=validate -->",
            f"action_kind=validate claimed_by={AGENT_ID} -->",
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=LoopX Test",
            "-c",
            "user.email=loopx-test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "peer fixture",
        ],
        cwd=project,
        check=True,
    )
    linked_worktree = tmp_path / "linked-worktree"
    subprocess.run(
        ["git", "worktree", "add", "--quiet", "--detach", str(linked_worktree)],
        cwd=project,
        check=True,
    )

    refresh_args = (
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "peer_delivery_validated",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "outcome_progress",
        *binding,
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    canonical_rc, canonical = _run_cli(
        registry_path,
        runtime,
        *refresh_args,
        cwd=project,
    )
    assert canonical_rc == 1, canonical
    assert "must be refreshed from the independent git worktree" in canonical["error"]
    assert _classification_count(runtime, "peer_delivery_validated") == 0

    linked_rc, linked = _run_cli(
        registry_path,
        runtime,
        *refresh_args,
        cwd=linked_worktree,
    )
    assert linked_rc == 0, linked
    assert linked["delivery_workspace"]["workspace_kind"] == (
        "independent_git_worktree"
    )


def test_same_turn_receipt_replay_defers_newly_due_higher_priority_monitor(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_read_only_todo(project)
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        TURN_ID,
        "--scan-path",
        str(project),
    )

    first_rc, first = _run_cli(registry_path, runtime, *guard_args)
    assert first_rc == 0, first
    assert first["selected_todo"]["todo_id"] == TODO_ID
    assert first["heartbeat_receipt"]["settlement_identity"]["todo_id"] == TODO_ID

    _append_newly_due_monitor(project)
    capability_args = (
        "--available-capability",
        "network",
        "--available-capability",
        "external_evidence_poll",
    )
    replay_rc, replay = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        *capability_args,
    )

    assert replay_rc == 0, replay
    assert replay["selected_todo"]["todo_id"] == TODO_ID
    assert replay["selected_todo"]["selection_binding"] == "heartbeat_receipt"
    assert replay["agent_lane_next_action"]["todo_id"] == TODO_ID
    assert replay["work_lane_contract"]["selection_binding"] == "heartbeat_receipt"
    assert replay["work_lane_contract"]["selected_todo_id"] == TODO_ID
    assert (
        replay["work_lane_contract"]["deferred_work_lane"]["selected_todo_id"]
        == DUE_MONITOR_TODO_ID
    )
    assert replay["heartbeat_receipt"]["status"] == "replayed"
    plan = replay["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert plan["identity"]["todo_id"] == TODO_ID
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 1

    next_turn_rc, next_turn = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        "turn-settlement-cli-2",
        "--scan-path",
        str(project),
        *capability_args,
    )
    assert next_turn_rc == 0, next_turn
    assert next_turn["effective_action"] == "unsettled_host_turn_recovery"
    assert next_turn["unsettled_host_turn_recovery"]["binding_id"] == TODO_ID
    assert "selected_todo" not in next_turn

    binding = (
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        TURN_ID,
    )
    complete_args = (
        "todo",
        "complete",
        "--goal-id",
        GOAL_ID,
        *binding,
        "--claimed-by",
        AGENT_ID,
        "--evidence",
        "receipt-bound delivery validated",
        "--next-agent-todo",
        "Continue after the receipt-bound delivery.",
        "--next-claimed-by",
        AGENT_ID,
        "--next-action-kind",
        "implement",
    )
    complete_rc, complete = _run_cli(registry_path, runtime, *complete_args)
    complete_replay_rc, complete_replay = _run_cli(
        registry_path,
        runtime,
        *complete_args,
    )
    assert complete_rc == 0, complete
    assert complete_replay_rc == 0, complete_replay
    assert complete_replay["idempotent_replay"] is True

    refresh_args = (
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "receipt_bound_delivery_validated",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "outcome_progress",
        *binding,
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    refresh_rc, refresh = _run_cli(registry_path, runtime, *refresh_args)
    refresh_replay_rc, refresh_replay = _run_cli(
        registry_path,
        runtime,
        *refresh_args,
    )
    assert refresh_rc == 0, refresh
    assert refresh_replay_rc == 0, refresh_replay
    assert refresh_replay["idempotent_replay"] is True
    assert _classification_count(runtime, "receipt_bound_delivery_validated") == 1

    spend_args = (
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
        *binding,
        "--scan-path",
        str(project),
    )
    spend_rc, spend = _run_cli(registry_path, runtime, *spend_args)
    spend_replay_rc, spend_replay = _run_cli(
        registry_path,
        runtime,
        *spend_args,
    )
    assert spend_rc == 0, spend
    assert spend_replay_rc == 0, spend_replay
    assert spend_replay["idempotent_replay"] is True
    assert spend_replay["appended"] is False
    assert _spend_run_count(runtime) == 1

    resumed_turn_rc, resumed_turn = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        "turn-settlement-cli-2",
        "--scan-path",
        str(project),
        *capability_args,
    )
    assert resumed_turn_rc == 0, resumed_turn
    assert resumed_turn["selected_todo"]["todo_id"] == DUE_MONITOR_TODO_ID


def test_read_only_settlement_omits_non_causal_delivery_workspace(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["goals"][0]["control_plane"] = {
        "periodic_report": {
            "enabled": True,
            "profile_preset": "weekly",
            "route_ref": "project-room",
        }
    }
    registry_path.write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    state_path = _configure_read_only_todo(project)
    state_path.write_text(
        state_path.read_text(encoding="utf-8").replace(
            "## Agent Todo\n\n",
            "## User Todo\n\n## Agent Todo\n\n",
            1,
        ),
        encoding="utf-8",
    )
    binding = (
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        TURN_ID,
    )

    guard_rc, guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        TURN_ID,
        "--scan-path",
        str(project),
    )
    assert guard_rc == 0, guard
    assert guard["heartbeat_receipt"]["delivery_workspace_causality"] == {
        "schema_version": "delivery_workspace_causality_v0",
        "todo_id": TODO_ID,
        "requirement": "not_required",
        "source": "selected_todo_contract",
        "reason": "explicit_non_delivery_without_repository_writes",
    }

    premature_rc, premature = _run_cli(
        registry_path,
        runtime,
        "todo",
        "complete",
        "--goal-id",
        GOAL_ID,
        *binding,
        "--claimed-by",
        AGENT_ID,
        "--evidence",
        "read-only characterization validated",
        "--no-follow-up",
    )
    assert premature_rc == 1, premature
    assert "requires matching writeback and quota spend receipts" in premature["error"]

    conflict_rc, conflict = _run_cli(
        registry_path,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "read_only_characterization_complete",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "outcome_progress",
        "--delivery-workspace-path",
        str(project),
        *binding,
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    assert conflict_rc == 1, conflict
    assert "explicit non-delivery settlement contract" in conflict["error"]

    refresh_rc, refresh = _run_cli(
        registry_path,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "read_only_characterization_complete",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "outcome_progress",
        "--vision-state",
        "vision_closed",
        "--vision-summary",
        "The bounded read-only characterization is complete.",
        "--vision-acceptance",
        "The characterization evidence is validated and durably written.",
        *binding,
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    assert refresh_rc == 0, refresh
    assert refresh["delivery_workspace_causality"]["requirement"] == "not_required"
    assert "delivery_workspace" not in refresh
    assert refresh["post_writeback_hooks"]["intent_count"] == 0

    ordinary_args = (
        "todo",
        "complete",
        "--goal-id",
        GOAL_ID,
        *binding,
        "--claimed-by",
        AGENT_ID,
        "--evidence",
        "ordinary completion before terminal settlement",
    )
    ordinary_rc, ordinary = _run_cli(
        registry_path,
        runtime,
        *ordinary_args,
    )
    assert ordinary_rc == 0, ordinary
    assert ordinary["changed"] is True
    assert ordinary["completion_continuation"] == "active_goal"
    assert ordinary["completion_recovery"] is None
    assert ordinary["post_writeback_hooks"]["intent_count"] == 0

    spend_args = (
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
        *binding,
        "--scan-path",
        str(project),
    )
    spend_rc, spend = _run_cli(registry_path, runtime, *spend_args)
    replay_rc, replay = _run_cli(registry_path, runtime, *spend_args)

    assert spend_rc == 0, spend
    assert spend["delivery_workspace_causality"]["requirement"] == "not_required"
    assert spend["delivery_workspace_validated"] is False
    assert replay_rc == 0, replay
    assert replay["idempotent_replay"] is True
    assert _spend_run_count(runtime) == 1

    terminal_args = (
        "todo",
        "complete",
        "--goal-id",
        GOAL_ID,
        *binding,
        "--claimed-by",
        AGENT_ID,
        "--evidence",
        "read-only characterization validated",
        "--no-follow-up",
    )
    complete_rc, complete = _run_cli(
        registry_path,
        runtime,
        *terminal_args,
    )
    assert complete_rc == 0, complete
    assert complete["changed"] is True
    assert complete["completion_continuation"] == "no_followup"
    assert complete["completion_recovery"] == "same_turn_terminal_closeout"
    assert complete["post_writeback_hooks"]["intent_count"] == 1
    trigger_intent = complete["post_writeback_hooks"]["intents"][0]
    assert trigger_intent["intent_kind"] == "periodic_report.trigger_evaluation"
    assert trigger_intent["requested_write_scope"] == []
    assert trigger_intent["payload"]["generation_authorized"] is False
    assert trigger_intent["payload"]["external_delivery_authorized"] is False
    assert [
        receipt["step_kind"] for receipt in complete["settlement_result"]["receipts"]
    ] == [
        "validation",
        "durable_writeback",
        "quota_spend",
        "terminal_closeout",
    ]
    assert "no_followup=true" in state_path.read_text(encoding="utf-8")

    event_log = runtime / "goals" / GOAL_ID / "rollout-event-log.jsonl"
    completion_events = [
        event
        for line in event_log.read_text(encoding="utf-8").splitlines()
        if (event := json.loads(line)).get("event_kind") == "todo_complete"
        and event.get("run_id") == TURN_ID
    ]
    assert [event["details"]["no_followup"] for event in completion_events] == [
        False,
        True,
    ]
    assert [
        event["details"]["completion_continuation"] for event in completion_events
    ] == ["active_goal", "no_followup"]
    assert [event["details"]["completion_recovery"] for event in completion_events] == [
        None,
        "same_turn_terminal_closeout",
    ]
    assert completion_events[0]["event_id"] != completion_events[1]["event_id"]

    complete_replay_rc, complete_replay = _run_cli(
        registry_path,
        runtime,
        *terminal_args,
    )
    assert complete_replay_rc == 0, complete_replay
    assert complete_replay["idempotent_replay"] is True
    assert complete_replay["changed"] is False
    assert complete_replay["post_writeback_hooks"]["invoked_count"] == 0
    assert complete_replay["post_writeback_hooks"]["replayed_hooks"] == [
        "periodic_report.runtime_trigger"
    ]
    assert complete_replay["post_writeback_hooks"]["intents"] == [trigger_intent]
    ordinary_replay_rc, ordinary_replay = _run_cli(
        registry_path,
        runtime,
        *ordinary_args,
    )
    assert ordinary_replay_rc == 0, ordinary_replay
    assert ordinary_replay["idempotent_replay"] is True
    replayed_completion_events = [
        event
        for line in event_log.read_text(encoding="utf-8").splitlines()
        if (event := json.loads(line)).get("event_kind") == "todo_complete"
        and event.get("run_id") == TURN_ID
    ]
    assert len(replayed_completion_events) == 2


def test_same_turn_terminal_receipt_replay_preempts_autonomous_replan(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_read_only_todo(project)
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        TURN_ID,
        "--scan-path",
        str(project),
    )
    binding = (
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        TURN_ID,
    )

    first_rc, first = _run_cli(registry_path, runtime, *guard_args)
    assert first_rc == 0, first
    assert first["selected_todo"]["todo_id"] == TODO_ID

    refresh_rc, refresh = _run_cli(
        registry_path,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "terminal_receipt_replay_characterized",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "outcome_progress",
        *binding,
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    assert refresh_rc == 0, refresh

    spend_rc, spend = _run_cli(
        registry_path,
        runtime,
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
        *binding,
        "--scan-path",
        str(project),
    )
    assert spend_rc == 0, spend

    complete_rc, complete = _run_cli(
        registry_path,
        runtime,
        "todo",
        "complete",
        "--goal-id",
        GOAL_ID,
        *binding,
        "--claimed-by",
        AGENT_ID,
        "--evidence",
        "terminal receipt replay characterized",
        "--no-follow-up",
    )
    assert complete_rc == 0, complete
    assert complete["completion_continuation"] == "no_followup"

    replay_rc, replay = _run_cli(registry_path, runtime, *guard_args)

    assert replay_rc == 0, replay
    assert replay["decision"] == "skip", replay
    assert replay["should_run"] is False
    assert replay["effective_action"] == "heartbeat_settled_skip"
    assert replay["execution_obligation"]["must_attempt_work"] is False
    assert replay.get("selected_todo") is None
    assert replay.get("replan_action_packet") is None
    assert replay.get("autonomous_replan_obligation") is None
    assert replay["heartbeat_receipt"]["status"] == "replayed"
    assert replay["heartbeat_receipt"]["settlement_identity"]["todo_id"] == TODO_ID
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 1
    assert _spend_run_count(runtime) == 1

    fresh_turn_args = tuple(
        "turn-settlement-cli-2" if value == TURN_ID else value for value in guard_args
    )
    fresh_rc, fresh = _run_cli(registry_path, runtime, *fresh_turn_args)
    assert fresh_rc == 0, fresh
    assert fresh["decision"] == "autonomous_replan_required", fresh
    assert fresh["should_run"] is True
    assert fresh["replan_action_packet"]["obligation_id"]


def test_legacy_read_only_workspace_mismatch_fails_then_corrects_from_todo_contract(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    state_path = _configure_read_only_todo(project)
    _initialize_git_checkout(project)
    binding = (
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        TURN_ID,
    )

    guard_rc, guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        TURN_ID,
        "--scan-path",
        str(project),
    )
    assert guard_rc == 0, guard
    _strip_heartbeat_workspace_causality(runtime)

    complete_rc, complete = _run_cli(
        registry_path,
        runtime,
        "todo",
        "complete",
        "--goal-id",
        GOAL_ID,
        "--todo-id",
        TODO_ID,
        "--agent-id",
        AGENT_ID,
        "--claimed-by",
        AGENT_ID,
        "--evidence",
        "legacy read-only characterization validated",
        "--no-follow-up",
    )
    assert complete_rc == 0, complete

    refresh_rc, refresh = _run_cli(
        registry_path,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "legacy_read_only_characterization_complete",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "outcome_progress",
        "--delivery-workspace-path",
        str(project),
        *binding,
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    assert refresh_rc == 0, refresh
    assert refresh["delivery_workspace"]["workspace_kind"] == "canonical_checkout"

    causal_state = state_path.read_text(encoding="utf-8")
    state_path.write_text(
        causal_state.replace(
            "continuation_policy=same_agent_non_delivery "
            "required_capabilities=shell%2Cfilesystem_read ",
            "",
        ),
        encoding="utf-8",
    )
    spend_args = (
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
        *binding,
        "--scan-path",
        str(project),
    )
    failed_rc, failed = _run_cli(registry_path, runtime, *spend_args)
    assert failed_rc == 1, failed
    assert failed["workspace_guard"]["current_workspace"] == "foreign_git_worktree"
    assert _spend_run_count(runtime) == 0

    state_path.write_text(causal_state, encoding="utf-8")
    corrected_rc, corrected = _run_cli(registry_path, runtime, *spend_args)
    replay_rc, replay = _run_cli(registry_path, runtime, *spend_args)

    assert corrected_rc == 0, corrected
    assert corrected["delivery_workspace_causality"]["source"] == (
        "completed_todo_contract_fallback"
    )
    assert corrected["delivery_workspace_causality"]["requirement"] == ("not_required")
    assert corrected["delivery_workspace_validated"] is False
    assert replay_rc == 0, replay
    assert replay["idempotent_replay"] is True
    assert [
        receipt["step_kind"] for receipt in replay["settlement_result"]["receipts"]
    ] == ["validation", "durable_writeback", "quota_spend"]
    assert _spend_run_count(runtime) == 1
