"""Fixture builders and pure observation helpers for semantic replan behavior tests."""

from __future__ import annotations

import json
import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast

from ...heartbeat_prompt import build_heartbeat_prompt
from ..quota.effective_action import EffectiveAction
from ..quota.turn_envelope import quota_action_signature_document
from .model_tool_behavior import (
    QuotaToolContext,
    argument_value,
    build_qualification_receipt,
    digest_text,
    execute_bounded_read_steps,
    execute_loopx_cli,
    hermetic_clock_output,
    is_fixture_quota_guard,
    loopx_command_tokens,
)
from .replan_vision_closeout_behavior import REQUIRED_VISION_CLOSEOUT_MAX_CALLS
from .selected_todo_tool_behavior import (
    BoundedWorkspaceReadStep,
    _SelectedTodoToolFixture,
    bounded_workspace_read_plan,
)

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
        has_capture = (
            self.quota_context is not None and self.quota_context.capture_path is not None
        )
        return REPLAN_SEMANTIC_ACTION_BEHAVIOR_MAX_CALLS + (
            REPLAN_SEMANTIC_ACTION_NATIVE_READ_MAX_CALLS if has_capture else 0
        )

    @property
    def maximum_tool_call_limit(self) -> int:
        if self.fixture.required_vision:
            return REQUIRED_VISION_CLOSEOUT_MAX_CALLS
        return (
            REPLAN_SEMANTIC_ACTION_BEHAVIOR_MAX_CALLS
            + REPLAN_SEMANTIC_ACTION_NATIVE_READ_MAX_CALLS
        )


def _as_mapping(value: object) -> JsonMapping:
    return cast(JsonMapping, value) if isinstance(value, Mapping) else {}


def _optional_mapping(value: object) -> JsonMapping | None:
    return cast(JsonMapping, value) if isinstance(value, Mapping) else None


def _exact_nonbool_int(mapping: JsonMapping, key: str) -> int | None:
    value = mapping.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


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


def _resolve_shell_cli_command(argv: list[str], cwd: Path) -> str:
    tokens = ["loopx", *argv]
    if "--agent-vision-json" in tokens:
        index = tokens.index("--agent-vision-json") + 1
        if index < len(tokens):
            tokens[index] = str((cwd / tokens[index]).resolve())
    return shlex.join(tokens)
