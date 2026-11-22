from __future__ import annotations

import json
import os
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast

from ...heartbeat_prompt import build_heartbeat_prompt
from ..quota.effective_action import EffectiveAction
from ..quota.turn_envelope import quota_action_signature_document
from ..work_items.progress_observation import ProgressResultClass
from .model_tool_behavior import (
    _module_reader_tokens, DoubaoExecToolBehaviorActorBase, DoubaoExecToolClient,
    ExecToolCall, LoopxCliExecutionError, QuotaToolContext, append_exec_tool_exchange,
    argument_value, build_qualification_receipt, digest_text,
    execute_bounded_read_steps, execute_loopx_cli, hermetic_clock_output,
    is_context_read_command, is_fixture_quota_guard, loopx_command_tokens,
    qualification_turn_instance_id, replan_view_command_tokens,
)
from .replan_vision_closeout_behavior import (
    REQUIRED_VISION_CLOSEOUT_MAX_CALLS, VISION_EXEC_TOOL_DESCRIPTION,
    VISION_HOST_INSTRUCTION, dispatch_vision_closeout, observe_shell_evidence,
)
from .selected_todo_tool_behavior import (
    BoundedWorkspaceReadStep, _SelectedTodoToolFixture, bounded_workspace_read_plan,
)
from .vision_shell_host import VisionShellHost

JsonObject = dict[str, object]
JsonMapping = Mapping[str, object]

REPLAN_SEMANTIC_ACTION_BEHAVIOR_RECEIPT_SCHEMA_VERSION = (
    "replan_semantic_action_behavior_receipt_v0"
)
# Native progressive delivery adds three bounded reads (capture, review, and
# writeback) to the same semantic action path. Keep the original seven-call
# behavior budget for ordinary work and grant a separate allowance only after
# the quota result identifies a native capture.
REPLAN_SEMANTIC_ACTION_BEHAVIOR_MAX_CALLS = 7
REPLAN_SEMANTIC_ACTION_NATIVE_READ_MAX_CALLS = 3
_NATIVE_CONTEXT_READ_KINDS = frozenset({"context_read", "replan_context_read"})
_MAX_HOST_TOOL_ERROR_OUTPUT_CHARS = 8192

_FIXTURE_GOAL_ID = "replan-semantic-action-fixture"
_FIXTURE_AGENT_ID = "codex-replan-semantic-action"
_FIXTURE_WORK_ITEM_OPERATION_ID = "fixture-replan-semantic-monitor"
_FIXTURE_WORK_ITEM_ID = (
    "todo_" + sha256(_FIXTURE_WORK_ITEM_OPERATION_ID.encode()).hexdigest()[:24]
)
_FIXTURE_FRONTIER_FILE = "replan-frontier.json"
_FIXTURE_SOURCE_FILE = "fixture/permission-config.json"
_FIXTURE_NEW_SURFACE_ID = "surface-permission-config"
_FIXTURE_NEW_HYPOTHESIS_ID = "hypothesis-permission-default"
_FIXTURE_NEW_PROBE_KIND = "static-contract-read"
_FIXTURE_NEW_EVIDENCE_ID = "evidence-permission-config"
_FIXTURE_COMPOSITION_EXPERIMENT_ID = "experiment-permission-composition"

_ORDINARY_HOST_SYSTEM_PROMPT = (
    "You are Codex running one LoopX heartbeat against a hermetic "
    "project. Follow the heartbeat task and use the shell tool. "
    "The shell working directory is the connected goal project "
    "root; relative paths in the active-state action resolve there. "
    "Treat the quota packet as the control-plane authority. If the "
    "guard returns native context navigation, execute its recovery "
    "reader before acting. If the delivered replan obligation names "
    "a context_read command, execute that exact command before "
    "choosing a semantic result. Read the work evidence. "
    "If the current replan_action_packet supplies a writeback command, "
    "execute that exact command to obtain the typed action contract. "
    "Otherwise use its inline writeback_contract. Complete "
    "one bounded in-scope action and persist its result through the "
    "real LoopX CLI. After quota, `cat` only the exact path named "
    "in active_state_next_action, then `cat` only the source_ref "
    "returned by that target, and fill and execute "
    "interaction_contract.cli_channel.next_cli_actions[0]. Do not "
    "use ls, find, rg, or paged help. If refresh-state syntax is "
    "still unclear, run exactly `loopx refresh-state --help` once, "
    "then execute the projected typed writeback. Author the "
    "evidence-linked Vision and current stride decision in a "
    "project draft using the ordinary shell. Draft commands run "
    "in OS isolation; original inputs, authority and private data "
    "are protected. Shell composition may call only the same "
    "bounded CLI operations. Follow the "
    "current writeback contract for a direct outcome or a bound "
    "successor, then execute the returned original-Turn settlement "
    "command. Creating a successor does not settle the Turn or "
    "authorize executing that successor in this Turn."
)

_EXPECTED_BEHAVIOR_FAILURES = frozenset(
    {
        "repeated_explore_turn_context", "required_explore_turn_context_missing",
        "repeated_quota_should_run", "repeated_clock", "semantic_action_before_quota",
        "semantic_action_before_delivery_context_read",
        "semantic_action_before_replan_context_read", "semantic_action_before_frontier_read",
        "semantic_action_before_work_source_read",
        "semantic_action_before_replan_writeback_read",
        "semantic_action_misses_uncovered_frontier", "refresh_state_help_before_quota",
        "refresh_state_help_before_frontier_read", "refresh_state_help_before_work_source_read",
        "refresh_state_help_before_replan_writeback_read", "repeated_refresh_state_help",
        "successor_create_before_quota", "successor_create_before_delivery_context_read",
        "successor_create_before_replan_context_read", "successor_create_before_frontier_read",
        "successor_create_before_work_source_read",
        "successor_create_before_replan_writeback_read",
        "successor_create_obligation_mismatch", "successor_create_turn_mismatch",
        "successor_create_composition_binding_mismatch", "repeated_successor_create",
        "successor_create_failed", "successor_transition_missing",
        "successor_reentry_quota_invalid", "successor_reentry_selected_todo_missing",
        "successor_reentry_selected_todo_mismatch", "successor_reentry_replan_not_closed",
        "successor_reentry_not_runnable", "successor_reentry_composition_not_scheduled",
        "quota_obligation_missing", "semantic_writeback_failed",
        "semantic_writeback_binding_mismatch", "semantic_writeback_requires_authored_draft",
        "semantic_writeback_draft_outside_fixture",
        "semantic_writeback_durable_receipt_missing", "semantic_writeback_settlement_missing",
        "semantic_spend_before_writeback", "semantic_spend_identity_mismatch",
        "semantic_settlement_readback_failed", "semantic_reentry_quota_invalid",
        "semantic_reentry_replan_not_closed", "semantic_reentry_not_future_monitor_wait",
        "semantic_reentry_did_not_exit", "manual_evidence_read_is_not_replan",
        "replan_context_read_before_quota", "replan_context_read_before_delivery_context",
        "replan_writeback_read_before_review", "repeated_replan_context_read",
        "repeated_replan_writeback_read", "replan_writeback_read_invalid",
        "unexpected_replan_context_read", "replan_context_read_invalid", "unexpected_command",
    }
)


