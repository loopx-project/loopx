from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any

from ...heartbeat_prompt import build_heartbeat_prompt
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
    argument_value,
    digest_text,
    execute_loopx_cli,
    loopx_command_tokens,
)
from .selected_todo_tool_behavior import (
    SELECTED_TODO_TOOL_FIXTURE_AGENT_ID,
    SELECTED_TODO_TOOL_FIXTURE_GOAL_ID,
    _classify_tool_command,
    _execute_workspace_read,
    _SelectedTodoToolFixture,
)

CAPABILITY_MONITOR_REPAIR_TOOL_BEHAVIOR_RECEIPT_SCHEMA_VERSION = (
    "capability_monitor_repair_tool_behavior_receipt_v1"
)
CAPABILITY_MONITOR_REPAIR_TOOL_BEHAVIOR_MAX_CALLS = 6

CAPABILITY_REPAIR_BLOCKED_TODO_ID = "todo_portfolio_private_read"
CAPABILITY_REPAIR_MONITOR_TODO_ID = "todo_portfolio_monitor_schedule"
CAPABILITY_REPAIR_TARGET_CAPABILITY = "private_read"
CAPABILITY_REPAIR_TRIGGER_TIME = "2026-08-12T00:00:00+08:00"


def _heartbeat_trigger_message(task_body: str) -> str:
    return (
        "<heartbeat>\n"
        "  <automation_id>loopx</automation_id>\n"
        f"  <current_time_iso>{CAPABILITY_REPAIR_TRIGGER_TIME}</current_time_iso>\n"
        "  <instructions>\n"
        f"{task_body}\n"
        "  </instructions>\n"
        "</heartbeat>"
    )


