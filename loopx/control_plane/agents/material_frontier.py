from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from ..runtime.public_safety import public_safe_compact_text
from ..runtime.time import now_utc_iso, parse_timestamp


AGENT_MATERIAL_FRONTIER_SCHEMA_VERSION = "agent_material_frontier_v0"
MATERIAL_USAGE_RECEIPT_SCHEMA_VERSION = "material_usage_receipt_v0"

_PUBLIC_BOUNDARIES = {"public", "public_safe"}
_BLOCKED_GATE_STATES = {
    "blocked",
    "denied",
    "forbidden",
    "missing_authority",
    "owner_gate",
    "owner_review_required",
    "private_read_required",
    "unavailable",
    "user_gate",
}
_STALE_FRESHNESS = {"needs_refresh", "outdated", "stale", "unknown"}
_RELATIONS = {"required", "producer", "reviewer", "maintainer", "watcher"}
_RECEIPT_OUTCOMES = {"read", "unavailable", "used", "verified"}
_RECEIPT_CURRENT_OUTCOMES = {"read", "used", "verified"}
_REQUIREMENT_FIELDS = (
    "material_refs",
    "required_material_refs",
    "required_materials",
)
_TOPIC_FIELDS = (
    "material_topics",
    "required_material_topics",
    "topic_defaults",
)
_SOURCE_PRIORITY = {
    "profile": 10,
    "vision": 20,
    "todo": 30,
    "handoff": 30,
}
_STATE_RANK = {
    "missing": 0,
    "inaccessible": 1,
    "stale": 2,
    "required_unread": 3,
    "current": 4,
}


def _safe_text(value: Any, *, field: str, limit: int = 180) -> str | None:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return None
    if len(text) > limit:
        raise ValueError(f"{field} must be at most {limit} characters")
    if public_safe_compact_text(text, limit=limit) != text:
        raise ValueError(f"{field} must be public-safe")
    return text


