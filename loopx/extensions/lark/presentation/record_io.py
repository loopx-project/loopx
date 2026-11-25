from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol


class RecordTarget(Protocol):
    @property
    def cli_bin(self) -> str: ...
    @property
    def identity(self) -> str: ...
    @property
    def base_token(self) -> str: ...
    @property
    def table_id(self) -> str: ...


def lark_record_rows(payload: Mapping[str, object]) -> list[dict[str, object]]:
    raw_data = payload.get("data")
    data: Mapping[str, object] = raw_data if isinstance(raw_data, Mapping) else payload
    fields = data.get("fields")
    rows = data.get("data")
    record_ids = data.get("record_id_list")
    if not isinstance(fields, list) or not isinstance(rows, list):
        return []
    records: list[dict[str, object]] = []
    for index, values in enumerate(rows):
        if not isinstance(values, list):
            continue
        record: dict[str, object] = {
            str(field): values[pos] if pos < len(values) else None
            for pos, field in enumerate(fields)
        }
        if isinstance(record_ids, list) and index < len(record_ids):
            record["_record_id"] = record_ids[index]
        records.append(record)
    return records


def todo_record_entries(parsed: object) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    payload: Mapping[str, object] = parsed if isinstance(parsed, dict) else {}
    for record in lark_record_rows(payload):
        todo_id = str(record.get("LoopX Todo ID") or "").strip()
        goal_id = str(record.get("LoopX Goal ID") or "").strip()
        record_id = str(record.get("_record_id") or "").strip()
        if todo_id and goal_id and record_id:
            result.append({"key": f"{goal_id}:{todo_id}", "record_id": record_id})
    return result


def record_list_is_complete(parsed: object) -> bool:
    if not isinstance(parsed, dict):
        return False
    raw_data = parsed.get("data")
    data: object = raw_data if isinstance(raw_data, dict) else parsed
    if not isinstance(data, dict):
        return False
    return (
        data.get("has_more") is False
        and not str(data.get("page_token") or data.get("next_page_token") or "").strip()
    )


def todo_record_list_is_authoritative(parsed: object) -> bool:
    if not record_list_is_complete(parsed) or not isinstance(parsed, dict):
        return False
    raw_data = parsed.get("data")
    data: object = raw_data if isinstance(raw_data, dict) else parsed
    if not isinstance(data, dict):
        return False
    fields = data.get("fields")
    rows = data.get("data")
    record_ids = data.get("record_id_list")
    return (
        isinstance(fields, list)
        and {"LoopX Goal ID", "LoopX Todo ID"}.issubset(
            {str(field) for field in fields}
        )
        and isinstance(rows, list)
        and isinstance(record_ids, list)
        and len(record_ids) == len(rows)
    )


def build_record_delete_command(
    config: RecordTarget,
    *,
    record_ids: Sequence[str],
) -> list[str]:
    args = [
        config.cli_bin,
        "base",
        "+record-delete",
        "--as",
        config.identity,
        "--base-token",
        config.base_token,
        "--table-id",
        config.table_id,
    ]
    for record_id in record_ids:
        args.extend(["--record-id", record_id])
    return [*args, "--yes"]
