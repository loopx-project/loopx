from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ...file_lock import exclusive_run_index_lock
from ...history import load_index, reserve_unique_run_paths
from .time import now_local_iso


def _matches_complete_projection(
    item: dict[str, Any],
    *,
    marker_field: str,
    identity_fields: tuple[str, ...],
    identity: tuple[Any, ...],
) -> bool:
    marker = item.get(marker_field)
    return (
        isinstance(marker, dict)
        and tuple(marker.get(field) for field in identity_fields) == identity
        and item.get("json_exists") is True
        and item.get("markdown_exists") is True
    )


def write_compact_runtime_projection(
    *,
    target_runtime_root: Path,
    goal_id: str,
    record: dict[str, Any],
    index_record: dict[str, Any],
    marker_field: str,
    identity_fields: tuple[str, ...],
    markdown_renderer: Callable[[dict[str, Any]], str],
    dry_run: bool,
) -> dict[str, Any]:
    """Idempotently append one compact projection and verify shared readback."""

    marker = record.get(marker_field)
    if not isinstance(marker, dict):
        raise ValueError(f"projection record must include object marker {marker_field!r}")
    identity = tuple(marker.get(field) for field in identity_fields)
    if not all(identity):
        raise ValueError(
            f"projection marker {marker_field!r} is missing identity fields: "
            + ", ".join(identity_fields)
        )

    result: dict[str, Any] = {
        "ok": True,
        "status": "would_project" if dry_run else "projected",
        "dry_run": dry_run,
        "target_runtime_root": str(target_runtime_root),
        "readback_verified": False if dry_run else None,
    }
    if dry_run:
        return result

    runs_dir = target_runtime_root / "goals" / goal_id / "runs"
    index_path = runs_dir / "index.jsonl"
    with exclusive_run_index_lock(index_path, operation="runtime_projection_append"):
        existing, _ = load_index(index_path)
        for item in existing:
            if _matches_complete_projection(
                item,
                marker_field=marker_field,
                identity_fields=identity_fields,
                identity=identity,
            ):
                result.update(
                    {
                        "status": "already_current",
                        "index_path": str(index_path),
                        "readback_verified": True,
                    }
                )
                return result

        json_path, markdown_path = reserve_unique_run_paths(
            runs_dir,
            str(record.get("generated_at") or now_local_iso()),
        )
        index_record["json_path"] = str(json_path)
        index_record["markdown_path"] = str(markdown_path)
        json_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        markdown_path.write_text(
            markdown_renderer(record) + "\n",
            encoding="utf-8",
        )
        with index_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(index_record, ensure_ascii=False) + "\n")

        rows, _ = load_index(index_path)
        readback_verified = any(
            _matches_complete_projection(
                item,
                marker_field=marker_field,
                identity_fields=identity_fields,
                identity=identity,
            )
            for item in rows
        )
        if not readback_verified:
            raise OSError("runtime projection append did not pass index readback")

    result.update(
        {
            "json_path": str(json_path),
            "markdown_path": str(markdown_path),
            "index_path": str(index_path),
            "readback_verified": True,
        }
    )
    return result
