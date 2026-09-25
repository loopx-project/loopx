from __future__ import annotations

from typing import Any

from .contract import TODO_STATUS_OPEN, todo_marker_for_status


def render_todo_markdown(payload: dict[str, Any]) -> str:
    if payload.get("command") == "project-markdown":
        return "\n".join(
            [
                "# LoopX Todo Markdown Projection",
                "",
                f"- ok: `{payload.get('ok')}`",
                f"- goal_id: `{payload.get('goal_id')}`",
                f"- dry_run: `{payload.get('dry_run')}`",
                f"- executed: `{payload.get('executed')}`",
                f"- changed: `{payload.get('changed')}`",
                f"- source_authority: `{payload.get('source_authority')}`",
                f"- provider_revision: `{payload.get('provider_revision')}`",
                f"- todo_count: `{payload.get('todo_count')}`",
                f"- parse_render_parity: `{payload.get('parse_render_parity')}`",
                f"- narrative_preserved: `{payload.get('narrative_preserved')}`",
                f"- error: `{payload.get('error')}`" if payload.get("error") else "",
            ]
        ).rstrip()
    if payload.get("command") == "list":
        if payload.get("thin"):
            field_projection = payload.get("todo_list_field_projection")
            field_projection = (
                field_projection if isinstance(field_projection, dict) else {}
            )
            cold_paths = field_projection.get("full_detail_cold_paths") or []
            lines = [
                "# LoopX Todo List",
                "",
                f"- goal_id: `{payload.get('goal_id')}`",
                f"- role: `{payload.get('role')}`",
                f"- status_filter: `{payload.get('status_filter')}`",
                f"- todo_count: `{payload.get('todo_count')}`",
                f"- matched_todo_count: `{payload.get('matched_todo_count')}`",
                f"- returned_todo_count: `{payload.get('returned_todo_count')}`",
                f"- omitted_todo_count: `{payload.get('omitted_todo_count')}`",
                (
                    "- item_limit_per_role: `"
                    f"{field_projection.get('item_limit_per_role')}`"
                ),
                f"- view: `{field_projection.get('view')}`",
                (
                    "- full_detail_cold_path: `"
                    f"{cold_paths[0] if cold_paths else 'todo list without --thin'}`"
                ),
            ]
            for key, heading in (
                ("user_todos", "User Todo"),
                ("agent_todos", "Agent Todo"),
            ):
                summary = payload.get(key)
                if not isinstance(summary, dict):
                    continue
                lines.extend(["", f"## {heading}", ""])
                expected_role = key.removesuffix("_todos")
                items = [
                    item
                    for item in payload.get("todos") or []
                    if isinstance(item, dict)
                    and item.get("role") == expected_role
                ]
                if not items:
                    lines.append("- none")
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    marker = todo_marker_for_status(
                        item.get("status") or TODO_STATUS_OPEN
                    )
                    text = item.get("text") or item.get("title") or ""
                    metadata = [
                        f"{metadata_key}={item.get(metadata_key)}"
                        for metadata_key in (
                            "todo_id",
                            "status",
                            "claimed_by",
                            "blocks_agent",
                            "next_due_at",
                        )
                        if item.get(metadata_key)
                    ]
                    suffix = f" <!-- {' '.join(metadata)} -->" if metadata else ""
                    lines.append(f"- [{marker}] {text}{suffix}")
            return "\n".join(lines)

        lines = [
            "# LoopX Todo List",
            "",
            f"- ok: `{payload.get('ok')}`",
            f"- read_only: `{payload.get('read_only')}`",
            f"- goal_id: `{payload.get('goal_id')}`",
            f"- role: `{payload.get('role')}`",
            f"- status_filter: `{payload.get('status_filter')}`",
            f"- source: `{payload.get('source')}`",
            f"- todo_count: `{payload.get('todo_count')}`",
            f"- state_file: `{payload.get('state_file')}`",
        ]
        if payload.get("agent_id_filter"):
            lines.extend(
                [
                    f"- agent_id_filter: `{payload.get('agent_id_filter')}`",
                    (
                        "- unfiltered_todo_count: `"
                        f"{payload.get('unfiltered_todo_count')}`"
                    ),
                    f"- filter_semantics: `{payload.get('filter_semantics')}`",
                ]
            )
        projection = payload.get("todo_list_projection")
        if isinstance(projection, dict):
            lines.append(
                f"- returned_todo_count: `{payload.get('returned_todo_count')}`"
            )
        for key, heading in (
            ("user_todos", "User Todo"),
            ("agent_todos", "Agent Todo"),
        ):
            summary = payload.get(key)
            if not isinstance(summary, dict):
                continue
            lines.extend(["", f"## {heading}", ""])
            items = summary.get("items") or []
            if not items:
                lines.append("- none")
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                marker = todo_marker_for_status(item.get("status") or TODO_STATUS_OPEN)
                text = item.get("text") or item.get("title") or ""
                metadata = []
                for metadata_key in (
                    "todo_id",
                    "status",
                    "task_class",
                    "action_kind",
                    "task_domain",
                    "task_repository",
                    "continuation_policy",
                    "claimed_by",
                    "bound_agent",
                    "goal_bound",
                    "blocks_agent",
                    "global_gate",
                    "excluded_agents",
                    "target_key",
                    "cadence",
                    "next_due_at",
                    "expires_at",
                ):
                    if item.get(metadata_key):
                        metadata.append(f"{metadata_key}={item.get(metadata_key)}")
                suffix = f" <!-- {' '.join(metadata)} -->" if metadata else ""
                lines.append(f"- [{marker}] {text}{suffix}")
        if payload.get("error"):
            lines.append(f"- error: {payload.get('error')}")
        if payload.get("operator_action"):
            action = payload["operator_action"]
            lines.append(f"- error_code: `{payload.get('error_code')}`")
            lines.append(
                "- operator_action: "
                f"action={action.get('action')} "
                f"holder_pid={action.get('holder_pid') or 'unknown'} "
                f"retry_mode={action.get('retry_mode')}"
            )
        return "\n".join(lines)

    lines = [
        "# LoopX Todo",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- dry_run: `{payload.get('dry_run')}`",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- role: `{payload.get('role')}`",
        f"- section: `{payload.get('section')}`",
        f"- state_file: `{payload.get('state_file')}`",
    ]
    if "moved_count" in payload:
        lines.extend(
            [
                f"- changed: `{payload.get('changed')}`",
                f"- archive_section: `{payload.get('archive_section')}`",
                f"- active_done_before: `{payload.get('active_done_before')}`",
                f"- active_done_after: `{payload.get('active_done_after')}`",
                f"- max_active_done: `{payload.get('max_active_done')}`",
                f"- moved_count: `{payload.get('moved_count')}`",
            ]
        )
    else:
        lines.extend(
            [
                f"- changed: `{payload.get('changed')}`",
                f"- added: `{payload.get('added')}`",
                f"- already_exists: `{payload.get('already_exists')}`",
                f"- todo_id: `{payload.get('todo_id')}`",
                f"- status: `{payload.get('status')}`",
                f"- required_capabilities: `{payload.get('required_capabilities')}`",
                f"- target_capabilities: `{payload.get('target_capabilities')}`",
                f"- claimed_by: `{payload.get('claimed_by')}`",
                f"- bound_agent: `{payload.get('bound_agent')}`",
                f"- goal_bound: `{payload.get('goal_bound')}`",
                f"- blocks_agent: `{payload.get('blocks_agent')}`",
                f"- excluded_agents: `{payload.get('excluded_agents')}`",
                f"- global_gate: `{payload.get('global_gate')}`",
                f"- resume_when: `{payload.get('resume_when')}`",
                f"- target_key: `{payload.get('target_key')}`",
                f"- cadence: `{payload.get('cadence')}`",
                f"- next_due_at: `{payload.get('next_due_at')}`",
                f"- expires_at: `{payload.get('expires_at')}`",
            ]
        )
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
        if payload.get("operator_action"):
            action = payload["operator_action"]
            lines.append(f"- error_code: `{payload.get('error_code')}`")
            lines.append(
                "- operator_action: "
                f"action={action.get('action')} "
                f"holder_pid={action.get('holder_pid') or 'unknown'} "
                f"retry_mode={action.get('retry_mode')}"
            )
    elif "todo" in payload:
        marker = todo_marker_for_status(payload.get("status") or TODO_STATUS_OPEN)
        lines.extend(["", "## Todo", "", f"- [{marker}] {payload.get('todo')}"])
    correctness = payload.get("local_state_write_correctness")
    if isinstance(correctness, dict):
        intent = correctness.get("write_intent") if isinstance(correctness.get("write_intent"), dict) else {}
        apply_result = (
            correctness.get("apply_result")
            if isinstance(correctness.get("apply_result"), dict)
            else {}
        )
        lines.extend(
            [
                "",
                "## Local State Write Correctness",
                "",
                f"- schema_version: `{correctness.get('schema_version')}`",
                f"- write_id: `{intent.get('write_id')}`",
                f"- write_class: `{intent.get('write_class')}`",
                f"- idempotency_key: `{intent.get('idempotency_key')}`",
                f"- status: `{apply_result.get('status')}`",
            ]
        )
    validation = payload.get("validation")
    if isinstance(validation, dict):
        lines.extend(
            [
                "",
                "## Validation",
                "",
                f"- validation_blocked_completion: `{payload.get('validation_blocked_completion')}`",
                f"- command_label: `{validation.get('command_label')}`",
                f"- passed: `{validation.get('passed')}`",
                f"- status: `{validation.get('status')}`",
                f"- exit_code: `{validation.get('exit_code')}`",
            ]
        )
        summary = validation.get("summary")
        if summary:
            lines.append(f"- summary: {summary}")
    return "\n".join(lines)
