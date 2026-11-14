"""Read-only scheduling hints for the existing private-message reply worker."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

from ...chat_store import RESUMABLE_SESSION_STATES, ChatSessionStore, _read_json
from ...capabilities.native_chat.file_discovery import FileChatDiscovery, file_change_fingerprint
from .private_progress import legacy_recovery_repairs


def prepared_steward_binding_waits(
    request: dict[str, Any], bindings: list[dict[str, Any]], store: ChatSessionStore
) -> bool:
    """Skip provider reads while a recorded executor mismatch persists."""
    session = store.load_session(request["session_id"])
    binding = next((row for row in bindings if row["binding_id"] == request["binding_id"]), None)
    if not session or not binding or not binding.get("enabled"):
        return False
    allocation = session.get("manager_executor_allocation")
    return (session.get("status") in RESUMABLE_SESSION_STATES
            and session.get("executor_endpoint_id") != binding.get("executor_endpoint_id")
            and isinstance(allocation, dict) and allocation.get("selection_policy") in ("preferred", "pinned"))


class PrivateDeliveryDiscovery:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._completed: dict[Path, tuple[tuple[int, ...], dict[str, Any], bool]] = {}
        self._web_inputs = FileChatDiscovery()

    def web_input_turn_refs(self, store: ChatSessionStore, session_id: str) -> list[tuple[str, str]]:
        """Observe App user message/Turn references, never bodies or effect authority."""
        path = store._session_dir(session_id) / "messages.jsonl"

        def read(_: Path) -> dict[str, Any]:
            refs = []
            for message in store.messages(session_id, strict=True):
                if message.get("role") == "user" and message.get("origin") == "web":
                    message_id, turn_id = message["message_id"], message["turn_id"]
                    if not isinstance(message_id, str) or not isinstance(turn_id, str):
                        raise ValueError("invalid App input reference")
                    refs.append((message_id, turn_id))
            return {"refs": refs}

        [(_, view)] = self._web_inputs.rows(path.parent, (path,), read=read, project=lambda _, value: value)
        return cast(list[tuple[str, str]], view["refs"])

    def rows(self) -> Iterator[tuple[Path, dict[str, Any], bool]]:
        """Reuse only unchanged delivered-file hints, never bodies or authority.

        Pending files stay fresh. Reconciliation still reads the complete
        journal and current Core/provider state before any delivery or recovery.
        """
        seen = set()

        for path in sorted(self.root.glob("*.json")):
            seen.add(path)
            try:
                row, repairs = self._observe(path)
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                # Keep unreadable or disappearing journals intact and evict
                # their stale hints. The next scan can observe a readable
                # receipt; unrelated replies stay discoverable now.
                self._completed.pop(path, None)
                continue
            yield path, row, repairs
        for path in self._completed.keys() - seen:
            del self._completed[path]

    def _observe(self, path: Path) -> tuple[dict[str, Any], bool]:
        before = file_change_fingerprint(path)
        saved = self._completed.get(path)
        if before is not None and saved is not None and saved[0] == before:
            return saved[1], saved[2]

        self._completed.pop(path, None)
        row = _read_json(path)
        if row["status"] != "delivered":
            return row, False

        repairs = bool(row.get("session_id") and row.get("turn_id") and legacy_recovery_repairs(row))
        view = {key: row.get(key) for key in
                ("status", "request_ref", "binding_id", "session_id", "turn_id")}
        view.update(agent_target=bool(row.get("agent_target")),
                    event={"chat_type": row.get("event", {}).get("chat_type")})
        historic_images = row.get("historical_answer_images") or {}
        if historic_images:
            view["historical_images_admitted"] = True
        if historic_images.get("status") == "pending":
            view["historical_images_due_at"] = historic_images.get("next_check_at", 0)
        if before is not None and file_change_fingerprint(path) == before:
            self._completed[path] = (before, view, repairs)
        return view, repairs
