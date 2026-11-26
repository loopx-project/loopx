"""Read-only frontstage channel projection for LoopX goals."""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from .goal_channel_projection_contracts import (
    GOAL_CHANNEL_PROJECTION_SCHEMA_VERSION as GOAL_CHANNEL_PROJECTION_SCHEMA_VERSION,
    PRIVATE_VALUE_HINTS as PRIVATE_VALUE_HINTS,
    RAW_MATERIAL_KEY_HINTS as RAW_MATERIAL_KEY_HINTS,
    GoalChannelDecisionFrame as GoalChannelDecisionFrame,
    GoalChannelSourceRefs as GoalChannelSourceRefs,
    GoalChannelSourceWarning as GoalChannelSourceWarning,
    GoalChannelTruthContract as GoalChannelTruthContract,
    _GoalChannelMode,
    _TodoRole,
    _as_mapping as _as_mapping,
    _as_mappings as _as_mappings,
    _copy_compact_text_fields,
    _first_text as _first_text,
    _interaction_channel,
    _mapping_or_empty,
    _private_value as _private_value,
    _project_asset as _project_asset,
    _raw_material_keys as _raw_material_keys,
    _text as _text,
    compact_goal_channel_text as compact_goal_channel_text,
)


def _source_refs(
    *,
    status_payload: Mapping[str, object],
    project_asset: Mapping[str, object],
    run_history_goal: Mapping[str, object],
    review_packet: Mapping[str, object],
) -> GoalChannelSourceRefs:
    latest_runs_raw = run_history_goal.get("latest_runs")
    latest_runs = latest_runs_raw if isinstance(latest_runs_raw, list) else []
    latest_run: Mapping[str, object] = (
        latest_runs[0] if latest_runs and isinstance(latest_runs[0], Mapping) else {}
    )
    return {
        "status_generated_at": _first_text(
            status_payload.get("generated_at"),
            project_asset.get("status_generated_at"),
            limit=80,
        ),
        "active_state_updated_at": _first_text(
            project_asset.get("state_updated_at"),
            project_asset.get("active_state_updated_at"),
            status_payload.get("state_updated_at"),
            limit=80,
        ),
        "latest_run_generated_at": _first_text(latest_run.get("generated_at"), limit=80),
        "review_packet_generated_at": _first_text(
            review_packet.get("generated_at"),
            review_packet.get("created_at"),
            limit=80,
        ),
        "event_ledger_source": "run_history",
    }


def _compact_quota(
    quota_payload: Mapping[str, object],
    project_asset: Mapping[str, object],
) -> dict[str, object]:
    raw_quota = quota_payload.get("quota")
    source: Mapping[str, object] = raw_quota if isinstance(raw_quota, Mapping) else {}
    if not source:
        asset_quota = project_asset.get("quota")
        if isinstance(asset_quota, Mapping):
            source = asset_quota
    compact: dict[str, object] = {}
    _copy_compact_text_fields(
        compact, source, ("state", "reason", "spend_policy", "spent_slots", "allowed_slots"), limit=220,
    )
    return compact


def _todo_candidates(summary: Mapping[str, object]) -> list[dict[str, object]]:
    for key in (
        "first_open_items",
        "first_executable_items",
        "open_items",
        "backlog_items",
        "items",
    ):
        values = summary.get(key)
        if isinstance(values, list) and values:
            return [dict(item) for item in values if isinstance(item, Mapping)]
    return []


def _compact_todo(item: Mapping[str, object]) -> dict[str, object] | None:
    title = _first_text(item.get("title"), item.get("text"), limit=220)
    if not title:
        return None
    compact: dict[str, object] = {
        "title": title,
        "status": _first_text(item.get("status"), limit=40) or "open",
    }
    _copy_compact_text_fields(
        compact,
        item,
        (
            "todo_id",
            "priority",
            "claimed_by",
            "bound_agent",
            "goal_bound",
            "task_class",
            "action_kind",
            "task_repository",
        ),
        limit=120,
    )
    return compact


def _compact_todos(project_asset: Mapping[str, object], role: _TodoRole) -> list[dict[str, object]]:
    summary = project_asset.get(f"{role}_todos")
    if not isinstance(summary, Mapping):
        return []
    compact: list[dict[str, object]] = []
    for item in _todo_candidates(summary):
        todo = _compact_todo(item)
        if todo:
            compact.append(todo)
    return compact


