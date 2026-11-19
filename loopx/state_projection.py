from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import re

from .control_plane.goals.active_state_metadata import todo_role_for_heading
from .control_plane.status.active_state_projection import (
    BACKLOG_HYGIENE_BULLET_PATTERN,
    SECTION_HEADING_PATTERN,
)
from .control_plane.todos.contract import (
    TODO_TASK_PATTERN,
    build_todo_id,
    compact_todo_text,
    normalize_required_capabilities,
    normalize_required_write_scopes,
    normalize_target_capabilities,
    normalize_todo_action_kind,
    normalize_todo_blocks_agent,
    normalize_todo_bound_agent,
    normalize_todo_capability_binding_ref,
    normalize_todo_claimed_by,
    normalize_todo_continuation_policy,
    normalize_todo_decision_scope,
    normalize_todo_excluded_agents,
    normalize_todo_global_gate,
    normalize_todo_goal_bound,
    normalize_todo_id,
    normalize_todo_id_list,
    normalize_todo_no_followup,
    normalize_todo_required_decision_scopes,
    normalize_todo_resume_when,
    normalize_todo_status,
    normalize_todo_task_domain,
    normalize_todo_task_repository,
    normalize_todo_task_class,
    parse_todo_metadata_line,
    todo_done_for_status,
    todo_status_from_marker,
)


STATE_PROJECTION_GAP_SCHEMA_VERSION = "state_projection_gap_v0"
NEXT_ACTION_PROJECTION_WARNING_SCHEMA_VERSION = "next_action_projection_warning_v0"
STATE_ACTION_PROJECTION_WARNING_SCHEMA_VERSION = "state_action_projection_warning_v0"
ACTIVE_STATE_STRUCTURED_PROJECTION_SCHEMA_VERSION = "active_state_structured_projection_v0"
ACTIVE_STATE_PROJECTION_DIAGNOSTICS_SCHEMA_VERSION = "active_state_projection_diagnostics_v0"
TODO_ITEM_SCHEMA_VERSION = "todo_item_v0"

PRIORITY_PATTERN = re.compile(r"^\[(P[0-4])\]\s+(.+)$", re.IGNORECASE)
NEXT_ACTION_EXECUTABLE_PATTERN = re.compile(
    r"(?i)\b(?:run|repair|fix|implement|add|update|write|record|validate|"
    r"rerun|debug|inspect|analy[sz]e|sync|refresh|test|benchmark|trace|"
    r"replan|expand|split|todo)\b|"
    r"(?:推进|修复|实现|更新|写入|记录|验证|重跑|调试|检查|分析|审计|扩展|"
    r"拆分|补全|规划|待办)"
)
NEXT_ACTION_USER_WAIT_PATTERN = re.compile(
    r"(?i)\b(?:wait(?:ing)? for|await(?:ing)?|blocked by|gated by|"
    r"need(?:s|ed)?|requires?|request(?:s|ed)?|ask(?:ing)? for|pending)"
    r"\b.{0,120}\b(?:owner|user|operator|controller|human|approval|approve|"
    r"decision|permission|choice)\b|"
    r"\b(?:owner|user|operator|controller|human)\s+"
    r"(?:gate|todo|action|decision|approval|permission|choice)\b|"
    r"\b(?:approval|permission)\s+(?:required|needed|pending)\b|"
    r"(?:等待|需要|受阻于|被.{0,20}阻塞).{0,80}"
    r"(?:用户|人工|决策|审批|批准|确认|owner)|"
    r"需(?:用户|人工|owner).{0,40}(?:决策|审批|批准|确认)|"
    r"需(?:决策|审批|批准|确认)|"
    r"请(?:用户|人工|owner).{0,40}(?:决策|审批|批准|确认)|"
    r"请(?:确认|审批|批准)|"
    r"待(?:用户|人工|owner)?(?:决策|审批|批准|确认)"
)
TODO_METADATA_KEYS = (
    "action_kind",
    "task_domain",
    "capability_binding_ref",
    "task_repository",
    "continuation_policy",
    "required_write_scopes",
    "required_capabilities",
    "target_capabilities",
    "decision_scope",
    "required_decision_scopes",
    "claimed_by",
    "bound_agent",
    "goal_bound",
    "blocks_agent",
    "excluded_agents",
    "global_gate",
    "unblocks_todo_id",
    "successor_todo_ids",
    "completion_continuation",
    "completion_recovery",
    "resume_when",
    "no_followup",
    "target_key",
    "cadence",
    "next_due_at",
    "last_checked_at",
    "result_hash",
    "consecutive_no_change",
    "material_change",
    "max_no_change_before_replan",
    "note",
    "evidence",
    "reason",
    "completed_at",
    "updated_at",
    "superseded_by",
)

