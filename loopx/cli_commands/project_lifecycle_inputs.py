"""Typed inline input codecs for project lifecycle CLI arguments.

These helpers translate repeated CLI flags into the typed packets the refresh
path already accepts. They stay separate from the command owner so the
registration and dispatch module keeps one cohesive responsibility.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

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


def split_explore_result_input(vision_packet, result_path, *, scope_context=None):
    """Transport explicit attachments; the capability owns validation/conflicts.

    Keep the generic vision packet separate. No attachment means no capability
    call, and neither ordinary vision fields nor local files imply a finding.
    """
    attachments = []
    if isinstance(vision_packet, dict) and "explore_result" in vision_packet:
        vision_packet = dict(vision_packet)
        attachments.append(vision_packet.pop("explore_result"))
    if result_path:
        attachments.append(json.loads(Path(result_path).expanduser().read_text(encoding="utf-8")))
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


def reject_non_standard_json_constant(name: str) -> object:
    # json.loads would otherwise accept NaN/Infinity/-Infinity, which json.dump
    # then re-emits as non-standard JSON that breaks strict ledger consumers.
    raise ValueError(
        f"--usage-json must be strict JSON; non-standard constant {name} is not allowed"
    )


def inline_progress_observation(
    args: argparse.Namespace,
) -> dict[str, object] | None:
    fields = {
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
        raise ValueError("typed progress observation requires --progress-result-class")
    if fields["result_class"] in {
        ProgressResultClass.EXPLORATION_EXHAUSTED.value,
        ProgressResultClass.NO_FOLLOWUP.value,
    } and not fields["coverage_scope_id"]:
        raise ValueError(
            f"--progress-result-class {fields['result_class']} requires "
            "--progress-coverage-scope-id"
        )
    if (
        fields["result_class"] == ProgressResultClass.EXPLORATION_EXHAUSTED.value
        and fields["coverage_complete"] is not True
    ):
        raise ValueError(
            "--progress-result-class exploration_exhausted requires "
            "--progress-coverage-complete"
        )
    return {
        "schema_version": "typed_progress_observation_v0",
        **{key: value for key, value in fields.items() if value is not None},
        "evidence_ids": evidence_ids,
    }
