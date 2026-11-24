from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ..markdown import markdown_code, markdown_table_row, markdown_table_separator


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _mapping_list(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _int_field(mapping: Mapping[str, object], key: str, default: int = 0) -> int:
    value = mapping.get(key)
    if isinstance(value, bool):
        return int(value) if value else default
    return int(value) if isinstance(value, (int, float, str)) and value != "" else default


def _markdown_report_lines(
    title: str, payload: Mapping[str, object], header_fields: Sequence[tuple[str, object]],
    *, columns: Sequence[str] = (), rows: Iterable[Sequence[object]] = (),
) -> list[str] | str:
    if not payload.get("ok"):
        return f"# {title}\n\n- ok: `False`\n- error: {payload.get('error')}"
    lines = [f"# {title}", "", *(f"- {label}: `{value}`" for label, value in header_fields)]
    if columns:
        lines.extend(["", markdown_table_row(list(columns)), markdown_table_separator(len(columns))])
        lines.extend(markdown_table_row(list(row)) for row in rows)
    return lines


def render_index_collision_rebuild_markdown(payload: Mapping[str, object]) -> str:
    plan = _as_mapping(payload.get("review_plan"))
    lines = _markdown_report_lines(
        "LoopX Artifact Collision Rebuild",
        payload,
        [
            ("dry_run", payload.get("dry_run")),
            ("rebuilt", payload.get("rebuilt")),
            ("collision_groups", payload.get("collision_group_count")),
            ("total_collision_groups", payload.get("total_collision_group_count")),
            ("truncated", payload.get("truncated")),
            ("review_required", payload.get("review_required")),
            ("plan_sha256", plan.get("plan_sha256")),
            ("destructive_row_deletion", False),
            ("contract", "review the exact plan, then execute with --review-plan-json"),
        ],
    )
    return lines if isinstance(lines, str) else "\n".join(lines)


def render_index_duplicate_repair_markdown(payload: Mapping[str, object]) -> str:
    rendered = _markdown_report_lines(
        "LoopX Index Duplicate Repair",
        payload,
        [
            (label, payload.get(key))
            for label, key in (
                ("dry_run", "dry_run"), ("repaired", "repaired"), ("runtime_root", "runtime_root"),
                ("registry", "registry"), ("goal_filter", "goal_filter"), ("checked_goals", "checked_goal_count"),
                ("raw_index_records", "raw_index_records"), ("removed_rows", "removed_row_count"),
                ("preserved_reward_overlay_rows", "preserved_reward_overlay_rows"),
                ("unrepaired_groups", "unrepaired_group_count"), ("truncated", "truncated"),
            )
        ],
        columns=("goal", "generated_at", "action", "repairable", "kept", "removed", "reason"),
        rows=(
            (
                markdown_code(group.get("goal_id")), markdown_code(group.get("generated_at")),
                markdown_code(group.get("action")), markdown_code(group.get("repairable")),
                markdown_code(group.get("kept_line_numbers")), markdown_code(group.get("removed_line_numbers")),
                group.get("reason"),
            )
            for group in _mapping_list(payload.get("groups"))
        ),
    )
    return rendered if isinstance(rendered, str) else "\n".join(rendered)


def render_history_markdown(payload: Mapping[str, object]) -> str:
    goals = _mapping_list(payload.get("goals"))
    unreadable = sum(_int_field(goal, "unreadable_index_rows") for goal in goals)
    lines = _markdown_report_lines(
        "LoopX Run History",
        payload,
        [
            (label, payload.get(key))
            for label, key in (
                ("runtime_root", "runtime_root"), ("registry", "registry"),
                ("goal_filter", "goal_filter"), ("goals", "goal_count"), ("unique_runs", "run_count"),
            )
        ],
        columns=("generated_at", "goal", "classification", "files", "action"),
        rows=(
            (
                markdown_code(run.get("generated_at")), markdown_code(run.get("goal_id")),
                markdown_code(run.get("classification")),
                f"{run.get('json_exists')}/{run.get('markdown_exists')}", run.get("recommended_action"),
            )
            for run in _mapping_list(payload.get("runs"))
        ),
    )
    if isinstance(lines, str):
        return lines
    time_filter = _as_mapping(payload.get("time_filter"))
    if time_filter:
        lines.extend([
            "", "## Time window",
            f"- since (inclusive): {markdown_code(time_filter.get('since_inclusive'))}",
            f"- until (exclusive): {markdown_code(time_filter.get('until_exclusive'))}",
            f"- excluded undated runs: {time_filter.get('excluded_undated_runs')}",
            "- Quota accounting remains Goal-wide; this window only filters history readback.",
        ])
    if unreadable:
        lines.insert(
            7,
            f"- WARNING unreadable_index_rows: `{unreadable}` "
            "(rows that could not be decoded were skipped; the listing below is incomplete)",
        )
    lines.extend(["", "## Goals"])
    for goal in goals:
        unreadable_suffix = (
            f", unreadable_index_rows=`{goal.get('unreadable_index_rows')}`"
            if goal.get("unreadable_index_rows") else ""
        )
        lines.append(
            f"- `{goal.get('id')}`: status=`{goal.get('status')}`, "
            f"registry_member=`{goal.get('registry_member')}`, "
            f"adapter=`{goal.get('adapter_kind')}:{goal.get('adapter_status')}`, "
            f"index_exists=`{goal.get('index_exists')}`, "
            f"records=`{goal.get('raw_index_records')}`, "
            f"unique_runs=`{goal.get('unique_runs')}`{unreadable_suffix}"
        )
    return "\n".join(lines)


def render_index_duplicate_inspection_markdown(payload: Mapping[str, object]) -> str:
    def row(group: Mapping[str, object]) -> tuple[object, ...]:
        raw_classes = group.get("classifications")
        class_items = (
            raw_classes
            if isinstance(raw_classes, Sequence) and not isinstance(raw_classes, (str, bytes))
            else []
        )
        return (
            markdown_code(group.get("goal_id")), markdown_code(group.get("generated_at")),
            markdown_code(group.get("duplicate_kind")), markdown_code(group.get("severity")),
            markdown_code(group.get("line_numbers")),
            markdown_code(", ".join(str(item) for item in class_items)),
            group.get("repair_hint"),
        )

    rendered = _markdown_report_lines(
        "LoopX Index Duplicate Inspection",
        payload,
        [
            (label, payload.get(key))
            for label, key in (
                ("runtime_root", "runtime_root"), ("registry", "registry"), ("goal_filter", "goal_filter"),
                ("checked_goals", "checked_goal_count"), ("raw_index_records", "raw_index_records"),
                ("duplicate_groups", "duplicate_group_count"), ("duplicate_rows", "duplicate_row_count"),
                ("truncated", "truncated"),
            )
        ],
        columns=("goal", "generated_at", "kind", "severity", "rows", "classifications", "repair_hint"),
        rows=(row(group) for group in _mapping_list(payload.get("groups"))),
    )
    return rendered if isinstance(rendered, str) else "\n".join(rendered)
