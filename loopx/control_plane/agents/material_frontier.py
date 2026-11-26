from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from ..runtime.public_safety import public_safe_compact_text
from ..runtime.time import now_utc_iso, parse_timestamp

Payload = dict[str, object]
PayloadView = Mapping[str, object]

AGENT_MATERIAL_FRONTIER_SCHEMA_VERSION = "agent_material_frontier_v0"
MATERIAL_USAGE_RECEIPT_SCHEMA_VERSION = "material_usage_receipt_v0"

_PUBLIC_BOUNDARIES = frozenset({"public", "public_safe"})
_BLOCKED_GATE_STATES = frozenset({
    "blocked", "denied", "forbidden", "missing_authority", "owner_gate",
    "owner_review_required", "private_read_required", "unavailable", "user_gate",
})
_STALE_FRESHNESS = frozenset({"needs_refresh", "outdated", "stale", "unknown"})
_RELATIONS = frozenset({"required", "producer", "reviewer", "maintainer", "watcher"})
_RECEIPT_OUTCOMES = frozenset({"read", "unavailable", "used", "verified"})
_RECEIPT_CURRENT_OUTCOMES = frozenset({"read", "used", "verified"})
_REQUIREMENT_FIELDS = ("material_refs", "required_material_refs", "required_materials")
_TOPIC_FIELDS = ("material_topics", "required_material_topics", "topic_defaults")
_SOURCE_PRIORITY: dict[str, int] = {"profile": 10, "vision": 20, "todo": 30, "handoff": 30}
_STATE_RANK: dict[str, int] = {
    "missing": 0, "inaccessible": 1, "stale": 2, "required_unread": 3, "current": 4,
}
_REQUIRED_READ_REASONS: dict[str, str] = {
    "missing": "material is not registered in canonical goal authority",
    "inaccessible": "material boundary or gate does not allow the current read",
    "stale": "observed revision or authority freshness is stale",
    "required_unread": "no matching material usage receipt exists",
}
_TRUTH_CONTRACT: dict[str, bool] = {
    "authority_is_goal_owned": True,
    "projection_is_read_only": True,
    "introduces_task_runtime": False,
    "grants_cross_agent_authority": False,
    "evidence_log_implies_material_read": False,
    "raw_source_body_recorded": False,
}


@dataclass(frozen=True, slots=True)
class _RequirementBinding:
    """Normalized material requirement emitted from one owner record."""

    material_id: str
    relation: str
    bound_by: dict[str, str]
    source_priority: int
    purpose: str | None = None
    todo_id: str | None = None
    required_revision_hint: str | None = None
    topic: str | None = None


@dataclass(slots=True)
class _MergedRequirement:
    """Merged material requirement accumulator per material_id."""

    material_id: str
    relation: str
    bound_by: list[dict[str, str]]
    source_priority: int
    topics: list[str]
    purpose: str | None = None
    todo_id: str | None = None
    required_revision_hint: str | None = None


@dataclass(frozen=True, slots=True)
class _ValidatedReceipt:
    """Pre-validated receipt with pre-parsed chronological tie-breaker."""

    receipt: PayloadView
    sort_key: tuple[datetime, str]
    receipt_id: str


def _safe_text(value: object, *, field: str, limit: int = 180) -> str | None:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return None
    if len(text) > limit:
        raise ValueError(f"{field} must be at most {limit} characters")
    if public_safe_compact_text(text, limit=limit) != text:
        raise ValueError(f"{field} must be public-safe")
    return text


def _required_text(value: object, *, field: str, limit: int = 180) -> str:
    text = _safe_text(value, field=field, limit=limit)
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _opt_text(row: PayloadView | None, key: str, *, field: str, limit: int = 180) -> str | None:
    return _safe_text(row.get(key) if row is not None else None, field=field, limit=limit)


def _mapping_list(value: object) -> list[PayloadView]:
    if isinstance(value, Mapping):
        return [cast(PayloadView, value)]
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return [cast(PayloadView, item) for item in value if isinstance(item, Mapping)]
    return []


