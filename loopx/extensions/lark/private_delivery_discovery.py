"""Read-only scheduling hints for the existing private-message reply worker."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ...chat_store import _read_json
from ...capabilities.native_chat.file_discovery import file_change_fingerprint
from .private_progress import legacy_recovery_repairs


class PrivateDeliveryDiscovery:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._completed: dict[Path, tuple[tuple[int, ...], dict[str, Any], bool]] = {}

    def rows(self) -> Iterator[tuple[Path, dict[str, Any], bool]]:
        """Reuse only unchanged delivered-file hints, never bodies or authority.

        Pending files stay fresh. Reconciliation still reads the complete
        journal and current Core/provider state before any delivery or recovery.
        """
        seen = set()

        for path in sorted(self.root.glob("*.json")):
            seen.add(path)
            before = file_change_fingerprint(path)
            saved = self._completed.get(path)
            if before is not None and saved is not None and saved[0] == before:
                yield path, saved[1], saved[2]
                continue
            self._completed.pop(path, None)
            row = _read_json(path)
            repairs = False
            if row["status"] == "delivered":
                repairs = bool(row.get("session_id") and row.get("turn_id") and legacy_recovery_repairs(row))
                view = {key: row.get(key) for key in
                        ("status", "request_ref", "binding_id", "session_id", "turn_id")}
                view.update(agent_target=bool(row.get("agent_target")),
                            event={"chat_type": row.get("event", {}).get("chat_type")})
                if before is not None and file_change_fingerprint(path) == before:
                    self._completed[path] = (before, view, repairs)
                row = view
            yield path, row, repairs
        for path in self._completed.keys() - seen:
            del self._completed[path]
