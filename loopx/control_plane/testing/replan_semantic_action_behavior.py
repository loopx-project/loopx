from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from ...heartbeat_prompt import build_heartbeat_prompt
from ..quota.effective_action import EffectiveAction
from ..quota.turn_envelope import quota_action_signature_document
from ..work_items.progress_observation import (
    ProgressResultClass,
    semantic_delta_from_writeback,
)
from .doubao_model_behavior_actor import (
    ARK_API_KEY_ENV,
    DOUBAO_2_1_PRO_MODEL,
    DOUBAO_MODEL_ENV,
    DoubaoActorTransport,
    _direct_ark_transport,
)
from .model_tool_behavior import (
    LoopxCliExecutionError,
    DoubaoExecToolClient,
    argument_value,
    digest_text,
    execute_loopx_cli,
    loopx_command_tokens,
)
from .selected_todo_tool_behavior import bounded_workspace_read_plan
from .replan_vision_closeout_behavior import (
    dispatch_vision_closeout, VISION_HOST_INSTRUCTION, VISION_EXEC_TOOL_DESCRIPTION,
    REQUIRED_VISION_CLOSEOUT_MAX_CALLS,
    observe_shell_evidence,
)
from .vision_shell_host import VisionShellHost

REPLAN_SEMANTIC_ACTION_BEHAVIOR_RECEIPT_SCHEMA_VERSION = (
    "replan_semantic_action_behavior_receipt_v0"
)
REPLAN_SEMANTIC_ACTION_BEHAVIOR_MAX_CALLS = 7


