"""Codex terminal observations/effects for Core's frozen conversation stop.

Chat's canonical history supplies the eligible Turn identities. Native item
and terminal inventories prove which of those Turns still own processes. This
does not change completed Turn receipts or enumerate another native thread.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...chat_store import CHAT_SESSION_MODE_ATTACHED, TERMINAL_TURN_STATES
from ...chat_codex_goal import UPSTREAM_MODE as CODEX_GOAL_CHAT_MODE


def _adapter(controller: Any, session: dict[str, Any]) -> Any:
    from ...chat_runtime import CodexAppServerAdapter

    adapter = controller._ensure_adapter_locked(session, work_dir=Path("."), objective="")
    if not isinstance(adapter, CodexAppServerAdapter) or adapter.goal_driver is not None:
        raise ValueError("completed command control requires ordinary native Codex Chat")
    if adapter.upstream_thread_id != session.get("upstream_thread_id"):
        raise ValueError("background control belongs to another native thread")
    return adapter


def completed_turn_ids(controller: Any, session_id: str) -> list[str]:
    """Canonical candidates only: never start/resume a native transport here."""
    with controller._session_adapter_lock(session_id):
        session = controller.store.load_session(session_id)
        if (not session or session.get("session_mode") == CHAT_SESSION_MODE_ATTACHED
                or session.get("agent_id") != "codex" or session.get("upstream_mode") == CODEX_GOAL_CHAT_MODE):
            return []
        identities = {}
        for message in controller.store.messages(session_id):
            turn_id = message.get("turn_id")
            if not turn_id or turn_id in identities:
                continue
            turn = controller.store.load_turn(session_id, turn_id)
            if turn and turn.get("status") in TERMINAL_TURN_STATES and turn.get("upstream_turn_id"):
                identities[turn_id] = turn["upstream_turn_id"]
        return list(identities)


def completed_background_turns(controller: Any, session_id: str, turn_ids: list[str]) -> list[str]:
    """Narrow an already frozen canonical set; later Turns are never eligible."""
    with controller._session_adapter_lock(session_id):
        session = controller.store.load_session(session_id)
        if not session:
            raise ValueError("the original background command Session is unavailable")
        identities = {}
        for turn_id in turn_ids:
            turn = controller.store.load_turn(session_id, turn_id)
            if not turn or turn.get("status") not in TERMINAL_TURN_STATES or not turn.get("upstream_turn_id"):
                raise ValueError("the original completed command Turn is unavailable")
            identities[turn_id] = turn["upstream_turn_id"]
        if not identities:
            return []
        adapter = _adapter(controller, session)
        live = set(adapter.session.background_turn_ids(list(identities.values())))
        return [turn_id for turn_id, upstream in identities.items() if upstream in live]


def interrupt_completed_background_turns(controller: Any, session_id: str, turn_ids: list[str]) -> None:
    with controller._session_adapter_lock(session_id):
        session = controller.store.load_session(session_id)
        if not session or session.get("session_mode") == CHAT_SESSION_MODE_ATTACHED:
            raise ValueError("the original background command Session is unavailable")
        upstream_ids = []
        for turn_id in turn_ids:
            turn = controller.store.load_turn(session_id, turn_id)
            if not turn or turn.get("status") not in TERMINAL_TURN_STATES or not turn.get("upstream_turn_id"):
                raise ValueError("the original completed command Turn is unavailable")
            upstream_ids.append(turn["upstream_turn_id"])
        adapter = _adapter(controller, session)
        for upstream in upstream_ids:
            # The existing interrupt owner also handles already terminal Turns
            # and verifies that only their command items lose live terminals.
            adapter.interrupt_turn(upstream)
