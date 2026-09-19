#!/usr/bin/env python3
"""Smoke-test the reusable heartbeat automation prompt contract."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


from heartbeat_prompt_fixtures import (  # noqa: E402
    ACTIVE_STATE,
    DOC,
    GETTING_STARTED,
    GOAL_ID,
    INTEGRATION_DOC,
    PROJECT_SKILL,
    README,
    REPO_ROOT,
    assert_interface_budget_payload,
    assert_no_project_specific_prompt_leaks,
    assert_ordered,
    assert_prompt_budget,
    normalized,
)
from loopx.heartbeat_prompt import (  # noqa: E402
    HEARTBEAT_AGENT_INPUT_SCHEMA_VERSION,
    build_heartbeat_prompt,
)


def user_output_policy(task_body: str, *, mode: str) -> dict[str, str]:
    body = normalized(task_body)
    assert "sync state if needed, and `NOTIFY`." not in body, (
        f"{mode}: bare unconditional NOTIFY after read-only map"
    )
    assert "`NOTIFY` only for an artifact, gate, blocker, or self-stop;" not in body, (
        f"{mode}: artifact-gated return overrides user_channel.notify authority"
    )
    assert "`NOTIFY` only for artifact/gate/blocker/self-stop." not in body, (
        f"{mode}: brief artifact-gated return overrides user_channel.notify authority"
    )
    if mode == "compact":
        policy_start = body.index("Output policy:") + len("Output policy:")
        policy_end = body.index("If `should_run=false`:", policy_start)
        policy = {}
        for item in body[policy_start:policy_end].strip(" .").split(";"):
            key, value = item.strip().split("=", maxsplit=1)
            policy[key] = value.strip("`")
        return policy
    if mode == "full":
        assert "`interaction_contract.user_channel.notify` controls output" in body
        assert "Only if `user_channel.notify=NOTIFY`" in body
        assert "repair the projection internally and stay quiet" in body
        assert "notify only under `NOTIFY`" in body
        assert "Return compactly under `interaction_contract.user_channel.notify`" in body
        # No bare report directives outside NOTIFY condition
        assert "and report compactly" not in body, (
            "full: bare 'and report compactly' (old form outside NOTIFY)"
        )
        assert "report, write back" not in body, (
            "full: bare 'report, write back' (old form outside NOTIFY)"
        )
    else:
        assert "`user_channel.notify` controls OUTPUT only" in body
        assert "NOTIFY=向用户输出动作; DONT_NOTIFY=安静输出" in body
        assert "Due/peer非用户动作" in body
        assert "NOTIFY缺动作→" in body
        assert "具体user todo未投影" in body
        assert "需修复LoopX状态投影" in body
        assert "静默时内部修复" in body
        if mode == "brief":
            assert "仅 `user_channel.notify=NOTIFY` 时输出，否则静默。" in body
    return {
        "authority": "interaction_contract.user_channel.notify",
        "external": "NOTIFY",
        "quiet": "DONT_NOTIFY",
        "quiet_missing_action": "internal_repair",
    }


def assert_sole_notification_authority(task_body: str, *, mode: str) -> None:
    body = normalized(task_body)
    assert "no-change=surface_only/no spend" in body, mode
    assert "material=outcome+vision" in body, mode
    assert "缺则同轮按返回命令补齐再terminal" in body, mode
    assert "unchanged→真实--vision-unchanged-reason" in body, mode

    if mode == "full":
        assert (
            "once. Only if `user_channel.notify=NOTIFY`: report the result compactly; "
            "if `user_todo_summary.open_count > 0`, include those todos; if none exists, "
            "report the gate. Under `DONT_NOTIFY`, stay quiet after the safe-bypass work."
        ) in body, body
        assert (
            "state/board/ledger, add todos if needed, then spend once after validation. "
            "Only if `user_channel.notify=NOTIFY`: report the new evidence. Under "
            "`DONT_NOTIFY`, stay quiet."
        ) in body, body
        return

    if mode == "compact":
        assert (
            "Only under `NOTIFY`, `state=operator_gate`/"
            "`notify_user_on_open_todo=true` permit concrete blocker-push; else quiet."
        ) in body, body
        assert "Return only under `user_channel.notify=NOTIFY`; otherwise stay quiet." in body
        return

    if mode == "brief":
        assert "仅 `user_channel.notify=NOTIFY` 时输出，否则静默。" in body
        return

    assert mode == "thin", mode
    assert "`user_channel.notify` controls OUTPUT only: NOTIFY=向用户输出动作; DONT_NOTIFY=安静输出。" in body


def assert_peer_scope_notification_authority(task_body: str) -> None:
    body = normalized(task_body)
    assert (
        "If a todo is claimed or leased by another peer, choose another in-scope item "
        "or record no in-scope work internally. Only `NOTIFY` reports it; "
        "`DONT_NOTIFY` stays quiet."
    ) in body, body


def user_output_outcome(
    task_body: str,
    decision: dict[str, object],
    *,
    mode: str,
) -> dict[str, bool]:
    policy = user_output_policy(task_body, mode=mode)
    assert policy["authority"] == "interaction_contract.user_channel.notify", policy
    interaction_contract = decision["interaction_contract"]
    assert isinstance(interaction_contract, dict), decision
    user_channel = interaction_contract["user_channel"]
    assert isinstance(user_channel, dict), decision
    notify = user_channel.get(policy["authority"].rsplit(".", maxsplit=1)[-1])
    action_required = user_channel.get("action_required") is True
    missing_action = action_required and not isinstance(user_channel.get("actions"), list)
    external_output = notify == policy["external"]
    return {
        "external_output": external_output,
        "blocker_push": external_output
        and bool(
            decision.get("state") == "operator_gate"
            or decision.get("notify_user_on_open_todo") is True
            or action_required
        ),
        "internal_projection_repair": bool(
            notify == policy["quiet"]
            and missing_action
            and policy["quiet_missing_action"] == "internal_repair"
        ),
    }


def main() -> int:
    default_payload = build_heartbeat_prompt(goal_id=GOAL_ID, active_state=ACTIVE_STATE)
    payload = build_heartbeat_prompt(goal_id=GOAL_ID, active_state=ACTIVE_STATE, full=True)
    compact_payload = build_heartbeat_prompt(goal_id=GOAL_ID, active_state=ACTIVE_STATE, compact=True)
    brief_payload = build_heartbeat_prompt(goal_id=GOAL_ID, active_state=ACTIVE_STATE, brief=True)
    thin_payload = build_heartbeat_prompt(goal_id=GOAL_ID, active_state=ACTIVE_STATE, thin=True)
    registry_default_payload = build_heartbeat_prompt(goal_id=GOAL_ID, compact=True)
    scoped_payload = build_heartbeat_prompt(
        goal_id=GOAL_ID,
        active_state=ACTIVE_STATE,
        compact=True,
        agent_id="codex-side-bypass",
        agent_scopes=[
            "control-plane coordination and todo claim ergonomics",
            "do not take benchmark execution todos unless reassigned",
        ],
        registered_agents=["codex-main-control", "codex-side-bypass"],
    )
    full_scoped_payload = build_heartbeat_prompt(
        goal_id=GOAL_ID,
        active_state=ACTIVE_STATE,
        full=True,
        agent_id="codex-side-bypass",
        agent_scopes=["control-plane coordination and todo claim ergonomics"],
        registered_agents=["codex-main-control", "codex-side-bypass"],
    )
    primary_scoped_payload = build_heartbeat_prompt(
        goal_id=GOAL_ID,
        active_state=ACTIVE_STATE,
        compact=True,
        agent_id="codex-main-control",
        agent_scopes=["benchmark readiness and final review"],
        registered_agents=["codex-main-control", "codex-side-bypass"],
    )
    thin_scoped_payload = build_heartbeat_prompt(
        goal_id=GOAL_ID,
        active_state=ACTIVE_STATE,
        thin=True,
        agent_id="codex-side-bypass",
        agent_scopes=["control-plane coordination and todo claim ergonomics"],
        registered_agents=["codex-main-control", "codex-side-bypass"],
    )
    profile_scoped_payload = build_heartbeat_prompt(
        goal_id=GOAL_ID,
        active_state=ACTIVE_STATE,
        thin=True,
        agent_id="codex-side-bypass",
        agent_profile={
            "schema_version": "agent_profile_v1",
            "agent_id": "codex-side-bypass",
            "scope_summary": "productization showcase docs lane",
            "private_note": "must stay out of heartbeat payload",
        },
        registered_agents=["codex-main-control", "codex-side-bypass"],
    )
    capability_scoped_payload = build_heartbeat_prompt(
        goal_id=GOAL_ID,
        active_state=ACTIVE_STATE,
        thin=True,
        agent_id="codex-side-bypass",
        registered_agents=["codex-main-control", "codex-side-bypass"],
        available_capabilities=["network", "external_evidence_poll", "network"],
    )
    live_peer_payload = build_heartbeat_prompt(
        goal_id="loopx-meta",
        active_state=Path("the registry-declared active state"),
        thin=True,
        agent_id="codex-product-capability",
        agent_profile={
            "schema_version": "agent_profile_v1",
            "agent_id": "codex-product-capability",
            "scope_summary": (
                "Agent-facing CLI output budgets, deterministic control-plane "
                "qualification, model-behavior shadow evaluation, and release "
                "outcome baseline correctness."
            ),
        },
        registered_agents=["codex-main-control", "codex-product-capability"],
        available_capabilities=["network", "external_evidence_poll"],
    )
    missing_agent_id = None
    try:
        build_heartbeat_prompt(
            goal_id=GOAL_ID,
            active_state=ACTIVE_STATE,
            agent_scopes=["control-plane coordination and todo claim ergonomics"],
        )
    except ValueError as exc:
        missing_agent_id = str(exc)
    assert missing_agent_id and "requires --agent-id" in missing_agent_id, missing_agent_id
    registered_without_agent_id = None
    try:
        build_heartbeat_prompt(
            goal_id=GOAL_ID,
            active_state=ACTIVE_STATE,
            compact=True,
            registered_agents=["codex-main-control", "codex-side-bypass"],
        )
    except ValueError as exc:
        registered_without_agent_id = str(exc)
    assert registered_without_agent_id and "identity-aware peer heartbeat prompt required" in registered_without_agent_id, (
        registered_without_agent_id
    )
    assert "--agent-id codex-main-control" in registered_without_agent_id, registered_without_agent_id
    unregistered_agent = None
    try:
        build_heartbeat_prompt(
            goal_id=GOAL_ID,
            active_state=ACTIVE_STATE,
            agent_id="unregistered-agent",
            registered_agents=["codex-side-bypass"],
        )
    except ValueError as exc:
        unregistered_agent = str(exc)
    assert unregistered_agent and "is not registered" in unregistered_agent, unregistered_agent
    assert_prompt_budget("full", str(payload["task_body"]))
    assert_prompt_budget("thin", str(default_payload["task_body"]))
    assert_prompt_budget("compact", str(compact_payload["task_body"]))
    assert_prompt_budget("brief", str(brief_payload["task_body"]))
    assert_prompt_budget("thin", str(thin_payload["task_body"]))
    assert_interface_budget_payload("full", payload)
    assert_interface_budget_payload("thin", default_payload)
    assert_interface_budget_payload("compact", compact_payload)
    assert_interface_budget_payload("brief", brief_payload)
    assert_interface_budget_payload("thin", thin_payload)
    assert_interface_budget_payload("compact", scoped_payload)
    assert_interface_budget_payload("compact", primary_scoped_payload)
    assert_interface_budget_payload("thin", thin_scoped_payload)
    assert_interface_budget_payload("thin", profile_scoped_payload)
    assert_no_project_specific_prompt_leaks("full", str(payload["task_body"]))
    assert_no_project_specific_prompt_leaks("thin", str(default_payload["task_body"]))
    assert_no_project_specific_prompt_leaks("compact", str(compact_payload["task_body"]))
    assert_no_project_specific_prompt_leaks("brief", str(brief_payload["task_body"]))
    assert_no_project_specific_prompt_leaks("thin", str(thin_payload["task_body"]))
    for prompt_payload in (payload, default_payload, compact_payload, brief_payload, thin_payload):
        task_body = str(prompt_payload["task_body"])
        assert "host_action=pause_or_delete_current_heartbeat" in task_body, task_body
        assert "automation_update" in task_body and "stop" in task_body, task_body
        assert (
            "`agent_read_required`" in task_body or "lark_event_inbox" in task_body
        ), task_body
        assert "drain" in task_body and "ACK" in task_body, task_body
        assert "Graph-on" not in task_body, task_body
        assert "Generic Kanban" not in task_body, task_body
        if prompt_payload is not payload:
            if "lark_event_inbox" in task_body:
                assert "drain_command" in task_body, task_body
            assert "writeback" in task_body, task_body
    thin_task = str(thin_payload["task_body"])
    assert default_payload["task_body"] == thin_payload["task_body"], default_payload
    assert default_payload["thin"] is True, default_payload
    assert default_payload["interface_budget"]["mode"] == "thin", default_payload
    assert payload["interface_budget"]["mode"] == "full", payload
    assert "full" not in default_payload, default_payload
    assert "full" not in payload, payload
    assert "Observed capabilities -> `--available-capability`; never user gates." in thin_task, thin_task
    assert payload["quota_guard_command"] == (
        'loopx --format json --registry "$HOME/.codex/loopx/registry.global.json" '
        'quota should-run --goal-id public-heartbeat-goal '
        '--turn-instance-id "${LOOPX_TURN:?}"'
    ), payload
    assert payload["quota_spend_command"] == (
        'loopx --format json --registry "$HOME/.codex/loopx/registry.global.json" '
        "quota spend-slot --goal-id public-heartbeat-goal --slots 1 --source heartbeat --execute"
    ), payload
    assert compact_payload["compact"] is True, compact_payload
    assert compact_payload["brief"] is False, compact_payload
    assert compact_payload["thin"] is False, compact_payload
    assert payload["active_state_source"] == "explicit", payload
    assert payload["resolved_active_state"] == str(ACTIVE_STATE), payload
    assert compact_payload["quota_guard_command"] == payload["quota_guard_command"], compact_payload
    assert compact_payload["quota_spend_command"] == payload["quota_spend_command"], compact_payload
    for prompt_label, prompt_payload in (
        ("full", payload),
        ("compact", compact_payload),
    ):
        task_body = str(prompt_payload["task_body"])
        progress_refresh = str(prompt_payload["progress_refresh_state_command"])
        quota_spend = str(prompt_payload["quota_spend_command"])
        state_only_refresh = str(prompt_payload["refresh_state_command"])
        assert task_body.index(progress_refresh) < task_body.index(quota_spend), prompt_label
        assert task_body.index(quota_spend) < task_body.rindex(state_only_refresh), prompt_label
    assert len(str(compact_payload["task_body"])) < len(str(payload["task_body"])), (
        len(str(compact_payload["task_body"])),
        len(str(payload["task_body"])),
    )
    compact_task = normalized(str(compact_payload["task_body"]))
    notification_cases = (
        (
            "quiet operator gate",
            {
                "should_run": True,
                "state": "operator_gate",
                "interaction_contract": {
                    "user_channel": {
                        "action_required": False,
                        "notify": "DONT_NOTIFY",
                    }
                },
            },
            {
                "external_output": False,
                "blocker_push": False,
                "internal_projection_repair": False,
            },
        ),
        (
            "quiet open-todo flag",
            {
                "should_run": True,
                "notify_user_on_open_todo": True,
                "interaction_contract": {
                    "user_channel": {
                        "action_required": False,
                        "notify": "DONT_NOTIFY",
                    }
                },
            },
            {
                "external_output": False,
                "blocker_push": False,
                "internal_projection_repair": False,
            },
        ),
        (
            "quiet missing action repair",
            {
                "should_run": True,
                "interaction_contract": {
                    "user_channel": {
                        "action_required": True,
                        "notify": "DONT_NOTIFY",
                    }
                },
            },
            {
                "external_output": False,
                "blocker_push": False,
                "internal_projection_repair": True,
            },
        ),
        (
            "notified blocker",
            {
                "should_run": True,
                "state": "operator_gate",
                "interaction_contract": {
                    "user_channel": {
                        "action_required": True,
                        "notify": "NOTIFY",
                        "actions": [{"text": "Approve the bounded action."}],
                    }
                },
            },
            {
                "external_output": True,
                "blocker_push": True,
                "internal_projection_repair": False,
            },
        ),
        (
            "notified missing action repair",
            {
                "should_run": True,
                "interaction_contract": {
                    "user_channel": {
                        "action_required": True,
                        "notify": "NOTIFY",
                    }
                },
            },
            {
                "external_output": True,
                "blocker_push": True,
                "internal_projection_repair": False,
            },
        ),
    )
    mode_payloads = (
        ("full", payload),
        ("compact", compact_payload),
        ("brief", brief_payload),
        ("thin", thin_payload),
    )
    for mode_label, mode_payload in mode_payloads:
        mode_body = str(mode_payload["task_body"])
        assert_sole_notification_authority(mode_body, mode=mode_label)
        for label, decision, expected in notification_cases:
            assert user_output_outcome(
                mode_body,
                decision,
                mode=mode_label,
            ) == expected, f"{mode_label}: {label}"
    assert_peer_scope_notification_authority(str(full_scoped_payload["task_body"]))

    assert "`state=operator_gate` / `notify_user_on_open_todo=true` /" not in compact_task
    assert (
        "Only under `NOTIFY`, `state=operator_gate`/"
        "`notify_user_on_open_todo=true` permit concrete blocker-push; "
        "else quiet."
    ) in compact_task
    for phrase in (
        "Compact policy: registry/state/adapter/`goal_boundary`",
        "Detail:",
        "loopx heartbeat-prompt --full --goal-id public-heartbeat-goal --active-state /tmp/public-heartbeat-goal/ACTIVE_GOAL_STATE.md",
        'loopx --format json --registry "$HOME/.codex/loopx/registry.global.json" quota should-run --goal-id public-heartbeat-goal',
        "state=operator_gate",
        "notify_user_on_open_todo=true",
        "`user_channel.notify=NOTIFY`",
        "concrete blocker-push",
        "else quiet",
        "safe_bypass_allowed=true",
        "safe_bypass_kind=outcome_floor_recovery",
        "receipts do not self-stop",
        "ranker/cross-domain evidence artifact",
        "one read-only poll",
        "heartbeat_recommendation",
        "task_orchestration_contract",
        "spawn admitted child lanes",
        "goal_boundary",
        "delivery_batch_scale",
        "delivery_outcome",
        "post_handoff_outcome_gap_streak",
        "handoff_delivery_contract",
        "Legacy/raw fallback is not owner/gate/stop authority",
        "run_first_read_only_map",
        "mapped_noop_if_unchanged",
        "steering audit",
        "bottleneck lens",
        "no-progress self-repair",
        "Gate only the affected path; continue independent allowed work",
        "loopx todo add --goal-id public-heartbeat-goal --role user --task-class user_gate|user_action",
        "owner todos and `--role agent` for agent todos, not prose",
        "Done->successor; final->refresh/spend/no-follow-up",
        'loopx --format json --registry "$HOME/.codex/loopx/registry.global.json" quota spend-slot --goal-id public-heartbeat-goal --slots 1 --source heartbeat --execute',
        "Account actual class/scale/outcome",
        "once unpiped; never retry",
        "Optional state-only post-spend",
        "No spend for quiet skips",
    ):
        assert phrase in compact_task, phrase
    registry_default_task = normalized(str(registry_default_payload["task_body"]))
    assert registry_default_payload["active_state"] == "the registry-declared active state", registry_default_payload
    assert registry_default_payload["active_state_source"] == "registry", registry_default_payload
    assert registry_default_payload["expanded_prompt_command"] == (
        "loopx heartbeat-prompt --full --goal-id public-heartbeat-goal"
    ), registry_default_payload
    assert registry_default_payload["compact_prompt_command"] == (
        "loopx heartbeat-prompt --compact --goal-id public-heartbeat-goal"
    ), registry_default_payload
    assert "using `the registry-declared active state`" in registry_default_task, registry_default_task
    scoped_task = normalized(str(scoped_payload["task_body"]))
    assert scoped_payload["agent_id"] == "codex-side-bypass", scoped_payload
    assert scoped_payload["agent_role"] == "peer-agent", scoped_payload
    assert "primary_agent" not in scoped_payload, scoped_payload
    assert scoped_payload["agent_scopes"] == [
        "control-plane coordination and todo claim ergonomics",
        "do not take benchmark execution todos unless reassigned",
    ], scoped_payload
    assert scoped_payload["registered_agents"] == ["codex-main-control", "codex-side-bypass"], scoped_payload
    assert "--agent-id codex-side-bypass" in scoped_payload["compact_prompt_command"], scoped_payload
    assert "--agent-scope 'control-plane coordination and todo claim ergonomics'" in scoped_payload["compact_prompt_command"], (
        scoped_payload
    )
    assert scoped_payload["quota_guard_command"].endswith(
        "quota should-run --goal-id public-heartbeat-goal --agent-id codex-side-bypass "
        '--turn-instance-id "${LOOPX_TURN:?}"'
    ), scoped_payload
    assert scoped_payload["quota_spend_command"].endswith(
        "quota spend-slot --goal-id public-heartbeat-goal --slots 1 --source heartbeat --execute --agent-id codex-side-bypass"
    ), scoped_payload
    assert primary_scoped_payload["quota_spend_command"].endswith(
        "quota spend-slot --goal-id public-heartbeat-goal --slots 1 --source heartbeat --execute --agent-id codex-main-control"
    ), primary_scoped_payload
    assert profile_scoped_payload["agent_scopes"] == ["productization showcase docs lane"], profile_scoped_payload
    assert profile_scoped_payload["agent_scope_source"] == "agent_profile_v1", profile_scoped_payload
    assert "private_note" not in profile_scoped_payload["agent_profile"], profile_scoped_payload
    assert profile_scoped_payload["thin_prompt_command"] == (
        "loopx heartbeat-prompt --thin --goal-id public-heartbeat-goal "
        "--active-state /tmp/public-heartbeat-goal/ACTIVE_GOAL_STATE.md --agent-id codex-side-bypass"
    ), profile_scoped_payload
    assert "--agent-scope" not in profile_scoped_payload["thin_prompt_command"], profile_scoped_payload
    assert "available_capabilities" not in capability_scoped_payload, capability_scoped_payload
    for command_key in (
        "quota_guard_command",
        "quota_spend_command",
        "thin_prompt_command",
    ):
        command = str(capability_scoped_payload[command_key])
        assert "--available-capability network" in command, command
        assert "--available-capability external_evidence_poll" in command, command
    capability_task_body = str(capability_scoped_payload["task_body"])
    assert "--available-capability network" in capability_task_body, capability_task_body
    assert "--available-capability external_evidence_poll" in capability_task_body, (
        capability_task_body
    )
    assert "heartbeat-prequota" in capability_task_body, capability_task_body
    assert capability_scoped_payload["pr_review_pre_quota_command"] == (
        "loopx heartbeat-prequota -g public-heartbeat-goal -a codex-side-bypass"
    ), capability_scoped_payload
    assert "productization showcase docs lane" in normalized(str(profile_scoped_payload["task_body"])), (
        profile_scoped_payload
    )
    live_peer_task = normalized(str(live_peer_payload["task_body"]))
    live_peer_budget = live_peer_payload["interface_budget"]
    assert isinstance(live_peer_budget, dict), live_peer_payload
    assert live_peer_budget["mode"] == "thin", live_peer_budget
    assert live_peer_budget["char_count"] == len(str(live_peer_payload["task_body"])), live_peer_budget
    assert live_peer_budget["budget_char_count"] <= live_peer_budget["max_chars"], live_peer_budget
    assert live_peer_budget["within_budget"] is True, live_peer_budget
    assert len(str(live_peer_payload["task_body"])) <= int(live_peer_budget["max_chars"]), live_peer_budget
    assert "correctness.." not in live_peer_task, live_peer_task
    assert live_peer_task.index("LOOPX_TURN=<current_time_iso>") < live_peer_task.index(
        "quota should-run"
    ), live_peer_task
    for phrase in (
        "Equal peer `codex-product-capability` (peer_v1)",
        "Agent-facing CLI output budgets, deterministic control-plane qualification, "
        "model-behavior shadow evaluation, and release outcome baseline correctness",
        "Claim/lease first",
        "independent repo worktree",
        "todo continuation",
        "no cross-agent authority",
        "no scope in todo metadata",
        "Normal turns use CLI `interaction_contract`; use `loopx-project` for "
        "lifecycle/registry and `loopx-self-repair` for runtime/projection drift",
        "use selection_command when required",
        "heartbeat-prequota",
        'loopx --format json --registry "$HOME/.codex/loopx/registry.global.json" quota should-run '
        "--goal-id loopx-meta --agent-id codex-product-capability --available-capability network "
        "--available-capability external_evidence_poll",
        "`user_channel.notify` controls OUTPUT only: NOTIFY=向用户输出动作; DONT_NOTIFY=安静输出",
        "Due/peer非用户动作",
        "NOTIFY缺动作→",
        "具体user todo未投影",
        "Observed capabilities -> `--available-capability`; never user gates",
        "host_action=pause_or_delete_current_heartbeat->automation_update stop(no-spend)",
        "else RRULE/projected-fallback_hint/ack/fail",
        "no-change=surface_only/no spend",
        "unchanged→真实--vision-unchanged-reason",
        "guard; 2 stalls->replan",
        "`agent_read_required`",
        "drain/read/triage before work; settle/ACK",
        "P0 blocked: safe P1/P2; monitor quiet/no-spend",
        "No learning queue unless asked",
        "Destructive Git/production requires explicit authorization",
    ):
        assert phrase in live_peer_task, phrase
    for phrase in (
        "Agent identity and scope",
        "model: peer_v1",
        "equal peer agent",
        "independent worktree",
        "Task-scoped coordination",
        "agent_id `codex-side-bypass`",
        "control-plane coordination and todo claim ergonomics",
        "do not take benchmark execution todos unless reassigned",
        "loopx todo claim --goal-id public-heartbeat-goal --todo-id <todo_id> --claimed-by codex-side-bypass",
        "Do not write scope into todo metadata",
    ):
        assert phrase in scoped_task, phrase
    primary_task = normalized(str(primary_scoped_payload["task_body"]))
    assert primary_scoped_payload["agent_role"] == "peer-agent", primary_scoped_payload
    assert "model: peer_v1" in primary_task, primary_task
    assert "single primary agent" not in primary_task, primary_task
    thin_scoped_task = normalized(str(thin_scoped_payload["task_body"]))
    assert "(peer_v1)" in thin_scoped_task, thin_scoped_task
    assert "independent repo worktree" in thin_scoped_task, thin_scoped_task
    assert "primary_agent" not in thin_scoped_task, thin_scoped_task
    assert "--active-state" not in registry_default_payload["expanded_prompt_command"], registry_default_payload
    assert brief_payload["brief"] is True, brief_payload
    assert brief_payload["compact"] is False, brief_payload
    assert brief_payload["thin"] is False, brief_payload
    assert brief_payload["quota_guard_command"] == payload["quota_guard_command"], brief_payload
    assert brief_payload["quota_spend_command"] == payload["quota_spend_command"], brief_payload
    # Preserve size ordering and the absolute budgets checked above. Essential
    # shared guidance need not shrink by an arbitrary percentage in each mode.
    assert len(str(brief_payload["task_body"])) < len(str(compact_payload["task_body"])), (
        len(str(brief_payload["task_body"])),
        len(str(compact_payload["task_body"])),
    )
    brief_task = normalized(str(brief_payload["task_body"]))
    for phrase in (
        "Brief 详情：",
        "loopx heartbeat-prompt --compact --goal-id public-heartbeat-goal --active-state /tmp/public-heartbeat-goal/ACTIVE_GOAL_STATE.md",
        "Run assignment and guard as separate statements in one shell",
        'loopx --format json --registry "$HOME/.codex/loopx/registry.global.json" quota should-run --goal-id public-heartbeat-goal',
        "`user_channel.notify` controls OUTPUT only: NOTIFY=向用户输出动作; DONT_NOTIFY=安静输出",
        "Due/peer非用户动作",
        "Todo验收非结算",
        "NOTIFY缺动作→",
        "具体user todo未投影",
        "按 user channel",
        "monitor_quiet_skip",
        "记 receipt/stall",
        "同 id 重试",
        "只读一次",
        "outcome-floor recovery",
        "推进 evidence",
        "status --limit 3",
        "review-packet --handoff-only",
        "heartbeat_recommendation.agent_must_attempt",
        "遵守 quota 权限/结果/handoff",
        "交付并验证",
        "execution_obligation.must_attempt_work",
        "interaction_contract.cli_channel.settlement_plan.ordered_steps",
        "精确 identity/effect 顺序结算",
        "不使用旧 refresh/spend 配方",
        "仅 terminal no-follow-up 收尾",
        "静默跳过、preflight 失败、blocker-push 提问、dry-run、重复记账均不扣额",
        "No learning queue unless asked.",
        "No permission asks in a trusted session.",
    ):
        assert phrase in brief_task, phrase
    for command_key in ("quota_spend_command", "refresh_state_command", "progress_refresh_state_command"):
        assert brief_payload[command_key] not in brief_payload["task_body"]
    assert thin_payload["thin"] is True, thin_payload
    assert thin_payload["brief"] is False, thin_payload
    assert thin_payload["compact"] is False, thin_payload
    assert thin_payload["thin_prompt_command"] == (
        "loopx heartbeat-prompt --thin --goal-id public-heartbeat-goal "
        "--active-state /tmp/public-heartbeat-goal/ACTIVE_GOAL_STATE.md"
    ), thin_payload
    assert len(str(thin_payload["task_body"])) < len(str(brief_payload["task_body"])), (
        len(str(thin_payload["task_body"])),
        len(str(brief_payload["task_body"])),
    )
    thin_task = normalized(str(thin_payload["task_body"]))
    for phrase in (
        "Advance `public-heartbeat-goal` from /tmp/public-heartbeat-goal/ACTIVE_GOAL_STATE.md",
        "Normal turns use CLI `interaction_contract`; use `loopx-project` for "
        "lifecycle/registry and `loopx-self-repair` for runtime/projection drift",
        "use selection_command when required",
        "quota should-run",
        "`user_channel.notify` controls OUTPUT only: NOTIFY=向用户输出动作; DONT_NOTIFY=安静输出",
        "Due/peer非用户动作",
        "NOTIFY缺动作→",
        "具体user todo未投影",
        "host_action=pause_or_delete_current_heartbeat->automation_update stop(no-spend)",
        "else RRULE/projected-fallback_hint/ack/fail",
        "no-change=surface_only/no spend",
        "unchanged→真实--vision-unchanged-reason",
        "guard; 2 stalls->replan",
        "P0 blocked: safe P1/P2",
        "monitor quiet/no-spend",
        "No learning queue unless asked",
        "Destructive Git/production requires explicit authorization",
    ):
        assert phrase in thin_task, phrase
    for label, task in (
        ("full", normalized(str(payload["task_body"]))),
        ("compact", compact_task),
        ("brief", brief_task),
        ("thin", thin_task),
    ):
        assert "no-change=surface_only/no spend" in task, label
        assert "--vision-unchanged-reason" in task, label
        assert "material=outcome+vision" in task, label
    assert "if absent say" not in thin_task, thin_task
    assert "If false/0: quiet/no-user-todo" not in thin_task, thin_task

    doc = DOC.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    integration_doc = INTEGRATION_DOC.read_text(encoding="utf-8")
    project_skill = PROJECT_SKILL.read_text(encoding="utf-8")
    generated = str(payload["task_body"])
    compact_generated = normalized(generated)

    must_have = (
        "<ACTIVE_GOAL_STATE_PATH>",
        "<GOAL_ID>",
        'export PATH="$HOME/.local/bin:$PATH"',
        'install_script="$HOME/loopx/scripts/install-local.sh"',
        "loopx doctor >/dev/null",
        'loopx --format json --registry "$HOME/.codex/loopx/registry.global.json" quota should-run --goal-id <GOAL_ID>',
        "project non-basic capabilities that are actually present",
        "without guessing capabilities the host does not have",
        "If that preflight still fails",
        "should_run=false",
        "state=operator_gate",
        "gate_prompt",
        "operator_question",
        "user_todo_summary",
        "user_todo_summary.open_count > 0",
        "never say \"no new user action\"",
        "Treat `interaction_contract.user_channel.notify` as the final notification signal",
        "When it is `NOTIFY`",
        "even when `action_required=false`",
        "`user_todo_summary.open_count=0`",
        "`non_blocking=true`",
        "non-blocking means the agent may continue independent work",
        'Never say only "owner gate"',
        "Only when `notify=DONT_NOTIFY`",
        '"无用户待办/无需通知"',
        "具体 user todo 未投影，需修复 LoopX 状态投影",
        "NOTIFY",
        "notify_user_on_open_todo=true",
        "blocker-push opportunity",
        "open_todo_notification_policy=repeat_until_resolved",
        "state=focus_wait",
        "waiting_on=external_evidence",
        "listing at most three first_open_items",
        "open_todo_notify_reason",
        "done, defer/not now, or a new evidence link/date/conclusion",
        "no spend",
        "safe_bypass_allowed=true",
        "gate blocks only the gated delivery path",
        "one bounded safe-bypass step",
        "include the projected user actions or todos concretely",
        "LOOPX_TURN",
        "<current_time_iso>",
        "idempotent receipt for every turn",
        "Do not append a second manual monitor poll",
        "delivery edits",
        "unchanged monitor-only receipts are not self-stop signals",
        "safe_bypass_kind=outcome_floor_recovery",
        "ranker/cross-domain evidence artifact",
        "explicitly a monitor",
        "status/log/metric/marker surfaces",
        "New eval/fail/complete/blocker",
        "state/board/ledger",
        "Still do not launch/stop/restart/sync/design code",
        "DONT_NOTIFY",
        "should_run=true",
        "When you inspect current LoopX routing",
        "attention_queue.items",
        "project_asset are authoritative",
        "raw queue fields are not owner/gate/stop authority",
        "run_history.latest_runs as evidence and drill-down only",
        "do not decide whether a gate is pending or approved from latest runs alone",
        "goal_boundary",
        "Stop for an open user/owner todo only when it belongs to this goal's guard payload or current project asset",
        "Dependency or sibling-goal todos found in `attention_queue.items` should be recorded as dependency blockers",
        "must not consume the whole eligible turn",
        "choose a gate-independent P0/P1/P2 candidate for this goal when one exists",
        "then use the blocker-push pattern above",
        "do not append quota spend",
        "heartbeat_recommendation",
        "recommended_mode=run_first_read_only_map",
        "real read-only map",
        "read_only_project_map result",
        "recommended_mode=mapped_noop_if_unchanged",
        "stop_if_unchanged=true",
        "no new user instruction, owner evidence, agent todo, stale source, or safe handoff",
        "return quiet `DONT_NOTIFY`: do not run, edit, or spend",
        "Check `delivery_batch_scale`, `delivery_outcome`,",
        "repeated-small or surface-only loops",
        "Run a short steering audit before choosing work",
        "list at least three plausible next-action candidates across different P0/P1/P2 lanes",
        "apply a continuation check",
        "keep compute quota separate from focus quota",
        "Include a product bottleneck lens",
        "user experience, agent capability, evidence quality, adapter readiness, or priority-rule gaps",
        "promote one concrete bottleneck candidate when it should outrank the nearest local TODO",
        "Run the no-progress self-repair check before choosing delivery work",
        "autonomous_replan_obligation",
        "execution_obligation.must_attempt_work=true",
        "2 consecutive eligible heartbeats are no-progress loops",
        "self-cancel turn",
        "repair path is",
        "Choose scope-bounded work toward a verifiable result",
        "a focused correction can also be sufficient",
        "obey budgets, explicit stops, settlement and replan requirements",
        "Stay inside goal_boundary when present",
        "Public-safe repo publication is not an operator gate by itself",
        "commit, push, and PR creation may proceed autonomously after validation",
        "clean public/private boundary scan",
        "private or company-internal material, credentials, destructive git operations, production actions",
        "Run validation proportionate to the change and risk",
        "Write back changed files, validation, critic, and next action",
        "Plan/top todo/route changes need todo/Next Action writeback",
        "If a user/owner todo appears",
        "do not hide it in prose",
        "loopx todo add --goal-id <GOAL_ID> --role user --task-class user_gate",
        "loopx todo add --goal-id <GOAL_ID> --role user --task-class user_action",
        "Use `--role agent` for project-agent follow-up work",
        "docs/project-agent-todo-contract.md",
        'loopx --format json --registry "$HOME/.codex/loopx/registry.global.json" quota spend-slot --goal-id <GOAL_ID> --todo-id <SELECTED_TODO_ID> --slots 1 --source heartbeat --execute',
        "loopx refresh-state --goal-id <GOAL_ID>",
        "--classification <PUBLIC_SAFE_PROGRESS_CLASSIFICATION>",
        "--delivery-batch-scale <ACTUAL_DELIVERY_BATCH_SCALE>",
        "--delivery-outcome <ACTUAL_DELIVERY_OUTCOME>",
        "Never default or upgrade smaller/preparatory work",
        "append exactly one",
        "If spend output is ambiguous, verify with read-only quota status; never rerun.",
        "Do not append spend for quiet should_run=false skips, preflight failures, pure dry-run previews, or duplicate accounting attempts",
        "safe_bypass_allowed=true and you actually completed a bounded safe-bypass step",
        "safe_bypass_kind=outcome_floor_recovery",
        "ranker/cross-domain evidence artifact",
        "Return a compact final report",
    )
    compact_doc = normalized(doc)
    for phrase in must_have:
        assert phrase in compact_doc, phrase
    assert "if absent say" not in compact_doc, compact_doc
    for phrase in (
        'export PATH="$HOME/.local/bin:$PATH"',
        'install_script="$HOME/loopx/scripts/install-local.sh"',
        "loopx doctor >/dev/null",
        'loopx --format json --registry "$HOME/.codex/loopx/registry.global.json" quota should-run --goal-id public-heartbeat-goal',
        "If that preflight still fails",
        "should_run=false",
        "state=operator_gate",
        "gate_prompt",
        "operator_question",
        "user_todo_summary",
        "user_todo_summary.open_count > 0",
        "never say \"no new user action\"",
        "`interaction_contract.user_channel.notify` controls output",
        "`should_run`/due monitor and other-agent scoped todos",
        "are not user prompts",
        "`action_required` without an action",
        "具体 user todo 未投影，需修复 LoopX 状态投影",
        "NOTIFY",
        "notify_user_on_open_todo=true",
        "blocker-push",
        "open_todo_notification_policy=repeat_until_resolved",
        "user_gate_notification_cooldown.notification_suppressed=true",
        "reminder window/change",
        "open_todo_notify_reason",
        "No delivery/spend",
        "safe_bypass_allowed=true",
        "gate blocks only the gated delivery path",
        "one bounded safe-bypass step",
        "include those todos",
        "LOOPX_TURN",
        "--turn-instance-id",
        "receipt/stall is written",
        "On receipt write failure",
        "No edits/spend",
        "receipts do not self-stop",
        "explicitly a monitor",
        "status/log/metric/marker surfaces",
        "New eval/fail/complete/blocker",
        "state/board/ledger",
        "Still do not launch/stop/restart/sync/design code",
        "DONT_NOTIFY",
        "When you inspect current LoopX routing",
        "attention_queue.items",
        "project_asset` are authoritative",
        "raw queue fields are not owner/gate/stop authority",
        "run_history.latest_runs` as evidence and drill-down only",
        "do not decide whether a gate is pending or approved from latest runs alone",
        "goal_boundary",
        "then use the blocker-push pattern above",
        "no spend",
        "heartbeat_recommendation",
        "recommended_mode=run_first_read_only_map",
        "run its `command` as a real read-only map",
        "read_only_project_map` result",
        "recommended_mode=mapped_noop_if_unchanged",
        "stop_if_unchanged=true",
        "no new user instruction, owner evidence, agent todo, stale source, or safe handoff",
        "return quiet `DONT_NOTIFY`: do not run, edit, or spend",
        "Check `delivery_batch_scale`, `delivery_outcome`,",
        "repeated-small or surface-only loops",
        "Run a short steering audit before choosing work",
        "list at least three plausible next-action candidates across different P0/P1/P2 lanes",
        "apply a continuation check",
        "keep compute quota separate from focus quota",
        "Include a product bottleneck lens",
        "user experience, agent capability, evidence quality, adapter readiness, or priority-rule gaps",
        "promote one concrete bottleneck candidate when it should outrank the nearest local TODO",
        "Run the no-progress self-repair check",
        "autonomous_replan_obligation",
        "execution_obligation.must_attempt_work=true",
        "2 consecutive eligible heartbeats are no-progress loops",
        "self-cancel turn",
        "repair path is",
        "授权/预算内推进可验证结果",
        "a focused correction may suffice",
        "Stay inside `goal_boundary` when present",
        "Follow user authority and repository rules",
        "publish public-safe evidence",
        "Destructive Git/production requires explicit authorization",
        "Plan/top todo/route changes need todo/Next Action writeback",
        "If a user/owner todo appears",
        "loopx todo add --goal-id public-heartbeat-goal --role user --task-class user_gate",
        "loopx todo add --goal-id public-heartbeat-goal --role user --task-class user_action",
        "docs/project-agent-todo-contract.md",
        'loopx --format json --registry "$HOME/.codex/loopx/registry.global.json" quota spend-slot --goal-id public-heartbeat-goal --slots 1 --source heartbeat --execute',
        "loopx refresh-state --goal-id public-heartbeat-goal",
        "--classification <PUBLIC_SAFE_PROGRESS_CLASSIFICATION>",
        "--delivery-batch-scale <ACTUAL_DELIVERY_BATCH_SCALE>",
        "--delivery-outcome <ACTUAL_DELIVERY_OUTCOME>",
        "never default or upgrade to `multi_surface` / `outcome_progress`",
        "Spend consumes this causal record",
        "Do not append spend for quiet `should_run=false` skips",
        "safe_bypass_kind=outcome_floor_recovery",
        "ranker/cross-domain evidence artifact",
    ):
        assert phrase in compact_generated, phrase
    assert "if absent say" not in compact_generated, compact_generated
    assert "Only if action_required=true/open_count>0" not in compact_generated, compact_generated
    assert "If false/0, allow quiet/no-user-todo" not in compact_generated, compact_generated

    assert_ordered(
        doc[doc.index("Before spending delivery compute, first make the LoopX CLI reachable"):],
        (
            "Before spending delivery compute, first make the LoopX CLI reachable",
            'export PATH="$HOME/.local/bin:$PATH"',
            "loopx doctor >/dev/null",
            'loopx --format json --registry "$HOME/.codex/loopx/registry.global.json" quota should-run --goal-id <GOAL_ID>',
            "If that preflight still fails",
            "If the result says should_run=false",
            "state=operator_gate",
            "gate_prompt",
            "notify_user_on_open_todo=true",
            "blocker-push opportunity",
            "open_todo_notification_policy=repeat_until_resolved",
            "safe_bypass_allowed=true",
            "explicitly a monitor",
            "status/log/metric/marker surfaces",
            "New eval/fail/complete/blocker",
            "If the result says should_run=true",
            "When you inspect current LoopX routing",
            "attention_queue.items",
            "run_history.latest_runs",
            "Also inspect goal_boundary",
            "then use the blocker-push pattern above",
            "execution_obligation",
            "heartbeat_recommendation.notify is only the user-notification policy",
            "not an execution gate",
            "must_attempt_work=true",
            "recommended_mode=run_first_read_only_map",
            "recommended_mode=mapped_noop_if_unchanged",
            "Run a short steering audit before choosing work",
            "Include a product bottleneck lens",
            "Run the no-progress self-repair check before choosing delivery work",
            "Choose scope-bounded work toward a verifiable result",
            "Public-safe repo publication is not an operator gate by itself",
            "Run validation proportionate to the change and risk",
            "loopx refresh-state --goal-id <GOAL_ID>",
            'loopx --format json --registry "$HOME/.codex/loopx/registry.global.json" quota spend-slot --goal-id <GOAL_ID> --todo-id <SELECTED_TODO_ID> --slots 1 --source heartbeat --execute',
            "If the dashboard or controller needs a state-only update after spend",
            "Return a compact final report",
        ),
    )

    getting_started = GETTING_STARTED.read_text(encoding="utf-8")
    assert "docs/guides/getting-started.md" in readme, readme
    assert "loopx heartbeat-prompt" in readme, readme
    assert "quota should-run.scheduler_hint" in readme, readme
    assert "loopx quota spend-slot" in readme, readme
    assert "Generate a guarded Codex App heartbeat body" in getting_started, getting_started
    assert "3-minute bootstrap cadence" in getting_started, getting_started
    assert "scheduler_hint" in getting_started, getting_started
    assert "loopx heartbeat-prompt --thin" in getting_started, getting_started
    assert "loopx heartbeat-prompt --compact" in getting_started, getting_started
    assert "loopx-canary" in getting_started, getting_started
    assert "release snapshot" in getting_started, getting_started
    assert "execution_obligation" in getting_started, getting_started
    assert "safe-bypass or self-repair hints" in getting_started, getting_started
    assert "../heartbeat-automation-prompt.md" in getting_started, getting_started
    assert "execution_obligation" in doc, doc
    assert "Create a heartbeat automation starting at 3 minutes" in doc, doc
    assert "quota should-run.scheduler_hint" in doc, doc
    assert "automation_update" in doc, doc
    assert "scheduler_hint.app_automation.stateful_backoff" in doc, doc
    assert "scheduler_hint.action=stop_until_explicit_resume" in doc, doc
    assert "host_action=pause_or_delete_current_heartbeat" in doc, doc
    assert "apply_needed=true" in doc, doc
    assert "app_automation.ack_hint.cli_args" in doc, doc
    assert "quota scheduler-ack-current" in doc, doc
    assert "scheduler_hint.app_automation.failure_hint.cli_args" in doc, doc
    assert "recommended_rrule" in doc, doc
    normalized_doc = normalized(doc)
    assert "Attempt the host update at most once per hint and turn" in normalized_doc, doc
    assert "do not retry or ACK" in normalized_doc, doc
    assert "Exact repeats are then suppressed" in normalized_doc, doc
    assert "must_attempt_work=true" in doc, doc
    assert "not an execution gate" in normalized(doc), doc
    assert "loopx heartbeat-prompt" in doc, doc
    assert "--compact" in doc, doc
    assert "--brief" in doc, doc
    assert "--thin" in doc, doc
    assert "--cli-bin loopx-canary" in doc, doc
    assert "Do not hand-edit per-project lifecycle branches" in doc, doc
    assert "loopx heartbeat-prompt" in integration_doc, integration_doc
    assert "loopx heartbeat-prompt --compact" in integration_doc, integration_doc
    assert "loopx heartbeat-prompt --brief" in integration_doc, integration_doc
    assert "loopx heartbeat-prompt --thin" in integration_doc, integration_doc
    assert "loopx-canary heartbeat-prompt" in integration_doc, integration_doc
    assert "--cli-bin loopx-canary" in integration_doc, integration_doc
    assert "local release snapshot" in integration_doc, integration_doc
    assert "visible goal text can stay short" in integration_doc, integration_doc
    assert "shares the same quota, gate," in integration_doc, integration_doc
    assert "steering-audit, writeback, refresh, and spend lifecycle" in integration_doc, integration_doc
    assert "heartbeat_recommendation" in integration_doc, integration_doc
    assert "execution_obligation" in integration_doc, integration_doc
    assert "Do not hand-edit one-off automation prompt branches" in normalized(integration_doc), integration_doc
    assert "public commit, push, and PR creation as autonomous" in normalized(integration_doc), integration_doc
    assert "Two Prompt Layers" in doc, doc
    assert "Visible goal text" in doc, doc
    assert "heartbeat automation task body" in doc, doc
    assert "LoopX is not an autonomous production controller" in readme, readme
    assert "loopx heartbeat-prompt" in project_skill, project_skill
    assert "--bootstrap --thin --codex-app" in project_skill, project_skill
    assert "thin/compact/brief/full execution body" in project_skill, project_skill
    assert "goal_boundary" in project_skill, project_skill
    assert "smoke" in project_skill and "contract" in project_skill, project_skill
    assert "Set Up Recurring Heartbeats" in project_skill, project_skill
    assert "visible goal text short" in project_skill, project_skill
    assert "refresh-state" in project_skill and "spend" in project_skill, project_skill
    assert "--classification <PUBLIC_SAFE_PROGRESS_CLASSIFICATION>" in project_skill, project_skill
    assert "--delivery-batch-scale <ACTUAL_DELIVERY_BATCH_SCALE>" in project_skill, project_skill
    assert "--delivery-outcome <ACTUAL_DELIVERY_OUTCOME>" in project_skill, project_skill
    assert "Never default or upgrade a smaller/preparatory turn" in normalized(project_skill), project_skill
    assert "do not infer scale/outcome from the classification name" in normalized(project_skill), project_skill
    assert "no-progress self-repair guard" in project_skill, project_skill
    assert "2 consecutive stalled turns" in normalized(project_skill), project_skill
    assert "one idempotent receipt on every heartbeat" in normalized(project_skill), project_skill
    assert "retry with the same turn id" in normalized(project_skill), project_skill
    assert "unchanged monitor-only polls are liveness-preserving no-ops" in normalized(project_skill), project_skill
    assert "Routine public repo publication is a boundary decision" in project_skill, project_skill
    assert "Do not reintroduce a user gate for public-safe publication itself" in project_skill, project_skill
    assert "notify_user_on_open_todo=true" in project_skill, project_skill
    assert "open_todo_notification_policy=repeat_until_resolved" in project_skill, project_skill
    assert "blocker-push `NOTIFY`" in project_skill, project_skill
    assert "sibling-goal todos" in project_skill, project_skill
    assert "must not consume the whole eligible turn" in project_skill, project_skill
    assert "heartbeat_recommendation" in project_skill, project_skill
    assert "execution_obligation" in project_skill, project_skill
    assert "scheduler_hint" in project_skill, project_skill
    assert "automation_update" in project_skill, project_skill
    assert "scheduler_hint.app_automation.stateful_backoff" in project_skill, project_skill
    assert "scheduler_hint.action=stop_until_explicit_resume" in project_skill, project_skill
    assert "host_action=pause_or_delete_current_heartbeat" in project_skill, project_skill
    assert "apply_needed=true" in project_skill, project_skill
    assert "app_automation.ack_hint.cli_args" in project_skill, project_skill
    assert "quota scheduler-ack-current" in project_skill, project_skill
    assert "scheduler_hint.app_automation.failure_hint.cli_args" in project_skill, project_skill
    assert "recommended_rrule" in project_skill, project_skill
    normalized_project_skill = normalized(project_skill)
    assert "Attempt the host update at most once per hint and turn" in normalized_project_skill, project_skill
    assert "do not retry or ACK" in normalized_project_skill, project_skill
    assert "suppress the exact repeat" in normalized_project_skill, project_skill
    assert "must_attempt_work=true" in project_skill, project_skill
    assert "not an execution gate" in normalized(project_skill), project_skill
    assert "mapped_noop_if_unchanged" in project_skill, project_skill
    assert "legacy/raw fallback" in project_skill, project_skill
    assert "do not infer" in project_skill, project_skill
    assert "reason to paste one-off control logic into the scheduler" in project_skill, project_skill

    cli_json = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "heartbeat-prompt",
            "--goal-id",
            GOAL_ID,
            "--active-state",
            str(ACTIVE_STATE),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    cli_payload = json.loads(cli_json.stdout)
    cli_expected_payload = build_heartbeat_prompt(
        goal_id=GOAL_ID,
        active_state=ACTIVE_STATE,
        reward_memory_enabled=False,
    )
    assert cli_payload["task_body"] == cli_expected_payload["task_body"], cli_payload
    assert set(cli_payload) == {
        "schema_version",
        "ok",
        "goal_id",
        "task_body",
        "interface_budget",
    }, cli_payload
    assert cli_payload["schema_version"] == HEARTBEAT_AGENT_INPUT_SCHEMA_VERSION
    assert cli_payload["interface_budget"]["mode"] == "thin", cli_payload

    cli_full_json = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "heartbeat-prompt",
            "--goal-id",
            GOAL_ID,
            "--active-state",
            str(ACTIVE_STATE),
            "--full",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    cli_full_payload = json.loads(cli_full_json.stdout)
    cli_full_expected_payload = build_heartbeat_prompt(
        goal_id=GOAL_ID,
        active_state=ACTIVE_STATE,
        full=True,
        reward_memory_enabled=False,
    )
    assert cli_full_payload["task_body"] == cli_full_expected_payload["task_body"], cli_full_payload
    assert cli_full_payload["thin"] is False, cli_full_payload
    assert cli_full_payload["interface_budget"]["mode"] == "full", cli_full_payload
    assert "full" not in cli_full_payload, cli_full_payload

    cli_compact_json = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "heartbeat-prompt",
            "--goal-id",
            GOAL_ID,
            "--active-state",
            str(ACTIVE_STATE),
            "--compact",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    cli_compact_payload = json.loads(cli_compact_json.stdout)
    cli_compact_expected_payload = build_heartbeat_prompt(
        goal_id=GOAL_ID,
        active_state=ACTIVE_STATE,
        compact=True,
        reward_memory_enabled=False,
    )
    assert cli_compact_payload["task_body"] == cli_compact_expected_payload["task_body"], cli_compact_payload
    assert cli_compact_payload["compact"] is True, cli_compact_payload

    cli_brief_json = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "heartbeat-prompt",
            "--goal-id",
            GOAL_ID,
            "--active-state",
            str(ACTIVE_STATE),
            "--brief",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    cli_brief_payload = json.loads(cli_brief_json.stdout)
    cli_brief_expected_payload = build_heartbeat_prompt(
        goal_id=GOAL_ID,
        active_state=ACTIVE_STATE,
        brief=True,
        reward_memory_enabled=False,
    )
    assert cli_brief_payload["task_body"] == cli_brief_expected_payload["task_body"], cli_brief_payload
    assert cli_brief_payload["brief"] is True, cli_brief_payload
    assert cli_brief_payload["cli_bin"] == "loopx", cli_brief_payload

    cli_thin_json = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "heartbeat-prompt",
            "--goal-id",
            GOAL_ID,
            "--active-state",
            str(ACTIVE_STATE),
            "--thin",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    cli_thin_payload = json.loads(cli_thin_json.stdout)
    cli_thin_expected_payload = build_heartbeat_prompt(
        goal_id=GOAL_ID,
        active_state=ACTIVE_STATE,
        thin=True,
        reward_memory_enabled=False,
    )
    assert cli_thin_payload["task_body"] == cli_thin_expected_payload["task_body"], cli_thin_payload
    assert cli_thin_payload["schema_version"] == HEARTBEAT_AGENT_INPUT_SCHEMA_VERSION
    assert "thin_prompt_command" not in cli_thin_payload, cli_thin_payload

    cli_canary_json = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "heartbeat-prompt",
            "--goal-id",
            GOAL_ID,
            "--active-state",
            str(ACTIVE_STATE),
            "--brief",
            "--cli-bin",
            "loopx-canary",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    cli_canary_payload = json.loads(cli_canary_json.stdout)
    assert cli_canary_payload["cli_bin"] == "loopx-canary", cli_canary_payload
    assert "loopx-canary doctor" in cli_canary_payload["cli_preflight"], cli_canary_payload
    assert "loopx-canary --format json" in cli_canary_payload["quota_guard_command"], cli_canary_payload
    assert "loopx-canary heartbeat-prompt --compact" in cli_canary_payload["task_body"], cli_canary_payload

    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        project = root / "project"
        state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
        registry_path = project / ".loopx" / "registry.json"
        state_file.parent.mkdir(parents=True)
        registry_path.parent.mkdir(parents=True)
        state_file.write_text("# Active State\n", encoding="utf-8")
        registry_path.write_text(
            json.dumps(
                {
                    "goals": [
                        {
                            "id": GOAL_ID,
                            "domain": "smoke",
                            "status": "active",
                            "repo": str(project),
                            "state_file": f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md",
                            "adapter": {"kind": "generic_project_goal_v0", "status": "connected"},
                            "explore_graph": {"enabled": True},
                            "control_plane": {
                                "lark_kanban": {"heartbeat_sync_enabled": True}
                            },
                            "coordination": {
                                "registered_agents": ["codex-main-control", "codex-side-bypass"],
                                "agent_model": "peer_v1",
                                "agent_profiles": {
                                    "codex-side-bypass": {
                                        "schema_version": "agent_profile_v1",
                                        "scope_summary": "productization showcase docs lane",
                                    }
                                },
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        cli_registry_default_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "heartbeat-prompt",
                "--goal-id",
                GOAL_ID,
                "--brief",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert cli_registry_default_result.returncode != 0, cli_registry_default_result.stdout
        cli_registry_default_error = json.loads(cli_registry_default_result.stdout)
        assert "identity-aware peer heartbeat prompt required" in cli_registry_default_error["error"], (
            cli_registry_default_error
        )
        assert "--agent-id codex-main-control" in cli_registry_default_error["error"], (
            cli_registry_default_error
        )
        assert "--agent-scope" in cli_registry_default_error["error"], cli_registry_default_error

        cli_registry_default_json = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "heartbeat-prompt",
                "--goal-id",
                GOAL_ID,
                "--brief",
                "--agent-id",
                "codex-main-control",
                "--agent-scope",
                "primary review and coordination",
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        cli_registry_default_payload = json.loads(cli_registry_default_json.stdout)
        assert cli_registry_default_payload["brief"] is True, cli_registry_default_payload
        assert cli_registry_default_payload["agent_id"] == "codex-main-control", cli_registry_default_payload
        assert cli_registry_default_payload["active_state"] == "the registry-declared active state", (
            cli_registry_default_payload
        )
        assert cli_registry_default_payload["active_state_source"].startswith("registry:"), cli_registry_default_payload
        assert cli_registry_default_payload["resolved_active_state"] == str(state_file), cli_registry_default_payload
        assert "sink_activation" not in cli_registry_default_payload
        assert "Explore Graph" not in cli_registry_default_payload["task_body"]
        assert "Generic Kanban" not in cli_registry_default_payload["task_body"]
        assert cli_registry_default_payload["expanded_prompt_command"] == (
            "loopx heartbeat-prompt --full --goal-id public-heartbeat-goal "
            "--agent-id codex-main-control --agent-scope 'primary review and coordination'"
        ), cli_registry_default_payload
        assert "loopx heartbeat-prompt --compact --goal-id public-heartbeat-goal" in (
            cli_registry_default_payload["task_body"]
        ), cli_registry_default_payload
        assert "--active-state" not in cli_registry_default_payload["task_body"], cli_registry_default_payload
        cli_registry_thin_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "heartbeat-prompt",
                "--goal-id",
                GOAL_ID,
                "--thin",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert cli_registry_thin_result.returncode != 0, cli_registry_thin_result.stdout
        cli_registry_thin_error = json.loads(cli_registry_thin_result.stdout)
        assert "identity-aware peer heartbeat prompt required" in cli_registry_thin_error["error"], (
            cli_registry_thin_error
        )
        assert "heartbeat-prompt --thin" in cli_registry_thin_error["error"], cli_registry_thin_error

        cli_registry_thin_json = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "heartbeat-prompt",
                "--goal-id",
                GOAL_ID,
                "--thin",
                "--agent-id",
                "codex-main-control",
                "--agent-scope",
                "primary review and coordination",
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        cli_registry_thin_payload = json.loads(cli_registry_thin_json.stdout)
        assert cli_registry_thin_payload["schema_version"] == HEARTBEAT_AGENT_INPUT_SCHEMA_VERSION
        assert cli_registry_thin_payload["agent_id"] == "codex-main-control", cli_registry_thin_payload
        assert "active_state" not in cli_registry_thin_payload, cli_registry_thin_payload
        assert "resolved_active_state" not in cli_registry_thin_payload, cli_registry_thin_payload
        assert "Advance `public-heartbeat-goal` from the registry-declared active state." in (
            cli_registry_thin_payload["task_body"]
        ), cli_registry_thin_payload
        assert "--active-state" not in cli_registry_thin_payload["task_body"], cli_registry_thin_payload

        cli_scoped_json = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "heartbeat-prompt",
                "--goal-id",
                GOAL_ID,
                "--compact",
                "--agent-id",
                "codex-side-bypass",
                "--agent-scope",
                "control-plane coordination and todo claim ergonomics",
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        cli_scoped_payload = json.loads(cli_scoped_json)
        assert cli_scoped_payload["agent_id"] == "codex-side-bypass", cli_scoped_payload
        assert cli_scoped_payload["agent_role"] == "peer-agent", cli_scoped_payload
        assert "primary_agent" not in cli_scoped_payload, cli_scoped_payload
        assert cli_scoped_payload["agent_scopes"] == [
            "control-plane coordination and todo claim ergonomics"
        ], cli_scoped_payload
        assert cli_scoped_payload["registered_agents"] == [
            "codex-main-control",
            "codex-side-bypass",
        ], cli_scoped_payload
        assert "todo claim --goal-id public-heartbeat-goal" in cli_scoped_payload["task_body"], cli_scoped_payload

        cli_profile_scoped_json = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "heartbeat-prompt",
                "--goal-id",
                GOAL_ID,
                "--thin",
                "--agent-id",
                "codex-side-bypass",
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        cli_profile_scoped_payload = json.loads(cli_profile_scoped_json)
        assert cli_profile_scoped_payload["agent_id"] == "codex-side-bypass", cli_profile_scoped_payload
        assert "agent_scopes" not in cli_profile_scoped_payload, cli_profile_scoped_payload
        assert "agent_scope_source" not in cli_profile_scoped_payload, cli_profile_scoped_payload
        assert "thin_prompt_command" not in cli_profile_scoped_payload, cli_profile_scoped_payload
        assert "productization showcase docs lane" in normalized(cli_profile_scoped_payload["task_body"]), (
            cli_profile_scoped_payload
        )

        cli_unknown_scoped = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "heartbeat-prompt",
                "--goal-id",
                GOAL_ID,
                "--agent-id",
                "unregistered-agent",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert cli_unknown_scoped.returncode != 0, cli_unknown_scoped.stdout
        cli_unknown_payload = json.loads(cli_unknown_scoped.stdout)
        assert "is not registered" in cli_unknown_payload["error"], cli_unknown_payload

        legacy_registry_path = project / ".loopx" / "legacy-registry.json"
        legacy_registry_path.write_text(
            json.dumps(
                {
                    "goals": [
                        {
                            "id": GOAL_ID,
                            "domain": "legacy-smoke",
                            "status": "active",
                            "repo": str(project),
                            "state_file": f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md",
                            "adapter": {"kind": "generic_project_goal_v0", "status": "connected"},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        cli_legacy_scoped = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "--registry",
                str(legacy_registry_path),
                "heartbeat-prompt",
                "--goal-id",
                GOAL_ID,
                "--agent-id",
                "codex-side-bypass",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert cli_legacy_scoped.returncode != 0, cli_legacy_scoped.stdout
        cli_legacy_payload = json.loads(cli_legacy_scoped.stdout)
        assert "Register this peer identity first" in cli_legacy_payload["error"], cli_legacy_payload
        assert "--registered-agent codex-side-bypass" in cli_legacy_payload["error"], cli_legacy_payload

    with tempfile.TemporaryDirectory() as fallback_tmp:
        root = Path(fallback_tmp)
        project = root / "project"
        runtime = root / "runtime"
        state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
        local_registry_path = project / ".loopx" / "registry.json"
        global_registry_path = runtime / "registry.global.json"
        state_file.parent.mkdir(parents=True)
        local_registry_path.parent.mkdir(parents=True)
        global_registry_path.parent.mkdir(parents=True)
        state_file.write_text("# Active State\n", encoding="utf-8")
        local_registry_path.write_text(
            json.dumps(
                {
                    "goals": [
                        {
                            "id": "other-local-goal",
                            "domain": "smoke",
                            "status": "active",
                            "repo": str(project),
                            "state_file": ".codex/goals/other-local-goal/ACTIVE_GOAL_STATE.md",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        global_registry_path.write_text(
            json.dumps(
                {
                    "goals": [
                        {
                            "id": GOAL_ID,
                            "domain": "smoke",
                            "status": "active",
                            "repo": str(project),
                            "state_file": f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md",
                            "coordination": {
                                "registered_agents": ["codex-side-bypass"],
                                "agent_model": "peer_v1",
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)
        cli_global_fallback_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "--runtime-root",
                str(runtime),
                "heartbeat-prompt",
                "--goal-id",
                GOAL_ID,
                "--thin",
            ],
            cwd=project,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert cli_global_fallback_result.returncode != 0, cli_global_fallback_result.stdout
        cli_global_fallback_error = json.loads(cli_global_fallback_result.stdout)
        assert "identity-aware peer heartbeat prompt required" in cli_global_fallback_error["error"], (
            cli_global_fallback_error
        )
        assert "--agent-id codex-side-bypass" in cli_global_fallback_error["error"], (
            cli_global_fallback_error
        )

        cli_global_fallback_json = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "--runtime-root",
                str(runtime),
                "heartbeat-prompt",
                "--goal-id",
                GOAL_ID,
                "--thin",
                "--agent-id",
                "codex-side-bypass",
                "--agent-scope",
                "control-plane coordination",
            ],
            cwd=project,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        cli_global_fallback_payload = json.loads(cli_global_fallback_json.stdout)
        assert cli_global_fallback_payload["schema_version"] == HEARTBEAT_AGENT_INPUT_SCHEMA_VERSION
        assert cli_global_fallback_payload["agent_id"] == "codex-side-bypass", cli_global_fallback_payload
        assert "active_state_source" not in cli_global_fallback_payload, cli_global_fallback_payload
        assert "resolved_active_state" not in cli_global_fallback_payload, cli_global_fallback_payload
        cli_global_agent_json = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "--runtime-root",
                str(runtime),
                "heartbeat-prompt",
                "--goal-id",
                GOAL_ID,
                "--compact",
                "--agent-id",
                "codex-side-bypass",
                "--agent-scope",
                "control-plane coordination",
            ],
            cwd=project,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        cli_global_agent_payload = json.loads(cli_global_agent_json.stdout)
        assert cli_global_agent_payload["active_state_source"] == f"registry:{global_registry_path}", (
            cli_global_agent_payload
        )
        assert cli_global_agent_payload["registered_agents"] == ["codex-side-bypass"], (
            cli_global_agent_payload
        )

    cli_markdown = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "heartbeat-prompt",
            "--goal-id",
            GOAL_ID,
            "--active-state",
            str(ACTIVE_STATE),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "# Heartbeat Automation Prompt" in cli_markdown, cli_markdown
    assert "Copy this thin task body into a Codex App heartbeat automation." in cli_markdown, cli_markdown
    assert str(ACTIVE_STATE) in cli_markdown, cli_markdown
    print("heartbeat-prompt-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
