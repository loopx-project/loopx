from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .core import (
    TRIGGER_DECISION_SCHEMA,
    _boolean,
    _digest,
    _integer,
    _list,
    _object,
    _optional_text,
    _reject_raw_keys,
    _text,
    _timestamp,
    _token,
)


TRIGGER_REQUEST_SCHEMA = "periodic_report_trigger_request_v0"

_REPORTABLE_KINDS = {
    "bounded_segment_milestone",
    "cadence_due",
    "manual",
    "material_blocker",
    "material_decision",
    "material_recovery",
    "primary_goal_outcome",
    "vision_closed",
}
_NON_REPORTABLE_KINDS = {
    "monitor_unchanged",
    "state_refreshed",
    "surface_only",
    "todo_completed",
    "vision_checkpoint",
}
_TRIGGER_PRIORITY = {
    "manual": 100,
    "primary_goal_outcome": 90,
    "vision_closed": 80,
    "material_blocker": 70,
    "material_recovery": 60,
    "material_decision": 50,
    "bounded_segment_milestone": 40,
    "cadence_due": 10,
}
_REPORT_KIND = {
    "bounded_segment_milestone": "milestone_update",
    "cadence_due": "cadence_digest",
    "manual": "manual_update",
    "material_blocker": "exception_update",
    "material_decision": "milestone_update",
    "material_recovery": "milestone_update",
    "primary_goal_outcome": "milestone_update",
    "vision_closed": "milestone_update",
}
_COOLDOWN_BYPASS_KINDS = {
    "manual",
    "material_blocker",
    "primary_goal_outcome",
    "vision_closed",
}


def _normalize_profile(raw: object) -> dict[str, str]:
    profile = _object(raw, "profile")
    normalized = {
        "profile_id": _token(profile.get("profile_id"), "profile.profile_id"),
        "profile_version": _token(
            profile.get("profile_version"), "profile.profile_version"
        ),
    }
    profile_ref = _optional_text(
        profile.get("profile_ref"), "profile.profile_ref", maximum=500
    )
    if profile_ref:
        normalized["profile_ref"] = profile_ref
    return normalized


def _normalize_policy(raw: object) -> dict[str, Any]:
    policy = _object(raw, "trigger_policy")
    enabled_raw = policy.get("enabled_kinds", sorted(_REPORTABLE_KINDS))
    enabled: list[str] = []
    for index, value in enumerate(_list(enabled_raw, "trigger_policy.enabled_kinds")):
        kind = _token(value, f"trigger_policy.enabled_kinds[{index}]")
        if kind not in _REPORTABLE_KINDS:
            raise ValueError(f"trigger_policy.enabled_kinds[{index}] is not reportable")
        if kind not in enabled:
            enabled.append(kind)
    if not enabled:
        raise ValueError("trigger_policy.enabled_kinds must not be empty")
    normalized: dict[str, Any] = {
        "enabled_kinds": sorted(enabled),
        "minimum_interval_seconds": _integer(
            policy.get("minimum_interval_seconds", 0),
            "trigger_policy.minimum_interval_seconds",
            maximum=31 * 24 * 60 * 60,
        ),
    }
    if policy.get("aggregation") is not None:
        aggregation = _object(policy.get("aggregation"), "trigger_policy.aggregation")
        _reject_unknown_facts(
            aggregation,
            allowed={
                "promote_replan",
                "stage_completion_required",
                "todo_completed_threshold",
                "window_seconds",
            },
            label="trigger_policy.aggregation",
        )
        threshold = aggregation.get("todo_completed_threshold")
        promote_replan = _boolean(
            aggregation.get("promote_replan", False),
            "trigger_policy.aggregation.promote_replan",
        )
        normalized_aggregation: dict[str, Any] = {
            "window_seconds": _integer(
                aggregation.get("window_seconds", 7 * 24 * 60 * 60),
                "trigger_policy.aggregation.window_seconds",
                minimum=1,
                maximum=31 * 24 * 60 * 60,
            ),
            "promote_replan": promote_replan,
            "stage_completion_required": _boolean(
                aggregation.get("stage_completion_required", True),
                "trigger_policy.aggregation.stage_completion_required",
            ),
        }
        if threshold is not None:
            normalized_aggregation["todo_completed_threshold"] = _integer(
                threshold,
                "trigger_policy.aggregation.todo_completed_threshold",
                minimum=1,
                maximum=64,
            )
        if (
            threshold is None
            and not promote_replan
            and not normalized_aggregation["stage_completion_required"]
        ):
            raise ValueError(
                "trigger_policy.aggregation must require a stage completion, "
                "enable a todo threshold, or enable replan promotion"
            )
        normalized["aggregation"] = normalized_aggregation
    return normalized


