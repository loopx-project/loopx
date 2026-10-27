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
    QUOTA_FIRST_TOOL_INSTRUCTION,
    digest_text,
    execute_loopx_cli,
)
from .selected_todo_tool_behavior import (
    SELECTED_TODO_TOOL_FIXTURE_AGENT_ID,
    SELECTED_TODO_TOOL_FIXTURE_GOAL_ID,
    _classify_tool_command,
    _execute_selected_read,
    _execute_workspace_read,
    _SelectedTodoToolFixture,
)

SCOPED_GATE_SUCCESSOR_TOOL_BEHAVIOR_RECEIPT_SCHEMA_VERSION = (
    "scoped_gate_successor_tool_behavior_receipt_v0"
)
SCOPED_GATE_SUCCESSOR_TOOL_BEHAVIOR_MAX_CALLS = 6
SCOPED_GATE_NOTICE_MAX_BYTES = 16_384

SCOPED_GATE_SUCCESSOR_TODO_ID = "todo_portfolio_deferred"
SCOPED_GATE_PREREQUISITE_TODO_ID = "todo_portfolio_prerequisite"
SCOPED_GATE_USER_TODO_ID = "todo_portfolio_first_screen"
SCOPED_GATE_SUCCESSOR_TARGET = "fixture/scoped-successor.json"
SCOPED_GATE_SUCCESSOR_ACTION_TEXT = (
    f"Inspect `{SCOPED_GATE_SUCCESSOR_TARGET}` and verify the ready successor."
)
SCOPED_GATE_USER_ACTION_TEXT = "Review the unrelated product first screen."
SCOPED_GATE_FINALIZATION_MESSAGE = (
    "The host has closed the bounded tool phase after the selected Todo action "
    "completed. Return the heartbeat final response now using the original "
    "interaction contract. Read the latest quota tool result: include its required "
    "user action in the final response and make clear that this notice did not "
    "block the selected successor action that already executed. No tools are "
    "available. Report only effects observed in the tool results; do not claim "
    "writeback, spend, or state changes that were not executed."
)
SCOPED_GATE_READ_RETRY_MESSAGE = (
    "The hermetic host rejected this read shape without executing it. Retry with "
    "one bounded read-only command inside the current workspace using pwd, ls, "
    "find, rg --files, cat, head, or sed."
)


