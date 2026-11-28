"""Read the original monitor poll and project its receipt-owned continuation."""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import shlex
from typing import cast

from ...turn_identity import normalize_turn_instance_id
from ..todos.contract import normalize_todo_id

Payload = dict[str, object]
PayloadView = Mapping[str, object]

QUOTA_MONITOR_POLL_CLASSIFICATION = "quota_monitor_poll"


def _find_monitor_poll_turn(
    index_path: Path,
    *,
    goal_id: str,
    agent_id: str | None,
    turn_instance_id: str,
    todo_id: str | None = None,
    target_key: str | None = None,
    goal_ref: PayloadView | None = None,
) -> Payload | None:
    normalized_todo_id = normalize_todo_id(todo_id) if todo_id else None
    normalized_target_key = (str(target_key or "").strip() or None)
    expected_goal_ref = {str(key): value for key, value in goal_ref.items()} if goal_ref is not None else None
    try:
        lines = index_path.read_text(encoding="utf-8").split("\n")
    except OSError:
        return None
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if (
            row.get("classification") == QUOTA_MONITOR_POLL_CLASSIFICATION
            and str(row.get("goal_id") or "") == goal_id
            and str(row.get("agent_id") or "") == agent_id
            and str(row.get("turn_instance_id") or "") == turn_instance_id
            and (
                row.get("goal_ref") == expected_goal_ref
                if expected_goal_ref is not None
                else "goal_ref" not in row
            )
            and (
                normalized_todo_id is None
                or normalize_todo_id(row.get("todo_id")) == normalized_todo_id
            )
            and (
                normalized_target_key is None
                or (str(row.get("target_key") or "").strip() or None) == normalized_target_key
            )
        ):
            return row
    return None


def find_quota_monitor_poll_turn(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str | None,
    turn_instance_id: str,
    todo_id: str | None = None,
    target_key: str | None = None,
    goal_ref: PayloadView | None = None,
) -> Payload | None:
    """Return the latest matching monitor observation for one heartbeat turn."""

    normalized_turn_id = normalize_turn_instance_id(turn_instance_id)
    if not normalized_turn_id:
        return None
    return _find_monitor_poll_turn(
        runtime_root / "goals" / goal_id / "runs" / "index.jsonl",
        goal_id=goal_id,
        agent_id=agent_id,
        turn_instance_id=normalized_turn_id,
        todo_id=todo_id,
        target_key=target_key,
        goal_ref=goal_ref,
    )


def attach_auxiliary_monitor_settlement_resume(
    result: dict[str, object],
    *,
    runtime_root: Path | None,
    registry_path: Path | None,
    goal_id: str,
    agent_id: str | None,
    todo_id: str | None,
    turn_instance_id: str | None,
    available_capabilities: object,
    goal_ref: Mapping[str, object] | None,
) -> None:
    # The open-work readback may now select another Todo. Render the
    # original receipt's closeout separately; never borrow that selection
    # to construct this Turn's refresh/spend commands.
    from ..agents.capability_gate import runtime_capabilities_for_cli_projection
    from .settlement import (
        read_heartbeat_settlement,
        attach_settlement_progress,
        build_turn_scoped_cli_settlement_plan,
    )

    readback = read_heartbeat_settlement(
        cast(Path, runtime_root), goal_id=goal_id, agent_id=agent_id, todo_id=todo_id,
        turn_instance_id=turn_instance_id, registry_path=registry_path, goal_ref=goal_ref,
    )
    if readback is None or readback.identity.value is None:
        raise RuntimeError("auxiliary monitor receipt lost its original settlement readback")
    attach_settlement_progress(
        result, readback, registry_path=registry_path, runtime_root=runtime_root, goal_ref=goal_ref,
    )
    identity = readback.identity.value
    prefix = "loopx" + (f" --registry {shlex.quote(str(registry_path))}" if registry_path is not None else "")
    prefix += f" --runtime-root {shlex.quote(str(runtime_root))}"
    scoped_args = "".join(
        f" --available-capability {shlex.quote(capability)}"
        for capability in runtime_capabilities_for_cli_projection(available_capabilities)
    )
    progress = readback.progress
    plan = build_turn_scoped_cli_settlement_plan(
        goal_id=identity.goal_id, agent_id=identity.agent_id, todo_id=identity.todo_id,
        turn_instance_id=identity.turn_instance_id, replan_obligation_id=identity.replan_obligation_id,
        command_prefix=prefix, scoped_cli_args=scoped_args, lifecycle_actor_args="",
        quota_spend_source=str(progress["quota_spend_source"]), goal_ref=goal_ref, composed_settlement=True,
    )
    raw_steps = plan.as_dict().get("ordered_steps")
    ordered_steps = [step for step in raw_steps if isinstance(step, dict)] if isinstance(raw_steps, list) else []
    result["settlement_resume"] = {
        "schema_version": "auxiliary_monitor_settlement_resume_v0",
        "identity": identity.as_dict(),
        "progress_ref": "$.settlement_progress",
        "next_step": next((step for step in ordered_steps if step.get("kind") == progress["next_step"]), None),
        "grants_new_delivery": False,
    }
    # A later discovery projection is not a second settlement plan. The
    # top-level resume/readback above owns this original Turn's closeout.
    raw_after = result.get("after")
    after = raw_after if isinstance(raw_after, Mapping) else {}
    contract = after.get("interaction_contract")
    cli = contract.get("cli_channel") if isinstance(contract, Mapping) else None
    selected = after.get("selected_todo")
    selected_id = selected.get("todo_id") if isinstance(selected, Mapping) else None
    if isinstance(cli, dict) and selected_id != identity.todo_id:
        cli.pop("settlement_plan", None)
        cli.pop("next_cli_actions", None)
        cli.update(spend_allowed_now=False, spend_after_validation=False, settlement_resume_ref="$.settlement_resume")
