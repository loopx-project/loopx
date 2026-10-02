"""Append-only NDJSON diagnostic ledger under the LoopX runtime root.

The ledger is LoopX-owned diagnostic state, independent from goal, todo,
gate, and session-runtime authority. Callers resolve the runtime root through
``loopx.paths.resolve_runtime_root`` and never publish the absolute path; the
public reference is the relative ``ledger_ref``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .envelope import IDENTITY_TOKEN_PATTERN

LEDGER_DIRNAME = "reliability_diagnostics"


def _goal_file_stem(goal_id: str) -> str:
    if not isinstance(goal_id, str) or not IDENTITY_TOKEN_PATTERN.match(goal_id):
        raise ValueError("goal_id must be an identity token")
    return hashlib.sha256(goal_id.encode("utf-8")).hexdigest()


def ledger_ref(goal_id: str) -> str:
    """Public-safe, runtime-root-relative ledger reference."""

    return f"{LEDGER_DIRNAME}/by-goal/{_goal_file_stem(goal_id)}.ndjson"


def ledger_path(runtime_root: Path, goal_id: str) -> Path:
    return Path(runtime_root).expanduser() / ledger_ref(goal_id)


def resolve_ledger_path(runtime_root: Path, goal_id: str, *, for_write: bool = False) -> Path:
    """Read legacy evidence without moving it; never split history on a write."""

    canonical = ledger_path(runtime_root, goal_id)
    legacy = canonical.parent.parent / (goal_id.replace(":", "_") + ".ndjson")
    if legacy.exists() or legacy.is_symlink():
        if for_write or canonical.exists() or canonical.is_symlink():
            raise ValueError(
                "legacy diagnostic ledger requires offline reconciliation; stop observers "
                "and follow reliability_diagnostics/docs/local-retention-v0.md before writing"
            )
        return legacy
    return canonical


def append_ledger_records(path: Path, records: Iterable[Mapping[str, Any]]) -> int:
    """Append records as one JSON object per line; returns the appended count."""

    lines = [json.dumps(dict(record), sort_keys=True, ensure_ascii=True) for record in records]
    if not lines:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return len(lines)


def read_ledger_records(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Return ledger objects in file order plus the malformed-line count."""

    if not path.is_file():
        return [], 0
    records: list[dict[str, Any]] = []
    malformed = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(value, dict):
                malformed += 1
                continue
            records.append(value)
    return records, malformed


def parse_ndjson_lines(lines: Iterable[str]) -> tuple[list[Any], int]:
    """Parse NDJSON text lines without interpreting them; count malformed ones."""

    parsed: list[Any] = []
    malformed = 0
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            parsed.append(json.loads(text))
        except json.JSONDecodeError:
            malformed += 1
    return parsed, malformed