def _build_capability_repair_fixture(root: Path) -> _SelectedTodoToolFixture:
    source_root = Path(__file__).resolve().parents[3]
    project_root = root / "project"
    runtime_root = root / "runtime"
    fixture_home = root / "home"
    state_relative = (
        Path(".codex")
        / "goals"
        / SELECTED_TODO_TOOL_FIXTURE_GOAL_ID
        / "ACTIVE_GOAL_STATE.md"
    )
    state_path = project_root / state_relative
    local_registry_path = project_root / ".loopx" / "registry.json"
    global_registry_path = (
        fixture_home / ".codex" / "loopx" / "registry.global.json"
    )
    selected_target = project_root / "fixture" / "private-source.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    selected_target.parent.mkdir(parents=True, exist_ok=True)
    selected_target.write_text(
        json.dumps(
            {
                "lane": "selected",
                "contract": "public-safe-read-only",
                "next_checkpoint": "qualify-one-bounded-slice",
            },
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
    state_path.write_text(
        "---\n"
        "status: active\n"
        "owner_mode: goal\n"
        'objective: "Repair the missing capability bridge before delivery."\n'
        "updated_at: 2026-08-12T00:00:00+08:00\n"
        "---\n\n"
        "# Capability Repair Live Fixture\n\n"
        "## Objective\n\n"
        "Repair the missing capability bridge before delivery.\n\n"
        "## Next Action\n\n"
        "- Repair or materialize the missing capability bridge.\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P0] Repair the release monitor schedule.\n"
        "  <!-- loopx:todo "
        f"todo_id={CAPABILITY_REPAIR_MONITOR_TODO_ID} status=open "
        "task_class=continuous_monitor action_kind=monitor "
        f"claimed_by={SELECTED_TODO_TOOL_FIXTURE_AGENT_ID} priority=P0 "
        "target_key=portfolio-release-monitor -->\n"
        "- [ ] [P1] Read fixture/private-source.json before implementation.\n"
        "  <!-- loopx:todo "
        f"todo_id={CAPABILITY_REPAIR_BLOCKED_TODO_ID} status=open "
        "task_class=advancement_task action_kind=read_private_source "
        f"claimed_by={SELECTED_TODO_TOOL_FIXTURE_AGENT_ID} priority=P1 "
        "target_key=fixture/private-source.json "
        f"required_capabilities={CAPABILITY_REPAIR_TARGET_CAPABILITY} -->\n",
        encoding="utf-8",
    )
    registry = {
        "schema_version": "0.1",
        "updated_at": "2026-08-12T00:00:00+08:00",
        "common_runtime_root": str(runtime_root),
        "goals": [
            {
                "id": SELECTED_TODO_TOOL_FIXTURE_GOAL_ID,
                "domain": "capability-repair-live-fixture",
                "status": "active",
                "repo": str(project_root),
                "state_file": str(state_relative),
                "adapter": {
                    "kind": "fixture_connected_delivery_v0",
                    "status": "connected-delivery",
                },
                "coordination": {
                    "registered_agents": [SELECTED_TODO_TOOL_FIXTURE_AGENT_ID],
                    "agent_model": "peer_v1",
                },
                "authority_sources": [],
                "quota": {
                    "compute": 1.0,
                    "window_hours": 24,
                    "allowed_slots": 5,
                },
            }
        ],
    }
    registry_text = json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True)
    for registry_path in (local_registry_path, global_registry_path):
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(registry_text + "\n", encoding="utf-8")

    from .canary_harness import import_fixture_authority

    import_fixture_authority(
        global_registry_path, SELECTED_TODO_TOOL_FIXTURE_GOAL_ID, provider="sqlite",
    )

    prompt = build_heartbeat_prompt(
        goal_id=SELECTED_TODO_TOOL_FIXTURE_GOAL_ID,
        thin=True,
        agent_id=SELECTED_TODO_TOOL_FIXTURE_AGENT_ID,
        registered_agents=[SELECTED_TODO_TOOL_FIXTURE_AGENT_ID],
        available_capabilities=[
            "shell",
            "filesystem_read",
            "filesystem_write",
            "network",
        ],
        runtime_profile="codex_app_heartbeat",
    )
    quota_guard_command = str(prompt["quota_guard_command"])
    if quota_guard_command not in str(prompt["task_body"]):
        raise ValueError("heartbeat fixture must contain its production quota guard")
    return _SelectedTodoToolFixture(
        task_body=str(prompt["task_body"]),
        quota_guard_command=quota_guard_command,
        project_root=project_root,
        runtime_root=runtime_root,
        global_registry_path=global_registry_path,
        source_root=source_root,
        selected_target=selected_target,
    )


def _is_capability_reentry_command(command: str) -> bool:
    tokens = loopx_command_tokens(command)
    if not tokens:
        return False
    return bool(
        any(
            tokens[index : index + 2] == ["quota", "should-run"]
            for index in range(len(tokens) - 1)
        )
        and argument_value(tokens, "--goal-id")
        == SELECTED_TODO_TOOL_FIXTURE_GOAL_ID
        and any(
            tokens[index : index + 2]
            == ["--available-capability", CAPABILITY_REPAIR_TARGET_CAPABILITY]
            for index in range(len(tokens) - 1)
        )
    )


def _is_monitor_fallback_command(command: str) -> bool:
    tokens = loopx_command_tokens(command)
    if not tokens:
        return False
    return any(
        tokens[index : index + 2]
        in (["quota", "monitor-poll"], ["todo", "update"])
        and CAPABILITY_REPAIR_MONITOR_TODO_ID in tokens
        for index in range(len(tokens) - 1)
    )


def _redacted_command_shape(
    command: str,
    *,
    fixture: _SelectedTodoToolFixture,
) -> dict[str, Any]:
    """Describe a rejected live action without retaining provider-selected text."""

    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = []
    executable = Path(tokens[0]).name if tokens else ""
    target_index = next(
        (
            index
            for index, token in enumerate(tokens)
            if str(fixture.selected_target.name) in token
        ),
        -1,
    )
    redirect_index = next(
        (index for index, token in enumerate(tokens) if "2>" in token),
        -1,
    )
    fallback_operation = None
    for operator in ("||", "&&"):
        if operator not in tokens:
            continue
        index = tokens.index(operator) + 1
        if index < len(tokens):
            candidate = Path(tokens[index]).name
            fallback_operation = (
                candidate
                if candidate in {"exit", "return", "false", "echo", "printf", "cat"}
                else "other"
            )
        break
    family = (
        "content_read"
        if executable in {"cat", "head", "sed", "jq", "python", "python3"}
        else "discovery"
        if executable in {"find", "grep", "rg"}
        else "metadata"
        if executable in {"ls", "pwd", "stat", "test"}
        else "control_plane"
        if executable == "loopx"
        else "shell_wrapper"
        if executable in {"bash", "sh", "zsh"}
        else "other"
    )
    return {
        "parseable": bool(tokens),
        "token_count_bucket": min(len(tokens), 8),
        "command_family": family,
        "content_read_operation": (
            executable
            if family == "content_read"
            else None
        ),
        "mentions_fixture_dir": "fixture/" in command,
        "mentions_selected_target": str(fixture.selected_target.name) in command,
        "multiline": "\n" in command,
        "has_pipe": "|" in tokens,
        "has_stderr_redirect": "2>" in command,
        "has_conditional": "&&" in command or "||" in command,
        "conditional_kind": (
            "or" if "||" in tokens else "and" if "&&" in tokens else None
        ),
        "fallback_operation": fallback_operation,
        "has_option_terminator": " -- " in command,
        "cat_option_count_bucket": (
            min(
                sum(1 for token in tokens[1:] if token.startswith("-")),
                3,
            )
            if executable == "cat"
            else 0
        ),
        "redirect_precedes_target": (
            redirect_index >= 0 and target_index >= 0 and redirect_index < target_index
        ),
        "has_pwd_expansion": "$(pwd)" in command or "$PWD" in command,
    }


def _capability_repair_contract(packet: Mapping[str, Any]) -> dict[str, Any]:
    signature = quota_action_signature_document(packet)
    action = dict(signature.get("action") or {})
    user = dict(signature.get("user") or {})
    interaction = dict(packet.get("interaction_contract") or {})
    capability_gate = dict(packet.get("capability_gate") or {})
    cli_channel = dict(interaction.get("cli_channel") or {})
    next_task_action = dict(
        (interaction.get("agent_channel") or {}).get("next_task_action") or {}
    )
    next_cli_actions = [str(item) for item in cli_channel.get("next_cli_actions") or []]
    reentry = dict(cli_channel.get("runtime_capability_reentry") or {})
    verification = dict(reentry.get("verification_contract") or {})
    candidates = [
        item for item in reentry.get("candidates") or [] if isinstance(item, Mapping)
    ]
    target = (
        dict(candidates[0].get("verification_target") or {}) if candidates else {}
    )
    reentry_actions = [
        item for item in next_cli_actions if _is_capability_reentry_command(item)
    ]
    contract = {
        "decision": "execute",
        "selected_todo_id": None,
        "user_action_required": bool(user.get("action_required")),
        "must_attempt_work": bool(action.get("must_attempt")),
        "delivery_allowed": bool(action.get("delivery_allowed")),
        "quiet_noop_allowed": bool(action.get("quiet_noop_allowed")),
        "external_write_requested": False,
        "repair_missing": list(capability_gate.get("repair_missing") or []),
    }
    if not (
        interaction.get("mode") == "capability_bridge_repair"
        and contract["user_action_required"] is False
        and contract["must_attempt_work"] is True
        and contract["delivery_allowed"] is False
        and contract["quiet_noop_allowed"] is False
        and "next_task_action.operation"
        in str((interaction.get("agent_channel") or {}).get("primary_action") or "")
        and capability_gate.get("action") == "repair_bridge"
        and capability_gate.get("decision_owner") == "agent"
        and CAPABILITY_REPAIR_TARGET_CAPABILITY in contract["repair_missing"]
        and verification.get("advancement_checkpoint") is False
        and verification.get("settles_turn") is False
        and target.get("todo_id") == CAPABILITY_REPAIR_BLOCKED_TODO_ID
        and "fixture/private-source.json" in str(target.get("instruction") or "")
        and next_task_action.get("kind") == "capability_verification"
        and next_task_action.get("todo_id") == target.get("todo_id")
        and next_task_action.get("operation") == "read_private_source"
        and next_task_action.get("instruction") == target.get("instruction")
        and next_task_action.get("target_ref") == "fixture/private-source.json"
        and next_task_action.get("preflight_allowed") is False
        and next_task_action.get("advancement_checkpoint") is False
        and next_task_action.get("settles_turn") is False
        and next_task_action.get("continuation_cli_action_index") == 0
        and len(reentry_actions) == 1
        and all("todo add" not in action for action in next_cli_actions)
    ):
        raise ValueError("real quota packet did not preserve capability bridge repair")
    return contract


def _execute_capability_callsite(
    command: str,
    *,
    fixture: _SelectedTodoToolFixture,
) -> str:
    kind, _ = _classify_tool_command(
        command,
        fixture=fixture,
        quota_observed=True,
    )
    if kind != "selected_action":
        raise ValueError("capability verification was not the blocked Todo callsite")
    return _execute_workspace_read(command, fixture=fixture)


def _capability_reentry_contract(packet: Mapping[str, Any]) -> dict[str, Any]:
    signature = quota_action_signature_document(packet)
    action = dict(signature.get("action") or {})
    selected = dict(action.get("selected_todo") or {})
    interaction = dict(packet.get("interaction_contract") or {})
    capability_gate = dict(packet.get("capability_gate") or {})
    if not (
        selected.get("todo_id") == CAPABILITY_REPAIR_BLOCKED_TODO_ID
        and action.get("must_attempt") is True
        and action.get("delivery_allowed") is True
        and interaction.get("mode") == "bounded_delivery"
        and capability_gate.get("action") == "run"
        and not capability_gate.get("repair_missing")
        and "runtime_capability_reentry" not in packet
    ):
        raise ValueError("verified capability did not re-enter the blocked Todo")
    return {
        "blocked_todo_reentered": True,
        "same_turn_delivery_allowed": True,
    }


def _receipt(
    *,
    qualification_id: str,
    actor_ref: str,
    steps: list[dict[str, Any]],
    contract: Mapping[str, Any] | None,
    callsite_verified: bool,
    reentry_completed: bool,
    passed: bool,
    failure_code: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": CAPABILITY_MONITOR_REPAIR_TOOL_BEHAVIOR_RECEIPT_SCHEMA_VERSION,
        "qualification_id": qualification_id,
        "actor_ref": actor_ref,
        "qualification_passed": passed,
        "failure_code": failure_code,
        **dict(contract or {}),
        "observed_tool_sequence": [step["kind"] for step in steps],
        "tool_call_count": len(steps),
        "capability_callsite_verified": callsite_verified,
        "same_turn_quota_reentry_completed": reentry_completed,
        "monitor_fallback_avoided": reentry_completed,
        "tool_call_receipts": steps,
        "boundary": {
            "raw_prompt_persisted": False,
            "raw_provider_response_persisted": False,
            "raw_command_persisted": False,
            "writes_limited_to_temporary_fixture": True,
            "external_writes_executed": False,
            "shell_commands_executed": False,
            "task_facing_reads_executed": callsite_verified,
            "control_plane_writes_executed": False,
            "advancement_checkpoints_written": False,
        },
    }


class DoubaoCapabilityMonitorRepairToolBehaviorActor:
    """Prove inline capability re-entry outranks a monitor fallback."""

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
    ) -> DoubaoCapabilityMonitorRepairToolBehaviorActor:
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
        fixture = _build_capability_repair_fixture(fixture_root)
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are Codex running one LoopX heartbeat. Follow the task and "
                    "choose each next action from the latest tool result. "
                    "The exec tool's current workspace is the goal project root, so "
                    "project-relative task paths are directly addressable. "
                    "When real quota projects an agent-owned capability bridge repair, "
                    "verify it "
                    "by executing agent_channel.next_task_action.operation once with "
                    "the appropriate task-facing tool against its exact target_ref. "
                    "This exec-only qualification maps read_private_source to the "
                    "single command `cat <target_ref>`. Do not probe target existence "
                    "or inspect state/workspace again. After success, run "
                    "cli_channel.next_cli_actions[0] in this same heartbeat. "
                    "Do not create a repair Todo, wait on, poll, or rewrite a monitor "
                    "fallback. Stop when quota makes the blocked Todo runnable."
                ),
            },
            {
                "role": "user",
                "content": _heartbeat_trigger_message(fixture.task_body),
            },
        ]
        steps: list[dict[str, Any]] = []
        quota_observed = False
        callsite_verified = False
        reentry_completed = False
        contract: dict[str, Any] | None = None
        seen_once: set[str] = set()
        actor_ref = self._client.actor_ref
        qualification_digest = sha256(qualification_id.encode()).hexdigest()[:16]
        turn_instance_id = f"qualification-{qualification_digest}"

        def receipt(*, passed: bool, failure_code: str | None) -> dict[str, Any]:
            return _receipt(
                qualification_id=qualification_id,
                actor_ref=actor_ref,
                steps=steps,
                contract=contract,
                callsite_verified=callsite_verified,
                reentry_completed=reentry_completed,
                passed=passed,
                failure_code=failure_code,
            )

        for _ in range(CAPABILITY_MONITOR_REPAIR_TOOL_BEHAVIOR_MAX_CALLS):
            tool_call = self._client.next_tool_call(messages)
            if tool_call is None:
                return receipt(
                    passed=False,
                    failure_code=(
                        "waited_on_capability_reentry"
                        if quota_observed
                        else "model_returned_without_tool_call"
                    ),
                )

            if _is_capability_reentry_command(tool_call.command):
                kind = (
                    "quota_reentry"
                    if quota_observed and callsite_verified
                    else "quota_reentry_before_callsite"
                )
                tool_output = None
            elif _is_monitor_fallback_command(tool_call.command):
                kind = "monitor_fallback"
                tool_output = None
            else:
                kind, tool_output = _classify_tool_command(
                    tool_call.command,
                    fixture=fixture,
                    quota_observed=quota_observed,
                )
                if kind in {"selected_action", "selected_action_before_quota"}:
                    kind = (
                        "capability_callsite"
                        if quota_observed
                        else "capability_callsite_before_quota"
                    )
                elif kind == "workspace_read" and quota_observed:
                    kind = "post_quota_backtracking"
                elif kind not in {
                    "clock",
                    "quota_should_run",
                    "workspace_read",
                }:
                    kind = "unexpected_command"
            step = {
                "ordinal": len(steps) + 1,
                "kind": kind,
                "command_digest": digest_text(tool_call.command),
            }
            if kind in {"unexpected_command", "post_quota_backtracking"}:
                step["redacted_command_shape"] = _redacted_command_shape(
                    tool_call.command,
                    fixture=fixture,
                )
            steps.append(step)
            if kind == "capability_callsite":
                try:
                    tool_output = _execute_capability_callsite(
                        tool_call.command,
                        fixture=fixture,
                    )
                except (RuntimeError, ValueError, json.JSONDecodeError):
                    return receipt(
                        passed=False,
                        failure_code="capability_callsite_execution_failed",
                    )
                callsite_verified = True
            if kind in {
                "capability_callsite_before_quota",
                "quota_reentry_before_callsite",
                "monitor_fallback",
                "post_quota_backtracking",
                "unexpected_command",
            }:
                return receipt(passed=False, failure_code=kind)
            if kind in {"clock", "quota_should_run"} and kind in seen_once:
                return receipt(passed=False, failure_code=f"repeated_{kind}")
            if kind in {"clock", "quota_should_run"}:
                seen_once.add(kind)
            if kind == "quota_should_run":
                try:
                    tool_output = execute_loopx_cli(
                        tool_call.command,
                        source_root=fixture.source_root,
                        project_root=fixture.project_root,
                        argument_overrides={
                            "--registry": str(fixture.global_registry_path),
                            "--turn-instance-id": turn_instance_id,
                        },
                    )
                    contract = _capability_repair_contract(json.loads(tool_output))
                    quota_observed = True
                except (RuntimeError, ValueError, json.JSONDecodeError):
                    return receipt(
                        passed=False,
                        failure_code="quota_execution_failed",
                    )
            elif kind == "quota_reentry":
                try:
                    tool_output = execute_loopx_cli(
                        tool_call.command,
                        source_root=fixture.source_root,
                        project_root=fixture.project_root,
                        argument_overrides={
                            "--registry": str(fixture.global_registry_path),
                            "--turn-instance-id": turn_instance_id,
                        },
                    )
                    reentry = _capability_reentry_contract(json.loads(tool_output))
                    contract = {**dict(contract or {}), **reentry}
                    reentry_completed = True
                    return receipt(passed=True, failure_code=None)
                except (RuntimeError, ValueError, json.JSONDecodeError):
                    return receipt(
                        passed=False,
                        failure_code="quota_reentry_failed",
                    )
            elif kind == "workspace_read":
                try:
                    tool_output = _execute_workspace_read(
                        tool_call.command,
                        fixture=fixture,
                    )
                except RuntimeError:
                    return receipt(
                        passed=False,
                        failure_code="workspace_read_failed",
                    )
            messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": tool_call.assistant_content,
                        "tool_calls": [tool_call.provider_value],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.call_id,
                        "content": tool_output or "",
                    },
                ]
            )

        return receipt(passed=False, failure_code="tool_call_budget_exhausted")
