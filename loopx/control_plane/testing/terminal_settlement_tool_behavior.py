from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from ..coordination.goal_storage_initialization import initialize_goal_storage_target
from ...heartbeat_prompt import build_heartbeat_prompt
from ...control_plane.todos.mutation_api import add_goal_todo, complete_goal_todo
from ..quota.turn_envelope import quota_action_signature_document
from .doubao_model_behavior_actor import (
    ARK_API_KEY_ENV,
    DOUBAO_2_1_PRO_MODEL,
    DOUBAO_MODEL_ENV,
    DoubaoActorTransport,
    _direct_ark_transport,
)
from .model_tool_behavior import (
    DoubaoExecToolClient,
    QUOTA_FIRST_TOOL_INSTRUCTION,
    argument_value,
    digest_text,
    execute_loopx_cli,
    loopx_command_tokens,
)
from .selected_todo_tool_behavior import bounded_workspace_read_plan

TERMINAL_SETTLEMENT_TOOL_BEHAVIOR_RECEIPT_SCHEMA_VERSION = (
    "terminal_settlement_tool_behavior_receipt_v0"
)
TERMINAL_SETTLEMENT_REENTRY_TOOL_BEHAVIOR_RECEIPT_SCHEMA_VERSION = (
    "terminal_settlement_reentry_tool_behavior_receipt_v0"
)
TERMINAL_SETTLEMENT_TOOL_BEHAVIOR_MAX_CALLS = 10
TERMINAL_SETTLEMENT_REENTRY_TOOL_BEHAVIOR_MAX_CALLS = 5

TERMINAL_SETTLEMENT_FIXTURE_GOAL_ID = "terminal-settlement-fixture"
TERMINAL_SETTLEMENT_FIXTURE_AGENT_ID = "codex-terminal-settlement"
TERMINAL_SETTLEMENT_FIXTURE_TODO_OPERATION_ID = (
    "terminal-settlement-fixture-todo"
)
TERMINAL_SETTLEMENT_FIXTURE_TODO_ID = (
    "todo_"
    + sha256(TERMINAL_SETTLEMENT_FIXTURE_TODO_OPERATION_ID.encode("utf-8")).hexdigest()[
        :24
    ]
)
TERMINAL_SETTLEMENT_FIXTURE_PROOF = "fixture/settlement-proof.json"
TERMINAL_SETTLEMENT_FIXTURE_ACTION_TEXT = (
    f"Read `{TERMINAL_SETTLEMENT_FIXTURE_PROOF}`, verify the bounded delivery, "
    "then settle this final Todo from the quota-projected plan. It has no successor."
)
TERMINAL_SETTLEMENT_REENTRY_FIXTURE_GOAL_ID = "terminal-reentry-fixture"
TERMINAL_SETTLEMENT_REENTRY_FIXTURE_AGENT_ID = "codex-terminal-reentry"
_EXPECTED_RECEIPT_STEPS = [
    "validation",
    "durable_writeback",
    "quota_spend",
    "terminal_closeout",
]


@dataclass(frozen=True)
class _TerminalSettlementFixture:
    task_body: str
    quota_guard_command: str
    project_root: Path
    runtime_root: Path
    global_registry_path: Path
    source_root: Path
    proof_target: Path


@dataclass(frozen=True)
class _TerminalSettlementReentryFixture:
    project_root: Path
    source_root: Path
    rejection_command: str
    rejection_packet: dict[str, Any]
    projected_actions_by_todo: dict[str, str]
    quota_reentry_action: str


@dataclass
class _ReentryQualificationState:
    fixture: _TerminalSettlementReentryFixture
    messages: list[dict[str, Any]]
    steps: list[dict[str, Any]] = field(default_factory=list)
    settled_todo_ids: list[str] = field(default_factory=list)
    provider_call_count: int = 0
    terminal_quota_observed: bool = False


@dataclass
class _QualificationState:
    fixture: _TerminalSettlementFixture
    messages: list[dict[str, Any]]
    turn_instance_id: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    quota_packet: dict[str, Any] | None = None
    contract: dict[str, Any] | None = None
    proof_read: bool = False
    writeback_committed: bool = False
    spend_committed: bool = False
    receipt_steps: list[str] = field(default_factory=list)
    seen_clock: bool = False


def _initialize_fixture_goal_storage(
    *,
    runtime_root: Path,
    goal: Mapping[str, Any],
    registry_path: Path,
) -> None:
    result = initialize_goal_storage_target(
        runtime_root,
        goal,
        registry_path=registry_path,
    )
    if not isinstance(result, dict) or result.get("authority_initialized") is not True:
        raise ValueError("fixture canonical authority initialization did not complete")