def _project_materials(authority_registry: PayloadView) -> dict[str, PayloadView]:
    if "project_materials" not in authority_registry:
        raise ValueError(
            "agent material frontier requires canonical authority_registry.project_materials; "
            "a compact authority summary is not sufficient"
        )
    raw = authority_registry.get("project_materials")
    if isinstance(raw, Mapping):
        raw_pairs: list[tuple[object, object]] = list(raw.items())
        err_msg = "authority_registry.project_materials values must be objects"
    elif isinstance(raw, list):
        raw_pairs = [(None, item) for item in raw]
        err_msg = "authority_registry.project_materials entries must be objects"
    else:
        raise ValueError("authority_registry.project_materials must be a map or list")

    # Validate element shapes across the full collection before id checks so error precedence matches.
    if any(not isinstance(raw_item, Mapping) for _, raw_item in raw_pairs):
        raise ValueError(err_msg)

    materials: dict[str, PayloadView] = {}
    for raw_id, raw_item in raw_pairs:
        item = cast(PayloadView, raw_item)
        material_id = _required_text(
            item.get("id") or item.get("source_id") or raw_id,
            field="authority material_id",
        )
        if material_id in materials:
            raise ValueError(f"duplicate authority material_id: {material_id}")
        materials[material_id] = item
    return materials


def _topic_material_ids(value: object) -> list[str]:
    raw_items: list[object] = value if isinstance(value, list) else [value]
    material_ids: list[str] = []
    for raw in raw_items:
        candidate = (
            raw.get("material_id") or raw.get("id") or raw.get("source_id")
            if isinstance(raw, Mapping)
            else raw
        )
        material_id = _safe_text(candidate, field="topic material_id")
        if material_id and material_id not in material_ids:
            material_ids.append(material_id)
    return material_ids


