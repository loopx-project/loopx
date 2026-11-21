"""Durable readback probes and receipt helpers for `loopx turn run-once`."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from ..control_plane.quota.settlement import (
    SettlementIdentity,
    SettlementStepKind,
    read_heartbeat_settlement,
)
from ..control_plane.todos.durable_completion import read_durable_completion_evidence
from ..control_plane.todos.path_resolution import resolve_todo_state_path

EXACT_SETTLEMENT_READBACK_NOT_FOUND = (
    "exact settlement readback unexpectedly returned not-found"
)


def require_settlement_effect_ref(
    effect_ref: str,
    step_kind: SettlementStepKind,
    *,
    settlement_identity: SettlementIdentity,
) -> None:
    expected = f"{settlement_identity.effect_id}#{step_kind.value}"
    if effect_ref != expected:
        raise ValueError(
            f"{step_kind.value} effect ref does not match Turn identity"
        )


def append_turn_settlement_event(
    effect_payload: Mapping[str, object],
    *,
    event_kind: str,
    status: str,
    details: Mapping[str, object],
    goal_id: str,
    registry_path: Path,
    runtime_root: Path,
    runtime_root_arg: str | None,
    goal_ref: Mapping[str, object] | None,
    settlement_identity: SettlementIdentity,
    append_cli_rollout_event: Callable[..., dict[str, object]],
) -> None:
    event_payload = {
        **dict(effect_payload),
        "ok": True,
        "goal_id": goal_id,
        "runtime_root": str(runtime_root),
    }
    append_cli_rollout_event(
        event_payload,
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
        event_kind=event_kind,
        goal_ref=goal_ref,
        agent_id=settlement_identity.agent_id,
        todo_id=settlement_identity.todo_id,
        run_id=settlement_identity.turn_instance_id,
        status=status,
        summary=f"Turn settlement {event_kind} recorded",
        details={
            **dict(details),
            "settlement_effect_id": settlement_identity.effect_id,
            **(
                {"replan_obligation_id": settlement_identity.replan_obligation_id}
                if settlement_identity.replan_obligation_id
                else {}
            ),
        },
        idempotency_fields=[
            "goal_id",
            "event_kind",
            "agent_id",
            *(("todo_id",) if settlement_identity.todo_id else ()),
            "run_id",
            *(("goal_ref",) if goal_ref is not None else ()),
            *(("status",) if event_kind == "todo_complete" else ()),
        ],
    )
    if event_payload.get("rollout_event_log_error"):
        raise OSError(f"failed to persist {event_kind} settlement receipt")


def finish_turn_explore_delivery(
    refresh: dict[str, object],
    *,
    registry_path: Path,
    runtime_root: Path,
    settlement_identity: SettlementIdentity,
) -> dict[str, object]:
    if refresh.get("explore_result") is None or refresh.get("ok") is not True:
        return refresh
    from ..capabilities.explore.result_writeback import deliver_result_attachment

    delivery = deliver_result_attachment(
        payload=refresh,
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=settlement_identity.goal_id,
        agent_id=settlement_identity.agent_id,
        todo_id=settlement_identity.todo_id,
        turn_instance_id=settlement_identity.turn_instance_id,
    )
    refresh["explore_result_delivery"] = delivery
    if not delivery["ok"]:
        refresh["ok"] = False
        refresh["error"] = (
            "Primary writeback committed; retry this original Turn to deliver Explore evidence"
        )
    return refresh


def build_turn_settlement_resolvers(
    *,
    goal_id: str,
    registry_path: Path,
    runtime_root: Path,
    goal_ref: Mapping[str, object] | None,
    selected_todo: Mapping[str, object],
    settlement_identity: SettlementIdentity,
    require_effect_ref: Callable[[str, SettlementStepKind], None],
    append_settlement_event: Callable[..., None],
    finish_explore_delivery: Callable[[dict[str, object]], dict[str, object]],
) -> tuple[
    Callable[[], dict[str, object] | None],
    Callable[[str], dict[str, object]],
    Callable[[str], dict[str, object]],
    Callable[[str], dict[str, object]],
]:
    def completion_readback() -> dict[str, object] | None:
        todo_id = str(selected_todo.get("todo_id") or "")
        if not todo_id:
            return None
        try:
            _state_project, state_file = resolve_todo_state_path(
                registry_path=registry_path,
                goal_id=goal_id,
                project=None,
                state_file=None,
            )
            return read_durable_completion_evidence(
                state_file,
                todo_id=todo_id,
                registry_path=registry_path,
                goal_id=goal_id,
                runtime_root=runtime_root,
                operation="outcome",
                completion_turn_key=settlement_identity.turn_instance_id,
            )
        except (OSError, ValueError):
            return None

    def terminal_completion_readback() -> dict[str, object] | None:
        todo_id = str(selected_todo.get("todo_id") or "")
        if not todo_id:
            raise ValueError("terminal completion requires one selected Todo")
        _state_project, state_file = resolve_todo_state_path(
            registry_path=registry_path,
            goal_id=goal_id,
            project=None,
            state_file=None,
        )
        readback = read_durable_completion_evidence(
            state_file,
            todo_id=todo_id,
            registry_path=registry_path,
            goal_id=goal_id,
            runtime_root=runtime_root,
            operation="terminal",
            completion_turn_key=settlement_identity.turn_instance_id,
        )
        if readback["kind"] == "absent":
            return None
        completion = readback.get("completion")
        if not isinstance(completion, dict):
            raise ValueError(
                "terminal completion readback is missing its completion"
            )
        return completion

    def writeback_resolver(effect_ref: str) -> dict[str, object]:
        try:
            require_effect_ref(
                effect_ref,
                SettlementStepKind.DURABLE_WRITEBACK,
            )
            readback = read_heartbeat_settlement(
                runtime_root,
                goal_id=settlement_identity.goal_id,
                agent_id=settlement_identity.agent_id,
                todo_id=settlement_identity.todo_id,
                turn_instance_id=settlement_identity.turn_instance_id,
                replan_obligation_id=settlement_identity.replan_obligation_id,
                registry_path=registry_path,
                goal_ref=goal_ref,
            )
            if readback is None:
                raise RuntimeError(EXACT_SETTLEMENT_READBACK_NOT_FOUND)
            run = readback.writeback_run
            event = readback.writeback_event
            if run is None and event is None:
                return {"kind": "absent"}
            if run is None:
                return {
                    "kind": "unknown",
                    "reason": "refresh-state receipt has no durable run",
                }
            delivered = finish_explore_delivery(
                {
                    **run,
                    "ok": True,
                    "appended": True,
                    "idempotent_replay": True,
                    "settlement_identity": settlement_identity.as_dict(),
                }
            )
            if delivered.get("ok") is not True:
                return {
                    "kind": "unknown",
                    "reason": "original Explore delivery is still pending",
                    "explore_result_delivery": delivered.get(
                        "explore_result_delivery"
                    ),
                }
            if event is None:
                append_settlement_event(
                    run,
                    event_kind="refresh_state",
                    status="receipt_repaired",
                    details={"command": "turn recovery probe"},
                )
            committed: dict[str, object] = {
                "ok": True,
                "appended": True,
                "effect_ref": effect_ref,
                "classification": run.get("classification"),
            }
            completion = completion_readback()
            if completion is not None:
                committed["completion"] = completion
            return {"kind": "committed", "payload": committed}
        except (OSError, ValueError):
            return {"kind": "unknown", "reason": "writeback readback failed"}

    def spend_resolver(effect_ref: str) -> dict[str, object]:
        try:
            require_effect_ref(effect_ref, SettlementStepKind.QUOTA_SPEND)
            readback = read_heartbeat_settlement(
                runtime_root,
                goal_id=settlement_identity.goal_id,
                agent_id=settlement_identity.agent_id,
                todo_id=settlement_identity.todo_id,
                turn_instance_id=settlement_identity.turn_instance_id,
                replan_obligation_id=settlement_identity.replan_obligation_id,
                registry_path=registry_path,
                goal_ref=goal_ref,
            )
            if readback is None:
                raise RuntimeError(EXACT_SETTLEMENT_READBACK_NOT_FOUND)
            run = readback.spend_run
            event = readback.spend_event
            if run is not None and run.get("effect_ref") != effect_ref:
                run = None
            if run is None and event is None:
                return {"kind": "absent"}
            if run is None:
                return {
                    "kind": "unknown",
                    "reason": "quota receipt has no durable spend run",
                }
            if event is None:
                append_settlement_event(
                    run,
                    event_kind="quota_spend",
                    status="receipt_repaired",
                    details={"command": "turn recovery probe"},
                )
            return {
                "kind": "committed",
                "payload": {
                    "ok": True,
                    "appended": True,
                    "effect_ref": effect_ref,
                    "mode": "spend-slot",
                },
            }
        except (OSError, ValueError):
            return {"kind": "unknown", "reason": "quota readback failed"}

    def terminal_closeout_resolver(effect_ref: str) -> dict[str, object]:
        try:
            require_effect_ref(
                effect_ref,
                SettlementStepKind.TERMINAL_CLOSEOUT,
            )
            readback = read_heartbeat_settlement(
                runtime_root,
                goal_id=settlement_identity.goal_id,
                agent_id=settlement_identity.agent_id,
                todo_id=settlement_identity.todo_id,
                turn_instance_id=settlement_identity.turn_instance_id,
                replan_obligation_id=settlement_identity.replan_obligation_id,
                registry_path=registry_path,
                goal_ref=goal_ref,
            )
            if readback is None:
                raise RuntimeError(EXACT_SETTLEMENT_READBACK_NOT_FOUND)
            event = readback.completion_event
            completion = terminal_completion_readback()
            if event is None and completion is None:
                return {"kind": "absent"}
            if (
                completion is None
                or completion.get("continuation") != "no_followup"
            ):
                return {
                    "kind": "unknown",
                    "reason": "Todo state does not prove terminal no-followup",
                }
            if event is None:
                append_settlement_event(
                    {"ok": True, "appended": True},
                    event_kind="todo_complete",
                    status="terminal_no_followup",
                    details={
                        "command": "turn recovery probe",
                        "no_followup": True,
                    },
                )
            return {
                "kind": "committed",
                "payload": {
                    "ok": True,
                    "appended": True,
                    "effect_ref": effect_ref,
                    "completion": completion,
                },
            }
        except (OSError, ValueError):
            return {"kind": "unknown", "reason": "closeout readback failed"}

    return (
        completion_readback,
        writeback_resolver,
        spend_resolver,
        terminal_closeout_resolver,
    )
