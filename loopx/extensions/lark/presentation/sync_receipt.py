from __future__ import annotations

from collections.abc import Mapping


OUTPUT_LIMIT = 1800


def _compact_text(value: object, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _command_error(command_result: Mapping[str, object]) -> str:
    parsed = command_result.get("json")
    if isinstance(parsed, Mapping):
        error = parsed.get("error")
        if isinstance(error, Mapping):
            return str(error.get("message") or error)
        if error:
            return str(error)
        if parsed.get("msg") and parsed.get("code") not in (None, 0):
            return str(parsed.get("msg"))
    stderr = " ".join(str(command_result.get("stderr") or "").split())
    if stderr:
        return stderr[:OUTPUT_LIMIT]
    stdout = " ".join(str(command_result.get("stdout") or "").split())
    return stdout[:OUTPUT_LIMIT]


def compact_lark_kanban_sync_receipt(
    payload: Mapping[str, object],
    *,
    include_command_details: bool = False,
) -> dict[str, object]:
    raw_records = payload.get("records")
    raw_commands = payload.get("commands")
    records = [item for item in (raw_records if isinstance(raw_records, list) else []) if isinstance(item, Mapping)]
    commands = [item for item in (raw_commands if isinstance(raw_commands, list) else []) if isinstance(item, Mapping)]
    result: dict[str, object] = dict(payload)
    record_success_count = 0
    record_failure_count = 0
    for item in records:
        item_cmd = item.get("command")
        if isinstance(item_cmd, Mapping) and item_cmd.get("ok") is True:
            record_success_count += 1
        else:
            record_failure_count += 1
    result.update(
        {
            "detail_level": "full" if include_command_details else "compact",
            "record_count": len(records),
            "record_success_count": record_success_count,
            "record_failure_count": record_failure_count,
            "command_count": len(commands),
            "command_failure_count": sum(
                1 for item in commands if item.get("ok") is not True
            ),
        }
    )
    if include_command_details:
        return result

    compact_records: list[dict[str, object]] = []
    for item in records:
        raw_command = item.get("command")
        raw_values = item.get("values")
        cmd_dict: dict[str, object] = (
            {str(k): v for k, v in raw_command.items()}
            if isinstance(raw_command, Mapping)
            else {}
        )
        values: dict[str, object] = (
            {str(k): v for k, v in raw_values.items()}
            if isinstance(raw_values, Mapping)
            else {}
        )
        compact_records.append(
            {
                "todo_id": item.get("todo_id"),
                "record_id": item.get("record_id"),
                "ok": cmd_dict.get("ok") is True,
                "executed": bool(cmd_dict.get("executed")),
                "returncode": cmd_dict.get("returncode"),
                "error": _command_error(cmd_dict) if cmd_dict.get("ok") is not True else None,
                "work_item_type": values.get("Work Item Type"),
                "repository": values.get("Repository"),
                "issue": values.get("Issue"),
                "pull_request": values.get("Pull Request"),
            }
        )
    result["records"] = compact_records
    result.pop("commands", None)
    return result


def render_lark_kanban_markdown(payload: Mapping[str, object]) -> str:
    lines = [
        "# LoopX Lark Kanban",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- schema_version: `{payload.get('schema_version')}`",
    ]
    summary_keys = (
        "base_token",
        "table_id",
        "view_id",
        "agent_id",
        "decision",
        "selected_record_id",
        "final_status",
        "detail_level",
        "todo_count",
        "issue_fix_outcome_count",
        "record_count",
        "record_success_count",
        "record_failure_count",
        "command_count",
        "command_failure_count",
    )
    for key in summary_keys:
        if payload.get(key) is not None:
            lines.append(f"- {key}: `{payload.get(key)}`")
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
    raw_commands = payload.get("commands")
    if isinstance(raw_commands, list):
        lines.extend(("", "## Commands"))
        for item in raw_commands:
            if isinstance(item, Mapping):
                marker = "ran" if item.get("executed") else "dry-run"
                lines.append(f"- `{marker}` `{item.get('command')}`")
    raw_records = payload.get("records")
    if payload.get("detail_level") == "compact" and isinstance(raw_records, list):
        lines.extend(("", "## Records"))
        for item in raw_records:
            if not isinstance(item, Mapping):
                continue
            status = "ok" if item.get("ok") else "failed"
            summary = (
                f"- `{item.get('todo_id') or ''}` -> `{item.get('record_id') or ''}` "
                f"status={status}"
            )
            if item.get("issue"):
                summary += f" issue={_compact_text(item.get('issue'), limit=160)}"
            if item.get("pull_request"):
                summary += f" pr={_compact_text(item.get('pull_request'), limit=160)}"
            if item.get("error"):
                summary += f" error={_compact_text(item.get('error'), limit=220)}"
            lines.append(summary)
    raw_writeback = payload.get("writeback")
    if isinstance(raw_writeback, Mapping):
        lines.extend(("", "## Writeback"))
        for key, value in raw_writeback.items():
            lines.append(f"- {key}: `{_compact_text(value, limit=220)}`")
    return "\n".join(lines)