class _HostToolError(RuntimeError):
    """A host tool result that supplies no semantic acceptance or extra budget."""

    def __init__(self, code: str, output: str, exit_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.output = output
        self.exit_code = exit_code


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
    messages: list[JsonObject]
    steps: list[JsonObject]
    turn_instance_id: str
    quota_packet: JsonObject | None = None
    quota_context: QuotaToolContext | None = None
    quota_observation: JsonObject | None = None
    semantic_delta: JsonObject | None = None
    seen_quota: bool = False
    seen_clock: bool = False
    seen_refresh_state_help: bool = False
    read_only_host_commands_executed: bool = False
    delivery_context_read: bool = False
    replan_context_read: bool = False
    replan_writeback_read: bool = False
    frontier_context_read: bool = False
    work_source_read: bool = False
    created_successor_id: str | None = None
    successor_transition: JsonObject | None = None
    successor_reentry_observation: JsonObject | None = None
    semantic_reentry_observation: JsonObject | None = None
    vision_closeout: JsonObject | None = None
    settlement_command: str | None = None
    semantic_result_class: str | None = None
    draft_shell_executed: bool = False
    replan_recovery_context: QuotaToolContext | None = None

    @property
    def tool_call_limit(self) -> int:
        if self.fixture.required_vision:
            return REQUIRED_VISION_CLOSEOUT_MAX_CALLS
        has_capture = self.quota_context is not None and self.quota_context.capture_path is not None
        return REPLAN_SEMANTIC_ACTION_BEHAVIOR_MAX_CALLS + (
            REPLAN_SEMANTIC_ACTION_NATIVE_READ_MAX_CALLS if has_capture else 0
        )

    @property
    def maximum_tool_call_limit(self) -> int:
        if self.fixture.required_vision:
            return REQUIRED_VISION_CLOSEOUT_MAX_CALLS
        return REPLAN_SEMANTIC_ACTION_BEHAVIOR_MAX_CALLS + REPLAN_SEMANTIC_ACTION_NATIVE_READ_MAX_CALLS


def _as_mapping(value: object) -> JsonMapping:
    return cast(JsonMapping, value) if isinstance(value, Mapping) else {}


def _as_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return int(str(value)) if value else 0


def _json_object(raw: str, *, error: str = "expected_json_object") -> JsonObject:
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError(error)
    return cast(JsonObject, decoded)


def _write_json(path: Path, payload: JsonMapping, *, pretty: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if pretty
        else json.dumps(payload, sort_keys=True)
    )
    path.write_text(text + "\n", encoding="utf-8")


def _write_fixture_inputs(
    project_root: Path, state_relative: Path, *, exhausted_frontier: bool,
) -> tuple[Path, Path]:
    """Write the fixture config, frontier inventory, and active-state file."""
    state_path = project_root / state_relative
    state_path.parent.mkdir(parents=True, exist_ok=True)
    frontier_target = project_root / _FIXTURE_FRONTIER_FILE
    source_target = project_root / _FIXTURE_SOURCE_FILE
    _write_json(source_target, {
        "schema_version": "permission_config_contract_v0",
        "default_role": "reader",
        "write_requires_explicit_grant": True,
        "public_safe": True,
    })
    uncovered: list[JsonObject] = [] if exhausted_frontier else [{
        "surface_id": _FIXTURE_NEW_SURFACE_ID,
        "hypothesis_id": _FIXTURE_NEW_HYPOTHESIS_ID,
        "probe_kind": _FIXTURE_NEW_PROBE_KIND,
        "evidence_id": _FIXTURE_NEW_EVIDENCE_ID,
        "source_ref": _FIXTURE_SOURCE_FILE,
    }]
    _write_json(frontier_target, {
        "schema_version": "replan_frontier_fixture_v0",
        "covered": [{
            "surface_id": "surface-existing", "hypothesis_id": "hypothesis-existing",
            "probe_kind": "probe-existing", "evidence_id": "evidence-existing",
        }],
        "uncovered": uncovered,
    })
    fixture_objective = (
        "Inspect the projected frontier and close the bounded goal when its coverage is exhausted."
        if exhausted_frontier
        else "Inspect the projected frontier and select one uncovered surface."
    )
    fixture_next_action = (
        f"Read `{_FIXTURE_FRONTIER_FILE}`. If no uncovered entries remain, "
        "persist a coverage-backed exploration_exhausted result through refresh-state."
        if exhausted_frontier
        else (
            f"Read `{_FIXTURE_FRONTIER_FILE}`, inspect the selected uncovered "
            "entry's `source_ref`, then persist the typed result through refresh-state."
        )
    )
    state_path.write_text(
        "---\nstatus: active\nowner_mode: goal\nhandoff_mode: soft_claim\n"
        f'objective: "{fixture_objective}"\nupdated_at: 2026-08-13T00:00:00+08:00\n---\n\n'
        f"# Replan Semantic Action Fixture\n\n## Objective\n\n{fixture_objective}\n\n"
        f"## Next Action\n\n- {fixture_next_action}\n\n## Agent Todo\n",
        encoding="utf-8",
    )
    return frontier_target, source_target


def _seed_fixture_authority_and_runs(
    *,
    source_root: Path,
    project_root: Path,
    runtime_root: Path,
    global_registry_path: Path,
    registry_goal: JsonMapping,
    composition_frontier: bool,
    required_vision: bool,
) -> None:
    """Initialize canonical File authority, seed runs, and optionally seed explore graph."""
    from ..coordination.goal_storage_initialization import initialize_goal_storage_target
    from ..todos.mutation_api import add_goal_todo

    initialized = initialize_goal_storage_target(
        runtime_root, registry_goal, registry_path=global_registry_path,
    )
    if not isinstance(initialized, dict) or (
        initialized.get("authority_initialized") is not True
        or initialized.get("legacy_fallback_used") is not False
    ):
        raise AssertionError("fixture did not initialize canonical File authority")
    monitor = add_goal_todo(
        registry_path=global_registry_path, runtime_root_arg=str(runtime_root),
        goal_id=_FIXTURE_GOAL_ID, role="agent",
        text="Observe the bounded fixture until its frontier changes.",
        priority="P1", task_class="continuous_monitor", action_kind="observe_fixture",
        claimed_by=_FIXTURE_AGENT_ID, agent_id=_FIXTURE_AGENT_ID,
        monitor_metadata={
            "target_key": "replan-semantic-fixture", "cadence": "1d",
            "next_due_at": "2999-01-01T00:00:00Z", "watch_only": "true",
        },
        operation_id=_FIXTURE_WORK_ITEM_OPERATION_ID,
    )
    if monitor.get("todo_id") != _FIXTURE_WORK_ITEM_ID:
        raise AssertionError("fixture monitor identity is not deterministic")
    if required_vision:
        add_goal_todo(
            registry_path=global_registry_path, runtime_root_arg=str(runtime_root),
            goal_id=_FIXTURE_GOAL_ID, role="agent",
            text="Continue the peer-owned implementation.",
            priority="P0", task_class="advancement_task",
            claimed_by="codex-fixture-peer", agent_id="codex-fixture-peer",
            operation_id="fixture-replan-peer-implementation",
        )
    runs_dir = runtime_root / "goals" / _FIXTURE_GOAL_ID / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    prior_runs: list[JsonObject] = []
    for minute in (() if required_vision else (2, 1)):
        generated_at = f"2026-08-13T00:0{minute}:00+00:00"
        run: JsonObject = {
            "generated_at": generated_at, "goal_id": _FIXTURE_GOAL_ID,
            "agent_id": _FIXTURE_AGENT_ID, "classification": "bounded_fixture_probe",
            "delivery_batch_scale": "single_surface", "delivery_outcome": "surface_only",
            "progress_observation": {
                "schema_version": "typed_progress_observation_v0", "result_class": "unchanged",
                "work_item_id": _FIXTURE_WORK_ITEM_ID, "surface_id": "surface-existing",
                "hypothesis_id": "hypothesis-existing", "probe_kind": "probe-existing",
                "evidence_ids": ["evidence-existing"],
            },
        }
        safe_timestamp = generated_at.replace(":", "-")
        json_path = runs_dir / f"{safe_timestamp}.json"
        markdown_path = runs_dir / f"{safe_timestamp}.md"
        _write_json(json_path, run, pretty=False)
        markdown_path.write_text("# Bounded fixture probe\n", encoding="utf-8")
        prior_runs.append({**run, "json_path": str(json_path), "markdown_path": str(markdown_path)})
    (runs_dir / "index.jsonl").write_text(
        "".join(json.dumps(run, sort_keys=True) + "\n" for run in prior_runs), encoding="utf-8",
    )
    if not composition_frontier:
        return
    nodes = (
        ("surface-existing", "Existing closed surface", "hypothesis", "resolved", "evidence-existing"),
        (
            _FIXTURE_NEW_SURFACE_ID, "Permission configuration surface",
            "hypothesis", "dead_end", _FIXTURE_NEW_EVIDENCE_ID,
        ),
        (
            _FIXTURE_COMPOSITION_EXPERIMENT_ID,
            "Jointly probe the existing and permission surfaces",
            "experiment", "open", None,
        ),
    )
    explore_commands: list[tuple[str, ...]] = [
        (
            "node", "--node-id", node_id, "--title", title, "--kind", kind, "--status", status,
            *(("--evidence-ref", evidence_ref) if evidence_ref else ()),
        )
        for node_id, title, kind, status, evidence_ref in nodes
    ] + [
        ("edge", "--from", _FIXTURE_COMPOSITION_EXPERIMENT_ID, "--to", target, "--type", "depends_on")
        for target in ("surface-existing", _FIXTURE_NEW_SURFACE_ID)
    ]
    for explore_args in explore_commands:
        execute_loopx_cli(
            shlex.join((
                "loopx", "--format", "json", "--registry", "fixture-registry",
                "--runtime-root", "fixture-runtime", "explore", *explore_args,
                "--goal-id", _FIXTURE_GOAL_ID,
            )),
            source_root=source_root, project_root=project_root,
            argument_overrides={"--registry": str(global_registry_path), "--runtime-root": str(runtime_root)},
        )


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
    state_relative = Path(".codex") / "goals" / _FIXTURE_GOAL_ID / "ACTIVE_GOAL_STATE.md"
    frontier_target, source_target = _write_fixture_inputs(
        project_root, state_relative, exhausted_frontier=exhausted_frontier,
    )
    local_registry_path = project_root / ".loopx" / "registry.json"
    global_registry_path = fixture_home / ".codex" / "loopx" / "registry.global.json"
    agents = [_FIXTURE_AGENT_ID, "codex-fixture-peer"] if required_vision else [_FIXTURE_AGENT_ID]
    task_classes = ["advancement_task", "continuous_monitor"] if required_vision else ["continuous_monitor"]
    registry_goal: JsonObject = {
        "id": _FIXTURE_GOAL_ID, "domain": "replan-semantic-action-fixture", "status": "active",
        "repo": str(project_root), "state_file": str(state_relative),
        "adapter": {"kind": "fixture_connected_delivery_v0", "status": "connected-delivery"},
        "coordination": {
            "registered_agents": agents, "agent_model": "peer_v1",
            "storage_target": {
                "schema_version": "loopx_new_goal_storage_target_v1",
                "provider": "file", "handoff_mode": "soft_claim",
            },
            "agent_profiles": {
                _FIXTURE_AGENT_ID: {
                    "schema_version": "agent_profile_v1", "agent_id": _FIXTURE_AGENT_ID,
                    "profile_role": "quality-qualification",
                    "scope_summary": "Qualify one bounded semantic replan.",
                    "default_task_classes": task_classes,
                    "vision_requirement": "required" if required_vision else "optional",
                }
            },
        },
        "authority_sources": [],
        "quota": {"compute": 1.0, "window_hours": 24, "allowed_slots": 5},
        "creation_operation_id": "goal-create:replan-semantic-action-fixture",
    }
    if composition_frontier:
        registry_goal["spawn_policy"] = {"explore_harness": {"enabled": True, "profile": "generic"}}
    registry: JsonObject = {
        "schema_version": "0.1", "updated_at": "2026-08-13T00:00:00+08:00",
        "common_runtime_root": str(runtime_root), "goals": [registry_goal],
    }
    for registry_path in (local_registry_path, global_registry_path):
        _write_json(registry_path, registry)
    _seed_fixture_authority_and_runs(
        source_root=source_root, project_root=project_root, runtime_root=runtime_root,
        global_registry_path=global_registry_path, registry_goal=registry_goal,
        composition_frontier=composition_frontier, required_vision=required_vision,
    )
    prompt = build_heartbeat_prompt(
        goal_id=_FIXTURE_GOAL_ID, thin=True, agent_id=_FIXTURE_AGENT_ID,
        registered_agents=[_FIXTURE_AGENT_ID],
        available_capabilities=["shell", "filesystem_read", "filesystem_write"],
        runtime_profile="codex_app_heartbeat",
        registry_path=os.path.relpath(global_registry_path, project_root),
    )
    quota_guard_command = str(prompt["quota_guard_command"])
    if quota_guard_command not in str(prompt["task_body"]):
        raise ValueError("heartbeat fixture must contain its production quota guard")
    return _ReplanSemanticActionFixture(
        task_body=str(prompt["task_body"]), quota_guard_command=quota_guard_command,
        project_root=project_root, runtime_root=runtime_root,
        global_registry_path=global_registry_path, source_root=source_root,
        frontier_target=frontier_target, work_source_target=source_target,
        composition_experiment_ref=_FIXTURE_COMPOSITION_EXPERIMENT_ID if composition_frontier else None,
        frontier_exhausted=exhausted_frontier, required_vision=required_vision,
    )


def _clock_output(command: str) -> str | None:
    return hermetic_clock_output(
        command, utc_output="2026-08-12T16:00:00Z\n", local_output="2026-08-13T00:00:00+08:00\n",
    )


def _is_quota_guard(command: str) -> bool:
    return is_fixture_quota_guard(command, goal_id=_FIXTURE_GOAL_ID, agent_id=_FIXTURE_AGENT_ID)


def _progress_observation_from_command(command: str) -> JsonObject | None:
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
    observation: JsonObject = {
        "schema_version": "typed_progress_observation_v0",
        "result_class": result_class,
        "work_item_id": _FIXTURE_WORK_ITEM_ID,
    }
    for option, field in (
        ("--progress-surface-id", "surface_id"), ("--progress-hypothesis-id", "hypothesis_id"),
        ("--progress-probe-kind", "probe_kind"), ("--progress-blocker-id", "blocker_id"),
        ("--progress-coverage-scope-id", "coverage_scope_id"),
    ):
        if value := argument_value(tokens, option):
            observation[field] = value
    evidence_ids = [
        tokens[index + 1] for index, token in enumerate(tokens[:-1]) if token == "--progress-evidence-id"
    ]
    if evidence_ids:
        observation["evidence_ids"] = evidence_ids
    if "--progress-coverage-complete" in tokens:
        observation["coverage_complete"] = True
    return observation


def _is_replan_successor_create(command: str) -> bool:
    tokens = loopx_command_tokens(command)
    if not tokens or "todo" not in tokens:
        return False
    todo_index = tokens.index("todo")
    return bool(
        tokens[todo_index : todo_index + 2] == ["todo", "add"]
        and argument_value(tokens, "--goal-id") == _FIXTURE_GOAL_ID
        and argument_value(tokens, "--role") == "agent"
        and argument_value(tokens, "--task-class") == "advancement_task"
        and argument_value(tokens, "--claimed-by") == _FIXTURE_AGENT_ID
        and argument_value(tokens, "--text")
    )


def _required_explore_read_command(packet: JsonMapping) -> str | None:
    """Accept only this fixture's current, scoped turn-start read contract."""
    agent_channel = _as_mapping(_as_mapping(packet.get("interaction_contract")).get("agent_channel"))
    raw_reads = agent_channel.get("required_reads")
    if not isinstance(raw_reads, list):
        raw_reads = packet.get("required_reads")
    reads = [
        read for read in (raw_reads if isinstance(raw_reads, list) else ())
        if isinstance(read, Mapping) and read.get("kind") == "explore_turn_context"
    ]
    if not reads:
        return None
    if len(reads) != 1:
        raise ValueError("unexpected_command")
    read = reads[0]
    if read.get("source") != "turn_start_capability_hook" or read.get("ordering") != "before_work":
        raise ValueError("unexpected_command")
    command = str(read.get("command") or "")
    tokens = loopx_command_tokens(command) or []
    if "explore" not in tokens:
        raise ValueError("unexpected_command")
    explore_index = tokens.index("explore")
    if (
        tokens[explore_index : explore_index + 2] != ["explore", "turn-context"]
        or argument_value(tokens, "--goal-id") != _FIXTURE_GOAL_ID
        or argument_value(tokens, "--agent-id") != _FIXTURE_AGENT_ID
    ):
        raise ValueError("unexpected_command")
    return command


def _quota_behavior_observation(
    packet: JsonMapping, *, replan_view: JsonMapping | None = None,
) -> JsonObject:
    _required_explore_read_command(packet)
    signature = quota_action_signature_document(packet)
    action, user = _as_mapping(signature.get("action")), _as_mapping(signature.get("user"))
    obligation = packet.get("autonomous_replan_obligation")
    replan_action = packet.get("replan_action_packet")
    if not isinstance(obligation, Mapping) or not isinstance(replan_action, Mapping):
        raise TypeError("quota must project an obligation and replan action packet")
    raw_context = obligation.get("replan_context")
    progressive_view = not isinstance(raw_context, Mapping)
    context: object = replan_view if progressive_view else raw_context
    if not isinstance(context, Mapping):
        raise TypeError("quota must deliver the replan coverage context")
    novelty = _as_mapping(obligation.get("replan_novelty_policy"))
    source_meta = novelty if progressive_view and novelty else context
    delivery = str(source_meta.get("delivery") or "")
    evidence_source = str(source_meta.get("evidence_source") or "")
    progressive_valid = bool(
        progressive_view
        and context.get("ok") is True
        and context.get("stage") == "review"
        and context.get("goal_id") == _FIXTURE_GOAL_ID
        and context.get("agent_id") == _FIXTURE_AGENT_ID
        and context.get("obligation_id") == obligation.get("obligation_id")
        and str(context.get("source_hash") or "").startswith("sha256:")
    )
    inline_valid = bool(
        not progressive_view
        and _as_mapping(context.get("delivery_receipt")).get("status") == "delivered"
    )
    if (
        packet.get("decision") != "autonomous_replan_required"
        or replan_action.get("decision") != "replan_required"
        or replan_action.get("obligation_id") != obligation.get("obligation_id")
        or delivery != "host_projected"
        or evidence_source != "compact_run_history"
        or not (progressive_valid or inline_valid)
    ):
        raise ValueError("quota does not expose the semantic replan contract")
    if not (
        action.get("must_attempt") is True
        and action.get("delivery_allowed") is False
        and action.get("quiet_noop_allowed") is False
    ):
        raise ValueError("semantic replan must require its Host action without ordinary delivery authority")
    frontier_holder = context if progressive_view else replan_action
    raw_required = _as_mapping(frontier_holder.get("uncovered_frontier")).get("required_any_of")
    raw_triggers = obligation.get("triggers")
    trigger_kinds = sorted({
        str(trigger["kind"])
        for trigger in (raw_triggers if isinstance(raw_triggers, list) else ())
        if isinstance(trigger, Mapping) and "kind" in trigger
    })
    return {
        "decision": "execute", "selected_todo_id": None,
        "user_action_required": bool(user.get("action_required")),
        "must_attempt_work": True, "delivery_allowed": False, "quiet_noop_allowed": False,
        "external_write_requested": False, "replan_obligation_id": obligation.get("obligation_id"),
        "context_delivery": "progressive_replan_view" if progressive_view else "host_projected",
        "required_semantic_outcomes": list(raw_required) if isinstance(raw_required, list) else [],
        "trigger_kinds": trigger_kinds,
    }


def _successor_reentry_observation(
    packet: JsonMapping, *, successor_todo_id: str, composition_experiment_ref: str | None,
) -> JsonObject:
    selected_todo = packet.get("selected_todo")
    if not isinstance(selected_todo, Mapping):
        raise ValueError("successor_reentry_selected_todo_missing")
    if str(selected_todo.get("todo_id") or "") != successor_todo_id:
        raise ValueError("successor_reentry_selected_todo_mismatch")
    if packet.get("decision") == "autonomous_replan_required" or isinstance(
        packet.get("autonomous_replan_obligation"), Mapping
    ):
        raise ValueError("successor_reentry_replan_not_closed")
    if not (packet.get("decision") == "run" and packet.get("effective_action") == EffectiveAction.NORMAL_RUN.value):
        raise ValueError("successor_reentry_not_runnable")
    observation: JsonObject = {
        "decision": packet.get("decision"), "effective_action": packet.get("effective_action"),
        "selected_todo_id": successor_todo_id, "replan_closed": True,
    }
    if composition_experiment_ref:
        raw_gaps = _as_mapping(packet.get("bounded_research_frontier")).get("gaps")
        gaps = raw_gaps if isinstance(raw_gaps, list) else ()
        gap = next(
            (
                item for item in gaps
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
    packet: JsonMapping, *, obligation_id: str, result_class: str,
) -> JsonObject:
    obligation = packet.get("autonomous_replan_obligation")
    if (
        (isinstance(obligation, Mapping) and obligation.get("obligation_id") == obligation_id)
        or packet.get("decision") == "autonomous_replan_required"
    ):
        raise ValueError("semantic_reentry_replan_not_closed")
    if packet.get("decision") != "skip":
        raise ValueError("semantic_reentry_did_not_exit")
    frontier = _as_mapping(packet.get("goal_frontier_projection"))
    normalized_progress = _as_mapping(frontier.get("normalized_progress"))
    monitor_lanes = _as_mapping(frontier.get("monitor_only_lanes"))
    quiet_skip = (
        packet.get("effective_action") == EffectiveAction.MONITOR_QUIET_SKIP.value
        and frontier.get("replan_required") is False
    )
    future_monitor_wait = bool(
        quiet_skip and monitor_lanes.get("present") is True
        and _as_int(normalized_progress.get("agent_monitor_open_count")) > 0
        and _as_int(normalized_progress.get("agent_monitor_due_count")) == 0
    )
    terminal_coverage_exit = bool(
        result_class in {ProgressResultClass.EXPLORATION_EXHAUSTED.value, ProgressResultClass.NO_FOLLOWUP.value}
        and quiet_skip and monitor_lanes.get("present") is False
        and _as_int(normalized_progress.get("agent_open_count")) == 0
    )
    if not future_monitor_wait and not terminal_coverage_exit:
        raise ValueError("semantic_reentry_not_future_monitor_wait")
    return {
        "decision": packet.get("decision"), "effective_action": packet.get("effective_action"),
        "replan_rule": "future_monitor_wait" if future_monitor_wait else "terminal_coverage_exhausted",
        "replan_closed": True, "exited": True,
    }


def _execute_loopx(
    command: str, *, fixture: _ReplanSemanticActionFixture, turn_instance_id: str,
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
            shlex.join(tokens), source_root=fixture.source_root, project_root=fixture.project_root,
            argument_overrides={
                "--registry": str(fixture.global_registry_path),
                "--runtime-root": str(fixture.runtime_root),
                "--turn-instance-id": turn_instance_id,
            },
        )
    except LoopxCliExecutionError as exc:
        if not fixture.required_vision:
            raise
        # The real writer puts its refusal and recovery in stdout. Keep the
        # nonzero status and let the shell's existing feedback bound apply.
        feedback = exc.stdout.strip() if "refresh-state" in tokens else ""
        raise _HostToolError("loopx_cli_nonzero", feedback or str(exc), exc.returncode) from None


def _run_state_cli(command: str, state: _QualificationState) -> str:
    return _execute_loopx(command, fixture=state.fixture, turn_instance_id=state.turn_instance_id)


def _bounded_workspace_read_plan(
    command: str, *, fixture: _ReplanSemanticActionFixture,
) -> list[BoundedWorkspaceReadStep] | None:
    read_fixture = _SelectedTodoToolFixture(
        task_body=fixture.task_body, quota_guard_command=fixture.quota_guard_command,
        project_root=fixture.project_root, runtime_root=fixture.runtime_root,
        global_registry_path=fixture.global_registry_path, source_root=fixture.source_root,
        selected_target=fixture.work_source_target,
    )
    return bounded_workspace_read_plan(command, fixture=read_fixture)


def _bounded_refresh_state_help_limit(command: str) -> int | None:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    if len(tokens) < 3 or Path(tokens[0]).name != "loopx" or tokens[1:3] != ["refresh-state", "--help"]:
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
    limit = int(limit_text) if limit_text.isdigit() else 0
    return limit if 1 <= limit <= 200 else None


def _execute_workspace_read(
    command: str, *, fixture: _ReplanSemanticActionFixture,
) -> tuple[str, bool, bool]:
    plan = _bounded_workspace_read_plan(command, fixture=fixture)
    if plan is None:
        raise ValueError("workspace read is outside the hermetic fixture")
    results, status = execute_bounded_read_steps(plan, cwd=fixture.project_root)
    frontier_resolved = fixture.frontier_target.resolve()
    source_resolved = fixture.work_source_target.resolve()
    frontier_read = False
    work_source_read = False
    for step, step_status, _ in results:
        if step_status == 0 and isinstance(step, BoundedWorkspaceReadStep) and step.target is not None:
            resolved = step.target.resolve()
            frontier_read = frontier_read or resolved == frontier_resolved
            work_source_read = work_source_read or resolved == source_resolved
    if status != 0:
        raise RuntimeError(f"workspace read failed with exit={status}")
    return "".join(output for _, _, output in results), frontier_read, work_source_read


def _qualification_receipt(
    state: _QualificationState, *, qualification_id: str, actor_ref: str, passed: bool, failure_code: str | None,
) -> JsonObject:
    if passed and state.fixture.required_vision and not (state.vision_closeout or {}).get("settled"):
        passed = False
        failure_code = "required_vision_closeout_incomplete"
    semantic_delta = state.semantic_delta or {}
    raw_outcomes = semantic_delta.get("satisfying_outcomes")
    boundary: JsonObject = {
        "filesystem_writes_executed": True, "writes_limited_to_temporary_fixture": True,
        "read_only_host_commands_executed": state.read_only_host_commands_executed,
    }
    if state.fixture.required_vision:
        boundary["shell_commands_executed"] = bool(state.steps)
    elif state.draft_shell_executed:
        boundary["shell_commands_executed"] = True
    receipt = cast(JsonObject, build_qualification_receipt(
        schema_version=REPLAN_SEMANTIC_ACTION_BEHAVIOR_RECEIPT_SCHEMA_VERSION,
        qualification_id=qualification_id, actor_ref=actor_ref, passed=passed,
        failure_code=failure_code, steps=state.steps,
        extra_fields={
            "semantic_action_accepted": bool(passed and semantic_delta.get("accepted") is True),
            "selected_semantic_outcomes": list(raw_outcomes) if isinstance(raw_outcomes, list) else [],
        },
        boundary=boundary,
    ))
    if state.quota_observation is not None:
        receipt.update(state.quota_observation)
    for key, value in (
        ("successor_reentry", state.successor_reentry_observation),
        ("successor_transition", state.successor_transition),
        ("semantic_reentry", state.semantic_reentry_observation),
    ):
        if value is not None:
            receipt[key] = value
    receipt["qualification_scope"] = "required_vision_closeout" if state.fixture.required_vision else "semantic_action"
    receipt["tool_call_limit"] = state.tool_call_limit
    if state.fixture.required_vision:
        receipt["execution_host"] = "os_isolated_shell"
    else:
        receipt["ordinary_tool_call_limit"] = REPLAN_SEMANTIC_ACTION_BEHAVIOR_MAX_CALLS
        receipt["native_context_read_call_limit"] = (
            REPLAN_SEMANTIC_ACTION_NATIVE_READ_MAX_CALLS
            if state.tool_call_limit > REPLAN_SEMANTIC_ACTION_BEHAVIOR_MAX_CALLS else 0
        )
    if state.vision_closeout is not None:
        receipt["vision_closeout"] = state.vision_closeout
        receipt["semantic_action_accepted"] = bool(semantic_delta.get("accepted"))
    return receipt


def _record_tool_step(
    state: _QualificationState, *, kind: str, command: str,
    exit_code: int | None = None, error_code: str | None = None,
) -> None:
    step: JsonObject = {"ordinal": len(state.steps) + 1, "kind": kind, "command_digest": digest_text(command)}
    if error_code:
        step["exit_code"] = exit_code
        step["error_code"] = error_code
    state.steps.append(step)


def _assert_vision_trigger(state: _QualificationState) -> None:
    if state.fixture.required_vision and state.quota_observation is not None:
        triggers = state.quota_observation.get("trigger_kinds")
        if not isinstance(triggers, list) or "required_agent_vision_missing" not in triggers:
            raise ValueError("required_vision_fixture_trigger_mismatch")


def _expected_replan_view_commands(packet: JsonMapping) -> tuple[str, str]:
    obligation = _as_mapping(packet.get("autonomous_replan_obligation"))
    action = _as_mapping(packet.get("replan_action_packet"))
    return str(obligation.get("context_read") or ""), str(action.get("writeback") or "")


def _handle_quota_command(command: str, state: _QualificationState) -> str:
    if state.seen_quota:
        raise ValueError("repeated_quota_should_run")
    output = _run_state_cli(command, state)
    state.quota_context = QuotaToolContext.from_output(output)
    decoded = cast(JsonObject, state.quota_context.packet)
    state.quota_packet = decoded
    obligation = decoded.get("autonomous_replan_obligation")
    if isinstance(obligation, Mapping) and isinstance(obligation.get("replan_context"), Mapping):
        state.quota_observation = _quota_behavior_observation(decoded)
    elif not (
        state.quota_context.capture_path is not None
        and isinstance(obligation, Mapping)
        and isinstance(obligation.get("context_read"), str)
        and obligation.get("context_read")
    ):
        raise TypeError("quota must deliver or reference the replan coverage context")
    _assert_vision_trigger(state)
    state.seen_quota = True
    state.read_only_host_commands_executed = True
    return output


def _handle_context_read(command: str, state: _QualificationState) -> str:
    if state.quota_context is None:
        raise ValueError("context read before quota")
    context = state.replan_recovery_context or state.quota_context
    output = context.read(command, source_root=state.fixture.source_root, project_root=state.fixture.project_root)
    state.delivery_context_read = True
    state.read_only_host_commands_executed = True
    return output


def _handle_replan_context_read(command: str, state: _QualificationState) -> str:
    if state.quota_packet is None or state.quota_context is None:
        raise ValueError("replan_context_read_before_quota")
    if state.quota_context.capture_path is not None and not state.delivery_context_read:
        raise ValueError("replan_context_read_before_delivery_context")
    review_command, writeback_command = _expected_replan_view_commands(state.quota_packet)
    normalized = replan_view_command_tokens(command)
    if normalized is None or command not in {review_command, writeback_command}:
        raise ValueError("unexpected_replan_context_read")
    is_writeback = command == writeback_command and command != review_command
    if is_writeback and not state.replan_context_read:
        raise ValueError("replan_writeback_read_before_review")
    if is_writeback and state.replan_writeback_read:
        raise ValueError("repeated_replan_writeback_read")
    if not is_writeback and state.replan_context_read:
        raise ValueError("repeated_replan_context_read")
    output = execute_loopx_cli(
        shlex.join(normalized), source_root=state.fixture.source_root, project_root=state.fixture.project_root,
    )
    view = _json_object(output, error="replan_context_read_invalid")
    if is_writeback:
        expected_id = _as_mapping(
            state.quota_packet.get("autonomous_replan_obligation")
        ).get("obligation_id")
        if (
            view.get("stage") != "writeback"
            or view.get("obligation_id") != expected_id
            or not isinstance(view.get("writeback_contract"), Mapping)
        ):
            raise ValueError("replan_writeback_read_invalid")
        state.replan_writeback_read = True
    else:
        state.quota_observation = _quota_behavior_observation(state.quota_packet, replan_view=view)
        _assert_vision_trigger(state)
        state.replan_context_read = True
    state.read_only_host_commands_executed = True
    return output


def _handle_clock_command(output: str, state: _QualificationState) -> str:
    if state.seen_clock:
        raise ValueError("repeated_clock")
    state.seen_clock = True
    state.read_only_host_commands_executed = True
    return output


def _handle_workspace_read(command: str, state: _QualificationState) -> str:
    output, read_frontier, read_work_source = _execute_workspace_read(command, fixture=state.fixture)
    state.frontier_context_read = state.frontier_context_read or read_frontier
    state.work_source_read = state.work_source_read or read_work_source
    state.read_only_host_commands_executed = True
    return output


def _handle_refresh_state_help(state: _QualificationState, *, line_limit: int) -> str:
    _require_observed_frontier(state, prefix="refresh_state_help")
    if state.seen_refresh_state_help:
        raise ValueError("repeated_refresh_state_help")
    output = execute_loopx_cli(
        "loopx refresh-state --help", source_root=state.fixture.source_root, project_root=state.fixture.project_root,
    )
    state.seen_refresh_state_help = True
    state.read_only_host_commands_executed = True
    return "\n".join(output.splitlines()[:line_limit]) + "\n"


def _expected_obligation_id(state: _QualificationState) -> str:
    if state.quota_packet is None:
        raise ValueError("semantic_action_before_quota")
    obligation = state.quota_packet.get("autonomous_replan_obligation")
    obligation_id = str(obligation.get("obligation_id") or "").strip() if isinstance(obligation, Mapping) else ""
    if not obligation_id:
        raise ValueError("quota_obligation_missing")
    return obligation_id


def _require_observed_frontier(
    state: _QualificationState, *, prefix: str, work_source_required: bool = True,
) -> None:
    if state.quota_packet is None:
        raise ValueError(f"{prefix}_before_quota")
    if (
        state.quota_context is not None
        and state.quota_context.capture_path is not None
        and not state.delivery_context_read
    ):
        raise ValueError(f"{prefix}_before_delivery_context_read")
    if state.quota_observation is None:
        raise ValueError(f"{prefix}_before_replan_context_read")
    if not state.frontier_context_read:
        raise ValueError(f"{prefix}_before_frontier_read")
    if work_source_required and not state.work_source_read:
        raise ValueError(f"{prefix}_before_work_source_read")
    if state.quota_observation.get("context_delivery") == "progressive_replan_view" and not state.replan_writeback_read:
        raise ValueError(f"{prefix}_before_replan_writeback_read")


def _handle_successor_command(command: str, state: _QualificationState) -> str:
    _require_observed_frontier(state, prefix="successor_create")
    if state.created_successor_id is not None:
        raise ValueError("repeated_successor_create")
    expected_obligation_id = _expected_obligation_id(state)
    tokens = loopx_command_tokens(command) or []
    if argument_value(tokens, "--replan-obligation-id") != expected_obligation_id:
        raise ValueError("successor_create_obligation_mismatch")
    if argument_value(tokens, "--turn-instance-id") != state.turn_instance_id:
        raise ValueError("successor_create_turn_mismatch")
    if (
        state.fixture.composition_experiment_ref
        and argument_value(tokens, "--explore-result-node-ref") != state.fixture.composition_experiment_ref
    ):
        raise ValueError("successor_create_composition_binding_mismatch")
    output = _run_state_cli(command, state)
    decoded = _json_object(output, error="successor_create_failed")
    todo_id = str(decoded.get("todo_id") or "").strip()
    state.created_successor_id = todo_id
    if not (decoded.get("ok") is True and decoded.get("added") is True and todo_id):
        raise ValueError("successor_create_failed")
    transition = decoded.get("replan_transition")
    if not (
        isinstance(transition, dict)
        and transition.get("recorded") is True
        and transition.get("obligation_id") == expected_obligation_id
        and transition.get("successor_todo_id") == todo_id
        and transition.get("outcome") == "new_runnable_successor"
    ):
        raise ValueError("successor_transition_missing")
    immediate_end = (
        transition.get("turn_boundary") == "end_current_heartbeat"
        and decoded.get("host_action") == "end_current_heartbeat"
    )
    settle_then_end = (
        transition.get("turn_boundary") == "settle_original_turn_before_end"
        and isinstance(decoded.get("settlement_identity"), Mapping)
        and isinstance(decoded.get("settlement_plan"), Mapping)
    )
    if not (immediate_end or settle_then_end):
        raise ValueError("successor_create_failed")
    state.successor_transition = {
        key: cast(object, transition[key])
        for key in ("recorded", "obligation_id", "successor_todo_id", "outcome", "turn_boundary")
    }
    state.semantic_delta = {"accepted": True, "satisfying_outcomes": ["new_runnable_successor"]}
    # A created successor is not an original-Turn settlement. Return the native
    # plan to the actor; only its later writeback/spend permits fresh re-entry.
    state.read_only_host_commands_executed = True
    return output


def _fixture_runs(state: _QualificationState) -> list[JsonObject]:
    index = state.fixture.runtime_root / "goals" / _FIXTURE_GOAL_ID / "runs" / "index.jsonl"
    return [_json_object(line) for line in index.read_text(encoding="utf-8").splitlines() if line]


def _authored_vision(command: str, state: _QualificationState) -> JsonObject:
    tokens = loopx_command_tokens(command) or []
    path = argument_value(tokens, "--agent-vision-json")
    if (
        not path
        or tokens.count("--agent-vision-json") != 1
        or any(token.startswith("--agent-vision-json=") for token in tokens)
    ):
        raise ValueError("semantic_writeback_requires_authored_draft")
    draft = (state.fixture.project_root / path).resolve()
    if not draft.is_relative_to(state.fixture.project_root.resolve()) or not draft.is_file():
        raise ValueError("semantic_writeback_draft_outside_fixture")
    return _json_object(draft.read_text(encoding="utf-8"), error="semantic_writeback_requires_authored_draft")


def _bound_semantic_writeback(command: str, state: _QualificationState) -> str:
    """Observe the real writer; never construct the actor's Vision or binding."""
    tokens = loopx_command_tokens(command) or []
    obligation_id = _expected_obligation_id(state)
    if (
        argument_value(tokens, "--replan-obligation-id") != obligation_id
        or argument_value(tokens, "--turn-instance-id") != state.turn_instance_id
    ):
        raise ValueError("semantic_writeback_binding_mismatch")
    _authored_vision(command, state)
    try:
        output = _run_state_cli(command, state)
    except LoopxCliExecutionError as exc:
        # Only a refusal from this bound real writer can authorize another
        # captured reader. Shell text and model-selected paths grant nothing.
        rejected = _json_object(exc.stdout)
        transition = _as_mapping(rejected.get("replan_transition"))
        recovery = _as_mapping(transition.get("context_recovery"))
        path, digest = recovery.get("path"), recovery.get("sha256")
        if (
            rejected.get("ok") is not False or rejected.get("appended") is not False
            or transition.get("obligation_id") != obligation_id
            or not isinstance(path, str) or not isinstance(digest, str)
            or not Path(path).resolve().is_relative_to(state.fixture.runtime_root.resolve())
        ):
            raise
        state.replan_recovery_context = QuotaToolContext(
            packet=rejected, output=exc.stdout, capture_path=Path(path).resolve(), capture_sha256=digest,
        )
        raise _HostToolError("loopx_cli_nonzero", exc.stdout, exc.returncode) from None
    decoded = _json_object(output, error="semantic_writeback_failed")
    if decoded.get("ok") is not True:
        raise ValueError("semantic_writeback_failed")
    row = _fixture_runs(state)[-1]
    semantic = dict(_as_mapping(_as_mapping(row.get("autonomous_replan_ack")).get("semantic_delta")))
    identity = _as_mapping(_as_mapping(state.quota_packet).get("heartbeat_receipt")).get("settlement_identity")
    if not (
        semantic.get("accepted") is True and semantic.get("obligation_id") == obligation_id
        and _as_mapping(row.get("vision_checkpoint")).get("satisfied") is True
        and row.get("settlement_identity") == identity
    ):
        raise ValueError("semantic_writeback_durable_receipt_missing")
    owed = decoded.get("settlement_owed")
    if not isinstance(owed, Mapping) or not isinstance(owed.get("command"), str):
        raise ValueError("semantic_writeback_settlement_missing")
    state.settlement_command = owed["command"]
    state.semantic_delta = semantic
    return output


def _read_quota_packet(fixture: _ReplanSemanticActionFixture, turn_instance_id: str) -> JsonObject:
    output = _execute_loopx(fixture.quota_guard_command, fixture=fixture, turn_instance_id=turn_instance_id)
    return cast(JsonObject, QuotaToolContext.from_output(output).packet)


def _handle_semantic_spend(command: str, state: _QualificationState) -> str:
    if not state.settlement_command:
        raise ValueError("semantic_spend_before_writeback")
    if loopx_command_tokens(command) != loopx_command_tokens(state.settlement_command):
        raise ValueError("semantic_spend_identity_mismatch")
    output = _run_state_cli(command, state)
    replay = _read_quota_packet(state.fixture, state.turn_instance_id)
    spends = [row for row in _fixture_runs(state) if row.get("classification") == "quota_slot_spent"]
    if replay.get("effective_action") != EffectiveAction.HEARTBEAT_SETTLED_SKIP.value or len(spends) != 1:
        raise ValueError("semantic_settlement_readback_failed")
    following = _read_quota_packet(state.fixture, f"{state.turn_instance_id}-readback")
    obligation_id = _expected_obligation_id(state)
    if _as_mapping(following.get("autonomous_replan_obligation")).get("obligation_id") == obligation_id:
        raise ValueError("semantic_reentry_replan_not_closed")
    if state.created_successor_id:
        state.successor_reentry_observation = _successor_reentry_observation(
            following, successor_todo_id=state.created_successor_id,
            composition_experiment_ref=state.fixture.composition_experiment_ref,
        )
    elif state.semantic_result_class in {
        ProgressResultClass.EXPLORATION_EXHAUSTED.value,
        ProgressResultClass.NO_FOLLOWUP.value,
    }:
        state.semantic_reentry_observation = _semantic_reentry_observation(
            following, obligation_id=obligation_id, result_class=state.semantic_result_class,
        )
    state.vision_closeout = {
        "checkpoint_satisfied": True, "bound_writeback": True,
        "settled": True, "spend_count": 1, "original_obligation_closed": True,
    }
    return output


def _handle_semantic_writeback(command: str, observation: JsonMapping, state: _QualificationState) -> str:
    result_class = str(observation.get("result_class") or "")
    terminal_result = result_class in {
        ProgressResultClass.BLOCKED.value,
        ProgressResultClass.EXPLORATION_EXHAUSTED.value,
        ProgressResultClass.NO_FOLLOWUP.value,
    }
    _require_observed_frontier(state, prefix="semantic_action", work_source_required=not terminal_result)
    if not terminal_result:
        raw_evidence = observation.get("evidence_ids")
        evidence_ids = set(raw_evidence) if isinstance(raw_evidence, list) else set()
        if not (
            observation.get("surface_id") == _FIXTURE_NEW_SURFACE_ID
            and observation.get("hypothesis_id") == _FIXTURE_NEW_HYPOTHESIS_ID
            and observation.get("probe_kind") == _FIXTURE_NEW_PROBE_KIND
            and _FIXTURE_NEW_EVIDENCE_ID in evidence_ids
        ):
            raise ValueError("semantic_action_misses_uncovered_frontier")
    # The writer owns semantic acceptance and recovery for the original Turn.
    # A compact admission packet may omit the review basis that it resolves.
    state.semantic_result_class = result_class
    return _bound_semantic_writeback(command, state)


def _behavior_command_kind(command: str, state: _QualificationState) -> str:
    """Classify a proposed command before its handler can fail."""
    if _is_quota_guard(command):
        return "quota_should_run"
    if is_context_read_command(command):
        return "replan_context_recovery" if state.replan_recovery_context else "context_read"
    if replan_view_command_tokens(command) is not None:
        return "replan_context_read"
    if (
        state.quota_packet is not None
        and (required := _required_explore_read_command(state.quota_packet))
        and shlex.split(command) == shlex.split(required)
    ):
        return "explore_turn_context"
    if _clock_output(command) is not None:
        return "clock"
    if _bounded_workspace_read_plan(command, fixture=state.fixture) is not None:
        return "workspace_read"
    if _bounded_refresh_state_help_limit(command) is not None:
        return "refresh_state_help"
    if _is_replan_successor_create(command):
        return "replan_successor_create"
    tokens = loopx_command_tokens(command) or []
    if "spend-slot" in tokens:
        return "quota_spend_slot"
    if "refresh-state" in tokens:
        return "semantic_replan_writeback"
    return "unexpected_command"


def _dispatch_behavior_command(command: str, state: _QualificationState) -> tuple[str, str, bool]:
    kind = _behavior_command_kind(command, state)
    if kind == "quota_should_run":
        return _handle_quota_command(command, state), kind, False
    if kind in {"context_read", "replan_context_recovery"}:
        return _handle_context_read(command, state), kind, False
    if kind == "replan_context_read":
        return _handle_replan_context_read(command, state), kind, False
    if state.quota_packet is not None and _required_explore_read_command(state.quota_packet):
        observed = any(step["kind"] == "explore_turn_context" for step in state.steps)
        if kind == "explore_turn_context":
            if observed:
                raise ValueError("repeated_explore_turn_context")
            output = _run_state_cli(command, state)
            context = _json_object(output)
            if context.get("goal_id") != _FIXTURE_GOAL_ID or context.get("agent_id") != _FIXTURE_AGENT_ID:
                raise ValueError("unexpected_command")
            return output, kind, False
        if not observed:
            raise ValueError("required_explore_turn_context_missing")
    if kind == "clock" and (clock_output := _clock_output(command)) is not None:
        return _handle_clock_command(clock_output, state), kind, False
    if kind == "workspace_read":
        return _handle_workspace_read(command, state), kind, False
    if kind == "refresh_state_help" and (limit := _bounded_refresh_state_help_limit(command)) is not None:
        return _handle_refresh_state_help(state, line_limit=limit), kind, False
    if kind == "replan_successor_create":
        return _handle_successor_command(command, state), kind, False
    if kind == "quota_spend_slot":
        return _handle_semantic_spend(command, state), kind, True
    if kind == "semantic_replan_writeback":
        if state.created_successor_id:
            return _bound_semantic_writeback(command, state), kind, False
        if (observation := _progress_observation_from_command(command)) is not None:
            return _handle_semantic_writeback(command, observation, state), kind, False
    if "history" in shlex.split(command):
        raise ValueError("manual_evidence_read_is_not_replan")
    raise ValueError("unexpected_command")


def _behavior_failure_code(exc: Exception, *, kind: str) -> str:
    failure = str(exc)
    return failure if failure in _EXPECTED_BEHAVIOR_FAILURES else f"{kind}_execution_failed"


def _tool_call_budget_exhausted(state: _QualificationState) -> bool:
    if state.fixture.required_vision:
        return len(state.steps) >= REQUIRED_VISION_CLOSEOUT_MAX_CALLS
    ordinary_calls = sum(step.get("kind") not in _NATIVE_CONTEXT_READ_KINDS for step in state.steps)
    return (
        ordinary_calls >= REPLAN_SEMANTIC_ACTION_BEHAVIOR_MAX_CALLS
        or len(state.steps) >= state.maximum_tool_call_limit
    )


def _standalone_cli(command: str) -> bool:
    """Do not discard a requested shell prefix when extracting a CLI suffix."""
    if "\n" in command:
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    parsed = loopx_command_tokens(command)
    return parsed is not None and (
        tokens == parsed or _module_reader_tokens(tokens) is not None
        or (tokens[0].startswith("LOOPX_TURN=") and tokens[1:] == parsed)
    )


def _execute_isolated_shell_step(
    tool_call: ExecToolCall, state: _QualificationState, shell: VisionShellHost,
) -> tuple[str, bool]:
    # A captured module reader uses the same supervised CLI bridge,
    # without allowing the shell to import the source checkout.
    reader = replan_view_command_tokens(tool_call.command)
    if reader is None:
        try:
            reader = _module_reader_tokens(shlex.split(tool_call.command))
        except ValueError:
            pass  # Let the shell report its original syntax error.
    output, exit_code = shell.execute(shlex.join(reader) if reader is not None else tool_call.command)
    observe_shell_evidence(output, state)
    state.read_only_host_commands_executed = True
    if exit_code:
        raise _HostToolError("shell_nonzero", output, exit_code)
    return output, bool((state.vision_closeout or {}).get("settled"))


def _run_qualification_loop(
    client: DoubaoExecToolClient,
    state: _QualificationState,
    *,
    qualification_id: str,
    shell: VisionShellHost | None = None,
    author_shell: Callable[[str], tuple[str, int]] | None = None,
) -> JsonObject:
    def receipt(*, passed: bool, failure_code: str | None) -> JsonObject:
        return _qualification_receipt(
            state, qualification_id=qualification_id, actor_ref=client.actor_ref,
            passed=passed, failure_code=failure_code,
        )

    for _ in range(state.maximum_tool_call_limit):
        if _tool_call_budget_exhausted(state):
            break
        tool_call = client.next_tool_call(
            state.messages,
            tool_description=VISION_EXEC_TOOL_DESCRIPTION if state.fixture.required_vision else None,
        )
        if tool_call is None:
            return receipt(
                passed=False,
                failure_code=(
                    "semantic_closeout_incomplete"
                    if state.settlement_command or state.created_successor_id
                    else "model_returned_without_semantic_action"
                ),
            )
        kind = "shell" if shell else _behavior_command_kind(tool_call.command, state)
        if not shell and loopx_command_tokens(tool_call.command) is not None and not _standalone_cli(tool_call.command):
            kind = "draft_shell"
        try:
            if shell is not None:
                output, completed = _execute_isolated_shell_step(tool_call, state, shell)
            elif author_shell and (
                kind == "draft_shell"
                or (kind == "unexpected_command" and loopx_command_tokens(tool_call.command) is None)
            ):
                _require_observed_frontier(
                    state, prefix="semantic_action", work_source_required=not state.fixture.frontier_exhausted,
                )
                kind = "draft_shell"
                output, exit_code = author_shell(tool_call.command)
                state.draft_shell_executed = True
                if exit_code:
                    raise _HostToolError("shell_nonzero", output, exit_code)
                completed = bool((state.vision_closeout or {}).get("settled"))
            else:
                output, kind, completed = _dispatch_behavior_command(tool_call.command, state)
        except _HostToolError as exc:
            _record_tool_step(state, kind=kind, command=tool_call.command, exit_code=exc.exit_code, error_code=exc.code)
            append_exec_tool_exchange(
                state.messages, tool_call,
                json.dumps({
                    "error_code": exc.code, "exit_code": exc.exit_code,
                    "output": exc.output[:_MAX_HOST_TOOL_ERROR_OUTPUT_CHARS],
                    "truncated": len(exc.output) > _MAX_HOST_TOOL_ERROR_OUTPUT_CHARS,
                }),
                include_assistant_content=False,
            )
            continue
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            _record_tool_step(state, kind=kind, command=tool_call.command)
            return receipt(passed=False, failure_code=_behavior_failure_code(exc, kind=kind))
        _record_tool_step(state, kind=kind, command=tool_call.command)
        if completed:
            return receipt(passed=True, failure_code=None)
        append_exec_tool_exchange(state.messages, tool_call, output, include_assistant_content=False)
    return receipt(passed=False, failure_code="tool_call_budget_exhausted")


def _resolve_shell_cli_command(argv: list[str], cwd: Path) -> str:
    tokens = ["loopx", *argv]
    if "--agent-vision-json" in tokens:
        index = tokens.index("--agent-vision-json") + 1
        if index < len(tokens):
            tokens[index] = str((cwd / tokens[index]).resolve())
    return shlex.join(tokens)


class DoubaoReplanSemanticActionBehaviorActor(DoubaoExecToolBehaviorActorBase):
    """Qualify a model-selected semantic replan through the real CLI boundary."""

    def qualify(
        self, *, qualification_id: str, fixture_root: Path,
        composition_frontier: bool = False, exhausted_frontier: bool = False, required_vision: bool = False,
    ) -> JsonObject:
        fixture = _build_fixture(
            fixture_root, composition_frontier=composition_frontier,
            exhausted_frontier=exhausted_frontier, required_vision=required_vision,
        )
        messages: list[JsonObject] = [
            {"role": "system", "content": VISION_HOST_INSTRUCTION if required_vision else _ORDINARY_HOST_SYSTEM_PROMPT},
            {"role": "user", "content": fixture.task_body},
        ]
        state = _QualificationState(
            fixture=fixture, messages=messages, steps=[],
            turn_instance_id=qualification_turn_instance_id(qualification_id),
        )
        if required_vision:
            def invoke(argv: list[str], cwd: Path, output: str) -> str:
                observe_shell_evidence(output, state)
                command = _resolve_shell_cli_command(argv, cwd)
                if "--help" in argv or "-h" in argv:
                    return _run_state_cli(command, state)
                if _is_quota_guard(command):
                    return _run_state_cli(command, state) if state.seen_quota else _handle_quota_command(command, state)
                if is_context_read_command(command):
                    return _handle_context_read(command, state)
                if (reader := replan_view_command_tokens(command)) is not None:
                    for expected in _expected_replan_view_commands(state.quota_packet or {}):
                        if expected and replan_view_command_tokens(expected) == reader:
                            return _handle_replan_context_read(expected, state)
                    raise ValueError("unexpected_replan_context_read")
                result = dispatch_vision_closeout(command, state, execute=_execute_loopx)
                if result is None:
                    raise ValueError(
                        "This fixture grants CLI authority for quota, help, "
                        "vision refresh and settlement only"
                    )
                return result[0]

            shell = VisionShellHost(fixture.project_root, invoke, turn_instance_id=state.turn_instance_id)
            try:
                return _run_qualification_loop(self._client, state, qualification_id=qualification_id, shell=shell)
            finally:
                shell.close()
        draft_hosts: list[VisionShellHost] = []

        def draft_cli(argv: list[str], cwd: Path, _output: str) -> str:
            if (state.vision_closeout or {}).get("settled"):
                raise ValueError("The original Turn is settled; no later CLI action is admitted")
            output, _kind, _completed = _dispatch_behavior_command(_resolve_shell_cli_command(argv, cwd), state)
            return output

        def author_shell(command: str) -> tuple[str, int]:
            if not draft_hosts:
                draft_hosts.append(
                    VisionShellHost(
                        fixture.project_root,
                        draft_cli,
                        turn_instance_id=state.turn_instance_id,
                    )
                )
            return draft_hosts[0].execute(command)

        try:
            return _run_qualification_loop(
                self._client,
                state,
                qualification_id=qualification_id,
                author_shell=author_shell,
            )
        finally:
            for host in draft_hosts:
                host.close()