def authority_topic_index(
    authority_registry: PayloadView,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
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


def _owner_field_values(owner: PayloadView, fields: tuple[str, ...]) -> list[tuple[str, object]]:
    """Collect ``(field, value)`` pairs across canonical single/list owner keys."""
    pairs: list[tuple[str, object]] = []
    for field in fields:
        raw = owner.get(field)
        if isinstance(raw, list):
            pairs.extend((field, item) for item in raw)
        elif raw not in (None, ""):
            pairs.append((field, raw))
    return pairs


def _owner_ref(owner: PayloadView, *, kind: str, agent_id: str) -> str:
    candidates: tuple[object, ...] = {
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
    raw: object,
    *,
    kind: str,
    owner: PayloadView,
    agent_id: str,
    default_relation: str,
    topic: str | None = None,
) -> _RequirementBinding:
    payload: PayloadView = cast(PayloadView, raw) if isinstance(raw, Mapping) else {"material_id": raw}
    material_id = _required_text(
        payload.get("material_id") or payload.get("id") or payload.get("source_id"),
        field=f"{kind} material_id",
    )
    relation = str(payload.get("relation") or default_relation).strip().lower()
    if relation not in _RELATIONS:
        raise ValueError(f"{kind} material relation must be one of {sorted(_RELATIONS)}")
    return _RequirementBinding(
        material_id=material_id,
        relation=relation,
        bound_by={"kind": kind, "ref": _owner_ref(owner, kind=kind, agent_id=agent_id)},
        source_priority=_SOURCE_PRIORITY[kind],
        purpose=_safe_text(payload.get("purpose"), field=f"{kind} purpose", limit=220),
        todo_id=_safe_text(payload.get("todo_id") or owner.get("todo_id"), field=f"{kind} todo_id"),
        required_revision_hint=_safe_text(
            payload.get("required_revision"), field=f"{kind} required_revision"
        ),
        topic=topic,
    )


def _requirements_from_owner(
    owner: PayloadView,
    *,
    kind: str,
    agent_id: str,
    topic_materials: Mapping[str, list[str]],
    default_relation: str,
) -> list[_RequirementBinding]:
    requirements = [
        _normalize_requirement(
            raw, kind=kind, owner=owner, agent_id=agent_id, default_relation=default_relation
        )
        for _, raw in _owner_field_values(owner, _REQUIREMENT_FIELDS)
    ]
    seen_topics: list[str] = []
    for field, value in _owner_field_values(owner, _TOPIC_FIELDS):
        topic = _safe_text(value, field=f"{field} topic", limit=120)
        if topic and topic not in seen_topics:
            seen_topics.append(topic)
    topic_relation = "watcher" if kind == "profile" else default_relation
    for topic in seen_topics:
        if topic not in topic_materials:
            raise ValueError(f"{kind} material topic is not registered in goal authority: {topic}")
        for material_id in topic_materials[topic]:
            requirements.append(
                _normalize_requirement(
                    material_id, kind=kind, owner=owner, agent_id=agent_id,
                    default_relation=topic_relation, topic=topic,
                )
            )
    return requirements


def _collect_requirements(
    *,
    agent_id: str,
    topic_materials: Mapping[str, list[str]],
    agent_profile: PayloadView | None,
    todos: Iterable[PayloadView] | PayloadView | None,
    vision: PayloadView | None,
    handoffs: Iterable[PayloadView] | PayloadView | None,
) -> dict[str, _MergedRequirement]:
    raw_requirements: list[_RequirementBinding] = []
    for kind, owner, default_relation in (
        ("profile", agent_profile, "watcher"),
        ("vision", vision, "required"),
    ):
        if isinstance(owner, Mapping):
            owner_agent = _safe_text(owner.get("agent_id"), field=f"{kind} agent_id")
            if owner_agent and owner_agent != agent_id:
                raise ValueError(f"agent {kind} must belong to the selected agent_id")
            raw_requirements.extend(
                _requirements_from_owner(
                    owner, kind=kind, agent_id=agent_id,
                    topic_materials=topic_materials, default_relation=default_relation,
                )
            )

    for kind, owners, field_name, agent_keys in (
        ("todo", todos, "todo agent_id", ("claimed_by", "agent_id")),
        ("handoff", handoffs, "handoff target", ("to_agent",)),
    ):
        for owner in _mapping_list(owners):
            raw_agent = next((cand for key in agent_keys if (cand := owner.get(key))), None)
            owner_agent = _safe_text(raw_agent, field=field_name)
            if owner_agent and owner_agent != agent_id:
                continue
            raw_requirements.extend(
                _requirements_from_owner(
                    owner, kind=kind, agent_id=agent_id,
                    topic_materials=topic_materials, default_relation="required",
                )
            )

    merged: dict[str, _MergedRequirement] = {}
    for req in raw_requirements:
        row = merged.get(req.material_id)
        if row is None:
            row = _MergedRequirement(
                material_id=req.material_id, relation=req.relation,
                bound_by=[], source_priority=-1, topics=[],
            )
            merged[req.material_id] = row
        if req.bound_by not in row.bound_by:
            row.bound_by.append(req.bound_by)
        if req.topic and req.topic not in row.topics:
            row.topics.append(req.topic)
        if req.source_priority >= row.source_priority:
            row.source_priority = req.source_priority
            row.relation = req.relation
            if req.purpose:
                row.purpose = req.purpose
            if req.todo_id:
                row.todo_id = req.todo_id
            if req.required_revision_hint:
                row.required_revision_hint = req.required_revision_hint
    return merged


def _validate_receipt(
    receipt: PayloadView, *, goal_id: str, agent_id: str, material_id: str, todo_id: str | None
) -> _ValidatedReceipt | None:
    if str(receipt.get("schema_version") or "") != MATERIAL_USAGE_RECEIPT_SCHEMA_VERSION:
        return None
    if str(receipt.get("goal_id") or "") != goal_id or str(receipt.get("agent_id") or "") != agent_id:
        return None
    if str(receipt.get("material_id") or "") != material_id:
        return None
    if todo_id and str(receipt.get("todo_id") or "") != todo_id:
        return None
    receipt_id = _safe_text(receipt.get("receipt_id"), field="receipt_id")
    if not receipt_id or str(receipt.get("outcome") or "").strip().lower() not in _RECEIPT_OUTCOMES:
        return None
    recorded_at = parse_timestamp(receipt.get("recorded_at"))
    if recorded_at is None:
        return None
    return _ValidatedReceipt(
        receipt=receipt,
        sort_key=(recorded_at, str(receipt.get("receipt_id") or "")),
        receipt_id=receipt_id,
    )


def _index_matching_receipts(
    receipts: Iterable[PayloadView],
    *,
    goal_id: str,
    agent_id: str,
    required_todos: Mapping[str, str | None],
) -> dict[str, _ValidatedReceipt]:
    """Index the latest matching receipt per required material in one pass."""
    if not required_todos:
        return {}
    indexed: dict[str, _ValidatedReceipt] = {}
    for receipt in receipts:
        material_id = str(receipt.get("material_id") or "")
        if material_id not in required_todos:
            continue
        validated = _validate_receipt(
            receipt, goal_id=goal_id, agent_id=agent_id,
            material_id=material_id, todo_id=required_todos[material_id],
        )
        if validated is None:
            continue
        current = indexed.get(material_id)
        if current is None or validated.sort_key > current.sort_key:
            indexed[material_id] = validated
    return indexed


def _material_is_accessible(material: PayloadView, *, available_boundaries: frozenset[str]) -> bool:
    boundary = str(material.get("boundary") or "unknown").strip().lower()
    gate_status = str(material.get("gate_status") or "").strip().lower()
    if gate_status in _BLOCKED_GATE_STATES:
        return False
    return boundary in _PUBLIC_BOUNDARIES or boundary in available_boundaries


def _frontier_state(
    *,
    material: PayloadView | None,
    receipt: PayloadView | None,
    required_revision: str | None,
    available_boundaries: frozenset[str],
) -> str:
    if material is None:
        return "missing"
    if not _material_is_accessible(material, available_boundaries=available_boundaries):
        return "inaccessible"
    if receipt is None:
        return "required_unread"
    outcome = str(receipt.get("outcome") or "").strip().lower()
    if outcome == "unavailable":
        return "inaccessible"
    if outcome not in _RECEIPT_CURRENT_OUTCOMES:
        return "stale"
    freshness = str(material.get("freshness") or material.get("status") or "").strip().lower()
    observed_revision = _safe_text(receipt.get("observed_revision"), field="receipt observed_revision")
    if freshness in _STALE_FRESHNESS:
        return "stale"
    return "current" if required_revision and observed_revision == required_revision else "stale"


def _required_read(item: PayloadView) -> Payload | None:
    state = str(item.get("state") or "")
    if state == "current":
        return None
    return {
        "kind": "material_frontier_item",
        "material_id": item.get("material_id"),
        "required_revision": item.get("required_revision"),
        "state": state,
        "reason": _REQUIRED_READ_REASONS.get(state, "material requires inspection"),
    }


def build_agent_material_frontier(
    *,
    goal_id: str,
    agent_id: str,
    authority_registry: PayloadView,
    agent_profile: PayloadView | None = None,
    todos: Iterable[PayloadView] | PayloadView | None = None,
    vision: PayloadView | None = None,
    handoffs: Iterable[PayloadView] | PayloadView | None = None,
    receipts: Iterable[PayloadView] | PayloadView | None = None,
    available_boundaries: Iterable[str] = (),
    generated_at: str | None = None,
) -> Payload:
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
        agent_id=safe_agent_id, topic_materials=topic_materials,
        agent_profile=agent_profile, todos=todos, vision=vision, handoffs=handoffs,
    )
    safe_boundaries = frozenset(
        boundary.lower()
        for raw in available_boundaries
        if (boundary := _safe_text(raw, field="available boundary", limit=80))
    )
    receipts_by_material = _index_matching_receipts(
        _mapping_list(receipts),
        goal_id=safe_goal_id,
        agent_id=safe_agent_id,
        required_todos={mid: req.todo_id for mid, req in requirements.items()},
    )

    sortable_items: list[tuple[tuple[int, int, str], Payload]] = []
    state_counts = {state: 0 for state in _STATE_RANK}
    for material_id, requirement in requirements.items():
        material = materials.get(material_id)
        authority_revision = _opt_text(material, "revision", field="authority revision")
        required_revision = authority_revision or requirement.required_revision_hint
        validated = receipts_by_material.get(material_id)
        receipt = validated.receipt if validated is not None else None
        state = _frontier_state(
            material=material, receipt=receipt,
            required_revision=required_revision, available_boundaries=safe_boundaries,
        )
        state_counts[state] = state_counts.get(state, 0) + 1
        topics = list(material_topics.get(material_id, []))
        for topic in requirement.topics:
            if topic not in topics:
                topics.append(topic)
        raw_item: Payload = {
            "material_id": material_id,
            "topics": topics,
            "relation": requirement.relation,
            "bound_by": requirement.bound_by,
            "purpose": requirement.purpose,
            "todo_id": requirement.todo_id,
            "required_revision": required_revision,
            "observed_revision": _opt_text(receipt, "observed_revision", field="receipt observed_revision"),
            "state": state,
            "boundary": _opt_text(material, "boundary", field="material boundary", limit=80),
            "gate_status": _opt_text(material, "gate_status", field="material gate_status", limit=80),
            "receipt_ref": f"material_receipt:{validated.receipt_id}" if validated else None,
            "last_verified_at": _opt_text(receipt, "recorded_at", field="receipt recorded_at"),
        }
        compact_item = {k: v for k, v in raw_item.items() if v not in (None, "", [], {})}
        sort_key = (-requirement.source_priority, _STATE_RANK.get(state, 9), material_id)
        sortable_items.append((sort_key, compact_item))

    sortable_items.sort(key=lambda entry: entry[0])
    items = [entry[1] for entry in sortable_items]
    required_reads = [read for item in items if (read := _required_read(item))]
    summary = {
        "required_count": len(items),
        "current_count": state_counts["current"],
        "stale_count": state_counts["stale"],
        "missing_count": state_counts["missing"],
        "inaccessible_count": state_counts["inaccessible"],
        "required_unread_count": state_counts["required_unread"],
    }
    return {
        "schema_version": AGENT_MATERIAL_FRONTIER_SCHEMA_VERSION,
        "goal_id": safe_goal_id,
        "agent_id": safe_agent_id,
        "generated_at": safe_generated_at or now_utc_iso(),
        "summary": summary,
        "items": items,
        "required_reads": required_reads,
        "truth_contract": _TRUTH_CONTRACT.copy(),
    }
