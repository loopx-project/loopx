"""Reward Memory lifecycle hooks for quota CLI projections."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..capabilities.agent_turn_recall import (
    run_configured_agent_turn_recall_fail_open,
)
from ..capabilities.reward_memory.codex_app_outcome import (
    stage_codex_app_turn_outcome_candidate_fail_open,
    run_staged_codex_app_turn_outcome_ingest_fail_open,
)
from ..control_plane.quota.settlement import read_heartbeat_settlement


def stage_reward_memory_outcome_candidate(
    payload: dict[str, Any],
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    todo_id: str,
    turn_instance_id: str,
    reflection_json: str,
    validation_workspace: Path,
) -> None:
    """Attach a privately staged outcome candidate after a valid refresh."""

    settlement_identity = payload.get("settlement_identity")
    settlement_identity = (
        settlement_identity if isinstance(settlement_identity, Mapping) else {}
    )
    state = payload.get("state")
    state = state if isinstance(state, Mapping) else {}
    payload["reward_memory_outcome_candidate"] = (
        stage_codex_app_turn_outcome_candidate_fail_open(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            todo_id=todo_id,
            turn_instance_id=turn_instance_id,
            effect_id=str(settlement_identity.get("effect_id") or ""),
            state_file=Path(str(state.get("path") or "")),
            validation_workspace=validation_workspace,
            reflection_json=reflection_json,
            observed_at=str(payload.get("generated_at") or ""),
        )
    )


def reward_memory_candidate_details(payload: Mapping[str, Any]) -> dict[str, str]:
    candidate = payload.get("reward_memory_outcome_candidate")
    candidate = candidate if isinstance(candidate, Mapping) else {}
    return {
        "reward_memory_candidate_id": str(candidate.get("candidate_id") or ""),
        "reward_memory_candidate_status": str(candidate.get("status") or ""),
        "reward_memory_reflection_digest": str(
            candidate.get("reflection_digest") or ""
        ),
    }


def attach_reward_memory_ingest_after_spend(
    payload: dict[str, Any],
    *,
    execute: bool,
    runtime_root: Path,
    registry_path: Path,
    goal_id: str,
    agent_id: str,
    todo_id: str | None,
    turn_instance_id: str,
    replan_obligation_id: str | None,
    goal_ref: Mapping[str, Any] | None = None,
) -> None:
    if not execute or payload.get("ok") is not True:
        return
    readback = read_heartbeat_settlement(
        runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        todo_id=todo_id,
        turn_instance_id=turn_instance_id,
        replan_obligation_id=replan_obligation_id,
        registry_path=registry_path,
        goal_ref=goal_ref,
    )
    identity = readback.identity.value if readback else None
    writeback_event = readback.writeback_event if readback else None
    details_value = (
        writeback_event.get("details") if isinstance(writeback_event, Mapping) else None
    )
    details: Mapping[str, Any] = details_value if isinstance(details_value, Mapping) else {}
    candidate_id = str(details.get("reward_memory_candidate_id") or "").strip()
    if not candidate_id or identity is None or not identity.todo_id:
        return
    payload["reward_memory_ingest"] = (
        run_staged_codex_app_turn_outcome_ingest_fail_open(
            registry_path=registry_path,
            goal_id=identity.goal_id,
            agent_id=identity.agent_id,
            todo_id=identity.todo_id,
            turn_instance_id=identity.turn_instance_id,
            effect_id=identity.effect_id,
            candidate_id=candidate_id,
            writeback_appended=readback.writeback.failure is None,
            spend_appended=readback.spend.failure is None,
        )
    )


def attach_reward_memory_recall_after_should_run(
    payload: dict[str, Any],
    *,
    quota_command: str,
    heartbeat_turn_id: str | None,
    registry_path: Path,
    goal_id: str,
    agent_id: str,
) -> None:
    heartbeat_receipt = payload.get("heartbeat_receipt")
    if (
        quota_command != "should-run"
        or not heartbeat_turn_id
        or payload.get("ok") is not True
        or payload.get("should_run") is not True
        or not isinstance(heartbeat_receipt, Mapping)
        or heartbeat_receipt.get("status") not in {"committed", "replayed"}
    ):
        return
    payload["reward_memory_recall"] = run_configured_agent_turn_recall_fail_open(
        registry_path=registry_path,
        goal_id=goal_id,
        agent_id=agent_id,
        quota_decision=payload,
        turn_instance_id=heartbeat_turn_id,
        execute=True,
    )
