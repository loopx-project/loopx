"""Decode Chat journal rows for both complete and scoped history reads."""

from dataclasses import dataclass
import json
from typing import Any


class CachedJournalRows(list):  # type: ignore[type-arg]
    """Immutable cached transcript rows tagged with their verified byte digest."""

    journal_sha256: bytes
    markers_sha256: bytes

    def __init__(self, rows: list[object], *, journal_sha256: bytes, markers_sha256: bytes = b"") -> None:
        super().__init__(rows)
        self.journal_sha256 = journal_sha256
        self.markers_sha256 = markers_sha256


_COMPACTION_SCHEMA = "loopx_chat_event_compaction_v0"


@dataclass(frozen=True)
class CompactedEventAppends:
    """Journal-local append identities and sequence floor retained by compaction."""

    through: int
    sequences: dict[str, int]

    def record(self) -> dict[str, Any]:
        return {"schema_version": _COMPACTION_SCHEMA, "through": self.through, "appends": self.sequences}


def _decode_journal_rows(lines: list[bytes], *, strict: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if strict:
                raise ValueError("invalid Chat journal record") from exc
            continue
        if isinstance(item, dict):
            rows.append(item)
        elif strict:
            raise ValueError("invalid Chat journal record")
    return rows


def _has_compaction_record(rows: list[dict[str, Any]]) -> bool:
    return bool(rows) and rows[0].get("schema_version") == _COMPACTION_SCHEMA


def decode_chat_journal(lines: list[bytes], *, strict: bool) -> list[dict[str, Any]]:
    rows = _decode_journal_rows(lines, strict=strict)
    return rows[1:] if _has_compaction_record(rows) else rows


def decode_chat_event_journal(
    lines: list[bytes], *, strict: bool,
) -> tuple[list[dict[str, Any]], CompactedEventAppends | None]:
    rows = _decode_journal_rows(lines, strict=strict)
    if not _has_compaction_record(rows):
        return rows, None
    through, sequences = rows[0].get("through"), rows[0].get("appends")
    if (
        type(through) is not int or through <= 0 or not isinstance(sequences, dict)
        or any(type(sequence) is not int or not 0 < sequence <= through for sequence in sequences.values())
    ):
        raise ValueError("invalid Chat event compaction record")
    return rows[1:], CompactedEventAppends(through, sequences)