def _user_gate_todos(user_todos: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [
        dict(todo)
        for todo in user_todos
        if todo.get("task_class") == "user_gate"
        and todo.get("status") not in {"done", "closed", "resolved"}
    ]


def _open_gates(
    *,
    quota_payload: Mapping[str, object],
    project_asset: Mapping[str, object],
    user_todos: Sequence[Mapping[str, object]],
    user_channel: Mapping[str, object] | None = None,
    user_gate_todos: Sequence[Mapping[str, object]] | None = None,
) -> list[dict[str, object]]:
    gates: list[dict[str, object]] = []
    resolved_user_gates = list(user_gate_todos) if user_gate_todos is not None else _user_gate_todos(user_todos)
    raw_gates = project_asset.get("open_gates")
    if isinstance(raw_gates, list):
        for gate in raw_gates:
            if not isinstance(gate, Mapping):
                continue
            status = _first_text(gate.get("status"), gate.get("state"), limit=80)
            if status in {None, "done", "closed", "resolved"}:
                continue
            compact: dict[str, object] = {
                "gate_id": _first_text(gate.get("gate_id"), gate.get("id"), limit=120) or "gate",
                "kind": _first_text(gate.get("kind"), gate.get("type"), limit=80) or "operator_gate",
                "status": status or "open",
            }
            blocks = gate.get("blocks")
            if isinstance(blocks, list):
                compact["blocks"] = [text for item in blocks if (text := _text(item, limit=120))]
            gates.append(compact)
    resolved_user_channel = (
        user_channel
        if user_channel is not None
        else _interaction_channel(quota_payload.get("interaction_contract"), "user_channel")
    )
    action_required = resolved_user_channel.get("action_required") is True
    if (action_required or resolved_user_gates) and not gates:
        blocking_todos = resolved_user_gates or list(user_todos)
        gates.append(
            {
                "gate_id": (
                    "interaction_contract_user_channel"
                    if action_required
                    else "project_asset_user_gate_todos"
                ),
                "kind": "user_channel" if action_required else "user_todo",
                "status": "action_required",
                "blocks": [
                    str(todo.get("todo_id") or todo.get("title") or "user_todo")
                    for todo in blocking_todos
                ],
            }
        )
    return gates


def _compact_coordination_entry(item: Mapping[str, object]) -> dict[str, object]:
    """The existing channel redaction boundary owns display text, not lease rules."""
    row: dict[str, object] = {}
    _copy_compact_text_fields(
        row,
        item,
        ("todo_id", "owner_agent", "claimed_by", "lease_until", "expires_at", "status", "reason"),
        limit=120,
    )
    for key in ("lease_version", "lease_epoch"):
        val = item.get(key)
        if isinstance(val, int) and not isinstance(val, bool):
            row[key] = val
    write_scope = item.get("write_scope")
    if isinstance(write_scope, list):
        row["write_scope"] = [text for value in write_scope if (text := _text(value, limit=120))]
    return row


def _compact_artifacts(artifacts: Sequence[Mapping[str, object]] | None) -> list[dict[str, object]]:
    if not artifacts:
        return []
    compact: list[dict[str, object]] = []
    for item in artifacts:
        if not isinstance(item, Mapping):
            continue
        artifact: dict[str, object] = {}
        _copy_compact_text_fields(artifact, item, ("kind", "label", "path", "run_id"), limit=160)
        if artifact:
            compact.append(artifact)
    return compact


def _recent_events(run_history_goal: Mapping[str, object]) -> list[dict[str, object]]:
    raw_runs = run_history_goal.get("latest_runs")
    runs = raw_runs if isinstance(raw_runs, list) else []
    events: list[dict[str, object]] = []
    for run in runs[:5]:
        if not isinstance(run, Mapping):
            continue
        event: dict[str, object] = {}
        _copy_compact_text_fields(event, run, ("generated_at", "classification"), limit=160)
        summary = _first_text(run.get("health_check"), run.get("recommended_action"), limit=260)
        if summary:
            event["summary"] = summary
        if event:
            events.append(event)
    return events


def _source_warnings(raw_keys: Sequence[str]) -> list[GoalChannelSourceWarning]:
    if not raw_keys:
        return []
    return [
        {
            "kind": "raw_or_private_material_omitted",
            "key_names": list(raw_keys),
            "message": (
                "raw/private-looking fields were omitted; inspect the compact "
                "source references instead of copying raw material into the "
                "frontstage channel projection"
            ),
        }
    ]


def build_goal_channel_projection(
    *,
    goal_id: str,
    status_item: Mapping[str, object] | None = None,
    status_payload: Mapping[str, object] | None = None,
    quota_payload: Mapping[str, object] | None = None,
    run_history_goal: Mapping[str, object] | None = None,
    review_packet: Mapping[str, object] | None = None,
    artifacts: Sequence[Mapping[str, object]] | None = None,
    active_leases: Sequence[Mapping[str, object]] | None = None,
    generated_at: str | None = None,
    runtime_root: object = None,
) -> dict[str, object]:
    """Build a read-only channel snapshot for a LoopX goal.

    The returned object is a frontstage projection.
    It summarizes existing compact status/quota/history/todo surfaces and
    records raw/private-looking inputs as warnings without copying their values.
    """

    status_item_dict = _mapping_or_empty(status_item)
    status_payload_dict = _mapping_or_empty(status_payload)
    quota_payload_dict = _mapping_or_empty(quota_payload)
    run_history_goal_dict = _mapping_or_empty(run_history_goal)
    review_packet_dict = _mapping_or_empty(review_packet)
    project_asset = _project_asset(status_item_dict)
    user_todos = _compact_todos(project_asset, "user")
    agent_todos = _compact_todos(project_asset, "agent")
    from .coordination_observation import (
        coordination_authority_transition,
        observe_goal_coordination,
    )

    observation = observe_goal_coordination(runtime_root=runtime_root, goal_id=str(goal_id))
    raw_keys = _raw_material_keys(
        status_item_dict,
        status_payload_dict,
        quota_payload_dict,
        run_history_goal_dict,
        review_packet_dict,
        artifacts or [],
        active_leases or [],
    )

    interaction = quota_payload_dict.get("interaction_contract")
    user_channel = _interaction_channel(interaction, "user_channel")
    agent_channel = _interaction_channel(interaction, "agent_channel")
    user_gate_todos = _user_gate_todos(user_todos)
    source_warnings = _source_warnings(raw_keys)

    raw_entries = observation.get("entries")
    entries: Sequence[object] = (
        raw_entries if isinstance(raw_entries, Sequence) and not isinstance(raw_entries, (str, bytes)) else ()
    )

    mode: _GoalChannelMode = "read_only"
    decision_frame: GoalChannelDecisionFrame = {
        "user_action_required": bool(user_channel.get("action_required") or user_gate_todos),
        "agent_action_required": bool(agent_channel.get("must_attempt")),
        "quiet_noop_allowed": bool(agent_channel.get("quiet_noop_allowed")),
    }
    truth_contract: GoalChannelTruthContract = {
        "event_ledger_is_source_of_truth": True,
        "projection_is_writable": False,
        "write_authority": "none",
        "recompute_rule": (
            "refresh from LoopX status/quota/run history; do not "
            "edit the channel projection as project truth"
        ),
    }

    projection: dict[str, object] = {
        "schema_version": GOAL_CHANNEL_PROJECTION_SCHEMA_VERSION,
        "goal_id": str(goal_id),
        "mode": mode,
        "generated_at": _text(generated_at, limit=80),
        "display_name": _first_text(
            # History carries the current registry name, including after a rename.
            run_history_goal_dict.get("display_name"),
            project_asset.get("display_name"),
            status_item_dict.get("display_name"),
            status_item_dict.get("domain"),
            goal_id,
            limit=120,
        ),
        "source_refs": _source_refs(
            status_payload=status_payload_dict,
            project_asset=project_asset,
            run_history_goal=run_history_goal_dict,
            review_packet=review_packet_dict,
        ),
        "waiting_on": _first_text(
            status_item_dict.get("waiting_on"),
            quota_payload_dict.get("waiting_on"),
            limit=80,
        ),
        "latest_status": _first_text(
            status_item_dict.get("status"),
            quota_payload_dict.get("status"),
            limit=160,
        ),
        "next_action": _first_text(
            quota_payload_dict.get("recommended_action"),
            project_asset.get("next_action"),
            status_item_dict.get("recommended_action"),
            limit=260,
        ),
        "decision_frame": decision_frame,
        "quota": _compact_quota(quota_payload_dict, project_asset),
        "user_todos": user_todos,
        "agent_todos": agent_todos,
        "open_gates": _open_gates(
            quota_payload=quota_payload_dict,
            project_asset=project_asset,
            user_todos=user_todos,
            user_channel=user_channel,
            user_gate_todos=user_gate_todos,
        ),
        "artifacts": _compact_artifacts(artifacts),
        "active_leases": [
            _compact_coordination_entry(row) for row in entries if isinstance(row, Mapping)
        ],
        "coordination_authority": coordination_authority_transition(observation),
        "recent_events": _recent_events(run_history_goal_dict),
        "source_warnings": source_warnings,
        "truth_contract": truth_contract,
    }
    if "source_authority" in observation:
        projection["coordination_observation"] = {
            key: observation[key]
            for key in (
                "status",
                "source_authority",
                "provider_revision",
                "observed_at",
                "legacy_fallback_used",
                "total_count",
                "truncated",
                "display_limit",
                "todo_count",
                "lease_count",
            )
            if key in observation
        }
    if observation.get("status") != "loaded":
        source_warnings.append(
            {
                "kind": "coordination_unavailable",
                "message": "Task ownership could not be read; an empty list does not prove that no task is owned.",
            }
        )
    elif observation.get("truncated") is True:
        source_warnings.append(
            {
                "kind": "coordination_truncated",
                "message": "Ownership display is limited to 100 entries; diagnostics are shown first. Read the full task/lease state before acting.",
            }
        )
    return {key: value for key, value in projection.items() if value is not None}
