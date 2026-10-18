"""First-party Goal Chat IO over the native admitted workspace route.

The transfer owner verifies cutover and physical bindings. Chat then reuses a
normal destination controller/store, never an alias for a source executor or
copied Session. No transfer decision, durable routing ledger or grant lives here.
Manager, external and ordinary project conversations retain their original owner.
"""
from __future__ import annotations

import threading
import logging
from pathlib import Path
from typing import Any

from .chat_runtime import ChatRuntimeController
from .chat_store import ChatSessionStore
from .chat_manager import MANAGER_AGENT_GOAL_ID, is_manager_channel
from .control_plane.goals.planned_transfer_io import read_goal_transfer_workspace_target
from .control_plane.turn_driver.turn_host_process import require_goal_runtime_current
from .registry import load_registry, registry_goals

_LOG = logging.getLogger(__name__)


def _owner_goal(session: dict[str, Any]) -> str | None:
    goal = session.get("goal_id")
    # Task/attached Sessions also belong to the registered Goal lifetime, even
    # though their conversation scope is not the owner's ordinary Goal Chat.
    return (str(goal) if goal and goal != MANAGER_AGENT_GOAL_ID
            and not is_manager_channel(session.get("channel_id"))
            and session.get("project_context") is None else None)


class ChatGoalRuntimeRoutes:
    def __init__(self, primary: ChatRuntimeController, *, store=None, registry_path=None) -> None:
        self.primary = primary
        self.store = store or primary.store
        self.registry_path = registry_path or getattr(primary, "registry_path", None)
        self.lock = threading.RLock()
        self.controllers: dict[tuple[str, str, str], ChatRuntimeController] = {}
        self.session_goals: dict[str, str] = {}

    def for_goal(self, goal_id: str | None, *, execute: bool = False) -> ChatRuntimeController:
        if goal_id is None or self.registry_path is None:
            return self.primary
        try:
            target = read_goal_transfer_workspace_target(registry_path=self.registry_path, goal_id=goal_id)
            if target is None:
                controller = self.primary
            else:
                key = goal_id, target["registry_path"], target["runtime_root"]
                with self.lock:
                    controller = self.controllers.get(key)
                    if controller is None:
                        controller = ChatRuntimeController(
                            store=ChatSessionStore(Path(target["runtime_root"])),
                            registry_path=Path(target["registry_path"]),
                            codex_bin=self.primary.codex_bin,
                            claude_bin=self.primary.claude_bin,
                            kiro_cli_bin=self.primary.kiro_cli_bin,
                            startup_timeout_sec=self.primary.acp_startup_timeout_sec,
                            idle_timeout_sec=self.primary.idle_timeout_sec,
                            hard_timeout_sec=self.primary.hard_timeout_sec,
                        )
                        # This process's current local account, not credentials
                        # or endpoint bindings carried in a transfer artifact.
                        controller.codex_home = self.primary.codex_home
                        self.controllers[key] = controller
            if execute:
                require_goal_runtime_current(getattr(controller, "coordination_runtime_root", self.store.root.parent), goal_id)
            return controller
        except (OSError, ValueError, RuntimeError) as exc:
            raise ValueError("The current Goal conversation is unavailable. Reconnect or resume the migration destination, then retry; the draft is unchanged.") from exc

    def for_session(self, session_id: str, *, execute: bool = False) -> ChatRuntimeController:
        original = self.store.load_session(session_id)
        if original is not None:
            goal = _owner_goal(original)
            if execute and goal is not None and self.for_goal(goal, execute=True) is not self.primary:
                raise ValueError("This is the previous Goal conversation. Open a new conversation in the migrated Goal; its history remains readable.")
            return self.primary
        # Reuse the observed Session's Goal address, never a cached destination
        # grant. Cold reconnect discovers only current registered Goal routes.
        with self.lock:
            goal = self.session_goals.get(session_id)
        goals = [goal] if goal is not None else self._goals()
        found = []
        for candidate in goals:
            controller = self.for_goal(candidate)
            if controller is self.primary:
                continue
            session = controller.store.load_session(session_id)
            if session is not None and _owner_goal(session) == candidate:
                found.append((candidate, controller))
        if len(found) != 1:
            if found:
                raise ValueError("The conversation address is ambiguous; reopen the intended Goal.")
            raise KeyError("chat session was not found")
        goal, controller = found[0]
        if execute:
            self.for_goal(goal, execute=True)
        with self.lock:
            self.session_goals[session_id] = goal
        return controller

    def historical(self, controller: ChatRuntimeController, session: dict[str, Any]) -> bool:
        goal = _owner_goal(session)
        return controller is self.primary and goal is not None and self.for_goal(goal) is not self.primary

    def store_for(self, controller: ChatRuntimeController) -> ChatSessionStore:
        return self.store if controller is self.primary else controller.store

    def list_sessions(self, *, goal_id=None, agent_id=None, channel_id=None) -> list[dict[str, Any]]:
        original = self.store.list_sessions(goal_id=goal_id, agent_id=agent_id, channel_id=channel_id)
        # A selected manager/project channel is not a Goal routing request.
        if channel_id and not channel_id.startswith("goal."):
            return original
        goals = [goal_id] if goal_id else self._goals()
        rows = list(original)
        for goal in goals:
            controller = self.for_goal(goal)
            if controller is self.primary:
                continue
            for row in rows:
                if _owner_goal(row) == goal:
                    row["resumable"] = False
            current = controller.store.list_sessions(goal_id=goal, agent_id=agent_id,
                channel_id=channel_id or f"goal.{goal}")
            with self.lock:
                self.session_goals.update({row["session_id"]: goal for row in current})
            rows.extend(current)
        return sorted(rows, key=lambda row: str(row.get("updated_at") or ""), reverse=True)

    def _goals(self) -> list[str]:
        return ([str(row["id"]) for row in registry_goals(load_registry(self.registry_path))]
                if self.registry_path else [])

    def continue_transferred_work(self, *, cancelled) -> None:
        """Host IO only; native admission and the Turn controller own each wake.

        Called from the existing Chat wake service, independently of its
        conversation pump. No per-Goal timer, queue or copied Host binding.
        """
        from .control_plane.goals.planned_transfer_io import read_goal_transfer_host_work
        from .control_plane.goals.planned_transfer_execution import continue_transferred_work
        from .file_lock import LockAcquireTimeoutError

        for goal in self._goals():
            if cancelled():
                return
            try:
                plan = read_goal_transfer_host_work(registry_path=self.registry_path, goal_id=goal)
                if plan is not None:
                    continue_transferred_work(registry_path=Path(plan["registry_path"]),
                        goal_id=goal, operation_id=plan["operation_id"], codex_bin=self.primary.codex_bin,
                        codex_home=self.primary.codex_home, host_service=True, cancelled=cancelled)
            except LockAcquireTimeoutError:
                # Another caller holds this exact original operation. This tick
                # skips it; the native journal/lease still owns any live effect.
                continue
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                _LOG.warning("Reviewed Goal continuation is unavailable; its original state is retained")

    def close(self) -> None:
        with self.lock:
            controllers = list(self.controllers.values())
        for controller in controllers:
            controller.close()