def normalize_periodic_report_trigger_policy(raw: object) -> dict[str, Any]:
    """Normalize the trigger portion of a project-owned report profile."""

    return _normalize_policy(raw)


def _normalize_last_report(raw: object) -> dict[str, Any] | None:
    if raw is None:
        return None
    report = _object(raw, "last_report")
    covered: list[str] = []
    for index, value in enumerate(
        _list(report.get("covered_trigger_ids", []), "last_report.covered_trigger_ids")
    ):
        trigger_id = _token(value, f"last_report.covered_trigger_ids[{index}]")
        if trigger_id not in covered:
            covered.append(trigger_id)
    return {
        "delivered_at": _timestamp(
            report.get("delivered_at"), "last_report.delivered_at"
        ),
        "covered_trigger_ids": sorted(covered),
    }


def _reject_unknown_facts(
    facts: Mapping[str, Any],
    *,
    allowed: set[str],
    label: str,
) -> None:
    unknown = sorted(set(facts) - allowed)
    if unknown:
        raise ValueError(f"{label} contains unsupported fields: {', '.join(unknown)}")


def _materiality(kind: str, facts: Mapping[str, Any], label: str) -> tuple[bool, str]:
    if kind in _NON_REPORTABLE_KINDS:
        _reject_unknown_facts(facts, allowed=set(), label=label)
        return False, "non_material_control_plane_event"
    if kind == "cadence_due":
        _reject_unknown_facts(facts, allowed={"due"}, label=label)
        return (
            _boolean(facts.get("due"), f"{label}.due"),
            "cadence_due" if facts.get("due") is True else "cadence_not_due",
        )
    if kind == "bounded_segment_milestone":
        _reject_unknown_facts(
            facts,
            allowed={
                "delivered_count",
                "durable_writeback",
                "remaining_todo_count",
                "segment_ref",
                "transition",
                "acceptance",
                "completed_at",
                "completion_receipt_ref",
                "stage_identity",
                "closed_vision_revision",
                "frontier_identity",
                "stage_transition",
                "outcome_checkpoint_satisfied",
                "status",
            },
            label=label,
        )
        _token(facts.get("segment_ref"), f"{label}.segment_ref")
        transition = _token(facts.get("transition"), f"{label}.transition")
        if transition not in {
            "replan_entered",
            "segment_completed",
            "vision_checkpoint",
        }:
            raise ValueError(f"{label}.transition is invalid")
        _integer(facts.get("delivered_count", 0), f"{label}.delivered_count")
        _integer(
            facts.get("remaining_todo_count", 0),
            f"{label}.remaining_todo_count",
        )
        durable_writeback = _boolean(
            facts.get("durable_writeback"), f"{label}.durable_writeback"
        )
        if transition not in {"replan_entered", "segment_completed"}:
            return False, "segment_checkpoint_only"
        if not durable_writeback:
            return False, "segment_writeback_pending"
        required_stage_facts = {
            "acceptance",
            "completed_at",
            "completion_receipt_ref",
            "stage_identity",
            "closed_vision_revision",
            "frontier_identity",
            "stage_transition",
            "outcome_checkpoint_satisfied",
            "status",
        }
        if not required_stage_facts.issubset(facts):
            return False, "stage_completion_not_proven"
        status = _token(facts.get("status"), f"{label}.status")
        acceptance = _token(facts.get("acceptance"), f"{label}.acceptance")
        stage_transition = _token(
            facts.get("stage_transition"), f"{label}.stage_transition"
        )
        _timestamp(facts.get("completed_at"), f"{label}.completed_at")
        _token(facts.get("stage_identity"), f"{label}.stage_identity")
        _text(
            facts.get("closed_vision_revision"),
            f"{label}.closed_vision_revision",
            maximum=128,
        )
        _text(
            facts.get("frontier_identity"),
            f"{label}.frontier_identity",
            maximum=256,
        )
        _text(
            facts.get("completion_receipt_ref"),
            f"{label}.completion_receipt_ref",
            maximum=256,
        )
        outcome_checkpoint_satisfied = _boolean(
            facts.get("outcome_checkpoint_satisfied"),
            f"{label}.outcome_checkpoint_satisfied",
        )
        material = (
            transition == "segment_completed"
            and status == "completed"
            and acceptance == "validated"
            and stage_transition
            in {"goal_terminal", "successor_frontier_settled"}
            and outcome_checkpoint_satisfied
        )
        return material, (
            "authoritative_stage_completed"
            if material
            else "stage_completion_not_proven"
        )
    if kind == "vision_closed":
        _reject_unknown_facts(
            facts,
            allowed={"acceptance", "continuation", "transition"},
            label=label,
        )
        transition = _token(facts.get("transition"), f"{label}.transition")
        acceptance = _token(facts.get("acceptance"), f"{label}.acceptance")
        continuation = _token(facts.get("continuation"), f"{label}.continuation")
        material = (
            transition == "vision_closed"
            and acceptance == "validated"
            and continuation in {"goal_terminal", "successor_established"}
        )
        return material, "vision_boundary_closed" if material else "vision_not_closed"
    if kind == "primary_goal_outcome":
        _reject_unknown_facts(
            facts,
            allowed={"delivery_outcome", "durable_writeback", "validated"},
            label=label,
        )
        material = (
            _token(facts.get("delivery_outcome"), f"{label}.delivery_outcome")
            == "primary_goal_outcome"
            and _boolean(facts.get("validated"), f"{label}.validated")
            and _boolean(facts.get("durable_writeback"), f"{label}.durable_writeback")
        )
        return (
            material,
            "primary_outcome_validated" if material else "outcome_unverified",
        )
    if kind == "material_decision":
        _reject_unknown_facts(
            facts,
            allowed={"decision_outcome", "durable_writeback", "route_changed"},
            label=label,
        )
        outcome = _token(facts.get("decision_outcome"), f"{label}.decision_outcome")
        if outcome not in {"approve", "cancel", "reject"}:
            raise ValueError(f"{label}.decision_outcome is invalid")
        material = _boolean(
            facts.get("route_changed"), f"{label}.route_changed"
        ) and _boolean(facts.get("durable_writeback"), f"{label}.durable_writeback")
        return (
            material,
            "decision_changed_route" if material else "decision_not_material",
        )
    if kind == "material_blocker":
        _reject_unknown_facts(
            facts,
            allowed={"blocks_primary_path", "severity", "transition"},
            label=label,
        )
        severity = _token(facts.get("severity"), f"{label}.severity")
        transition = _token(facts.get("transition"), f"{label}.transition")
        material = (
            severity == "p0"
            and transition in {"escalated", "opened"}
            and _boolean(
                facts.get("blocks_primary_path"), f"{label}.blocks_primary_path"
            )
        )
        return material, "primary_path_blocked" if material else "blocker_not_material"
    if kind == "material_recovery":
        _reject_unknown_facts(
            facts,
            allowed={"primary_path_reopened", "transition", "validated"},
            label=label,
        )
        material = (
            _token(facts.get("transition"), f"{label}.transition") == "resolved"
            and _boolean(
                facts.get("primary_path_reopened"),
                f"{label}.primary_path_reopened",
            )
            and _boolean(facts.get("validated"), f"{label}.validated")
        )
        return material, "primary_path_recovered" if material else "recovery_unverified"
    if kind == "manual":
        _reject_unknown_facts(facts, allowed={"authorized"}, label=label)
        material = _boolean(facts.get("authorized"), f"{label}.authorized")
        return material, "manual_authorized" if material else "manual_unauthorized"
    raise ValueError(f"unsupported trigger kind {kind!r}")