Payload = dict[str, object]
PayloadView = Mapping[str, object]

_TODO_FIELD_NORMALIZERS: tuple[tuple[str, Callable[[object], object], bool], ...] = (
    ("task_domain", normalize_todo_task_domain, False),
    ("capability_binding_ref", normalize_todo_capability_binding_ref, False),
    ("task_repository", normalize_todo_task_repository, False),
    ("continuation_policy", normalize_todo_continuation_policy, False),
    ("required_write_scopes", normalize_required_write_scopes, False),
    ("required_capabilities", normalize_required_capabilities, False),
    ("target_capabilities", normalize_target_capabilities, False),
    ("decision_scope", normalize_todo_decision_scope, False),
    ("required_decision_scopes", normalize_todo_required_decision_scopes, False),
    ("claimed_by", normalize_todo_claimed_by, False),
    ("bound_agent", normalize_todo_bound_agent, False),
    ("goal_bound", normalize_todo_goal_bound, True),
    ("blocks_agent", normalize_todo_blocks_agent, False),
    ("excluded_agents", normalize_todo_excluded_agents, False),
    ("global_gate", normalize_todo_global_gate, True),
    ("unblocks_todo_id", normalize_todo_id, False),
    ("successor_todo_ids", normalize_todo_id_list, False),
    ("resume_when", normalize_todo_resume_when, False),
    ("no_followup", normalize_todo_no_followup, True),
)


def _as_mapping(value: object) -> PayloadView | None:
    return value if isinstance(value, Mapping) else None


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _compact_text(value: object, *, limit: int = 220) -> str:
    text = compact_todo_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _action_projection_text(value: object, *, limit: int = 320) -> str:
    return _compact_text(value, limit=limit)


def _lane_action_text(agent_lane_next_action: object) -> str:
    lane = _as_mapping(agent_lane_next_action)
    return _action_projection_text(
        lane.get("text") if lane is not None else agent_lane_next_action
    )