class _HostToolError(RuntimeError):
    """A host tool result that supplies no semantic acceptance or extra budget."""

    def __init__(self, code: str, output: str, exit_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.output = output
        self.exit_code = exit_code

_FIXTURE_GOAL_ID = "replan-semantic-action-fixture"
_FIXTURE_AGENT_ID = "codex-replan-semantic-action"
_FIXTURE_WORK_ITEM_ID = "todo-replan-semantic-action"
_FIXTURE_FRONTIER_FILE = "replan-frontier.json"
_FIXTURE_SOURCE_FILE = "fixture/permission-config.json"
_FIXTURE_NEW_SURFACE_ID = "surface-permission-config"
_FIXTURE_NEW_HYPOTHESIS_ID = "hypothesis-permission-default"
_FIXTURE_NEW_PROBE_KIND = "static-contract-read"
_FIXTURE_NEW_EVIDENCE_ID = "evidence-permission-config"
_FIXTURE_COMPOSITION_EXPERIMENT_ID = "experiment-permission-composition"


@dataclass(frozen=True)
class _ReplanSemanticActionFixture:
    task_body: str
    quota_guard_command: str
    project_root: Path
    runtime_root: Path
    global_registry_path: Path
    source_root: Path
    frontier_target: Path
    work_source_target: Path
    composition_experiment_ref: str | None = None
    frontier_exhausted: bool = False
    required_vision: bool = False


@dataclass
class _QualificationState:
    fixture: _ReplanSemanticActionFixture
    messages: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    turn_instance_id: str
    quota_packet: dict[str, Any] | None = None
    quota_observation: dict[str, Any] | None = None
    semantic_delta: dict[str, Any] | None = None
    seen_quota: bool = False
    seen_clock: bool = False
    seen_refresh_state_help: bool = False
    read_only_host_commands_executed: bool = False
    frontier_context_read: bool = False
    work_source_read: bool = False
    created_successor_id: str | None = None
    successor_reentry_observation: dict[str, Any] | None = None
    semantic_reentry_observation: dict[str, Any] | None = None
    vision_closeout: dict[str, Any] | None = None

    @property
    def tool_call_limit(self) -> int:
        return (REQUIRED_VISION_CLOSEOUT_MAX_CALLS if self.fixture.required_vision
                else REPLAN_SEMANTIC_ACTION_BEHAVIOR_MAX_CALLS)


def _digest(value: str) -> str:
    return digest_text(value)


def _build_fixture(
    root: Path,
    *,
    composition_frontier: bool = False,
    exhausted_frontier: bool = False,
    required_vision: bool = False,
) -> _ReplanSemanticActionFixture:
    source_root = Path(__file__).resolve().parents[3]
    project_root = root / "project"
    runtime_root = root / "runtime"
    fixture_home = root / "home"
    state_relative = (
        Path(".codex")
        / "goals"
        / _FIXTURE_GOAL_ID
        / "ACTIVE_GOAL_STATE.md"
    )
    state_path = project_root / state_relative
    local_registry_path = project_root / ".loopx" / "registry.json"
    global_registry_path = (
        fixture_home / ".codex" / "loopx" / "registry.global.json"
    )
    state_path.parent.mkdir(parents=True, exist_ok=True)
    frontier_target = project_root / _FIXTURE_FRONTIER_FILE
    frontier_target.parent.mkdir(parents=True, exist_ok=True)
    source_target = project_root / _FIXTURE_SOURCE_FILE
    source_target.parent.mkdir(parents=True, exist_ok=True)
    source_target.write_text(
        json.dumps(
            {
                "schema_version": "permission_config_contract_v0",
                "default_role": "reader",
                "write_requires_explicit_grant": True,
                "public_safe": True,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    frontier_target.write_text(
        json.dumps(
            {
                "schema_version": "replan_frontier_fixture_v0",
                "covered": [
                    {
                        "surface_id": "surface-existing",
                        "hypothesis_id": "hypothesis-existing",
                        "probe_kind": "probe-existing",
                        "evidence_id": "evidence-existing",
                    }
                ],
                "uncovered": (
                    []
                    if exhausted_frontier
                    else [
                        {
                            "surface_id": _FIXTURE_NEW_SURFACE_ID,
                            "hypothesis_id": _FIXTURE_NEW_HYPOTHESIS_ID,
                            "probe_kind": _FIXTURE_NEW_PROBE_KIND,
                            "evidence_id": _FIXTURE_NEW_EVIDENCE_ID,
                            "source_ref": _FIXTURE_SOURCE_FILE,
                        }
                    ]
                ),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    fixture_objective = (
        "Inspect the projected frontier and close the bounded goal when its "
        "coverage is exhausted."
        if exhausted_frontier
        else "Inspect the projected frontier and select one uncovered surface."
    )
    fixture_next_action = (
        f"Read `{_FIXTURE_FRONTIER_FILE}`. If no uncovered entries remain, "
        "persist a coverage-backed exploration_exhausted result through "
        "refresh-state."
        if exhausted_frontier
        else (
            f"Read `{_FIXTURE_FRONTIER_FILE}`, inspect the selected uncovered "
            "entry's `source_ref`, then persist the typed result through "
            "refresh-state."
        )
    )
    fixture_agent_todo = (
        "- [ ] [P1-monitor] Observe the bounded fixture until its frontier "
        "changes.\n"
        "  <!-- loopx:todo "
        f"todo_id={_FIXTURE_WORK_ITEM_ID} status=open "
        "task_class=continuous_monitor action_kind=observe_fixture "
        f"claimed_by={_FIXTURE_AGENT_ID} "
        "target_key=replan-semantic-fixture "
        "cadence=1d next_due_at=2999-01-01T00%3A00%3A00Z -->\n"
    )
    if required_vision:
        fixture_agent_todo += (
            "- [ ] [P0] Continue the peer-owned implementation.\n"
            "  <!-- loopx:todo todo_id=todo_peer_implementation status=open "
            "task_class=advancement_task claimed_by=codex-fixture-peer -->\n"
        )
    state_path.write_text(
        "---\n"
        "status: active\n"
        "owner_mode: goal\n"
        f'objective: "{fixture_objective}"\n'
        "updated_at: 2026-08-13T00:00:00+08:00\n"
        "---\n\n"
        "# Replan Semantic Action Fixture\n\n"
        "## Objective\n\n"
        f"{fixture_objective}\n\n"
        "## Next Action\n\n"
        f"- {fixture_next_action}\n\n"
        "## Agent Todo\n\n"
        f"{fixture_agent_todo}",
        encoding="utf-8",
    )
    registry_goal: dict[str, Any] = {
        "id": _FIXTURE_GOAL_ID,
        "domain": "replan-semantic-action-fixture",
        "status": "active",
        "repo": str(project_root),
        "state_file": str(state_relative),
        "adapter": {
            "kind": "fixture_connected_delivery_v0",
            "status": "connected-delivery",
        },
        "coordination": {
            "registered_agents": [_FIXTURE_AGENT_ID, "codex-fixture-peer"] if required_vision else [_FIXTURE_AGENT_ID],
            "agent_model": "peer_v1",
            "agent_profiles": {
                _FIXTURE_AGENT_ID: {
                    "schema_version": "agent_profile_v1",
                    "agent_id": _FIXTURE_AGENT_ID,
                    "profile_role": "quality-qualification",
                    "scope_summary": "Qualify one bounded semantic replan.",
                    "default_task_classes": (
                        ["advancement_task", "continuous_monitor"]
                        if required_vision else ["continuous_monitor"]
                    ),
                    "vision_requirement": "required" if required_vision else "optional",
                }
            },
        },
        "authority_sources": [],
        "quota": {
            "compute": 1.0,
            "window_hours": 24,
            "allowed_slots": 5,
        },
    }
    if composition_frontier:
        registry_goal["spawn_policy"] = {
            "explore_harness": {"enabled": True, "profile": "generic"}
        }
    registry = {
        "schema_version": "0.1",
        "updated_at": "2026-08-13T00:00:00+08:00",
        "common_runtime_root": str(runtime_root),
        "goals": [registry_goal],
    }
    registry_text = json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True)
    for registry_path in (local_registry_path, global_registry_path):
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(registry_text + "\n", encoding="utf-8")

    runs_dir = runtime_root / "goals" / _FIXTURE_GOAL_ID / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    prior_runs: list[dict[str, Any]] = []
    for minute in (() if required_vision else (2, 1)):
        generated_at = f"2026-08-13T00:0{minute}:00+00:00"
        run = {
            "generated_at": generated_at,
            "goal_id": _FIXTURE_GOAL_ID,
            "agent_id": _FIXTURE_AGENT_ID,
            "classification": "bounded_fixture_probe",
            "delivery_batch_scale": "single_surface",
            "delivery_outcome": "surface_only",
            "progress_observation": {
                "schema_version": "typed_progress_observation_v0",
                "result_class": "unchanged",
                "work_item_id": _FIXTURE_WORK_ITEM_ID,
                "surface_id": "surface-existing",
                "hypothesis_id": "hypothesis-existing",
                "probe_kind": "probe-existing",
                "evidence_ids": ["evidence-existing"],
            },
        }
        safe_timestamp = generated_at.replace(":", "-")
        json_path = runs_dir / f"{safe_timestamp}.json"
        markdown_path = runs_dir / f"{safe_timestamp}.md"
        json_path.write_text(
            json.dumps(run, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        markdown_path.write_text("# Bounded fixture probe\n", encoding="utf-8")
        prior_runs.append(
            {
                **run,
                "json_path": str(json_path),
                "markdown_path": str(markdown_path),
            }
        )
    (runs_dir / "index.jsonl").write_text(
        "".join(json.dumps(run, sort_keys=True) + "\n" for run in prior_runs),
        encoding="utf-8",
    )

    if composition_frontier:
        explore_commands = (
            (
                "node",
                "--node-id",
                "surface-existing",
                "--title",
                "Existing closed surface",
                "--kind",
                "hypothesis",
                "--status",
                "resolved",
                "--evidence-ref",
                "evidence-existing",
            ),
            (
                "node",
                "--node-id",
                _FIXTURE_NEW_SURFACE_ID,
                "--title",
                "Permission configuration surface",
                "--kind",
                "hypothesis",
                "--status",
                "dead_end",
                "--evidence-ref",
                _FIXTURE_NEW_EVIDENCE_ID,
            ),
            (
                "node",
                "--node-id",
                _FIXTURE_COMPOSITION_EXPERIMENT_ID,
                "--title",
                "Jointly probe the existing and permission surfaces",
                "--kind",
                "experiment",
                "--status",
                "open",
            ),
            (
                "edge",
                "--from",
                _FIXTURE_COMPOSITION_EXPERIMENT_ID,
                "--to",
                "surface-existing",
                "--type",
                "depends_on",
            ),
            (
                "edge",
                "--from",
                _FIXTURE_COMPOSITION_EXPERIMENT_ID,
                "--to",
                _FIXTURE_NEW_SURFACE_ID,
                "--type",
                "depends_on",
            ),
        )
        for explore_args in explore_commands:
            execute_loopx_cli(
                shlex.join(
                    (
                        "loopx",
                        "--format",
                        "json",
                        "--registry",
                        "fixture-registry",
                        "--runtime-root",
                        "fixture-runtime",
                        "explore",
                        *explore_args,
                        "--goal-id",
                        _FIXTURE_GOAL_ID,
                    )
                ),
                source_root=source_root,
                project_root=project_root,
                argument_overrides={
                    "--registry": str(global_registry_path),
                    "--runtime-root": str(runtime_root),
                },
            )

    prompt = build_heartbeat_prompt(
        goal_id=_FIXTURE_GOAL_ID,
        thin=True,
        agent_id=_FIXTURE_AGENT_ID,
        registered_agents=[_FIXTURE_AGENT_ID],
        available_capabilities=[
            "shell",
            "filesystem_read",
            "filesystem_write",
        ],
        runtime_profile="codex_app_heartbeat",
    )
    quota_guard_command = str(prompt["quota_guard_command"])
    if quota_guard_command not in str(prompt["task_body"]):
        raise ValueError("heartbeat fixture must contain its production quota guard")
    return _ReplanSemanticActionFixture(
        task_body=str(prompt["task_body"]),
        quota_guard_command=quota_guard_command,
        project_root=project_root,
        runtime_root=runtime_root,
        global_registry_path=global_registry_path,
        source_root=source_root,
        frontier_target=frontier_target,
        work_source_target=source_target,
        composition_experiment_ref=(
            _FIXTURE_COMPOSITION_EXPERIMENT_ID if composition_frontier else None
        ),
        frontier_exhausted=exhausted_frontier,
        required_vision=required_vision,
    )


def _clock_output(command: str) -> str | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens or Path(tokens[0]).name != "date" or len(tokens) > 4:
        return None
    if any(
        token in {";", "&&", "||", "|"}
        or (index > 0 and not token.startswith(("-", "+")))
        for index, token in enumerate(tokens)
    ):
        return None
    if "-u" in tokens or "--utc" in tokens:
        return "2026-08-12T16:00:00Z\n"
    return "2026-08-13T00:00:00+08:00\n"


def _is_quota_guard(command: str) -> bool:
    tokens = loopx_command_tokens(command)
    if not tokens:
        return False
    try:
        quota_index = tokens.index("quota")
    except ValueError:
        return False
    return bool(
        tokens[quota_index : quota_index + 2] == ["quota", "should-run"]
        and argument_value(tokens, "--goal-id") == _FIXTURE_GOAL_ID
        and argument_value(tokens, "--agent-id") == _FIXTURE_AGENT_ID
        and "--codex-app" in tokens
        and argument_value(tokens, "--turn-instance-id")
    )


def _progress_observation_from_command(command: str) -> dict[str, Any] | None:
    tokens = loopx_command_tokens(command)
    if not tokens or "refresh-state" not in tokens:
        return None
    if (
        argument_value(tokens, "--goal-id") != _FIXTURE_GOAL_ID
        or argument_value(tokens, "--agent-id") != _FIXTURE_AGENT_ID
        or argument_value(tokens, "--progress-scope") != "agent_lane"
    ):
        raise ValueError("replan writeback must target the fixture agent lane")
    result_class = argument_value(tokens, "--progress-result-class")
    if result_class not in {item.value for item in ProgressResultClass}:
        raise ValueError("replan writeback must carry a typed result class")
    observation: dict[str, Any] = {
        "schema_version": "typed_progress_observation_v0",
        "result_class": result_class,
        "work_item_id": _FIXTURE_WORK_ITEM_ID,
    }
    for option, field in (
        ("--progress-surface-id", "surface_id"),
        ("--progress-hypothesis-id", "hypothesis_id"),
        ("--progress-probe-kind", "probe_kind"),
        ("--progress-blocker-id", "blocker_id"),
        ("--progress-coverage-scope-id", "coverage_scope_id"),
    ):
        value = argument_value(tokens, option)
        if value:
            observation[field] = value
    evidence_ids: list[str] = []
    for index, token in enumerate(tokens[:-1]):
        if token == "--progress-evidence-id":
            evidence_ids.append(tokens[index + 1])
    if evidence_ids:
        observation["evidence_ids"] = evidence_ids
    if "--progress-coverage-complete" in tokens:
        observation["coverage_complete"] = True
    return observation


def _is_replan_successor_create(command: str) -> bool:
    tokens = loopx_command_tokens(command)
    if not tokens:
        return False
    try:
        todo_index = tokens.index("todo")
    except ValueError:
        return False
    return bool(
        tokens[todo_index : todo_index + 2] == ["todo", "add"]
        and argument_value(tokens, "--goal-id") == _FIXTURE_GOAL_ID
        and argument_value(tokens, "--role") == "agent"
        and argument_value(tokens, "--task-class") == "advancement_task"
        and argument_value(tokens, "--claimed-by") == _FIXTURE_AGENT_ID
        and argument_value(tokens, "--text")
    )


def _quota_behavior_observation(packet: Mapping[str, Any]) -> dict[str, Any]:
    signature = quota_action_signature_document(packet)
    action = dict(signature.get("action") or {})
    user = dict(signature.get("user") or {})
    obligation = packet.get("autonomous_replan_obligation")
    replan_action = packet.get("replan_action_packet")
    if not isinstance(obligation, Mapping) or not isinstance(replan_action, Mapping):
        raise TypeError("quota must project an obligation and replan action packet")
    context = obligation.get("replan_context")
    if not isinstance(context, Mapping):
        raise TypeError("quota must host-project the replan coverage context")
    if (
        packet.get("decision") != "autonomous_replan_required"
        or replan_action.get("decision") != "replan_required"
        or replan_action.get("obligation_id") != obligation.get("obligation_id")
        or context.get("delivery") != "host_projected"
        or context.get("evidence_source") != "agent_scoped_evidence_log"
        or dict(context.get("delivery_receipt") or {}).get("status") != "delivered"
        or packet.get("required_reads") not in (None, [])
    ):
        raise ValueError("quota does not expose the semantic replan contract")
    if not (
        action.get("must_attempt") is True
        and action.get("delivery_allowed") is True
        and action.get("quiet_noop_allowed") is False
    ):
        raise ValueError("semantic replan must require one executable action")
    return {
        "decision": "execute",
        "selected_todo_id": None,
        "user_action_required": bool(user.get("action_required")),
        "must_attempt_work": True,
        "delivery_allowed": True,
        "quiet_noop_allowed": False,
        "external_write_requested": False,
        "replan_obligation_id": obligation.get("obligation_id"),
        "context_delivery": "host_projected",
        "required_semantic_outcomes": list(
            dict(replan_action.get("uncovered_frontier") or {}).get(
                "required_any_of"
            )
            or []
        ),
        "trigger_kinds": sorted({str(trigger["kind"]) for trigger in obligation.get("triggers", [])}),
    }


def _successor_reentry_observation(
    packet: Mapping[str, Any],
    *,
    successor_todo_id: str,
    composition_experiment_ref: str | None,
) -> dict[str, Any]:
    selected_todo = packet.get("selected_todo")
    if not isinstance(selected_todo, Mapping):
        raise ValueError("successor_reentry_selected_todo_missing")
    if str(selected_todo.get("todo_id") or "") != successor_todo_id:
        raise ValueError("successor_reentry_selected_todo_mismatch")
    if packet.get("decision") == "autonomous_replan_required" or isinstance(
        packet.get("autonomous_replan_obligation"), Mapping
    ):
        raise ValueError("successor_reentry_replan_not_closed")
    if not (
        packet.get("decision") == "run"
        and packet.get("effective_action") == EffectiveAction.NORMAL_RUN.value
    ):
        raise ValueError("successor_reentry_not_runnable")

    observation: dict[str, Any] = {
        "decision": packet.get("decision"),
        "effective_action": packet.get("effective_action"),
        "selected_todo_id": successor_todo_id,
        "replan_closed": True,
    }
    if composition_experiment_ref:
        frontier = packet.get("bounded_research_frontier")
        gaps = (
            frontier.get("gaps")
            if isinstance(frontier, Mapping)
            and isinstance(frontier.get("gaps"), list)
            else []
        )
        gap = next(
            (
                item
                for item in gaps
                if isinstance(item, Mapping)
                and item.get("experiment_node_ref") == composition_experiment_ref
            ),
            None,
        )
        if not isinstance(gap, Mapping) or gap.get("status") != "scheduled":
            raise ValueError("successor_reentry_composition_not_scheduled")
        observation["composition_experiment_ref"] = composition_experiment_ref
        observation["composition_status"] = "scheduled"
    return observation


def _semantic_reentry_observation(
    packet: Mapping[str, Any],
    *,
    obligation_id: str,
) -> dict[str, Any]:
    obligation = packet.get("autonomous_replan_obligation")
    if (
        isinstance(obligation, Mapping)
        and obligation.get("obligation_id") == obligation_id
    ):
        raise ValueError("semantic_reentry_replan_not_closed")
    if packet.get("decision") == "autonomous_replan_required":
        raise ValueError("semantic_reentry_replan_not_closed")
    if packet.get("decision") != "skip":
        raise ValueError("semantic_reentry_did_not_exit")
    frontier = packet.get("goal_frontier_projection")
    normalized_progress = (
        frontier.get("normalized_progress")
        if isinstance(frontier, Mapping)
        else None
    )
    monitor_lanes = (
        frontier.get("monitor_only_lanes")
        if isinstance(frontier, Mapping)
        else None
    )
    future_monitor_wait = bool(
        packet.get("effective_action") == EffectiveAction.MONITOR_QUIET_SKIP.value
        and isinstance(frontier, Mapping)
        and frontier.get("replan_required") is False
        and isinstance(monitor_lanes, Mapping)
        and monitor_lanes.get("present") is True
        and isinstance(normalized_progress, Mapping)
        and int(normalized_progress.get("agent_monitor_open_count") or 0) > 0
        and int(normalized_progress.get("agent_monitor_due_count") or 0) == 0
    )
    if not future_monitor_wait:
        raise ValueError("semantic_reentry_not_future_monitor_wait")
    return {
        "decision": packet.get("decision"),
        "effective_action": packet.get("effective_action"),
        "replan_rule": "future_monitor_wait",
        "replan_closed": True,
        "exited": True,
    }


def _execute_loopx(
    command: str,
    *,
    fixture: _ReplanSemanticActionFixture,
    turn_instance_id: str,
) -> str:
    tokens = loopx_command_tokens(command)
    if tokens is None:
        raise ValueError("fixture requires one bounded LoopX command")
    # Scope adapter context even when a projected command relies on cwd defaults.
    # Never supply missing semantic/binding fields on the actor's behalf.
    if "--registry" not in tokens:
        tokens[1:1] = ["--registry", str(fixture.global_registry_path)]
    if "refresh-state" in tokens:
        tokens.extend(["--no-global-sync", "--suppress-external-sinks"])
    try:
        return execute_loopx_cli(
            shlex.join(tokens),
            source_root=fixture.source_root,
            project_root=fixture.project_root,
            argument_overrides={
                "--registry": str(fixture.global_registry_path),
                "--runtime-root": str(fixture.runtime_root),
                "--turn-instance-id": turn_instance_id,
            },
        )
    except LoopxCliExecutionError as exc:
        if not fixture.required_vision:
            raise
        raise _HostToolError("loopx_cli_nonzero", str(exc), exc.returncode) from None


def _bounded_workspace_read_plan(
    command: str,
    *,
    fixture: _ReplanSemanticActionFixture,
) -> list[Any] | None:
    return bounded_workspace_read_plan(command, fixture=fixture)


def _bounded_refresh_state_help_limit(command: str) -> int | None:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    if len(tokens) < 3 or Path(tokens[0]).name != "loopx":
        return None
    if tokens[1:3] != ["refresh-state", "--help"]:
        return None
    remainder = tokens[3:]
    if remainder[:3] == ["2", ">&", "1"]:
        remainder = remainder[3:]
    if not remainder:
        return 200
    if remainder[:2] != ["|", "head"]:
        return None
    if len(remainder) == 3 and remainder[2].startswith("-"):
        limit_text = remainder[2][1:]
    elif len(remainder) == 4 and remainder[2] == "-n":
        limit_text = remainder[3]
    else:
        return None
    if not limit_text.isdigit():
        return None
    limit = int(limit_text)
    return limit if 1 <= limit <= 200 else None


def _execute_workspace_read(
    command: str,
    *,
    fixture: _ReplanSemanticActionFixture,
) -> tuple[str, bool, bool]:
    plan = _bounded_workspace_read_plan(command, fixture=fixture)
    if plan is None:
        raise ValueError("workspace read is outside the hermetic fixture")
    outputs: list[str] = []
    status = 0
    frontier_read = False
    work_source_read = False
    for step in plan:
        should_run = bool(
            step.operator == ";"
            or (step.operator == "&&" and status == 0)
            or (step.operator == "||" and status != 0)
        )
        if not should_run:
            continue
        completed = subprocess.run(
            list(step.argv),
            cwd=fixture.project_root,
            check=False,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=10,
        )
        status = completed.returncode
        output = completed.stdout
        if step.line_limit is not None:
            output = "".join(output.splitlines(keepends=True)[: step.line_limit])
        outputs.append(output)
        frontier_read = frontier_read or (
            status == 0
            and step.target is not None
            and step.target.resolve() == fixture.frontier_target.resolve()
        )
        work_source_read = work_source_read or (
            status == 0
            and step.target is not None
            and step.target.resolve() == fixture.work_source_target.resolve()
        )
    if status != 0:
        raise RuntimeError(f"workspace read failed with exit={status}")
    return "".join(outputs), frontier_read, work_source_read


def _receipt(
    *,
    qualification_id: str,
    actor_ref: str,
    steps: list[dict[str, Any]],
    passed: bool,
    failure_code: str | None,
    quota_observation: Mapping[str, Any] | None,
    semantic_delta: Mapping[str, Any] | None,
    local_fixture_writes_executed: bool,
    read_only_host_commands_executed: bool,
) -> dict[str, Any]:
    receipt = {
        "schema_version": REPLAN_SEMANTIC_ACTION_BEHAVIOR_RECEIPT_SCHEMA_VERSION,
        "qualification_id": qualification_id,
        "actor_ref": actor_ref,
        "qualification_passed": passed,
        "failure_code": failure_code,
        "observed_tool_sequence": [step["kind"] for step in steps],
        "tool_call_count": len(steps),
        "semantic_action_accepted": bool(
            passed and semantic_delta and semantic_delta.get("accepted") is True
        ),
        "selected_semantic_outcomes": list(
            (semantic_delta or {}).get("satisfying_outcomes") or []
        ),
        "tool_call_receipts": steps,
        "boundary": {
            "raw_prompt_persisted": False,
            "raw_provider_response_persisted": False,
            "raw_command_persisted": False,
            "filesystem_writes_executed": local_fixture_writes_executed,
            "writes_limited_to_temporary_fixture": local_fixture_writes_executed,
            "external_writes_executed": False,
            "shell_commands_executed": False,
            "read_only_host_commands_executed": read_only_host_commands_executed,
        },
    }
    if quota_observation is not None:
        receipt.update(dict(quota_observation))
    return receipt


def _qualification_receipt(
    state: _QualificationState,
    *,
    qualification_id: str,
    actor_ref: str,
    passed: bool,
    failure_code: str | None,
) -> dict[str, Any]:
    if passed and state.fixture.required_vision and not (state.vision_closeout or {}).get("settled"):
        passed = False
        failure_code = "required_vision_closeout_incomplete"
    receipt = _receipt(
        qualification_id=qualification_id,
        actor_ref=actor_ref,
        steps=state.steps,
        passed=passed,
        failure_code=failure_code,
        quota_observation=state.quota_observation,
        semantic_delta=state.semantic_delta,
        local_fixture_writes_executed=True,
        read_only_host_commands_executed=state.read_only_host_commands_executed,
    )
    if state.successor_reentry_observation is not None:
        receipt["successor_reentry"] = state.successor_reentry_observation
    if state.semantic_reentry_observation is not None:
        receipt["semantic_reentry"] = state.semantic_reentry_observation
    receipt["qualification_scope"] = (
        "required_vision_closeout" if state.fixture.required_vision else "semantic_action"
    )
    receipt["tool_call_limit"] = state.tool_call_limit
    if state.fixture.required_vision:
        receipt["execution_host"] = "os_isolated_shell"
        receipt["boundary"]["shell_commands_executed"] = bool(state.steps)
    if state.vision_closeout is not None:
        receipt["vision_closeout"] = state.vision_closeout
        receipt["semantic_action_accepted"] = bool(state.semantic_delta and state.semantic_delta.get("accepted"))
    return receipt


def _record_tool_step(
    state: _QualificationState,
    *,
    kind: str,
    command: str,
    exit_code: int | None = None,
    error_code: str | None = None,
) -> None:
    state.steps.append(
        {
            "ordinal": len(state.steps) + 1,
            "kind": kind,
            "command_digest": _digest(command),
            **({"exit_code": exit_code, "error_code": error_code} if error_code else {}),
        }
    )


def _handle_quota_command(command: str, state: _QualificationState) -> str:
    if state.seen_quota:
        raise ValueError("repeated_quota_should_run")
    output = _execute_loopx(
        command,
        fixture=state.fixture,
        turn_instance_id=state.turn_instance_id,
    )
    decoded = json.loads(output)
    if not isinstance(decoded, dict):
        raise ValueError("quota result must be an object")
    state.quota_packet = decoded
    state.quota_observation = _quota_behavior_observation(decoded)
    if state.fixture.required_vision and "required_agent_vision_missing" not in state.quota_observation["trigger_kinds"]:
        raise ValueError("required_vision_fixture_trigger_mismatch")
    state.seen_quota = True
    state.read_only_host_commands_executed = True
    return output


def _handle_clock_command(output: str, state: _QualificationState) -> str:
    if state.seen_clock:
        raise ValueError("repeated_clock")
    state.seen_clock = True
    state.read_only_host_commands_executed = True
    return output


def _handle_workspace_read(command: str, state: _QualificationState) -> str:
    output, read_frontier, read_work_source = _execute_workspace_read(
        command,
        fixture=state.fixture,
    )
    state.frontier_context_read = state.frontier_context_read or read_frontier
    state.work_source_read = state.work_source_read or read_work_source
    state.read_only_host_commands_executed = True
    return output


def _handle_refresh_state_help(
    state: _QualificationState,
    *,
    line_limit: int,
) -> str:
    _require_observed_frontier(state, prefix="refresh_state_help")
    if state.seen_refresh_state_help:
        raise ValueError("repeated_refresh_state_help")
    output = execute_loopx_cli(
        "loopx refresh-state --help",
        source_root=state.fixture.source_root,
        project_root=state.fixture.project_root,
    )
    state.seen_refresh_state_help = True
    state.read_only_host_commands_executed = True
    return "\n".join(output.splitlines()[:line_limit]) + "\n"


def _expected_obligation_id(state: _QualificationState) -> str:
    if state.quota_packet is None:
        raise ValueError("semantic_action_before_quota")
    obligation = state.quota_packet.get("autonomous_replan_obligation")
    if not isinstance(obligation, Mapping):
        raise ValueError("quota_obligation_missing")
    obligation_id = str(obligation.get("obligation_id") or "").strip()
    if not obligation_id:
        raise ValueError("quota_obligation_missing")
    return obligation_id


def _require_observed_frontier(
    state: _QualificationState,
    *,
    prefix: str,
    work_source_required: bool = True,
) -> None:
    if state.quota_packet is None:
        raise ValueError(f"{prefix}_before_quota")
    if not state.frontier_context_read:
        raise ValueError(f"{prefix}_before_frontier_read")
    if work_source_required and not state.work_source_read:
        raise ValueError(f"{prefix}_before_work_source_read")


def _handle_successor_command(command: str, state: _QualificationState) -> str:
    _require_observed_frontier(state, prefix="successor_create")
    if state.created_successor_id is not None:
        raise ValueError("repeated_successor_create")
    expected_obligation_id = _expected_obligation_id(state)
    requested_obligation_id = argument_value(
        loopx_command_tokens(command) or [],
        "--replan-obligation-id",
    )
    if requested_obligation_id != expected_obligation_id:
        raise ValueError("successor_create_obligation_mismatch")
    if state.fixture.composition_experiment_ref:
        requested_experiment_ref = argument_value(
            loopx_command_tokens(command) or [],
            "--explore-result-node-ref",
        )
        if requested_experiment_ref != state.fixture.composition_experiment_ref:
            raise ValueError("successor_create_composition_binding_mismatch")
    output = _execute_loopx(
        command,
        fixture=state.fixture,
        turn_instance_id=state.turn_instance_id,
    )
    decoded = json.loads(output)
    state.created_successor_id = (
        str(decoded.get("todo_id") or "").strip()
        if isinstance(decoded, dict)
        else ""
    )
    if not (
        isinstance(decoded, dict)
        and decoded.get("ok") is True
        and decoded.get("added") is True
        and state.created_successor_id
        and decoded.get("host_action") == "end_current_heartbeat"
    ):
        raise ValueError("successor_create_failed")
    transition = decoded.get("replan_transition")
    if not (
        isinstance(transition, dict)
        and transition.get("recorded") is True
        and transition.get("obligation_id") == expected_obligation_id
        and transition.get("successor_todo_id") == state.created_successor_id
        and transition.get("outcome") == "new_runnable_successor"
    ):
        raise ValueError("successor_transition_missing")
    state.semantic_delta = {
        "accepted": True,
        "satisfying_outcomes": ["new_runnable_successor"],
    }
    reentry_output = _execute_loopx(
        state.fixture.quota_guard_command,
        fixture=state.fixture,
        turn_instance_id=f"{state.turn_instance_id}-successor",
    )
    reentry_packet = json.loads(reentry_output)
    if not isinstance(reentry_packet, dict):
        raise ValueError("successor_reentry_quota_invalid")
    state.successor_reentry_observation = _successor_reentry_observation(
        reentry_packet,
        successor_todo_id=state.created_successor_id,
        composition_experiment_ref=state.fixture.composition_experiment_ref,
    )
    state.read_only_host_commands_executed = True
    return output


def _handle_semantic_writeback(
    command: str,
    observation: Mapping[str, Any],
    state: _QualificationState,
) -> str:
    obligation_id = _expected_obligation_id(state)
    result_class = str(observation.get("result_class") or "")
    terminal_result = result_class in {
        ProgressResultClass.BLOCKED.value,
        ProgressResultClass.EXPLORATION_EXHAUSTED.value,
        ProgressResultClass.NO_FOLLOWUP.value,
    }
    _require_observed_frontier(
        state,
        prefix="semantic_action",
        work_source_required=not terminal_result,
    )
    if not terminal_result:
        matches_observed_surface = bool(
            observation.get("surface_id") == _FIXTURE_NEW_SURFACE_ID
            and observation.get("hypothesis_id") == _FIXTURE_NEW_HYPOTHESIS_ID
            and observation.get("probe_kind") == _FIXTURE_NEW_PROBE_KIND
            and _FIXTURE_NEW_EVIDENCE_ID in set(observation.get("evidence_ids") or [])
        )
        if not matches_observed_surface:
            raise ValueError("semantic_action_misses_uncovered_frontier")
    obligation = dict(
        (state.quota_packet or {}).get("autonomous_replan_obligation") or {}
    )
    if obligation.get("obligation_id") != obligation_id:
        raise ValueError("quota_obligation_missing")
    state.semantic_delta = semantic_delta_from_writeback(
        obligation=obligation,
        progress_observation=observation,
    )
    if state.semantic_delta.get("accepted") is not True:
        raise ValueError("non_semantic_replan_action")
    output = _execute_loopx(
        command,
        fixture=state.fixture,
        turn_instance_id=state.turn_instance_id,
    )
    decoded = json.loads(output)
    if not isinstance(decoded, dict) or decoded.get("ok") is not True:
        raise ValueError("semantic_writeback_failed")
    if result_class in {
        ProgressResultClass.EXPLORATION_EXHAUSTED.value,
        ProgressResultClass.NO_FOLLOWUP.value,
    }:
        reentry_output = _execute_loopx(
            state.fixture.quota_guard_command,
            fixture=state.fixture,
            turn_instance_id=f"{state.turn_instance_id}-semantic-reentry",
        )
        reentry_packet = json.loads(reentry_output)
        if not isinstance(reentry_packet, dict):
            raise ValueError("semantic_reentry_quota_invalid")
        state.semantic_reentry_observation = _semantic_reentry_observation(
            reentry_packet,
            obligation_id=obligation_id,
        )
        state.read_only_host_commands_executed = True
    return output


def _dispatch_behavior_command(
    command: str,
    state: _QualificationState,
) -> tuple[str, str, bool]:
    if _is_quota_guard(command):
        return _handle_quota_command(command, state), "quota_should_run", False
    if (clock_output := _clock_output(command)) is not None:
        return _handle_clock_command(clock_output, state), "clock", False
    if _bounded_workspace_read_plan(command, fixture=state.fixture) is not None:
        return _handle_workspace_read(command, state), "workspace_read", False
    if (help_limit := _bounded_refresh_state_help_limit(command)) is not None:
        return (
            _handle_refresh_state_help(state, line_limit=help_limit),
            "refresh_state_help",
            False,
        )
    if _is_replan_successor_create(command):
        return _handle_successor_command(command, state), "replan_successor_create", True
    if (observation := _progress_observation_from_command(command)) is not None:
        return (
            _handle_semantic_writeback(command, observation, state),
            "semantic_replan_writeback",
            True,
        )
    if "evidence-log" in command:
        raise ValueError("manual_evidence_read_is_not_replan")
    raise ValueError("unexpected_command")


def _behavior_command_kind(
    command: str,
    state: _QualificationState,
) -> str:
    """Classify a proposed command before its handler can fail."""

    if _is_quota_guard(command):
        return "quota_should_run"
    if _clock_output(command) is not None:
        return "clock"
    if _bounded_workspace_read_plan(command, fixture=state.fixture) is not None:
        return "workspace_read"
    if _bounded_refresh_state_help_limit(command) is not None:
        return "refresh_state_help"
    if _is_replan_successor_create(command):
        return "replan_successor_create"
    tokens = loopx_command_tokens(command) or []
    if "refresh-state" in tokens:
        return "semantic_replan_writeback"
    return "unexpected_command"


_EXPECTED_BEHAVIOR_FAILURES = frozenset(
    {
        "repeated_quota_should_run",
        "repeated_clock",
        "semantic_action_before_quota",
        "semantic_action_before_frontier_read",
        "semantic_action_before_work_source_read",
        "semantic_action_misses_uncovered_frontier",
        "refresh_state_help_before_quota",
        "refresh_state_help_before_frontier_read",
        "refresh_state_help_before_work_source_read",
        "repeated_refresh_state_help",
        "successor_create_before_quota",
        "successor_create_before_frontier_read",
        "successor_create_before_work_source_read",
        "successor_create_obligation_mismatch",
        "successor_create_composition_binding_mismatch",
        "repeated_successor_create",
        "successor_create_failed",
        "successor_transition_missing",
        "successor_reentry_quota_invalid",
        "successor_reentry_selected_todo_missing",
        "successor_reentry_selected_todo_mismatch",
        "successor_reentry_replan_not_closed",
        "successor_reentry_not_runnable",
        "successor_reentry_composition_not_scheduled",
        "quota_obligation_missing",
        "non_semantic_replan_action",
        "semantic_writeback_failed",
        "semantic_reentry_quota_invalid",
        "semantic_reentry_replan_not_closed",
        "semantic_reentry_not_future_monitor_wait",
        "semantic_reentry_did_not_exit",
        "manual_evidence_read_is_not_replan",
        "unexpected_command",
    }
)


def _behavior_failure_code(exc: Exception, *, kind: str) -> str:
    failure = str(exc)
    return failure if failure in _EXPECTED_BEHAVIOR_FAILURES else f"{kind}_execution_failed"


def _append_tool_response(
    state: _QualificationState,
    *,
    tool_call: Any,
    output: str,
) -> None:
    state.messages.extend(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [tool_call.provider_value],
            },
            {
                "role": "tool",
                "tool_call_id": tool_call.call_id,
                "content": output,
            },
        ]
    )


def _run_qualification_loop(
    client: DoubaoExecToolClient,
    state: _QualificationState,
    *,
    qualification_id: str,
    shell: VisionShellHost | None = None,
) -> dict[str, Any]:
    for _ in range(state.tool_call_limit):
        tool_call = client.next_tool_call(
            state.messages,
            tool_description=VISION_EXEC_TOOL_DESCRIPTION if state.fixture.required_vision else None,
        )
        if tool_call is None:
            return _qualification_receipt(
                state,
                qualification_id=qualification_id,
                actor_ref=client.actor_ref,
                passed=False,
                failure_code="model_returned_without_semantic_action",
            )
        kind = "shell" if shell else _behavior_command_kind(tool_call.command, state)
        try:
            if shell:
                output, exit_code = shell.execute(tool_call.command)
                observe_shell_evidence(output, state)
                state.read_only_host_commands_executed = True
                if exit_code:
                    raise _HostToolError("shell_nonzero", output, exit_code)
                completed = bool((state.vision_closeout or {}).get("settled"))
            else:
                output, kind, completed = _dispatch_behavior_command(tool_call.command, state)
        except _HostToolError as exc:
            _record_tool_step(state, kind=kind, command=tool_call.command,
                              exit_code=exc.exit_code, error_code=exc.code)
            _append_tool_response(state, tool_call=tool_call, output=json.dumps({
                "error_code": exc.code, "exit_code": exc.exit_code,
                "output": exc.output[:4096], "truncated": len(exc.output) > 4096,
            }))
            continue
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            _record_tool_step(state, kind=kind, command=tool_call.command)
            return _qualification_receipt(
                state,
                qualification_id=qualification_id,
                actor_ref=client.actor_ref,
                passed=False,
                failure_code=_behavior_failure_code(exc, kind=kind),
            )
        _record_tool_step(state, kind=kind, command=tool_call.command)
        if completed:
            return _qualification_receipt(
                state,
                qualification_id=qualification_id,
                actor_ref=client.actor_ref,
                passed=True,
                failure_code=None,
            )
        _append_tool_response(state, tool_call=tool_call, output=output)
    return _qualification_receipt(
        state,
        qualification_id=qualification_id,
        actor_ref=client.actor_ref,
        passed=False,
        failure_code="tool_call_budget_exhausted",
    )


class DoubaoReplanSemanticActionBehaviorActor:
    """Qualify a model-selected semantic replan through the real CLI boundary."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DOUBAO_2_1_PRO_MODEL,
        timeout_seconds: float = 90.0,
        transport: DoubaoActorTransport = _direct_ark_transport,
    ) -> None:
        self._client = DoubaoExecToolClient(
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    @classmethod
    def from_environment(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        transport: DoubaoActorTransport = _direct_ark_transport,
        timeout_seconds: float = 90.0,
    ) -> DoubaoReplanSemanticActionBehaviorActor:
        values = os.environ if environ is None else environ
        api_key = values.get(ARK_API_KEY_ENV, "")
        if not api_key.strip():
            raise RuntimeError(
                "ARK_API_KEY is not injected; live Doubao qualification is unavailable"
            )
        return cls(
            api_key=api_key,
            model=values.get(DOUBAO_MODEL_ENV, DOUBAO_2_1_PRO_MODEL),
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    def qualify(
        self,
        *,
        qualification_id: str,
        fixture_root: Path,
        composition_frontier: bool = False,
        exhausted_frontier: bool = False,
        required_vision: bool = False,
    ) -> dict[str, Any]:
        fixture = _build_fixture(
            fixture_root,
            composition_frontier=composition_frontier,
            exhausted_frontier=exhausted_frontier,
            required_vision=required_vision,
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": VISION_HOST_INSTRUCTION if required_vision else (
                    "You are Codex running one LoopX heartbeat against a hermetic "
                    "project. Follow the heartbeat task and use the shell tool. "
                    "The shell working directory is the connected goal project "
                    "root; relative paths in the active-state action resolve there. "
                    "Treat the quota packet as the control-plane authority. Complete "
                    "one bounded in-scope action and persist its result through the "
                    "real LoopX CLI. After quota, `cat` only the exact path named "
                    "in active_state_next_action, then `cat` only the source_ref "
                    "returned by that target, and fill and execute "
                    "interaction_contract.cli_channel.next_cli_actions[0]. Do not "
                    "use ls, find, rg, or paged help. If refresh-state syntax is "
                    "still unclear, run exactly `loopx refresh-state --help` once, "
                    "then execute the projected typed writeback. This scenario has "
                    "a direct refresh-state outcome: execute exactly one loopx "
                    "refresh-state command; do not create a Todo, chain commands, "
                    "or combine actions."
                ),
            },
            {"role": "user", "content": fixture.task_body},
        ]
        digest = sha256(qualification_id.encode()).hexdigest()[:16]
        state = _QualificationState(
            fixture=fixture,
            messages=messages,
            steps=[],
            turn_instance_id=f"qualification-{digest}",
        )
        if required_vision:
            def invoke(argv: list[str], cwd: Path, output: str) -> str:
                observe_shell_evidence(output, state)
                tokens = ["loopx", *argv]
                if "--agent-vision-json" in tokens:
                    index = tokens.index("--agent-vision-json") + 1
                    if index < len(tokens):
                        tokens[index] = str((cwd / tokens[index]).resolve())
                command = shlex.join(tokens)
                if "--help" in argv or "-h" in argv:
                    return _execute_loopx(command, fixture=fixture, turn_instance_id=state.turn_instance_id)
                if _is_quota_guard(command):
                    return (_execute_loopx(command, fixture=fixture, turn_instance_id=state.turn_instance_id)
                            if state.seen_quota else _handle_quota_command(command, state))
                result = dispatch_vision_closeout(command, state, execute=_execute_loopx)
                if result is None:
                    raise ValueError("This fixture grants CLI authority for quota, help, vision refresh and settlement only")
                return result[0]

            shell = VisionShellHost(fixture.project_root, invoke, turn_instance_id=state.turn_instance_id)
            try:
                return _run_qualification_loop(self._client, state, qualification_id=qualification_id, shell=shell)
            finally:
                shell.close()
        return _run_qualification_loop(
            self._client,
            state,
            qualification_id=qualification_id,
        )