def _required_text(value: Any, *, field: str, limit: int = 180) -> str:
    text = _safe_text(value, field=field, limit=limit)
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _mapping_list(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _project_materials(authority_registry: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if "project_materials" not in authority_registry:
        raise ValueError(
            "agent material frontier requires canonical authority_registry.project_materials; "
            "a compact authority summary is not sufficient"
        )
    raw = authority_registry.get("project_materials")
    if isinstance(raw, Mapping):
        if any(not isinstance(raw_item, Mapping) for raw_item in raw.values()):
            raise ValueError("authority_registry.project_materials values must be objects")
        items = list(raw.items())
    elif isinstance(raw, list):
        if any(not isinstance(raw_item, Mapping) for raw_item in raw):
            raise ValueError("authority_registry.project_materials entries must be objects")
        items = [
            (item.get("id") or item.get("source_id"), item)
            for item in raw
            if isinstance(item, Mapping)
        ]
    else:
        raise ValueError("authority_registry.project_materials must be a map or list")

    materials: dict[str, dict[str, Any]] = {}
    for raw_id, raw_item in items:
        material_id = _required_text(
            raw_item.get("id") or raw_item.get("source_id") or raw_id,
            field="authority material_id",
        )
        if material_id in materials:
            raise ValueError(f"duplicate authority material_id: {material_id}")
        materials[material_id] = dict(raw_item)
    return materials


def _topic_material_ids(value: Any) -> list[str]:
    raw_items: list[Any]
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = [value]
    material_ids: list[str] = []
    for raw in raw_items:
        if isinstance(raw, Mapping):
            raw = raw.get("material_id") or raw.get("id") or raw.get("source_id")
        material_id = _safe_text(raw, field="topic material_id")
        if material_id and material_id not in material_ids:
            material_ids.append(material_id)
    return material_ids


def authority_topic_index(authority_registry: Mapping[str, Any]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Interpret registered string/object/list references for every material reader."""
    raw_topics = authority_registry.get("topic_authority")
    if not isinstance(raw_topics, Mapping):
        return {}, {}
    by_topic: dict[str, list[str]] = {}
    by_material: dict[str, list[str]] = {}
    for raw_topic, raw_material in raw_topics.items():
        topic = _required_text(raw_topic, field="authority topic", limit=120)
        material_ids = _topic_material_ids(raw_material)
        if not material_ids:
            raise ValueError(f"authority topic must reference at least one material: {topic}")
        by_topic[topic] = material_ids
        for material_id in material_ids:
            by_material.setdefault(material_id, []).append(topic)
    return by_topic, by_material


def _requirement_refs(owner: Mapping[str, Any]) -> list[Any]:
    refs: list[Any] = []
    for field in _REQUIREMENT_FIELDS:
        raw = owner.get(field)
        if isinstance(raw, list):
            refs.extend(raw)
        elif raw not in (None, ""):
            refs.append(raw)
    return refs


def _topic_refs(owner: Mapping[str, Any]) -> list[str]:
    topics: list[str] = []
    for field in _TOPIC_FIELDS:
        raw = owner.get(field)
        values = raw if isinstance(raw, list) else [raw] if raw not in (None, "") else []
        for value in values:
            topic = _safe_text(value, field=f"{field} topic", limit=120)
            if topic and topic not in topics:
                topics.append(topic)
    return topics


def _owner_ref(owner: Mapping[str, Any], *, kind: str, agent_id: str) -> str:
    candidates = {
        "profile": (owner.get("agent_id"), agent_id),
        "todo": (owner.get("todo_id"),),
        "vision": (owner.get("vision_id"), owner.get("state"), agent_id),
        "handoff": (owner.get("handoff_id"), owner.get("todo_id")),
    }.get(kind, ())
    for candidate in candidates:
        text = _safe_text(candidate, field=f"{kind} ref")
        if text:
            return text
    return f"{kind}:{agent_id}"


def _normalize_requirement(
    raw: Any,
    *,
    kind: str,
    owner: Mapping[str, Any],
    agent_id: str,
    default_relation: str,
    topic: str | None = None,
) -> dict[str, Any]:
    payload = dict(raw) if isinstance(raw, Mapping) else {"material_id": raw}
    material_id = _required_text(
        payload.get("material_id") or payload.get("id") or payload.get("source_id"),
        field=f"{kind} material_id",
    )
    relation = str(payload.get("relation") or default_relation).strip().lower()
    if relation not in _RELATIONS:
        raise ValueError(f"{kind} material relation must be one of {sorted(_RELATIONS)}")
    todo_id = _safe_text(
        payload.get("todo_id") or owner.get("todo_id"),
        field=f"{kind} todo_id",
    )
    requirement: dict[str, Any] = {
        "material_id": material_id,
        "relation": relation,
        "bound_by": {
            "kind": kind,
            "ref": _owner_ref(owner, kind=kind, agent_id=agent_id),
        },
        "purpose": _safe_text(payload.get("purpose"), field=f"{kind} purpose", limit=220),
        "todo_id": todo_id,
        "required_revision_hint": _safe_text(
            payload.get("required_revision"),
            field=f"{kind} required_revision",
        ),
        "topic": topic,
        "source_priority": _SOURCE_PRIORITY[kind],
    }
    return {
        key: value
        for key, value in requirement.items()
        if value not in (None, "", [], {})
    }


def _requirements_from_owner(
    owner: Mapping[str, Any],
    *,
    kind: str,
    agent_id: str,
    topic_materials: Mapping[str, list[str]],
    default_relation: str,
) -> list[dict[str, Any]]:
    requirements = [
        _normalize_requirement(
            raw,
            kind=kind,
            owner=owner,
            agent_id=agent_id,
            default_relation=default_relation,
        )
        for raw in _requirement_refs(owner)
    ]
    for topic in _topic_refs(owner):
        if topic not in topic_materials:
            raise ValueError(
                f"{kind} material topic is not registered in goal authority: {topic}"
            )
        for material_id in topic_materials.get(topic, []):
            requirements.append(
                _normalize_requirement(
                    material_id,
                    kind=kind,
                    owner=owner,
                    agent_id=agent_id,
                    default_relation="watcher" if kind == "profile" else default_relation,
                    topic=topic,
                )
            )
    return requirements


def _collect_requirements(
    *,
    agent_id: str,
    topic_materials: Mapping[str, list[str]],
    agent_profile: Mapping[str, Any] | None,
    todos: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None,
    vision: Mapping[str, Any] | None,
    handoffs: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    raw_requirements: list[dict[str, Any]] = []
    if isinstance(agent_profile, Mapping):
        profile_agent = _safe_text(
            agent_profile.get("agent_id"),
            field="profile agent_id",
        )
        if profile_agent and profile_agent != agent_id:
            raise ValueError("agent profile must belong to the selected agent_id")
        raw_requirements.extend(
            _requirements_from_owner(
                agent_profile,
                kind="profile",
                agent_id=agent_id,
                topic_materials=topic_materials,
                default_relation="watcher",
            )
        )
    if isinstance(vision, Mapping):
        vision_agent = _safe_text(vision.get("agent_id"), field="vision agent_id")
        if vision_agent and vision_agent != agent_id:
            raise ValueError("agent vision must belong to the selected agent_id")
        raw_requirements.extend(
            _requirements_from_owner(
                vision,
                kind="vision",
                agent_id=agent_id,
                topic_materials=topic_materials,
                default_relation="required",
            )
        )
    for todo in _mapping_list(todos):
        todo_agent = _safe_text(
            todo.get("claimed_by") or todo.get("agent_id"),
            field="todo agent_id",
        )
        if todo_agent and todo_agent != agent_id:
            continue
        raw_requirements.extend(
            _requirements_from_owner(
                todo,
                kind="todo",
                agent_id=agent_id,
                topic_materials=topic_materials,
                default_relation="required",
            )
        )
    for handoff in _mapping_list(handoffs):
        handoff_target = _safe_text(handoff.get("to_agent"), field="handoff target")
        if handoff_target and handoff_target != agent_id:
            continue
        raw_requirements.extend(
            _requirements_from_owner(
                handoff,
                kind="handoff",
                agent_id=agent_id,
                topic_materials=topic_materials,
                default_relation="required",
            )
        )

    merged: dict[str, dict[str, Any]] = {}
    for requirement in raw_requirements:
        material_id = requirement["material_id"]
        row = merged.setdefault(
            material_id,
            {
                "material_id": material_id,
                "relation": requirement["relation"],
                "bound_by": [],
                "source_priority": -1,
                "topics": [],
            },
        )
        binding = requirement["bound_by"]
        if binding not in row["bound_by"]:
            row["bound_by"].append(binding)
        topic = requirement.get("topic")
        if topic and topic not in row["topics"]:
            row["topics"].append(topic)
        if requirement["source_priority"] >= row["source_priority"]:
            row["source_priority"] = requirement["source_priority"]
            row["relation"] = requirement["relation"]
            for field in ("purpose", "todo_id", "required_revision_hint"):
                if requirement.get(field):
                    row[field] = requirement[field]
    return merged


def _receipt_sort_key(receipt: Mapping[str, Any]) -> tuple[datetime, str]:
    recorded_at = parse_timestamp(receipt.get("recorded_at"))
    return (
        recorded_at or datetime.min.replace(tzinfo=timezone.utc),
        str(receipt.get("receipt_id") or ""),
    )


def _matching_receipt(
    receipts: Iterable[Mapping[str, Any]],
    *,
    goal_id: str,
    agent_id: str,
    material_id: str,
    todo_id: str | None,
) -> Mapping[str, Any] | None:
    matches: list[Mapping[str, Any]] = []
    for receipt in receipts:
        if str(receipt.get("schema_version") or "") != MATERIAL_USAGE_RECEIPT_SCHEMA_VERSION:
            continue
        if str(receipt.get("goal_id") or "") != goal_id:
            continue
        if str(receipt.get("agent_id") or "") != agent_id:
            continue
        if str(receipt.get("material_id") or "") != material_id:
            continue
        if todo_id and str(receipt.get("todo_id") or "") != todo_id:
            continue
        if not _safe_text(receipt.get("receipt_id"), field="receipt_id"):
            continue
        outcome = str(receipt.get("outcome") or "").strip().lower()
        if outcome not in _RECEIPT_OUTCOMES:
            continue
        if parse_timestamp(receipt.get("recorded_at")) is None:
            continue
        matches.append(receipt)
    return max(matches, key=_receipt_sort_key) if matches else None


def _material_is_accessible(
    material: Mapping[str, Any],
    *,
    available_boundaries: set[str],
) -> bool:
    boundary = str(material.get("boundary") or "unknown").strip().lower()
    gate_status = str(material.get("gate_status") or "").strip().lower()
    if gate_status in _BLOCKED_GATE_STATES:
        return False
    return boundary in _PUBLIC_BOUNDARIES or boundary in available_boundaries


def _frontier_state(
    *,
    material: Mapping[str, Any] | None,
    receipt: Mapping[str, Any] | None,
    required_revision: str | None,
    available_boundaries: set[str],
) -> str:
    if material is None:
        return "missing"
    if not _material_is_accessible(material, available_boundaries=available_boundaries):
        return "inaccessible"
    if receipt and str(receipt.get("outcome") or "").strip().lower() == "unavailable":
        return "inaccessible"
    if receipt is None:
        return "required_unread"
    outcome = str(receipt.get("outcome") or "").strip().lower()
    if outcome not in _RECEIPT_CURRENT_OUTCOMES:
        return "stale"
    freshness = str(material.get("freshness") or material.get("status") or "").strip().lower()
    observed_revision = _safe_text(
        receipt.get("observed_revision"),
        field="receipt observed_revision",
    )
    if freshness in _STALE_FRESHNESS:
        return "stale"
    if required_revision and observed_revision == required_revision:
        return "current"
    return "stale"


def _required_read(item: Mapping[str, Any]) -> dict[str, Any] | None:
    state = str(item.get("state") or "")
    if state == "current":
        return None
    reasons = {
        "missing": "material is not registered in canonical goal authority",
        "inaccessible": "material boundary or gate does not allow the current read",
        "stale": "observed revision or authority freshness is stale",
        "required_unread": "no matching material usage receipt exists",
    }
    return {
        "kind": "material_frontier_item",
        "material_id": item.get("material_id"),
        "required_revision": item.get("required_revision"),
        "state": state,
        "reason": reasons.get(state, "material requires inspection"),
    }


def build_agent_material_frontier(
    *,
    goal_id: str,
    agent_id: str,
    authority_registry: Mapping[str, Any],
    agent_profile: Mapping[str, Any] | None = None,
    todos: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    vision: Mapping[str, Any] | None = None,
    handoffs: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    receipts: Iterable[Mapping[str, Any]] | None = None,
    available_boundaries: Iterable[str] = (),
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Derive one agent's material consumption frontier from goal-owned facts.

    The builder is deliberately read-only. Requirements may be attached to an
    agent profile, todo, vision, or handoff, while revision consumption is
    proven only by an agent-scoped ``material_usage_receipt_v0`` row.
    """

    safe_goal_id = _required_text(goal_id, field="goal_id")
    safe_agent_id = _required_text(agent_id, field="agent_id")
    safe_generated_at = _safe_text(generated_at, field="generated_at")
    if safe_generated_at and parse_timestamp(safe_generated_at) is None:
        raise ValueError("generated_at must be an ISO timestamp")
    materials = _project_materials(authority_registry)
    topic_materials, material_topics = authority_topic_index(authority_registry)
    requirements = _collect_requirements(
        agent_id=safe_agent_id,
        topic_materials=topic_materials,
        agent_profile=agent_profile,
        todos=todos,
        vision=vision,
        handoffs=handoffs,
    )
    safe_boundaries = {
        boundary.lower()
        for raw in available_boundaries
        if (boundary := _safe_text(raw, field="available boundary", limit=80))
    }
    receipt_rows = _mapping_list(receipts)

    items: list[dict[str, Any]] = []
    for material_id, requirement in requirements.items():
        material = materials.get(material_id)
        authority_revision = _safe_text(
            material.get("revision") if material else None,
            field="authority revision",
        )
        required_revision = authority_revision or requirement.get("required_revision_hint")
        todo_id = requirement.get("todo_id")
        receipt = _matching_receipt(
            receipt_rows,
            goal_id=safe_goal_id,
            agent_id=safe_agent_id,
            material_id=material_id,
            todo_id=todo_id,
        )
        state = _frontier_state(
            material=material,
            receipt=receipt,
            required_revision=required_revision,
            available_boundaries=safe_boundaries,
        )
        topics = list(material_topics.get(material_id, []))
        for topic in requirement.get("topics") or []:
            if topic not in topics:
                topics.append(topic)
        item: dict[str, Any] = {
            "material_id": material_id,
            "topics": topics,
            "relation": requirement["relation"],
            "bound_by": requirement["bound_by"],
            "purpose": requirement.get("purpose"),
            "todo_id": todo_id,
            "required_revision": required_revision,
            "observed_revision": _safe_text(
                receipt.get("observed_revision") if receipt else None,
                field="receipt observed_revision",
            ),
            "state": state,
            "boundary": _safe_text(
                material.get("boundary") if material else None,
                field="material boundary",
                limit=80,
            ),
            "gate_status": _safe_text(
                material.get("gate_status") if material else None,
                field="material gate_status",
                limit=80,
            ),
            "receipt_ref": (
                f"material_receipt:{_required_text(receipt.get('receipt_id'), field='receipt_id')}"
                if receipt and receipt.get("receipt_id")
                else None
            ),
            "last_verified_at": _safe_text(
                receipt.get("recorded_at") if receipt else None,
                field="receipt recorded_at",
            ),
            "_source_priority": requirement["source_priority"],
        }
        items.append(
            {
                key: value
                for key, value in item.items()
                if value not in (None, "", [], {})
            }
        )

    items.sort(
        key=lambda item: (
            -int(item.get("_source_priority") or 0),
            _STATE_RANK.get(str(item.get("state") or ""), 9),
            str(item.get("material_id") or ""),
        )
    )
    for item in items:
        item.pop("_source_priority", None)

    summary = {
        "required_count": len(items),
        "current_count": sum(item.get("state") == "current" for item in items),
        "stale_count": sum(item.get("state") == "stale" for item in items),
        "missing_count": sum(item.get("state") == "missing" for item in items),
        "inaccessible_count": sum(item.get("state") == "inaccessible" for item in items),
        "required_unread_count": sum(item.get("state") == "required_unread" for item in items),
    }
    required_reads = [read for item in items if (read := _required_read(item))]
    return {
        "schema_version": AGENT_MATERIAL_FRONTIER_SCHEMA_VERSION,
        "goal_id": safe_goal_id,
        "agent_id": safe_agent_id,
        "generated_at": safe_generated_at or now_utc_iso(),
        "summary": summary,
        "items": items,
        "required_reads": required_reads,
        "truth_contract": {
            "authority_is_goal_owned": True,
            "projection_is_read_only": True,
            "introduces_task_runtime": False,
            "grants_cross_agent_authority": False,
            "evidence_log_implies_material_read": False,
            "raw_source_body_recorded": False,
        },
    }