def _action_projection_compare_text(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    text = re.sub(r"^todo_[0-9a-z]+:\s*", "", text)
    text = re.sub(r"^p[0-9]+:\s*", "", text)
    text = re.sub(r"^\[(?:p[0-9]+|[^\]]+)\]\s*", "", text)
    return re.sub(r"^(?:agent|user|owner|codex)\s*:\s*", "", text)


def _action_projection_label(value: object) -> str:
    text = _action_projection_compare_text(value)
    match = re.match(r"([^:]{8,120}):", text)
    if match:
        return match.group(1).strip()
    return text[:120].strip()


def _action_projection_prefix(value: object) -> str:
    text = _action_projection_compare_text(value)
    text = re.split(r"[,.;:，。；：]", text, maxsplit=1)[0].strip()
    words = text.split()
    if len(words) >= 4:
        return " ".join(words[:6])
    return text


def actions_are_projection_aligned(left: object, right: object) -> bool:
    left_text = _action_projection_compare_text(left)
    right_text = _action_projection_compare_text(right)
    if not left_text or not right_text:
        return False
    if left_text == right_text:
        return True
    for extractor, min_len in ((_action_projection_label, 8), (_action_projection_prefix, 24)):
        left_part = extractor(left_text)
        right_part = extractor(right_text)
        if (len(left_part) >= min_len and left_part in right_text) or (
            len(right_part) >= min_len and right_part in left_text
        ):
            return True
    shorter, longer = sorted((left_text, right_text), key=len)
    return len(shorter) >= 32 and shorter in longer


def next_action_projection_warning(
    *,
    active_state_next_action: object,
    latest_run_recommended_action: object,
    agent_lane_next_action: object = None,
) -> Payload | None:
    active_text = _action_projection_text(active_state_next_action)
    latest_text = _action_projection_text(latest_run_recommended_action)
    if not active_text or not latest_text or actions_are_projection_aligned(active_text, latest_text):
        return None
    lane = _as_mapping(agent_lane_next_action)
    lane_preserves_goal_next_action = (
        lane is not None and lane.get("preserves_goal_next_action") is True
    )
    warning: Payload = {
        "schema_version": NEXT_ACTION_PROJECTION_WARNING_SCHEMA_VERSION,
        "kind": "next_action_projection_mismatch",
        "severity": "info" if lane_preserves_goal_next_action else "warning",
        "requires_state_writeback": not lane_preserves_goal_next_action,
        "active_state_next_action": active_text,
        "latest_run_recommended_action": latest_text,
        **(
            {
                "reason": (
                    "current agent lane action differs from the durable goal route while "
                    "explicitly preserving the active-state Next Action"
                ),
                "recommended_action": (
                    "continue the agent's selected Todo; refresh-state --next-action records "
                    "a bound task step without replacing shared compatibility prose"
                ),
            }
            if lane_preserves_goal_next_action
            else {
                "reason": (
                    "latest run recommended_action differs from the durable active-state "
                    "Next Action"
                ),
                "recommended_action": (
                    "read the selected Todo route; only a matching bound recommendation "
                    "is a current task step. Historical prose is not execution authority"
                ),
            }
        ),
    }
    lane_text = _lane_action_text(agent_lane_next_action)
    if lane_text:
        warning["agent_lane_next_action"] = lane_text
    return warning


def next_action_resolution_trace(
    *,
    primary_action: object,
    mode: object = None,
    active_state_next_action: object,
    latest_run_recommended_action: object,
    selected_recommended_action: object,
    agent_lane_next_action: object = None,
) -> Payload | None:
    primary_text = _action_projection_text(primary_action)
    if not primary_text:
        return None
    selected_text = _action_projection_text(selected_recommended_action)
    active_text = _action_projection_text(active_state_next_action)
    latest_text = _action_projection_text(latest_run_recommended_action)
    lane_text = _lane_action_text(agent_lane_next_action)

    source = next(
        (
            candidate_source
            for candidate_source, candidate_text in (
                ("agent_lane", lane_text),
                ("selected", selected_text),
                ("active_next", active_text),
                ("latest_run", latest_text),
            )
            if candidate_text and actions_are_projection_aligned(primary_text, candidate_text)
        ),
        f"mode:{mode}" if mode else "interaction_contract",
    )

    has_drift = any(
        left and right and not actions_are_projection_aligned(left, right)
        for left, right in (
            (active_text, latest_text),
            (active_text, primary_text),
            (latest_text, primary_text),
            (selected_text, primary_text),
        )
    )

    return {
        "summary": f"source={source} drift={'true' if has_drift else 'false'}",
    }


def state_action_projection_warning(
    item: PayloadView,
    *,
    agent_todo_summary: PayloadView | None,
    selected_action: object,
    work_lane_contract: PayloadView | None,
) -> Payload | None:
    lane_contract = _as_mapping(work_lane_contract)
    if not (
        lane_contract is not None
        and lane_contract.get("lane") == "advancement_task"
        and "open_agent_todo" in _as_list(lane_contract.get("reason_codes"))
    ):
        return None
    project_asset = _as_mapping(item.get("project_asset")) or {}
    active_next_action = str(
        item.get("active_state_next_action")
        or project_asset.get("next_action")
        or ""
    ).strip()
    selected_text = str(selected_action or "").strip()
    if not active_next_action or not selected_text:
        return None
    todo_summary = _as_mapping(agent_todo_summary)
    if todo_summary is not None:
        claim_scope = _as_mapping(todo_summary.get("claim_scope"))
        selected_item = next(
            (
                entry
                for raw_entry in _as_list(todo_summary.get("first_executable_items"))
                if (entry := _as_mapping(raw_entry)) is not None
            ),
            None,
        )
        selected_claimed_by = normalize_todo_claimed_by(
            selected_item.get("claimed_by") if selected_item is not None else None
        )
        claim_agent_id = normalize_todo_claimed_by(
            claim_scope.get("agent_id") if claim_scope is not None else None
        )
        if (
            selected_item is not None
            and selected_claimed_by
            and claim_agent_id
            and selected_claimed_by == claim_agent_id
        ):
            return None
    if actions_are_projection_aligned(active_next_action, selected_text):
        return None
    return {
        "schema_version": STATE_ACTION_PROJECTION_WARNING_SCHEMA_VERSION,
        "kind": "state_action_projection_mismatch",
        "severity": "warning",
        "requires_state_writeback": True,
        "active_state_next_action": _action_projection_text(active_next_action, limit=320),
        "selected_recommended_action": _action_projection_text(selected_text, limit=320),
        "reason": "quota selected executable backlog while active Next Action differs",
        "recommended_action": (
            "run primary_action; sync active route only on route change"
        ),
    }


def is_user_wait_text(value: object) -> bool:
    return bool(NEXT_ACTION_USER_WAIT_PATTERN.search(str(value or "")))


def _role_for_heading(heading: str) -> str | None:
    """Classify a state heading exactly as the Todo region writer does.

    This module used to carry its own copy of the marker tuples. The copies had
    drifted: they matched bare ``owner``, so a prose section named ``Ownership``
    counted as user Todos; they missed ``codex todo``, which the writer creates;
    and they had no archive guard, so ``Agent Todo Archive`` counted as live
    agent Todos. The open counts feed ``state_projection_gap_warning``, where an
    inflated agent count suppresses the very warning that says a Next Action is
    executable with no agent Todo behind it.
    """
    return todo_role_for_heading(heading)


def _open_count(summary: PayloadView | None) -> int:
    mapping = _as_mapping(summary)
    if mapping is None:
        return 0
    value = mapping.get("open_count")
    if value is None:
        value = mapping.get("open")
    if not value or not isinstance(value, (int, float, str, bytes, bytearray)):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _section_lines(state_text: str, heading: str) -> list[str]:
    current = False
    lines: list[str] = []
    for line in state_text.splitlines():
        match = SECTION_HEADING_PATTERN.match(line)
        if match:
            if current:
                break
            current = match.group(1).strip().lower() == heading.lower()
            continue
        if current:
            lines.append(line)
    return lines


def _section_entries(
    lines: list[str],
    *,
    text_limit: int | None = 220,
) -> list[str]:
    entries: list[str] = []
    current: list[str] = []

    def append_entry(parts: list[str]) -> None:
        text = compact_todo_text(" ".join(parts))
        if not text:
            return
        entries.append(
            text if text_limit is None else _compact_text(text, limit=text_limit)
        )

    for line in lines:
        if line.strip().startswith("<!--"):
            continue
        bullet = BACKLOG_HYGIENE_BULLET_PATTERN.match(line)
        if bullet:
            if current:
                append_entry(current)
            current = [bullet.group(1)]
            continue
        if current and line.startswith((" ", "\t")):
            continuation = line.strip()
            if continuation:
                current.append(continuation)
            continue
        if current:
            append_entry(current)
            current = []
        stripped = line.strip()
        if stripped:
            append_entry([stripped])
    if current:
        append_entry(current)
    return [entry for entry in entries if entry]


def active_state_next_action_entries(
    state_text: str,
    *,
    limit: int | None = 3,
    text_limit: int | None = 220,
) -> list[str]:
    entries = _section_entries(
        _section_lines(state_text, "Next Action"),
        text_limit=text_limit,
    )
    if limit is None:
        return entries
    return entries[: max(0, limit)]


def summarize_state_todo_open_counts(state_text: str) -> dict[str, int]:
    role: str | None = None
    counts = {"user": 0, "agent": 0}
    for line in state_text.splitlines():
        heading = SECTION_HEADING_PATTERN.match(line)
        if heading:
            role = _role_for_heading(heading.group(1))
            continue
        if role is None:
            continue
        match = TODO_TASK_PATTERN.match(line)
        if not match:
            continue
        marker, _text = match.groups()
        if not todo_done_for_status(todo_status_from_marker(marker)):
            counts[role] += 1
    return counts


def parse_active_state_frontmatter(state_text: str) -> dict[str, str]:
    if not state_text.startswith("---"):
        return {}
    parts = state_text.split("---", 2)
    if len(parts) < 3:
        return {}
    result: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key:
            result[key] = value.strip().strip('"')
    return result


def _todo_priority_parts(text: str) -> tuple[str | None, str]:
    match = PRIORITY_PATTERN.match(text)
    if not match:
        return None, text
    return match.group(1).upper(), _compact_text(match.group(2), limit=500)


def _normalize_structured_todo_item(
    item: PayloadView,
    *,
    role: str,
    source_section: str,
) -> Payload:
    text = _compact_text(item.get("text"), limit=500)
    marker_status = todo_status_from_marker(item.get("marker"))
    metadata_status = normalize_todo_status(item.get("status"))
    status = metadata_status or marker_status
    todo_id = normalize_todo_id(item.get("todo_id"))
    todo_id_source = "metadata" if todo_id else "generated"
    todo_id = todo_id or build_todo_id(
        role=role,
        source_section=source_section,
        index=item.get("index"),
        text=text,
    )
    priority, title = _todo_priority_parts(text)
    action_kind = normalize_todo_action_kind(item.get("action_kind"))
    normalized: Payload = {
        "schema_version": TODO_ITEM_SCHEMA_VERSION,
        "todo_id": todo_id,
        "todo_id_source": todo_id_source,
        "role": role,
        "source_section": source_section,
        "index": item.get("index"),
        "status": status,
        "done": todo_done_for_status(status),
        "text": text,
        "task_class": normalize_todo_task_class(
            item.get("task_class"),
            text=text,
            action_kind=action_kind,
        ),
    }
    if priority:
        normalized["priority"] = priority
        normalized["title"] = title
    if action_kind:
        normalized["action_kind"] = action_kind
    for key, normalizer, allow_none_false in _TODO_FIELD_NORMALIZERS:
        value = normalizer(item.get(key))
        if (value is not None) if allow_none_false else bool(value):
            normalized[key] = value

    for key in TODO_METADATA_KEYS:
        if key in normalized or key not in item:
            continue
        value = item.get(key)
        if value not in (None, "", []):
            normalized[key] = value
    return normalized


def _parse_active_state_structured_todo_items(
    state_text: str,
) -> dict[str, list[Payload]]:
    role: str | None = None
    source_sections: dict[str, str] = {}
    raw_items: dict[str, list[Payload]] = {"user": [], "agent": []}
    current: Payload | None = None

    for line in state_text.splitlines():
        heading = SECTION_HEADING_PATTERN.match(line)
        if heading:
            section = heading.group(1).strip()
            role = _role_for_heading(section)
            current = None
            if role and role not in source_sections:
                source_sections[role] = section
            continue
        if role is None:
            continue
        match = TODO_TASK_PATTERN.match(line)
        if match:
            marker, text = match.groups()
            current = {
                "index": len(raw_items[role]) + 1,
                "marker": marker,
                "text": _compact_text(text, limit=500),
            }
            raw_items[role].append(current)
            continue
        if current is None or not line.startswith((" ", "\t")):
            continue
        metadata = parse_todo_metadata_line(line)
        if metadata:
            current.update(metadata)
            continue
        continuation = line.strip()
        if continuation:
            current["text"] = _compact_text(
                f"{current.get('text', '')} {continuation}",
                limit=500,
            )

    return {
        item_role: [
            _normalize_structured_todo_item(
                item,
                role=item_role,
                source_section=source_sections.get(item_role) or f"{item_role.title()} Todo",
            )
            for item in items
        ]
        for item_role, items in raw_items.items()
    }


def _active_state_todo_summary(items: list[Payload]) -> Payload:
    open_items = [item for item in items if item.get("status") == "open"]
    done_items = [item for item in items if item.get("done") is True]
    implicit_items = [item for item in items if item.get("todo_id_source") == "generated"]
    return {
        "total_count": len(items),
        "open_count": len(open_items),
        "done_count": len(done_items),
        "implicit_todo_id_count": len(implicit_items),
        "items": items,
    }


def build_active_state_projection_diagnostics(
    *,
    frontmatter: Mapping[str, object],
    next_action_entries: Sequence[str],
    todo_items: Mapping[str, Sequence[PayloadView]],
) -> Payload:
    warnings: list[Payload] = []
    errors: list[Payload] = []
    all_items = [item for items in todo_items.values() for item in items]
    if not frontmatter:
        warnings.append({"kind": "missing_frontmatter"})
    if not next_action_entries:
        warnings.append({"kind": "missing_next_action"})
    if not all_items:
        warnings.append({"kind": "missing_todo_sections"})
    implicit_items = [item for item in all_items if item.get("todo_id_source") == "generated"]
    if implicit_items:
        warnings.append(
            {
                "kind": "implicit_todo_ids",
                "count": len(implicit_items),
                "recommendation": (
                    "rewrite with loopx todo so every item carries explicit metadata"
                ),
            }
        )

    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for item in all_items:
        todo_id = str(item.get("todo_id") or "")
        seen[todo_id] = seen.get(todo_id, 0) + 1
        if seen[todo_id] == 2:
            duplicates.append(todo_id)
    if duplicates:
        errors.append({"kind": "duplicate_todo_ids", "todo_ids": duplicates})

    return {
        "schema_version": ACTIVE_STATE_PROJECTION_DIAGNOSTICS_SCHEMA_VERSION,
        "parseable": not errors,
        "migration_ready": bool(all_items) and not errors and not implicit_items,
        "warning_count": len(warnings),
        "error_count": len(errors),
        "warnings": warnings,
        "errors": errors,
    }


def build_active_state_structured_projection(
    state_text: str,
    *,
    goal_id: str | None = None,
    source_ref: str = "ACTIVE_GOAL_STATE.md",
) -> Payload:
    frontmatter = parse_active_state_frontmatter(state_text)
    next_entries = active_state_next_action_entries(state_text, limit=None)
    todo_items = _parse_active_state_structured_todo_items(state_text)
    diagnostics = build_active_state_projection_diagnostics(
        frontmatter=frontmatter,
        next_action_entries=next_entries,
        todo_items=todo_items,
    )
    projection: Payload = {
        "schema_version": ACTIVE_STATE_STRUCTURED_PROJECTION_SCHEMA_VERSION,
        "source": "markdown_active_state",
        "source_ref": source_ref,
        "frontmatter": frontmatter,
        "next_action": {
            "count": len(next_entries),
            "first": next_entries[0] if next_entries else None,
            "entries": next_entries,
        },
        "todos": {
            "user": _active_state_todo_summary(todo_items["user"]),
            "agent": _active_state_todo_summary(todo_items["agent"]),
        },
        "diagnostics": diagnostics,
    }
    if goal_id:
        projection["goal_id"] = goal_id
    return projection


def state_projection_gap_warning(
    state_text: str,
    *,
    user_todos: PayloadView | None = None,
    agent_todos: PayloadView | None = None,
) -> Payload | None:
    next_action_entries = _section_entries(_section_lines(state_text, "Next Action"))
    if not next_action_entries:
        return None

    fallback_counts = summarize_state_todo_open_counts(state_text)
    user_open = fallback_counts["user"] if user_todos is None else _open_count(user_todos)
    agent_open = fallback_counts["agent"] if agent_todos is None else _open_count(agent_todos)

    evidence: list[Payload] = []
    for entry in next_action_entries[:3]:
        if agent_open == 0 and NEXT_ACTION_EXECUTABLE_PATTERN.search(entry):
            evidence.append(
                {
                    "kind": "next_action_executable_without_agent_todo",
                    "target_role": "agent",
                    "section": "Next Action",
                    "text": entry,
                }
            )
        if user_open == 0 and is_user_wait_text(entry):
            evidence.append(
                {
                    "kind": "next_action_waits_without_user_todo",
                    "target_role": "user",
                    "section": "Next Action",
                    "text": entry,
                }
            )

    if not evidence:
        return None

    target_roles = sorted(
        {
            str(item.get("target_role") or "")
            for item in evidence
            if item.get("target_role")
        }
    )
    return {
        "schema_version": STATE_PROJECTION_GAP_SCHEMA_VERSION,
        "kind": "state_projection_gap",
        "severity": "warning",
        "requires_todo_expansion": True,
        "agent_open_count": agent_open,
        "user_open_count": user_open,
        "target_roles": target_roles,
        "evidence_count": len(evidence),
        "first_evidence": evidence[:3],
        "recommended_action": (
            "Next Action 与 todo projection 不一致；先把可执行后续工作扩展为 "
            "Agent Todo，或把 owner/user gate 扩展为 User Todo，再继续 heartbeat delivery"
        ),
    }