def _build_fixture(root: Path) -> _TerminalSettlementFixture:
    source_root = Path(__file__).resolve().parents[3]
    project_root = root / "project"
    runtime_root = root / "runtime"
    fixture_home = root / "home"
    state_relative = (
        Path(".codex")
        / "goals"
        / TERMINAL_SETTLEMENT_FIXTURE_GOAL_ID
        / "ACTIVE_GOAL_STATE.md"
    )
    state_path = project_root / state_relative
    local_registry_path = project_root / ".loopx" / "registry.json"
    global_registry_path = fixture_home / ".codex" / "loopx" / "registry.global.json"
    proof_target = project_root / TERMINAL_SETTLEMENT_FIXTURE_PROOF

    state_path.parent.mkdir(parents=True, exist_ok=True)
    proof_target.parent.mkdir(parents=True, exist_ok=True)
    proof_target.write_text(
        json.dumps(
            {
                "schema_version": "terminal_settlement_proof_v0",
                "bounded_delivery_validated": True,
                "classification": "validated_progress",
                "delivery_batch_scale": "single_surface",
                "delivery_outcome": "outcome_progress",
                "evidence": "fixture-settlement-proof",
                "follow_up_required": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "init", "-q"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=10,
    )
    subprocess.run(
        [
            "git",
            "remote",
            "add",
            "origin",
            "https://github.com/example/terminal-settlement-fixture.git",
        ],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=10,
    )
    state_path.write_text(
        "---\n"
        "status: active\n"
        "owner_mode: goal\n"
        'objective: "Settle one final validated delivery without losing quota accounting."\n'
        "updated_at: 2026-08-16T00:00:00+08:00\n"
        "---\n\n"
        "# Terminal Settlement Behavior Fixture\n\n"
        "## Objective\n\n"
        "Settle one final validated delivery without losing quota accounting.\n\n"
        "## Next Action\n\n"
        f"- {TERMINAL_SETTLEMENT_FIXTURE_ACTION_TEXT}\n\n"
        "## User Todo / Owner Review Reading Queue\n\n"
        "## Agent Todo\n\n"
        "\n",
        encoding="utf-8",
    )
    goal = {
        "id": TERMINAL_SETTLEMENT_FIXTURE_GOAL_ID,
        "creation_operation_id": "goal-create:terminal-settlement-fixture",
        "domain": "terminal-settlement-live-fixture",
        "status": "active",
        "repo": str(project_root),
        "state_file": str(state_relative),
        "adapter": {
            "kind": "fixture_connected_delivery_v0",
            "status": "connected-delivery",
        },
        "coordination": {
            "registered_agents": [TERMINAL_SETTLEMENT_FIXTURE_AGENT_ID],
            "agent_model": "peer_v1",
            "storage_target": {
                "schema_version": "loopx_new_goal_storage_target_v1",
                "provider": "file",
                "handoff_mode": "soft_claim",
            },
        },
        "authority_sources": [],
        "quota": {
            "compute": 1.0,
            "window_hours": 24,
            "allowed_slots": 5,
        },
    }
    registry = {
        "schema_version": "0.1",
        "updated_at": "2026-08-16T00:00:00+08:00",
        "common_runtime_root": str(runtime_root),
        "goals": [goal],
    }
    registry_text = json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True)
    for registry_path in (local_registry_path, global_registry_path):
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(registry_text + "\n", encoding="utf-8")
    _initialize_fixture_goal_storage(
        runtime_root=runtime_root,
        goal=goal,
        registry_path=global_registry_path,
    )
    created = add_goal_todo(
        registry_path=global_registry_path,
        goal_id=TERMINAL_SETTLEMENT_FIXTURE_GOAL_ID,
        role="agent",
        text=TERMINAL_SETTLEMENT_FIXTURE_ACTION_TEXT,
        priority="P0",
        task_class="advancement_task",
        action_kind="terminal_settlement_qualification",
        task_repository=(
            "git:github.com/example/terminal-settlement-fixture"
        ),
        continuation_policy="independent_handoff",
        claimed_by=TERMINAL_SETTLEMENT_FIXTURE_AGENT_ID,
        operation_id=TERMINAL_SETTLEMENT_FIXTURE_TODO_OPERATION_ID,
    )
    if created.get("todo_id") != TERMINAL_SETTLEMENT_FIXTURE_TODO_ID:
        raise ValueError("fixture canonical Todo identity drifted")

    prompt = build_heartbeat_prompt(
        goal_id=TERMINAL_SETTLEMENT_FIXTURE_GOAL_ID,
        thin=True,
        agent_id=TERMINAL_SETTLEMENT_FIXTURE_AGENT_ID,
        registered_agents=[TERMINAL_SETTLEMENT_FIXTURE_AGENT_ID],
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
    return _TerminalSettlementFixture(
        task_body=str(prompt["task_body"]),
        quota_guard_command=quota_guard_command,
        project_root=project_root,
        runtime_root=runtime_root,
        global_registry_path=global_registry_path,
        source_root=source_root,
        proof_target=proof_target,
    )


def _reentry_projection(
    packet: Mapping[str, Any],
    *,
    expected_todo_ids: set[str],
) -> tuple[dict[str, str], str]:
    transition = packet.get("replan_transition")
    if not (
        packet.get("ok") is False
        and isinstance(transition, Mapping)
        and transition.get("schema_version") == "replan_writeback_rejection_v0"
        and transition.get("host_action") == "settle_todo_lifecycle"
        and transition.get("resolution_mode") == "todo_lifecycle_settlement"
        and isinstance(transition.get("obligation_id"), str)
        and transition.get("obligation_id")
    ):
        raise ValueError("real refresh rejection did not expose lifecycle reentry")
    triggers = transition.get("triggers")
    actions = transition.get("next_cli_actions")
    if not isinstance(triggers, list) or not isinstance(actions, list):
        raise ValueError("lifecycle reentry projection is incomplete")
    projected_ids: set[str] = set()
    projected_keys: dict[str, str] = {}
    for trigger in triggers:
        if not isinstance(trigger, Mapping):
            raise ValueError("lifecycle reentry trigger is malformed")
        todo_id = str(trigger.get("todo_id") or "")
        completion_key = str(trigger.get("completion_turn_key") or "")
        if (
            trigger.get("kind") != "completed_advancement_without_successor"
            or todo_id not in expected_todo_ids
            or trigger.get("completion_identity_source") != "unscoped_completion"
            or not completion_key.startswith("local_completion_")
        ):
            raise ValueError("lifecycle reentry completion identity is malformed")
        projected_ids.add(todo_id)
        projected_keys[todo_id] = completion_key
    if projected_ids != expected_todo_ids:
        raise ValueError("lifecycle reentry did not project every completed Todo")

    actions_by_todo: dict[str, str] = {}
    quota_action = ""
    for action_value in actions:
        if not isinstance(action_value, str):
            raise ValueError("lifecycle reentry action must be a string")
        tokens = loopx_command_tokens(action_value)
        if not tokens:
            continue
        command_kind, command_action = _command_path(tokens)
        if command_kind == "todo" and command_action == "complete":
            todo_id = argument_value(tokens, "--todo-id") or ""
            if (
                todo_id not in expected_todo_ids
                or argument_value(tokens, "--goal-id")
                != TERMINAL_SETTLEMENT_REENTRY_FIXTURE_GOAL_ID
                or argument_value(tokens, "--agent-id")
                != TERMINAL_SETTLEMENT_REENTRY_FIXTURE_AGENT_ID
                or argument_value(tokens, "--completion-identity-key")
                != projected_keys[todo_id]
                or "--turn-instance-id" in tokens
                or "--replan-obligation-id" in tokens
                or "--no-follow-up" not in tokens
            ):
                raise ValueError("lifecycle reentry action identity is malformed")
            actions_by_todo[todo_id] = action_value
        elif command_kind == "quota" and command_action == "should-run":
            quota_action = action_value
        elif command_kind in {"refresh_state", "quota"}:
            raise ValueError("lifecycle reentry projected a prohibited write/spend")
    if set(actions_by_todo) != expected_todo_ids or not quota_action:
        raise ValueError("lifecycle reentry executable action set is incomplete")
    return actions_by_todo, quota_action


def _build_reentry_fixture(root: Path) -> _TerminalSettlementReentryFixture:
    source_root = Path(__file__).resolve().parents[3]
    project_root = root / "project"
    runtime_root = root / "runtime"
    state_relative = (
        Path(".codex")
        / "goals"
        / TERMINAL_SETTLEMENT_REENTRY_FIXTURE_GOAL_ID
        / "ACTIVE_GOAL_STATE.md"
    )
    state_path = project_root / state_relative
    registry_path = project_root / ".loopx" / "registry.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=10,
    )
    subprocess.run(
        [
            "git",
            "remote",
            "add",
            "origin",
            "https://github.com/example/terminal-reentry-fixture.git",
        ],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=10,
    )
    state_path.write_text(
        "---\n"
        "status: active\n"
        "owner_mode: goal\n"
        'objective: "Close validated terminal work without inventing a successor."\n'
        "updated_at: 2026-08-23T00:00:00+08:00\n"
        "---\n\n"
        "# Terminal Reentry Behavior Fixture\n\n"
        "## Objective\n\n"
        "Close validated terminal work without inventing a successor.\n\n"
        "## Next Action\n\n"
        "- Settle the two validated completed slices and stop.\n\n"
        "## User Todo / Owner Review Reading Queue\n\n"
        "## Agent Todo\n\n",
        encoding="utf-8",
    )
    goal = {
        "id": TERMINAL_SETTLEMENT_REENTRY_FIXTURE_GOAL_ID,
        "creation_operation_id": "goal-create:terminal-reentry-fixture",
        "domain": "terminal-reentry-live-fixture",
        "status": "active",
        "repo": str(project_root),
        "state_file": str(state_relative),
        "adapter": {
            "kind": "fixture_connected_delivery_v0",
            "status": "connected-delivery",
        },
        "coordination": {
            "registered_agents": [TERMINAL_SETTLEMENT_REENTRY_FIXTURE_AGENT_ID],
            "agent_model": "peer_v1",
            "storage_target": {
                "schema_version": "loopx_new_goal_storage_target_v1",
                "provider": "file",
                "handoff_mode": "soft_claim",
            },
        },
        "authority_sources": [],
        "quota": {
            "compute": 1.0,
            "window_hours": 24,
            "allowed_slots": 5,
        },
    }
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "updated_at": "2026-08-23T00:00:00+08:00",
                "common_runtime_root": str(runtime_root),
                "goals": [goal],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _initialize_fixture_goal_storage(
        runtime_root=runtime_root,
        goal=goal,
        registry_path=registry_path,
    )
    todos = [
        add_goal_todo(
            registry_path=registry_path,
            goal_id=TERMINAL_SETTLEMENT_REENTRY_FIXTURE_GOAL_ID,
            role="agent",
            text=f"Validate terminal slice {index}.",
            task_class="advancement_task",
            claimed_by=TERMINAL_SETTLEMENT_REENTRY_FIXTURE_AGENT_ID,
            operation_id=f"terminal-reentry-fixture-todo-{index}",
        )
        for index in (1, 2)
    ]
    expected_todo_ids = {str(todo["todo_id"]) for todo in todos}
    for todo_id in expected_todo_ids:
        result = complete_goal_todo(
            registry_path=registry_path,
            goal_id=TERMINAL_SETTLEMENT_REENTRY_FIXTURE_GOAL_ID,
            todo_id=todo_id,
            role="agent",
            evidence=f"validated terminal result for {todo_id}",
            agent_id=TERMINAL_SETTLEMENT_REENTRY_FIXTURE_AGENT_ID,
        )
        if result.get("ok") is not True:
            raise ValueError("unscoped completion did not commit")

    rejection_command = (
        "loopx --format json refresh-state "
        f"--goal-id {TERMINAL_SETTLEMENT_REENTRY_FIXTURE_GOAL_ID} "
        f"--agent-id {TERMINAL_SETTLEMENT_REENTRY_FIXTURE_AGENT_ID} "
        "--progress-scope agent_lane "
        "--classification terminal_delivery_closeout "
        "--delivery-batch-scale single_surface "
        "--delivery-outcome surface_only --no-global-sync"
    )
    rejection_output = execute_loopx_cli(
        rejection_command,
        source_root=source_root,
        project_root=project_root,
        accepted_return_codes=(1,),
    )
    rejection_packet = json.loads(rejection_output)
    if not isinstance(rejection_packet, dict):
        raise ValueError("refresh rejection must be a JSON object")
    projected_actions, quota_action = _reentry_projection(
        rejection_packet,
        expected_todo_ids=expected_todo_ids,
    )
    return _TerminalSettlementReentryFixture(
        project_root=project_root,
        source_root=source_root,
        rejection_command=rejection_command,
        rejection_packet=rejection_packet,
        projected_actions_by_todo=projected_actions,
        quota_reentry_action=quota_action,
    )


def _command_path(tokens: list[str]) -> tuple[str, str | None]:
    for index, token in enumerate(tokens):
        if token == "refresh-state":
            return "refresh_state", None
        if token == "quota" and index + 1 < len(tokens):
            return "quota", tokens[index + 1]
        if token == "todo" and index + 1 < len(tokens):
            return "todo", tokens[index + 1]
    return "unknown", None


def _redacted_command_shape(command: str) -> dict[str, Any]:
    """Describe a rejected action without retaining provider-selected text."""

    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = []
    loopx_index = next(
        (
            index
            for index, token in enumerate(tokens)
            if Path(token).name == "loopx"
        ),
        -1,
    )
    loopx_suffix = tokens[loopx_index:] if loopx_index >= 0 else []
    command_path, command_action = _command_path(loopx_suffix)
    operators = {";", "&&", "||", "|"}
    executable = Path(tokens[0]).name if tokens else ""
    operation_allowlist = {
        "cat",
        "find",
        "git",
        "grep",
        "head",
        "jq",
        "ls",
        "pwd",
        "python",
        "python3",
        "rg",
        "sed",
        "stat",
        "test",
    }
    python_module = (
        tokens[2]
        if executable in {"python", "python3"}
        and len(tokens) > 2
        and tokens[1] == "-m"
        else None
    )
    return {
        "parseable": bool(tokens),
        "token_count_bucket": min(len(tokens), 12),
        "executable_family": (
            "control_plane"
            if loopx_index == 0
            else "shell_wrapper"
            if tokens and Path(tokens[0]).name in {"bash", "sh", "zsh"}
            else "workspace"
        ),
        "workspace_operation": (
            executable if executable in operation_allowlist else "other"
        ),
        "git_operation": (
            tokens[1]
            if executable == "git" and len(tokens) > 1
            else None
        ),
        "python_module": python_module,
        "contains_loopx": loopx_index >= 0,
        "loopx_command_path": command_path if loopx_index >= 0 else None,
        "loopx_command_action": command_action if loopx_index >= 0 else None,
        "operator_before_loopx": any(
            token in operators for token in tokens[: max(loopx_index, 0)]
        ),
        "operator_after_loopx": any(token in operators for token in loopx_suffix),
        "has_environment_assignment": any(
            "=" in token and not token.startswith("-")
            for token in tokens[: max(loopx_index, 0)]
        ),
        "mentions_proof_target": TERMINAL_SETTLEMENT_FIXTURE_PROOF in command,
        "multiline": "\n" in command,
    }


def _matches_identity(tokens: list[str], state: _QualificationState) -> bool:
    return bool(
        argument_value(tokens, "--goal-id") == TERMINAL_SETTLEMENT_FIXTURE_GOAL_ID
        and argument_value(tokens, "--agent-id")
        == TERMINAL_SETTLEMENT_FIXTURE_AGENT_ID
        and argument_value(tokens, "--todo-id") == TERMINAL_SETTLEMENT_FIXTURE_TODO_ID
        and argument_value(tokens, "--turn-instance-id")
    )


def _quota_contract(packet: Mapping[str, Any]) -> dict[str, Any]:
    signature = quota_action_signature_document(packet)
    action = dict(signature.get("action") or {})
    user = dict(signature.get("user") or {})
    selected = dict(action.get("selected_todo") or {})
    settlement_plan = dict(
        dict(packet.get("interaction_contract") or {})
        .get("cli_channel", {})
        .get("settlement_plan", {})
    )
    ordered_steps = [
        str(item.get("kind"))
        for item in settlement_plan.get("ordered_steps", [])
        if isinstance(item, Mapping)
    ]
    contract = {
        "decision": "execute",
        "selected_todo_id": selected.get("todo_id"),
        "user_action_required": bool(user.get("action_required")),
        "must_attempt_work": bool(action.get("must_attempt")),
        "delivery_allowed": bool(action.get("delivery_allowed")),
        "quiet_noop_allowed": bool(action.get("quiet_noop_allowed")),
        "external_write_requested": False,
    }
    if (
        contract["selected_todo_id"] != TERMINAL_SETTLEMENT_FIXTURE_TODO_ID
        or contract["user_action_required"]
        or not contract["must_attempt_work"]
        or not contract["delivery_allowed"]
        or contract["quiet_noop_allowed"]
        or ordered_steps != _EXPECTED_RECEIPT_STEPS
    ):
        raise ValueError("real quota packet did not expose terminal settlement v1")
    return contract


def _settlement_receipt_steps(payload: Mapping[str, Any]) -> list[str]:
    settlement = payload.get("settlement_result")
    if not isinstance(settlement, Mapping) or settlement.get("ok") is not True:
        raise ValueError("settlement result is missing or failed")
    receipts = settlement.get("receipts")
    if not isinstance(receipts, list):
        raise ValueError("settlement receipts are missing")
    return [
        str(item.get("step_kind"))
        for item in receipts
        if isinstance(item, Mapping) and item.get("status") == "committed"
    ]


def _execute_cli(command: str, state: _QualificationState) -> dict[str, Any]:
    tokens = loopx_command_tokens(command)
    if tokens and "--format" not in tokens:
        command = shlex.join([tokens[0], "--format", "json", *tokens[1:]])
    output = execute_loopx_cli(
        command,
        source_root=state.fixture.source_root,
        project_root=state.fixture.project_root,
        argument_overrides={
            "--registry": str(state.fixture.global_registry_path),
            "--turn-instance-id": state.turn_instance_id,
        },
    )
    payload = json.loads(output)
    if not isinstance(payload, dict):
        raise ValueError("LoopX command output must be an object")
    return payload


def _execute_workspace_read(
    command: str,
    *,
    state: _QualificationState,
) -> str:
    plan = bounded_workspace_read_plan(
        command,
        fixture=cast(Any, state.fixture),
    )
    if not plan:
        raise ValueError("command is not one bounded workspace read")
    outputs: list[str] = []
    status = 0
    proof_read = False
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
            cwd=state.fixture.project_root,
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
        proof_read = proof_read or bool(
            status == 0
            and step.target is not None
            and step.target.resolve() == state.fixture.proof_target.resolve()
        )
    if status != 0:
        raise RuntimeError(f"workspace read failed with exit={status}")
    state.proof_read = state.proof_read or proof_read
    return "".join(outputs)


def _receipt(
    state: _QualificationState,
    *,
    qualification_id: str,
    actor_ref: str,
    passed: bool,
    failure_code: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": TERMINAL_SETTLEMENT_TOOL_BEHAVIOR_RECEIPT_SCHEMA_VERSION,
        "qualification_id": qualification_id,
        "actor_ref": actor_ref,
        "qualification_passed": passed,
        "failure_code": failure_code,
        **dict(state.contract or {}),
        "observed_tool_sequence": [step["kind"] for step in state.steps],
        "tool_call_count": len(state.steps),
        "settlement_receipt_steps": list(state.receipt_steps),
        "terminal_order_observed": bool(
            passed and state.receipt_steps == _EXPECTED_RECEIPT_STEPS
        ),
        "tool_call_receipts": list(state.steps),
        "boundary": {
            "raw_prompt_persisted": False,
            "raw_provider_response_persisted": False,
            "raw_command_persisted": False,
            "filesystem_writes_executed": True,
            "writes_limited_to_temporary_fixture": True,
            "external_writes_executed": False,
            "shell_commands_executed": False,
            "read_only_host_commands_executed": True,
        },
    }


def _execute_reentry_cli(
    command: str,
    *,
    state: _ReentryQualificationState,
    accepted_return_codes: tuple[int, ...] = (0,),
) -> dict[str, Any]:
    tokens = loopx_command_tokens(command)
    if tokens and "--format" not in tokens:
        command = shlex.join([tokens[0], "--format", "json", *tokens[1:]])
    output = execute_loopx_cli(
        command,
        source_root=state.fixture.source_root,
        project_root=state.fixture.project_root,
        accepted_return_codes=accepted_return_codes,
    )
    payload = json.loads(output)
    if not isinstance(payload, dict):
        raise ValueError("LoopX reentry command output must be an object")
    return payload


def _reentry_receipt(
    state: _ReentryQualificationState,
    *,
    qualification_id: str,
    actor_ref: str,
    passed: bool,
    failure_code: str | None,
) -> dict[str, Any]:
    expected_count = len(state.fixture.projected_actions_by_todo)
    return {
        "schema_version": (
            TERMINAL_SETTLEMENT_REENTRY_TOOL_BEHAVIOR_RECEIPT_SCHEMA_VERSION
        ),
        "qualification_id": qualification_id,
        "actor_ref": actor_ref,
        "scenario": "post_refresh_rejection_reentry",
        "qualification_passed": passed,
        "failure_code": failure_code,
        "provider_call_count": state.provider_call_count,
        "tool_call_count": len(state.steps),
        "projected_todo_count": expected_count,
        "settled_todo_count": len(state.settled_todo_ids),
        "completion_identity_preserved": bool(
            passed and len(state.settled_todo_ids) == expected_count
        ),
        "terminal_quota_observed": state.terminal_quota_observed,
        "model_stopped_after_terminal_quota": bool(
            passed and state.terminal_quota_observed
        ),
        "observed_tool_sequence": [step["kind"] for step in state.steps],
        "tool_call_receipts": list(state.steps),
        "boundary": {
            "raw_prompt_persisted": False,
            "raw_provider_response_persisted": False,
            "raw_command_persisted": False,
            "filesystem_writes_executed": True,
            "writes_limited_to_temporary_fixture": True,
            "external_writes_executed": False,
            "shell_commands_executed": False,
            "read_only_host_commands_executed": True,
        },
    }


class DoubaoTerminalSettlementToolBehaviorActor:
    """Prove the model follows the real post-spend terminal closeout plan."""

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
    ) -> DoubaoTerminalSettlementToolBehaviorActor:
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
    ) -> dict[str, Any]:
        fixture = _build_fixture(fixture_root)
        digest = sha256(qualification_id.encode("utf-8")).hexdigest()[:16]
        state = _QualificationState(
            fixture=fixture,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are Codex running one LoopX heartbeat. Follow the "
                        "production heartbeat task and choose each next action "
                        "from the latest tool result. "
                        f"{QUOTA_FIRST_TOOL_INSTRUCTION}"
                        "The exec tool's current workspace is the connected goal "
                        "project root, so task "
                        "paths are directly addressable. Read the exact proof target "
                        "once with `cat`; the host validates that tool result. Do not "
                        "run python, jq, test, echo, another validation command, or "
                        "re-read the workspace afterward. Use one command per tool "
                        "call. The tool call immediately after the proof read must "
                        "fill and execute quota-projected next_cli_actions[0] from "
                        "the proof's typed fields, then follow the ordered settlement "
                        "plan."
                    ),
                },
                {"role": "user", "content": fixture.task_body},
            ],
            turn_instance_id=f"qualification-{digest}",
        )
        actor_ref = self._client.actor_ref

        def receipt(*, passed: bool, failure_code: str | None) -> dict[str, Any]:
            return _receipt(
                state,
                qualification_id=qualification_id,
                actor_ref=actor_ref,
                passed=passed,
                failure_code=failure_code,
            )

        for _ in range(TERMINAL_SETTLEMENT_TOOL_BEHAVIOR_MAX_CALLS):
            step = self._client.next_step(state.messages)
            tool_call = step.tool_call
            if tool_call is None:
                return receipt(
                    passed=False,
                    failure_code=(
                        "model_returned_before_terminal_closeout"
                        if state.spend_committed
                        else "model_returned_without_settlement"
                    ),
                )
            tokens = loopx_command_tokens(tool_call.command)
            command_kind, command_action = (
                _command_path(tokens) if tokens else ("workspace_read", None)
            )
            kind = "unexpected_command"
            output = ""
            failure_code: str | None = None
            try:
                if command_kind == "quota" and command_action == "should-run":
                    if state.quota_packet is not None:
                        raise ValueError("repeated_quota_should_run")
                    payload = _execute_cli(tool_call.command, state)
                    state.quota_packet = payload
                    state.contract = _quota_contract(payload)
                    kind = "quota_should_run"
                    output = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                elif command_kind == "refresh_state":
                    if state.quota_packet is None:
                        raise ValueError("writeback_before_quota")
                    if not state.proof_read:
                        raise ValueError("writeback_before_validation")
                    if not tokens or not _matches_identity(tokens, state):
                        raise ValueError("writeback_identity_mismatch")
                    payload = _execute_cli(tool_call.command, state)
                    receipt_steps = _settlement_receipt_steps(payload)
                    if receipt_steps != _EXPECTED_RECEIPT_STEPS[:2]:
                        raise ValueError("writeback_receipt_mismatch")
                    state.writeback_committed = True
                    state.receipt_steps = receipt_steps
                    kind = "durable_writeback"
                    output = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                elif command_kind == "quota" and command_action == "spend-slot":
                    if not state.writeback_committed:
                        raise ValueError("quota_spend_before_writeback")
                    if not tokens or not _matches_identity(tokens, state):
                        raise ValueError("quota_spend_identity_mismatch")
                    if "--execute" not in tokens or argument_value(
                        tokens, "--source"
                    ) != "heartbeat":
                        raise ValueError("quota_spend_contract_mismatch")
                    payload = _execute_cli(tool_call.command, state)
                    receipt_steps = _settlement_receipt_steps(payload)
                    if receipt_steps != _EXPECTED_RECEIPT_STEPS[:3]:
                        raise ValueError("quota_spend_receipt_mismatch")
                    state.spend_committed = True
                    state.receipt_steps = receipt_steps
                    kind = "quota_spend"
                    output = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                elif command_kind == "todo" and command_action == "complete":
                    if not state.spend_committed:
                        raise ValueError("terminal_closeout_before_spend")
                    if (
                        not tokens
                        or not _matches_identity(tokens, state)
                        or "--no-follow-up" not in tokens
                    ):
                        raise ValueError("terminal_closeout_contract_mismatch")
                    payload = _execute_cli(tool_call.command, state)
                    receipt_steps = _settlement_receipt_steps(payload)
                    if receipt_steps != _EXPECTED_RECEIPT_STEPS:
                        raise ValueError("terminal_closeout_receipt_mismatch")
                    state.receipt_steps = receipt_steps
                    kind = "terminal_closeout"
                    output = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                else:
                    output = _execute_workspace_read(tool_call.command, state=state)
                    kind = "delivery_validation" if state.proof_read else "workspace_read"
            except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
                failure_code = str(exc)

            step_receipt = {
                "ordinal": len(state.steps) + 1,
                "kind": kind if failure_code is None else failure_code,
                "command_digest": digest_text(tool_call.command),
            }
            if failure_code is not None:
                step_receipt["redacted_command_shape"] = _redacted_command_shape(
                    tool_call.command
                )
            state.steps.append(step_receipt)
            if failure_code is not None:
                return receipt(passed=False, failure_code=failure_code)
            if kind == "terminal_closeout":
                return receipt(passed=True, failure_code=None)
            state.messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": step.assistant_content,
                        "tool_calls": [tool_call.provider_value],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.call_id,
                        "content": output,
                    },
                ]
            )

        return receipt(passed=False, failure_code="tool_call_budget_exhausted")

    def qualify_reentry(
        self,
        *,
        qualification_id: str,
        fixture_root: Path,
    ) -> dict[str, Any]:
        """Prove the model executes a real rejected-refresh recovery packet."""

        fixture = _build_reentry_fixture(fixture_root)
        seed_call_id = "seed-refresh-rejection"
        state = _ReentryQualificationState(
            fixture=fixture,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are Codex recovering one LoopX terminal turn. The "
                        "latest exec tool result is the authoritative typed "
                        "recovery projection. The user confirms no real successor "
                        "remains. Use one command per tool call. Execute every "
                        "projected Todo lifecycle settlement with a concise "
                        "public-safe note; do not repeat refresh-state, inspect the "
                        "workspace, spend quota, or invent filler work. Then run "
                        "the projected quota should-run command. When it reports "
                        "should_run=false, return a concise final response without "
                        "another tool call."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Both validated implementation slices are complete and the "
                        "bounded Goal is exhausted. No real successor remains. "
                        "Recover from the latest typed rejection and close out."
                    ),
                },
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": seed_call_id,
                            "type": "function",
                            "function": {
                                "name": "exec_command",
                                "arguments": json.dumps(
                                    {"cmd": fixture.rejection_command},
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ),
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": seed_call_id,
                    "content": json.dumps(
                        fixture.rejection_packet,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            ],
        )
        actor_ref = self._client.actor_ref

        def receipt(*, passed: bool, failure_code: str | None) -> dict[str, Any]:
            return _reentry_receipt(
                state,
                qualification_id=qualification_id,
                actor_ref=actor_ref,
                passed=passed,
                failure_code=failure_code,
            )

        for _ in range(TERMINAL_SETTLEMENT_REENTRY_TOOL_BEHAVIOR_MAX_CALLS):
            step = self._client.next_step(state.messages)
            state.provider_call_count += 1
            tool_call = step.tool_call
            if tool_call is None:
                if state.terminal_quota_observed:
                    return receipt(passed=True, failure_code=None)
                return receipt(
                    passed=False,
                    failure_code=(
                        "model_returned_before_terminal_quota"
                        if len(state.settled_todo_ids)
                        == len(fixture.projected_actions_by_todo)
                        else "model_returned_before_lifecycle_settlement"
                    ),
                )

            tokens = loopx_command_tokens(tool_call.command)
            command_kind, command_action = (
                _command_path(tokens) if tokens else ("unknown", None)
            )
            kind = "unexpected_command"
            output = ""
            failure_code: str | None = None
            try:
                if state.terminal_quota_observed:
                    raise ValueError("model_called_tool_after_terminal_quota")
                if command_kind == "refresh_state":
                    raise ValueError("repeated_refresh_state")
                if command_kind == "todo" and command_action == "complete":
                    if not tokens:
                        raise ValueError("lifecycle_settlement_command_malformed")
                    todo_id = argument_value(tokens, "--todo-id") or ""
                    expected_action = fixture.projected_actions_by_todo.get(todo_id)
                    expected_tokens = (
                        loopx_command_tokens(expected_action)
                        if expected_action is not None
                        else None
                    )
                    if todo_id in state.settled_todo_ids:
                        raise ValueError("repeated_lifecycle_settlement")
                    if (
                        expected_tokens is None
                        or argument_value(tokens, "--goal-id")
                        != TERMINAL_SETTLEMENT_REENTRY_FIXTURE_GOAL_ID
                        or argument_value(tokens, "--agent-id")
                        != TERMINAL_SETTLEMENT_REENTRY_FIXTURE_AGENT_ID
                        or argument_value(tokens, "--completion-identity-key")
                        != argument_value(
                            expected_tokens,
                            "--completion-identity-key",
                        )
                        or "--turn-instance-id" in tokens
                        or "--replan-obligation-id" in tokens
                        or "--no-follow-up" not in tokens
                        or "--note" in tokens
                    ):
                        raise ValueError("lifecycle_settlement_identity_mismatch")
                    payload = _execute_reentry_cli(tool_call.command, state=state)
                    if not (
                        payload.get("ok") is True
                        and payload.get("todo_id") == todo_id
                        and payload.get("completion_continuation") == "no_followup"
                        and payload.get("completion_recovery")
                        == "lifecycle_reentry_terminal_closeout"
                    ):
                        raise ValueError("lifecycle_settlement_execution_failed")
                    state.settled_todo_ids.append(todo_id)
                    kind = "todo_lifecycle_settlement"
                    output = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                elif command_kind == "quota" and command_action == "should-run":
                    if len(state.settled_todo_ids) != len(
                        fixture.projected_actions_by_todo
                    ):
                        raise ValueError("quota_reentry_before_lifecycle_settlement")
                    if not tokens or (
                        argument_value(tokens, "--goal-id")
                        != TERMINAL_SETTLEMENT_REENTRY_FIXTURE_GOAL_ID
                        or argument_value(tokens, "--agent-id")
                        != TERMINAL_SETTLEMENT_REENTRY_FIXTURE_AGENT_ID
                    ):
                        raise ValueError("quota_reentry_identity_mismatch")
                    payload = _execute_reentry_cli(
                        tool_call.command,
                        state=state,
                        accepted_return_codes=(0, 1),
                    )
                    if payload.get("should_run") is not False:
                        raise ValueError("quota_reentry_did_not_terminate")
                    state.terminal_quota_observed = True
                    kind = "terminal_quota_reentry"
                    output = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                elif command_kind == "todo" and command_action == "add":
                    raise ValueError("invented_lifecycle_successor")
                elif command_kind == "quota" and command_action == "spend-slot":
                    raise ValueError("unexpected_quota_spend")
                else:
                    raise ValueError("unexpected_command")
            except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
                failure_code = str(exc)

            step_receipt = {
                "ordinal": len(state.steps) + 1,
                "kind": kind if failure_code is None else failure_code,
                "command_digest": digest_text(tool_call.command),
            }
            if failure_code is not None:
                step_receipt["redacted_command_shape"] = _redacted_command_shape(
                    tool_call.command
                )
            state.steps.append(step_receipt)
            if failure_code is not None:
                return receipt(passed=False, failure_code=failure_code)
            state.messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": step.assistant_content,
                        "tool_calls": [tool_call.provider_value],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.call_id,
                        "content": output,
                    },
                ]
            )

        return receipt(passed=False, failure_code="tool_call_budget_exhausted")
