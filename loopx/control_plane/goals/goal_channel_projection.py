from __future__ import annotations

from typing import Any, Mapping, Sequence


GOAL_CHANNEL_PROJECTION_SCHEMA_VERSION = "goal_channel_projection_v0"

RAW_MATERIAL_KEY_HINTS = (
    "credential",
    "local_path",
    "log",
    "raw",
    "secret",
    "stderr",
    "stdout",
    "token",
    "trace",
    "transcript",
)

PRIVATE_VALUE_HINTS = (
    "/private/",
    "/users/",
    "credential" + "-value",
    "secret" + "-value",
    "token" + "-value",
)


def _as_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_mappings(values: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if not values:
        return []
    return [dict(item) for item in values if isinstance(item, Mapping)]


def _private_value(value: Any) -> bool:
    text = str(value).lower()
    private_doc_host = ".".join(("byte" + "dance", "lark" + "office"))
    return private_doc_host in text or any(hint in text for hint in PRIVATE_VALUE_HINTS)


def compact_goal_channel_text(value: Any, *, limit: int = 180) -> str | None:
    if value is None or _private_value(value):
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _text(value: Any, *, limit: int = 180) -> str | None:
    return compact_goal_channel_text(value, limit=limit)


def _raw_material_keys(*values: Any) -> list[str]:
    keys: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                lowered = str(key).lower()
                if any(hint in lowered for hint in RAW_MATERIAL_KEY_HINTS):
                    keys.add(str(key))
                if _private_value(child):
                    keys.add(str(key))
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for value in values:
        visit(value)
    return sorted(keys)


def _first_text(*values: Any, limit: int = 180) -> str | None:
    for value in values:
        text = _text(value, limit=limit)
        if text:
            return text
    return None


def _project_asset(status_item: Mapping[str, Any]) -> dict[str, Any]:
    asset = status_item.get("project_asset")
    return dict(asset) if isinstance(asset, Mapping) else {}


def _source_refs(
    *,
    status_payload: Mapping[str, Any],
    project_asset: Mapping[str, Any],
    run_history_goal: Mapping[str, Any],
    review_packet: Mapping[str, Any],
) -> dict[str, Any]:
    latest_runs = (
        run_history_goal.get("latest_runs")
        if isinstance(run_history_goal.get("latest_runs"), list)
        else []
    )
    latest_run = latest_runs[0] if latest_runs and isinstance(latest_runs[0], Mapping) else {}
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


def _compact_quota(quota_payload: Mapping[str, Any], project_asset: Mapping[str, Any]) -> dict[str, Any]:
    source = quota_payload.get("quota") if isinstance(quota_payload.get("quota"), Mapping) else {}
    if not source and isinstance(project_asset.get("quota"), Mapping):
        source = project_asset["quota"]
    compact: dict[str, Any] = {}
    for key in ("state", "reason", "spend_policy", "spent_slots", "allowed_slots"):
        value = _text(source.get(key), limit=220)
        if value is not None:
            compact[key] = value
    return compact


def _todo_candidates(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
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


def _compact_todo(item: Mapping[str, Any]) -> dict[str, Any] | None:
    title = _first_text(item.get("title"), item.get("text"), limit=220)
    if not title:
        return None
    compact: dict[str, Any] = {
        "title": title,
        "status": _first_text(item.get("status"), limit=40) or "open",
    }
    for key in (
        "todo_id",
        "priority",
        "claimed_by",
        "bound_agent",
        "goal_bound",
        "task_class",
        "action_kind",
        "task_repository",
    ):
        value = _text(item.get(key), limit=120)
        if value:
            compact[key] = value
    return compact


def _compact_todos(project_asset: Mapping[str, Any], role: str) -> list[dict[str, Any]]:
    summary = project_asset.get(f"{role}_todos")
    if not isinstance(summary, Mapping):
        return []
    compact: list[dict[str, Any]] = []
    for item in _todo_candidates(summary):
        todo = _compact_todo(item)
        if todo:
            compact.append(todo)
    return compact


def _user_gate_todos(user_todos: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(todo)
        for todo in user_todos
        if todo.get("task_class") == "user_gate"
        and todo.get("status") not in {"done", "closed", "resolved"}
    ]


def _open_gates(
    *,
    quota_payload: Mapping[str, Any],
    project_asset: Mapping[str, Any],
    user_todos: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    user_gate_todos = _user_gate_todos(user_todos)
    raw_gates = project_asset.get("open_gates")
    if isinstance(raw_gates, list):
        for gate in raw_gates:
            if not isinstance(gate, Mapping):
                continue
            status = _first_text(gate.get("status"), gate.get("state"), limit=80)
            if status in {None, "done", "closed", "resolved"}:
                continue
            compact = {
                "gate_id": _first_text(gate.get("gate_id"), gate.get("id"), limit=120)
                or "gate",
                "kind": _first_text(gate.get("kind"), gate.get("type"), limit=80)
                or "operator_gate",
                "status": status or "open",
            }
            blocks = gate.get("blocks")
            if isinstance(blocks, list):
                compact["blocks"] = [_text(item, limit=120) for item in blocks if _text(item, limit=120)]
            gates.append(compact)
    interaction = quota_payload.get("interaction_contract")
    user_channel = (
        interaction.get("user_channel")
        if isinstance(interaction, Mapping) and isinstance(interaction.get("user_channel"), Mapping)
        else {}
    )
    if (user_channel.get("action_required") is True or user_gate_todos) and not gates:
        blocking_todos = user_gate_todos or list(user_todos)
        gates.append(
            {
                "gate_id": (
                    "interaction_contract_user_channel"
                    if user_channel.get("action_required") is True
                    else "project_asset_user_gate_todos"
                ),
                "kind": (
                    "user_channel"
                    if user_channel.get("action_required") is True
                    else "user_todo"
                ),
                "status": "action_required",
                "blocks": [
                    str(todo.get("todo_id") or todo.get("title") or "user_todo")
                    for todo in blocking_todos
                ],
            }
        )
    return gates


def _compact_coordination_entry(item: Mapping[str, Any]) -> dict[str, Any]:
    """The existing channel redaction boundary owns display text, not lease rules."""
    row: dict[str, Any] = {}
    for key in ("todo_id", "owner_agent", "claimed_by", "lease_until", "expires_at", "status", "reason"):
        value = _text(item.get(key), limit=120)
        if value:
            row[key] = value
    for key in ("lease_version", "lease_epoch"):
        if isinstance(item.get(key), int) and not isinstance(item[key], bool):
            row[key] = item[key]
    if isinstance(item.get("write_scope"), list):
        row["write_scope"] = [text for value in item["write_scope"] if (text := _text(value, limit=120))]
    return row


def _compact_artifacts(artifacts: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in _as_mappings(artifacts):
        artifact: dict[str, Any] = {}
        for key in ("kind", "label", "path", "run_id"):
            value = _text(item.get(key), limit=160)
            if value:
                artifact[key] = value
        if artifact:
            compact.append(artifact)
    return compact


def _recent_events(run_history_goal: Mapping[str, Any]) -> list[dict[str, Any]]:
    runs = (
        run_history_goal.get("latest_runs")
        if isinstance(run_history_goal.get("latest_runs"), list)
        else []
    )
    events: list[dict[str, Any]] = []
    for run in runs[:5]:
        if not isinstance(run, Mapping):
            continue
        event: dict[str, Any] = {}
        for key in ("generated_at", "classification"):
            value = _text(run.get(key), limit=160)
            if value:
                event[key] = value
        summary = _first_text(run.get("health_check"), run.get("recommended_action"), limit=260)
        if summary:
            event["summary"] = summary
        if event:
            events.append(event)
    return events


def _source_warnings(raw_keys: Sequence[str]) -> list[dict[str, Any]]:
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
    status_item: Mapping[str, Any] | None = None,
    status_payload: Mapping[str, Any] | None = None,
    quota_payload: Mapping[str, Any] | None = None,
    run_history_goal: Mapping[str, Any] | None = None,
    review_packet: Mapping[str, Any] | None = None,
    artifacts: Sequence[Mapping[str, Any]] | None = None,
    active_leases: Sequence[Mapping[str, Any]] | None = None,
    generated_at: str | None = None,
    runtime_root: Any = None,
) -> dict[str, Any]:
    """Build a read-only channel snapshot for a LoopX goal.

    The returned object is a frontstage projection, not a new source of truth.
    It summarizes existing compact status/quota/history/todo surfaces and
    records raw/private-looking inputs as warnings without copying their values.
    """

    status_item_dict = _as_mapping(status_item)
    status_payload_dict = _as_mapping(status_payload)
    quota_payload_dict = _as_mapping(quota_payload)
    run_history_goal_dict = _as_mapping(run_history_goal)
    review_packet_dict = _as_mapping(review_packet)
    project_asset = _project_asset(status_item_dict)
    user_todos = _compact_todos(project_asset, "user")
    agent_todos = _compact_todos(project_asset, "agent")
    from .coordination_observation import (
        coordination_authority_transition,
        observe_goal_coordination,
    )

    explicit = None if active_leases is None else [
        _compact_coordination_entry({**{key: item[key] for key in ("todo_id", "status", "lease_until", "write_scope") if key in item},
                                     "owner_agent": _first_text(item.get("owner_agent"), item.get("claimed_by"), limit=120)})
        for item in _as_mappings(active_leases)
        if _text(item.get("todo_id"), limit=120) or _first_text(item.get("owner_agent"), item.get("claimed_by"), limit=120)
    ]
    observation = observe_goal_coordination(runtime_root=runtime_root, goal_id=str(goal_id),
        agent_todos=agent_todos, explicit_entries=explicit)
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
    user_channel = (
        interaction.get("user_channel")
        if isinstance(interaction, Mapping) and isinstance(interaction.get("user_channel"), Mapping)
        else {}
    )
    agent_channel = (
        interaction.get("agent_channel")
        if isinstance(interaction, Mapping) and isinstance(interaction.get("agent_channel"), Mapping)
        else {}
    )
    user_gate_todos = _user_gate_todos(user_todos)

    projection = {
        "schema_version": GOAL_CHANNEL_PROJECTION_SCHEMA_VERSION,
        "goal_id": str(goal_id),
        "mode": "read_only",
        "generated_at": _text(generated_at, limit=80),
        "display_name": _first_text(
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
        "decision_frame": {
            "user_action_required": bool(
                user_channel.get("action_required") or user_gate_todos
            ),
            "agent_action_required": bool(agent_channel.get("must_attempt")),
            "quiet_noop_allowed": bool(agent_channel.get("quiet_noop_allowed")),
        },
        "quota": _compact_quota(quota_payload_dict, project_asset),
        "user_todos": user_todos,
        "agent_todos": agent_todos,
        "open_gates": _open_gates(
            quota_payload=quota_payload_dict,
            project_asset=project_asset,
            user_todos=user_todos,
        ),
        "artifacts": _compact_artifacts(artifacts),
        "active_leases": [_compact_coordination_entry(row) for row in observation["entries"]],
        "coordination_authority": coordination_authority_transition(observation),
        "recent_events": _recent_events(run_history_goal_dict),
        "source_warnings": _source_warnings(raw_keys),
        "truth_contract": {
            "event_ledger_is_source_of_truth": True,
            "projection_is_writable": False,
            "write_authority": "none",
            "recompute_rule": (
                "refresh from LoopX status/quota/run history; do not "
                "edit the channel projection as project truth"
            ),
        },
    }
    if "source_authority" in observation:
        projection["coordination_observation"] = {key: observation[key] for key in (
            "status", "source_authority", "provider_revision", "observed_at", "legacy_fallback_used",
            "total_count", "truncated", "display_limit", "todo_count", "lease_count") if key in observation}
    if observation["status"] != "loaded":
        projection["source_warnings"].append({"kind": "coordination_unavailable",
            "message": "Task ownership could not be read; an empty list does not prove that no task is owned."})
    elif observation.get("truncated") is True:
        projection["source_warnings"].append({"kind": "coordination_truncated",
            "message": "Ownership display is limited to 100 entries; diagnostics are shown first. Read the full task/lease state before acting."})
    return {key: value for key, value in projection.items() if value is not None}
