from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable

from .file_lock import exclusive_file_lock


_DOMAIN_STATE_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")


def _domain_state_token(value: str, *, field: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise ValueError(f"{field} must be non-empty")
    if not _DOMAIN_STATE_TOKEN_RE.match(token):
        raise ValueError(
            f"{field} must be a compact token using letters, digits, dot, colon, dash, or underscore"
        )
    return token


def default_domain_state_file_path(
    *,
    project: str | Path = ".",
    goal_id: str,
    domain_pack: str,
    filename: str,
) -> Path:
    """Return the project-local domain-state path for a goal and pack."""

    compact_goal_id = _domain_state_token(goal_id, field="goal_id")
    compact_pack = _domain_state_token(domain_pack, field="domain_pack")
    compact_filename = _domain_state_token(filename, field="filename")
    return (
        Path(project).expanduser()
        / ".loopx"
        / "domain-state"
        / compact_goal_id
        / compact_pack
        / compact_filename
    )


def upsert_domain_state_jsonl(
    ledger_path: str | Path,
    payload: dict[str, Any],
    *,
    key: dict[str, Any],
    existing_key_fn: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    unchanged_fn: Callable[[dict[str, Any], dict[str, Any]], bool] | None = None,
    merge_existing_fn: Callable[
        [dict[str, Any], dict[str, Any]], dict[str, Any]
    ]
    | None = None,
) -> dict[str, Any]:
    """Upsert a payload into a JSONL domain-state file by stable key."""

    if not isinstance(key, dict) or not key:
        raise ValueError("domain-state upsert key must be a non-empty dict")
    path = Path(ledger_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(path):
        rows: list[dict[str, Any]] = []
        updated = False
        unchanged = False
        candidate = {**payload, "domain_state_key": key}
        if path.exists():
            for index, line in enumerate(
                path.read_text(encoding="utf-8").split("\n"), start=1
            ):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid JSONL row {index} in domain-state ledger"
                    ) from exc
                row_key = row.get("domain_state_key") if isinstance(row, dict) else None
                if row_key is None and existing_key_fn is not None and isinstance(row, dict):
                    row_key = existing_key_fn(row)
                if isinstance(row, dict) and row_key == key:
                    if not updated:
                        merged_candidate = (
                            merge_existing_fn(row, candidate)
                            if merge_existing_fn is not None
                            else candidate
                        )
                        if unchanged_fn is not None and unchanged_fn(row, merged_candidate):
                            rows.append(row)
                            unchanged = True
                        else:
                            rows.append(merged_candidate)
                        updated = True
                    continue
                rows.append(row)
        if not updated:
            rows.append(candidate)

        if not unchanged:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f"{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp_file:
                tmp_name = tmp_file.name
                tmp_file.write(
                    "".join(
                        json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n"
                        for row in rows
                    )
                )
            os.replace(tmp_name, path)
    return {
        "status": "unchanged" if unchanged else "updated" if updated else "inserted",
        "write_performed": not unchanged,
        "row_count": len(rows),
        "ledger_key": key,
        "path_recorded": False,
    }