def _build_scoped_gate_fixture(root: Path) -> _SelectedTodoToolFixture:
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
    selected_target = project_root / SCOPED_GATE_SUCCESSOR_TARGET
    state_path.parent.mkdir(parents=True, exist_ok=True)
    selected_target.parent.mkdir(parents=True, exist_ok=True)
    selected_target.write_text(
        json.dumps(
            {
                "contract": "public-safe-read-only",
                "lane": "selected",
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
        'objective: "Advance the ready public-safe successor."\n'
        "updated_at: 2026-08-12T00:00:00+08:00\n"
        "---\n\n"
        "# Scoped Gate Successor Live Fixture\n\n"
        "## Objective\n\n"
        "Advance the ready public-safe successor.\n\n"
        "## Next Action\n\n"
        f"- {SCOPED_GATE_SUCCESSOR_ACTION_TEXT}\n\n"
        "## User Todo\n\n"
        f"- [ ] [P2] {SCOPED_GATE_USER_ACTION_TEXT}\n"
        "  <!-- loopx:todo "
        f"todo_id={SCOPED_GATE_USER_TODO_ID} status=open "
        "task_class=user_gate action_kind=approve_product_first_screen "
        "decision_scope=write_scope:action:first-screen "
        f"blocks_agent={SELECTED_TODO_TOOL_FIXTURE_AGENT_ID} priority=P2 -->\n\n"
        "## Agent Todo\n\n"
        f"- [-] [P1] {SCOPED_GATE_SUCCESSOR_ACTION_TEXT}\n"
        "  <!-- loopx:todo "
        f"todo_id={SCOPED_GATE_SUCCESSOR_TODO_ID} status=deferred "
        "task_class=advancement_task action_kind=quality_hardening "
        f"claimed_by={SELECTED_TODO_TOOL_FIXTURE_AGENT_ID} priority=P1 "
        f"resume_when=todo_done:{SCOPED_GATE_PREREQUISITE_TODO_ID} -->\n\n"
        "## Completed Work Archive\n\n"
        "- [x] [P0] Complete the quality prerequisite.\n"
        "  <!-- loopx:todo "
        f"todo_id={SCOPED_GATE_PREREQUISITE_TODO_ID} role=agent status=done "
        "task_class=advancement_task priority=P0 -->\n",
        encoding="utf-8",
    )
    registry = {
        "schema_version": "0.1",
        "updated_at": "2026-08-12T00:00:00+08:00",
        "common_runtime_root": str(runtime_root),
        "goals": [
            {
                "id": SELECTED_TODO_TOOL_FIXTURE_GOAL_ID,
                "domain": "scoped-gate-successor-live-fixture",
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
        available_capabilities=["shell", "filesystem_read"],
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


def _scoped_gate_quota_contract(packet: Mapping[str, Any]) -> dict[str, Any]:
    signature = quota_action_signature_document(packet)
    action = dict(signature.get("action") or {})
    user = dict(signature.get("user") or {})
    selected = dict(action.get("selected_todo") or {})
    interaction = dict(packet.get("interaction_contract") or {})
    user_channel = dict(interaction.get("user_channel") or {})
    actions = [
        str(item).strip()
        for item in user_channel.get("actions") or []
        if str(item).strip()
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
    if not (
        contract["selected_todo_id"] == SCOPED_GATE_SUCCESSOR_TODO_ID
        and contract["user_action_required"]
        and contract["must_attempt_work"]
        and contract["delivery_allowed"]
        and not contract["quiet_noop_allowed"]
        and signature.get("response_plan") is None
        and interaction.get("mode") == "scoped_user_gate_fallback"
        and len(actions) == 1
        and actions[0].endswith(SCOPED_GATE_USER_ACTION_TEXT)
    ):
        raise ValueError(
            "real quota packet did not preserve the non-blocking gate successor"
        )
    return contract


def _notice_intent(content: str | None) -> bool:
    if (
        content is None
        or len(content.encode("utf-8")) > SCOPED_GATE_NOTICE_MAX_BYTES
    ):
        return False
    normalized = " ".join(content.lower().split())
    return "first screen" in normalized or "首屏" in content


def _read_only_command_intent(command: str) -> bool:
    """Recognize a safe read attempt even when the hermetic parser rejects it."""

    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return False
    command_names: list[str] = []
    expect_command = True
    for token in tokens:
        if token in {"&&", "||", ";", "|"}:
            expect_command = True
            continue
        if token in {"&", "<", "<<", ">>", "<<<"}:
            return False
        if token == ">":
            continue
        if expect_command and token.isdigit():
            continue
        if expect_command:
            command_names.append(Path(token).name)
            expect_command = False
    allowed = {"pwd", "ls", "find", "rg", "cat", "head", "sed", "echo", "grep", "sort", "wc"}
    return bool(command_names) and all(name in allowed for name in command_names)


def _receipt(
    *,
    qualification_id: str,
    actor_ref: str,
    steps: list[dict[str, Any]],
    contract: Mapping[str, Any] | None,
    notice_surfaced: bool,
    selected_action_executed: bool,
    passed: bool,
    failure_code: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": SCOPED_GATE_SUCCESSOR_TOOL_BEHAVIOR_RECEIPT_SCHEMA_VERSION,
        "qualification_id": qualification_id,
        "actor_ref": actor_ref,
        "qualification_passed": passed,
        "failure_code": failure_code,
        **dict(contract or {}),
        "observed_tool_sequence": [step["kind"] for step in steps],
        "tool_call_count": len(steps),
        "non_blocking_notice_surfaced": notice_surfaced,
        "selected_action_matched_todo": selected_action_executed,
        "tool_call_receipts": steps,
        "boundary": {
            "raw_prompt_persisted": False,
            "raw_provider_response_persisted": False,
            "raw_assistant_content_persisted": False,
            "raw_command_persisted": False,
            "filesystem_writes_executed": True,
            "writes_limited_to_temporary_fixture": True,
            "external_writes_executed": False,
            "shell_commands_executed": False,
            "read_only_host_commands_executed": bool(steps),
        },
    }


class DoubaoScopedGateSuccessorToolBehaviorActor:
    """Prove a scoped user notice does not suppress its ready successor."""

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
    ) -> DoubaoScopedGateSuccessorToolBehaviorActor:
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
        fixture = _build_scoped_gate_fixture(fixture_root)
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are Codex running one LoopX heartbeat. Follow the "
                    "heartbeat task and use the available shell tool when needed. "
                    f"{QUOTA_FIRST_TOOL_INSTRUCTION}"
                    "Choose each next action from the latest tool result. If quota "
                    "requires a user action but still selects runnable work, surface "
                    "the user notice as non-blocking in the final assistant response "
                    "after executing the selected Todo tool action; do not wait on "
                    "that notice. The quota user-action text is sufficient for the "
                    "notice and grants no authority to inspect or act on the user-gate "
                    "target. When selected_todo.text names one relative file, the "
                    "next tool call must be `cat <that exact path>`; do not run pwd, "
                    "ls, find, rg, or inspect state first. After the selected Todo "
                    "tool succeeds, call no more tools and return the final response."
                ),
            },
            {"role": "user", "content": fixture.task_body},
        ]
        steps: list[dict[str, Any]] = []
        quota_observed = False
        notice_surfaced = False
        selected_action_executed = False
        contract: dict[str, Any] | None = None
        seen_once: set[str] = set()
        rejected_read_count = 0
        actor_ref = self._client.actor_ref
        qualification_digest = sha256(qualification_id.encode()).hexdigest()[:16]
        turn_instance_id = f"qualification-{qualification_digest}"

        def receipt(*, passed: bool, failure_code: str | None) -> dict[str, Any]:
            return _receipt(
                qualification_id=qualification_id,
                actor_ref=actor_ref,
                steps=steps,
                contract=contract,
                notice_surfaced=notice_surfaced,
                selected_action_executed=selected_action_executed,
                passed=passed,
                failure_code=failure_code,
            )

        for _ in range(SCOPED_GATE_SUCCESSOR_TOOL_BEHAVIOR_MAX_CALLS):
            if selected_action_executed:
                messages.append(
                    {
                        "role": "system",
                        "content": SCOPED_GATE_FINALIZATION_MESSAGE,
                    }
                )
                final_content = self._client.next_final_content(messages)
                notice_surfaced = _notice_intent(final_content)
                return receipt(
                    passed=notice_surfaced,
                    failure_code=(
                        None
                        if notice_surfaced
                        else "non_blocking_notice_not_surfaced"
                    ),
                )
            assistant_step = self._client.next_step(messages)
            tool_call = assistant_step.tool_call
            if tool_call is None:
                if selected_action_executed:
                    notice_surfaced = _notice_intent(
                        assistant_step.assistant_content
                    )
                    return receipt(
                        passed=notice_surfaced,
                        failure_code=(
                            None
                            if notice_surfaced
                            else "non_blocking_notice_not_surfaced"
                        ),
                    )
                return receipt(
                    passed=False,
                    failure_code=(
                        "waited_on_non_blocking_notice"
                        if quota_observed
                        else "model_returned_without_tool_call"
                    ),
                )

            if quota_observed and _notice_intent(tool_call.assistant_content):
                notice_surfaced = True
            kind, tool_output = _classify_tool_command(
                tool_call.command,
                fixture=fixture,
                quota_observed=quota_observed,
            )
            if (
                kind == "unexpected_command"
                and rejected_read_count == 0
                and _read_only_command_intent(tool_call.command)
            ):
                kind = "workspace_read_rejected"
                tool_output = SCOPED_GATE_READ_RETRY_MESSAGE
                rejected_read_count += 1
            steps.append(
                {
                    "ordinal": len(steps) + 1,
                    "kind": kind,
                    "command_digest": digest_text(tool_call.command),
                    "assistant_content_present": bool(tool_call.assistant_content),
                    "notice_intent_observed": bool(
                        quota_observed
                        and _notice_intent(tool_call.assistant_content)
                    ),
                }
            )
            if kind == "selected_action":
                try:
                    tool_output = _execute_selected_read(
                        tool_call.command,
                        fixture=fixture,
                    )
                except (RuntimeError, ValueError, json.JSONDecodeError):
                    return receipt(
                        passed=False,
                        failure_code="selected_action_execution_failed",
                    )
                selected_action_executed = True
            if kind in {
                "selected_action_before_quota",
                "wrong_selected_todo_target",
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
                    quota_packet = json.loads(tool_output)
                    contract = _scoped_gate_quota_contract(quota_packet)
                    quota_observed = True
                except (RuntimeError, ValueError, json.JSONDecodeError):
                    return receipt(
                        passed=False,
                        failure_code="quota_execution_failed",
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
