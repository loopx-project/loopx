from __future__ import annotations

from collections.abc import Mapping

from .live_decision_contracts import (
    OperatorInboxUrgencyProjector,
    mapping_or_empty,
    non_negative_int,
)


def _fresh_operator_inbox_observation_count(
    dispatch: Mapping[str, object] | None,
) -> int:
    """Count operator-inbox observations in the current dispatch marked for agent read."""

    if not isinstance(dispatch, Mapping):
        return 0
    required_reads = dispatch.get("required_reads")
    results = dispatch.get("results")
    if not isinstance(required_reads, list) or not isinstance(results, list):
        return 0
    operator_inbox_hook_ids = {
        str(read.get("hook_id") or "")
        for read in required_reads
        if isinstance(read, Mapping)
        and read.get("kind") == "operator_inbox"
        and str(read.get("hook_id") or "")
    }
    return sum(
        non_negative_int(result.get("observation_count"))
        for result in results
        if isinstance(result, Mapping)
        and result.get("agent_read_required") is True
        and result.get("hook_id") in operator_inbox_hook_ids
    )


def _fresh_operator_inbox_read_required(
    dispatch: Mapping[str, object] | None,
) -> bool:
    """Return whether any projected operator-inbox read still demands agent review."""

    return _fresh_operator_inbox_observation_count(dispatch) > 0


def _fresh_read_covers_pending_count(
    fresh_count: int,
    projector: OperatorInboxUrgencyProjector | None,
) -> OperatorInboxUrgencyProjector | None:
    """Wrap the urgency projector using a precomputed fresh inbox observation count."""

    if projector is None:
        return None

    def project(**kwargs: object) -> dict[str, object]:
        urgency = dict(projector(**kwargs))
        pending_count = non_negative_int(urgency.get("pending_count"))
        if pending_count <= fresh_count:
            urgency["material_review_due"] = False
        return urgency

    return project


def _fresh_read_covers_all_pending_material(
    dispatch: Mapping[str, object] | None,
    projector: OperatorInboxUrgencyProjector | None,
) -> OperatorInboxUrgencyProjector | None:
    """Clear material-review-due when current turn-start reads cover all pending inbox rows."""

    if projector is None:
        return None
    fresh_count = _fresh_operator_inbox_observation_count(dispatch)
    return _fresh_read_covers_pending_count(fresh_count, projector)


def _turn_start_required_reads(
    dispatch: Mapping[str, object] | None,
) -> list[dict[str, object]]:
    """Read only the generic, kernel-validated pre-work projection."""

    if not isinstance(dispatch, Mapping):
        return []
    reads = dispatch.get("required_reads")
    if not isinstance(reads, list):
        return []
    projected: list[dict[str, object]] = []
    seen_commands: set[str] = set()
    for read in reads:
        if not isinstance(read, Mapping):
            continue
        command = read.get("command")
        if not isinstance(command, str) or not command.strip():
            continue
        normalized = command.strip()
        if normalized in seen_commands:
            continue
        projected.append(
            {
                key: read[key]
                for key in (
                    "kind",
                    "command",
                    "reason",
                    "source",
                    "ordering",
                    "hook_id",
                    "capability_id",
                    "prompt_budget_bytes",
                )
                if key in read
            }
        )
        seen_commands.add(normalized)
    return projected


def merge_turn_start_required_reads(
    existing: object,
    projected: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Append newly projected turn-start reads while preserving command order and uniqueness."""

    required_reads = (
        [dict(mapping_or_empty(item)) for item in existing if isinstance(item, Mapping)]
        if isinstance(existing, list)
        else []
    )
    seen_commands = {
        str(item.get("command") or "").strip()
        for item in required_reads
        if str(item.get("command") or "").strip()
    }
    for required_read in projected:
        command = str(required_read.get("command") or "").strip()
        if command in seen_commands:
            continue
        required_reads.append(required_read)
        seen_commands.add(command)
    return required_reads
