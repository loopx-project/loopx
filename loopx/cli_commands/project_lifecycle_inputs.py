"""Typed inline and file-backed input codecs for project lifecycle CLI arguments.

These helpers translate CLI arguments into the typed packets the refresh path
already accepts. They stay separate from the command owner so the registration
and dispatch module keeps one cohesive responsibility.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import stat

from ..control_plane.goals.first_party_host_admission import (
    capture_first_party_host_goal_ref,
)
from ..control_plane.goals.source_session_registry_state import exact_goal_ref
from ..control_plane.goals.vision_checkpoint import prepare_vision_refresh
from ..control_plane.work_items.progress_observation import ProgressResultClass

INLINE_VISION_FIELDS = {
    "vision_summary": "vision_summary",
    "vision_role_scope": "role_scope",
    "vision_acceptance": "acceptance_summary",
    "vision_advancement_policy": "advancement_policy",
    "vision_replan_trigger": "replan_trigger_summary",
    "vision_dreaming_policy": "dreaming_policy",
    "vision_last_patch": "last_patch_summary",
}


class ProgressObservationArgumentError(ValueError):
    """A pure argument rejection, before refresh dispatch or authority writes."""

    code = "progress_observation_invalid_arguments"


@dataclass(frozen=True, slots=True)
class RefreshStateInputs:
    outcome_update: dict[str, object] | None
    agent_vision_packet: dict[str, object] | None
    merge_agent_vision_patch: bool
    explore_result: dict[str, object] | None
    progress_observation: dict[str, object] | None
    reward_memory_reflection_json: str
    usage_measurement: dict[str, object] | None
    goal_ref: dict[str, str] | None


def reject_non_standard_json_constant(
    name: str, *, flag: str = "--usage-json"
) -> object:
    # json.loads would otherwise accept NaN/Infinity/-Infinity, which json.dump
    # then re-emits as non-standard JSON that breaks strict ledger consumers.
    raise ValueError(
        f"{flag} must be strict JSON; non-standard constant {name} is not allowed"
    )


def load_json_object_or_file(
    raw: str | Path, *, flag: str, max_bytes: int = 262144
) -> dict[str, object]:
    """Load a strict JSON object from inline JSON or a UTF-8 file path."""

    def parse_strict_object(text: str) -> dict[str, object]:
        try:
            parsed = json.loads(
                text,
                parse_constant=lambda name: reject_non_standard_json_constant(
                    name, flag=flag
                ),
            )
        except json.JSONDecodeError as exc:
            raise ValueError(f"{flag} must be valid JSON: {exc.msg}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{flag} must be a JSON object")
        return parsed

    if isinstance(raw, str) and raw.lstrip().startswith(("{", "[")):
        if len(raw.encode("utf-8")) > max_bytes:
            raise ValueError(f"{flag} exceeds {max_bytes // 1024} KiB")
        return parse_strict_object(raw)

    candidate: str | Path = (
        raw[1:] if isinstance(raw, str) and raw.startswith("@") else raw
    )
    if isinstance(candidate, str) and not candidate.strip():
        raise ValueError(f"{flag} must be a JSON object or valid file path")
    try:
        path = Path(candidate).expanduser()
        metadata = path.stat()
    except (OSError, ValueError) as exc:
        raise ValueError(f"{flag} must be a JSON object or valid file path") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{flag} must be a JSON object or valid file path")
    if metadata.st_size > max_bytes:
        raise ValueError(f"{flag} exceeds {max_bytes // 1024} KiB")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"{flag} must be a JSON object or valid file path") from exc
    return parse_strict_object(text)


def split_explore_result_input(vision_packet, result_path, *, scope_context=None):
    """Transport explicit attachments; the capability owns validation/conflicts.

    Keep the generic vision packet separate.
    """
    attachments = []
    if isinstance(vision_packet, dict) and "explore_result" in vision_packet:
        vision_packet = dict(vision_packet)
        attachments.append(vision_packet.pop("explore_result"))
    if result_path is not None:
        attachments.append(
            load_json_object_or_file(result_path, flag="--explore-result-json")
        )
    if not attachments:
        return vision_packet, None
    from ..capabilities.explore.result_writeback import normalize_result_attachment

    other = {"other_attachment": attachments[1]} if len(attachments) == 2 else {}
    return vision_packet, normalize_result_attachment(
        attachments[0], vision_packet=vision_packet, scope_context=scope_context, **other
    )


def inline_agent_vision_packet(args: argparse.Namespace) -> dict[str, object] | None:
    patch = {
        field: str(value).strip()
        for attr, field in INLINE_VISION_FIELDS.items()
        for value in [getattr(args, attr, None)]
        if str(value or "").strip()
    }
    todo_delta = [
        str(item or "").strip()
        for item in (getattr(args, "vision_todo_delta", None) or [])
        if str(item or "").strip()
    ]
    state = str(getattr(args, "vision_state", None) or "").strip()
    if not patch and not todo_delta and not state:
        return None
    if not str(getattr(args, "agent_id", None) or "").strip():
        raise ValueError("inline agent vision requires --agent-id")
    if not patch:
        raise ValueError("inline agent vision requires at least one --vision-* patch field")
    packet: dict[str, object] = {
        "schema_version": "goal_vision_replan_contract_v0",
        "vision_patch": patch,
        "todo_delta": todo_delta,
    }
    if state:
        packet["state"] = state
    return packet


def inline_progress_observation(
    args: argparse.Namespace,
) -> dict[str, object] | None:
    fields = {
        "work_item_id": getattr(args, "progress_work_item_id", None),
        "surface_id": getattr(args, "progress_surface_id", None),
        "hypothesis_id": getattr(args, "progress_hypothesis_id", None),
        "probe_kind": getattr(args, "progress_probe_kind", None),
        "result_class": getattr(args, "progress_result_class", None),
        "blocker_id": getattr(args, "progress_blocker_id", None),
        "coverage_scope_id": getattr(args, "progress_coverage_scope_id", None),
        "coverage_complete": getattr(args, "progress_coverage_complete", None),
    }
    evidence_ids = list(getattr(args, "progress_evidence_ids", None) or [])
    if not any(value is not None for value in fields.values()) and not evidence_ids:
        return None
    if not fields["result_class"]:
        raise ProgressObservationArgumentError("typed progress observation requires --progress-result-class")
    if fields["result_class"] in {
        ProgressResultClass.EXPLORATION_EXHAUSTED.value,
        ProgressResultClass.NO_FOLLOWUP.value,
    } and not fields["coverage_scope_id"]:
        raise ProgressObservationArgumentError(
            f"--progress-result-class {fields['result_class']} requires "
            "--progress-coverage-scope-id"
        )
    if (
        fields["result_class"] == ProgressResultClass.EXPLORATION_EXHAUSTED.value
        and fields["coverage_complete"] is not True
    ):
        raise ProgressObservationArgumentError(
            "--progress-result-class exploration_exhausted requires "
            "--progress-coverage-complete"
        )
    return {
        "schema_version": "typed_progress_observation_v0",
        **{key: value for key, value in fields.items() if value is not None},
        "evidence_ids": evidence_ids,
    }


def parse_refresh_state_inputs(
    args: argparse.Namespace,
    *,
    fmt: str,
    registry_path: Path,
) -> RefreshStateInputs:
    """Validate and decode CLI refresh-state inputs before any authority write."""
    if getattr(args, "settlement_receipt", False) and (not args.settle_turn or fmt != "json"):
        raise ValueError("--settlement-receipt requires --settle-turn and --format json")
    if getattr(args, "settle_turn", False) and not (
        args.agent_id and args.turn_instance_id and bool(args.todo_id) != bool(args.replan_obligation_id)
    ):
        raise ValueError(
            "--settle-turn requires --agent-id, --turn-instance-id and exactly one Todo/obligation binding"
        )

    outcome_update: dict[str, object] | None = None
    outcome_input = getattr(args, "goal_outcome_update_json", None)
    if outcome_input is not None:
        if not args.agent_id:
            raise ValueError("--goal-outcome-update-json requires the bound --agent-id")
        outcome_update = load_json_object_or_file(
            outcome_input, flag="--goal-outcome-update-json"
        )
        if (
            set(outcome_update)
            != {"operation_id", "expected_provider_revision", "refinement"}
            or not isinstance(outcome_update["refinement"], Mapping)
        ):
            raise ValueError(
                "outcome update requires operation_id, expected_provider_revision and refinement"
            )

    inline_vision_packet = inline_agent_vision_packet(args)
    vision_input = getattr(args, "agent_vision_json", None)
    if vision_input is not None and inline_vision_packet:
        raise ValueError(
            "--agent-vision-json cannot be combined with inline --vision-* fields"
        )
    agent_vision_packet: dict[str, object] | None = None
    merge_agent_vision_patch = False
    if vision_input is not None:
        agent_vision_packet = load_json_object_or_file(
            vision_input, flag="--agent-vision-json"
        )
    elif inline_vision_packet:
        agent_vision_packet = inline_vision_packet
        merge_agent_vision_patch = True

    agent_vision_packet, explore_result = split_explore_result_input(
        agent_vision_packet,
        getattr(args, "explore_result_json", None),
        scope_context=dict(
            registry_path=registry_path,
            runtime_root_override=args.runtime_root,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            todo_id=args.todo_id,
            turn_instance_id=args.turn_instance_id,
        ),
    )
    if agent_vision_packet is not None and (
        outcome_update is not None or not merge_agent_vision_patch
    ):
        # Reject malformed complete input before any history lock or outcome
        # refinement. Inline patches without an outcome update still merge
        # with current history first. Reuse the typed owner; this preflight
        # cannot authorize a transition or replace the original packet.
        prepare_vision_refresh(
            agent_vision_packet,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            existing_agent_vision=None,
            merge_patch=False,
            require_path_delta_for_durable_change=False,
            allow_terminal_stride_path=True,
        )

    progress_observation = inline_progress_observation(args)
    reward_memory_reflection_json = str(
        getattr(args, "reward_memory_reflection_json", None) or ""
    ).strip()
    if reward_memory_reflection_json and not (
        args.agent_id
        and getattr(args, "todo_id", None)
        and getattr(args, "turn_instance_id", None)
    ):
        raise ValueError(
            "--reward-memory-reflection-json requires --agent-id, "
            "--todo-id, and --turn-instance-id"
        )

    usage_measurement: dict[str, object] | None = None
    if getattr(args, "usage_json", None):
        loaded_usage = json.loads(
            args.usage_json,
            parse_constant=reject_non_standard_json_constant,
        )
        if not isinstance(loaded_usage, dict):
            raise ValueError("--usage-json must be a JSON object")
        usage_measurement = loaded_usage

    goal_instance_id = str(getattr(args, "goal_instance_id", None) or "").strip()
    if goal_instance_id and not getattr(args, "turn_instance_id", None):
        raise ValueError("--goal-instance-id requires --turn-instance-id")
    goal_ref: dict[str, str] | None = None
    if getattr(args, "turn_instance_id", None):
        goal_ref = (
            exact_goal_ref(args.goal_id, goal_instance_id)
            if goal_instance_id
            else capture_first_party_host_goal_ref(
                registry_path=registry_path,
                goal_id=args.goal_id,
            )
        )

    return RefreshStateInputs(
        outcome_update=outcome_update,
        agent_vision_packet=agent_vision_packet,
        merge_agent_vision_patch=merge_agent_vision_patch,
        explore_result=explore_result,
        progress_observation=progress_observation,
        reward_memory_reflection_json=reward_memory_reflection_json,
        usage_measurement=usage_measurement,
        goal_ref=goal_ref,
    )