def _normalize_candidates(raw: object, evaluated_at: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    values = _list(raw, "candidates")
    if not values:
        raise ValueError("candidates must not be empty")
    if len(values) > 64:
        raise ValueError("candidates must contain at most 64 items")
    for index, value in enumerate(values):
        label = f"candidates[{index}]"
        item = _object(value, label)
        kind = _token(item.get("trigger_kind"), f"{label}.trigger_kind")
        if kind not in _REPORTABLE_KINDS | _NON_REPORTABLE_KINDS:
            raise ValueError(f"{label}.trigger_kind is invalid")
        observed_at = _timestamp(item.get("observed_at"), f"{label}.observed_at")
        observed_value = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        evaluated_value = datetime.fromisoformat(evaluated_at.replace("Z", "+00:00"))
        if observed_value > evaluated_value:
            raise ValueError(f"{label}.observed_at must not be in the future")
        source_ref = _text(item.get("source_ref"), f"{label}.source_ref", maximum=500)
        evidence_digest = _text(
            item.get("evidence_digest"), f"{label}.evidence_digest", maximum=256
        )
        facts = _object(item.get("facts", {}), f"{label}.facts")
        _reject_raw_keys(facts, f"{label}.facts")
        material, materiality_reason = _materiality(kind, facts, f"{label}.facts")
        trigger_id = _digest(
            {
                "trigger_kind": kind,
                "source_ref": source_ref,
                "evidence_digest": evidence_digest,
            },
            prefix="trigger",
        )
        supplied_id = _optional_text(
            item.get("trigger_id"), f"{label}.trigger_id", maximum=128
        )
        if supplied_id and supplied_id != trigger_id:
            raise ValueError(f"{label}.trigger_id does not match trigger identity")
        if trigger_id in seen:
            raise ValueError(f"duplicate trigger identity {trigger_id!r}")
        seen.add(trigger_id)
        candidates.append(
            {
                "trigger_id": trigger_id,
                "trigger_kind": kind,
                "observed_at": observed_at,
                "source_ref": source_ref,
                "evidence_digest": evidence_digest,
                "facts": dict(facts),
                "material": material,
                "materiality_reason": materiality_reason,
            }
        )
    return sorted(
        candidates,
        key=lambda item: (
            -_TRIGGER_PRIORITY.get(str(item["trigger_kind"]), 0),
            datetime.fromisoformat(str(item["observed_at"]).replace("Z", "+00:00")),
            str(item["trigger_id"]),
        ),
    )


def _seconds_between(start_at: str, end_at: str) -> int:
    start = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
    end = datetime.fromisoformat(end_at.replace("Z", "+00:00"))
    return max(0, int((end - start).total_seconds()))


def build_periodic_report_trigger_decision(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Select one provider-neutral report trigger without external effects."""

    payload = _object(request, "request")
    _reject_raw_keys(payload, "request")
    schema_version = _text(payload.get("schema_version"), "schema_version")
    if schema_version != TRIGGER_REQUEST_SCHEMA:
        raise ValueError(f"schema_version must be {TRIGGER_REQUEST_SCHEMA!r}")
    evaluated_at = _timestamp(payload.get("evaluated_at"), "evaluated_at")
    profile = _normalize_profile(payload.get("profile"))
    policy = _normalize_policy(payload.get("trigger_policy", {}))
    last_report = _normalize_last_report(payload.get("last_report"))
    candidates = _normalize_candidates(payload.get("candidates"), evaluated_at)

    enabled = set(policy["enabled_kinds"])
    covered = set(last_report["covered_trigger_ids"]) if last_report else set()
    eligible: list[dict[str, Any]] = []
    suppressed: list[dict[str, str]] = []
    for candidate in candidates:
        trigger_id = str(candidate["trigger_id"])
        kind = str(candidate["trigger_kind"])
        if kind in _NON_REPORTABLE_KINDS:
            reason = str(candidate["materiality_reason"])
        elif kind not in enabled:
            reason = "disabled_by_profile"
        elif trigger_id in covered:
            reason = "already_covered"
        elif not candidate["material"]:
            reason = str(candidate["materiality_reason"])
        else:
            eligible.append(candidate)
            continue
        suppressed.append(
            {
                "trigger_id": trigger_id,
                "trigger_kind": kind,
                "reason": reason,
            }
        )

    cooldown_active = False
    next_eligible_at: str | None = None
    bypass_trigger_id: str | None = None
    if eligible and last_report and policy["minimum_interval_seconds"]:
        elapsed = _seconds_between(last_report["delivered_at"], evaluated_at)
        minimum_interval = int(policy["minimum_interval_seconds"])
        cooldown_active = elapsed < minimum_interval
        if cooldown_active:
            bypass = next(
                (
                    item
                    for item in eligible
                    if item["trigger_kind"] in _COOLDOWN_BYPASS_KINDS
                ),
                None,
            )
            if bypass:
                bypass_trigger_id = str(bypass["trigger_id"])
            else:
                delivered = datetime.fromisoformat(
                    str(last_report["delivered_at"]).replace("Z", "+00:00")
                )
                next_at = delivered.timestamp() + minimum_interval
                next_eligible_at = (
                    datetime.fromtimestamp(next_at, tz=delivered.tzinfo)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
                for candidate in eligible:
                    suppressed.append(
                        {
                            "trigger_id": str(candidate["trigger_id"]),
                            "trigger_kind": str(candidate["trigger_kind"]),
                            "reason": "cooldown_active",
                        }
                    )
                eligible = []

    selected = eligible[0] if eligible else None
    selected_trigger_id = str(selected["trigger_id"]) if selected else None
    selected_kind = str(selected["trigger_kind"]) if selected else None
    coalesced_ids = sorted(str(item["trigger_id"]) for item in eligible)
    report_kind = _REPORT_KIND[selected_kind] if selected_kind else None
    report_key = (
        _digest(
            {
                "profile": profile,
                "trigger_policy": policy,
                "report_kind": report_kind,
                "trigger_ids": coalesced_ids,
            },
            prefix="report",
        )
        if selected
        else None
    )
    decision_id = _digest(
        {
            "evaluated_at": evaluated_at,
            "profile": profile,
            "trigger_policy": policy,
            "last_report": last_report,
            "candidates": candidates,
        },
        prefix="trigger_decision",
    )
    if selected:
        reason = "trigger_selected"
    elif cooldown_active and next_eligible_at:
        reason = "cooldown_active"
    else:
        reason = "no_material_trigger"

    return {
        "ok": True,
        "schema_version": TRIGGER_DECISION_SCHEMA,
        "decision_id": decision_id,
        "evaluated_at": evaluated_at,
        "profile": profile,
        "trigger_policy": policy,
        "eligible": bool(selected),
        "reason": reason,
        "report_kind": report_kind,
        "report_key": report_key,
        "selected_trigger_id": selected_trigger_id,
        "selected_trigger_kind": selected_kind,
        "coalesced_trigger_ids": coalesced_ids,
        "suppressed_triggers": sorted(
            suppressed,
            key=lambda item: (item["trigger_kind"], item["trigger_id"]),
        ),
        "cooldown": {
            "active": cooldown_active,
            "bypassed": bool(bypass_trigger_id),
            "bypass_trigger_id": bypass_trigger_id,
            "next_eligible_at": next_eligible_at,
        },
        "boundary": {
            "provider_neutral": True,
            "project_schedule_owned_by_profile": True,
            "project_audience_owned_by_profile": True,
            "project_layout_owned_by_profile": True,
            "external_writes_performed": False,
            "raw_content_persisted": False,
        },
    }
