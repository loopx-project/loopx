"""Read host-frozen Chat provenance without opening or recovering a Chat store.

The address locates evidence; source grants and Goal lifetime decisions remain
with their existing typed owners. No model or peer supplies this address.
"""

from pathlib import Path
from typing import Any

from ...chat_store import (
    CHAT_SESSION_SCHEMA_VERSION, CHAT_TURN_SCHEMA_VERSION, ChatSessionStore, _opaque_id, _read_json,
)


def _source_chat_root(coordination_root: Path, route: dict[str, Any]) -> Path:
    locator = route.get("source_chat_runtime_root", str(Path(coordination_root).resolve()))
    if not isinstance(locator, str) or not locator or not Path(locator).is_absolute():
        raise ValueError("original source Chat store provenance is invalid")
    return Path(locator).resolve()


def is_source_chat_host(coordination_root: Path, route: dict[str, Any], store: ChatSessionStore) -> bool:
    """Match frozen provenance before a host admits or mutates a shared return."""
    return _source_chat_root(coordination_root, route) == store.root.parent.resolve()


def observe_source_chat(coordination_root: Path, route: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    source_root = _source_chat_root(coordination_root, route)
    session_id = _opaque_id(route.get("session_id"), field="session_id")
    client_turn_id = _opaque_id(route.get("client_turn_id"), field="client_turn_id")
    if session_id in {".", ".."}:
        raise ValueError("original source Chat session identity is invalid")
    session_dir = source_root / "chat" / "sessions" / session_id
    session = _read_json(session_dir / "session.json")
    turns = [value for path in sorted((session_dir / "turns").glob("*.json"))
             if not path.name.endswith(".events.json")
             and (value := _read_json(path)).get("schema_version") == CHAT_TURN_SCHEMA_VERSION
             and value.get("client_turn_id") == client_turn_id]
    if (session.get("schema_version") != CHAT_SESSION_SCHEMA_VERSION
            or session.get("session_id") != session_id or session.get("status") == "closed"
            or session.get("channel_id") != route.get("channel_id") or len(turns) != 1
            or turns[0].get("session_id") != session_id):
        raise ValueError(
            "original source conversation unavailable for peer context forwarding; "
            "recover its provenance through the original Chat host, then retry"
        )
    return session, turns[0]


def trusted_source_chat_root(store, route):
    """Validate the actual host store, never a routing claim from a caller."""
    root = store.root.parent.resolve()
    observe_source_chat(root, {**route, "source_chat_runtime_root": str(root)})
    return str(root)


def original_delivery_facts(row: dict[str, Any], route: dict[str, Any], turn: dict[str, Any],
                            *, source_id: str | None) -> dict[str, Any]:
    # Adapt trusted store/authority facts only. The common typed owner decides
    # whether a committed handoff survived a lost caller response.
    identity = {key: row[key] for key in ("request_id", "goal_id", "agent_id", "source_id", "goal_ref") if key in row}
    return {
        "request": identity,
        "route": route,
        "authorized_source_id": source_id,
        "turn": {
            "client_turn_id": turn.get("client_turn_id"),
            "status": turn.get("status"),
            "context_handoff_receipt": (turn.get("response") or {}).get("context_handoff_receipt"),
        },
    }
