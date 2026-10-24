"""Native identity preparation and managed Session admission in the Chat owner.

The typed conversation owner fixes identity and consent. Existing Goal creation
and configuration perform durable effects. No model, monitor, scheduler or
second Chat Session is created here. The ordinary local entry reuses the
existing worker and sandbox; private steward preparation remains distinct.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any

from ...agent_registry import load_goal_from_registry, registered_agent_ids_for_goal
from ...chat_store import _atomic_write_json
from ...control_plane.goals.activation import goal_is_stopped
from ...file_lock import exclusive_file_lock


class StewardManagedMode:
    def __init__(self, controller: Any, bindings: Any) -> None:
        self.controller, self.bindings = controller, bindings
        self.store = controller.store

    def _session(self, binding_id: str, session_id: str | None) -> dict[str, Any]:
        if session_id is not None:
            session = self.store.load_session(session_id)
        else:
            binding = next((row for row in self.bindings.read()["bindings"]
                            if row["binding_id"] == binding_id), {})
            actor = binding.get("managed_actor") or {}
            if actor.get("session_id"):
                session = self.store.load_session(actor["session_id"])
            else:
                candidates = self.bindings.local_sessions(binding_id)
                candidates = [row for row in candidates if row.get("status") != "closed"]
                channels = {row.get("channel_id") for row in candidates}
                if len(channels) != 1 or not candidates:
                    raise ValueError("select the original private steward conversation first")
                latest = max(candidates, key=lambda row: str(row.get("updated_at") or ""))
                session = self.store.load_session(latest["session_id"])
        if not session or (session.get("steward_context") or {}).get("binding_id") != binding_id:
            raise ValueError("the original steward Session is unavailable")
        return session

    def _workspace(self, session: dict[str, Any]) -> Path:
        # Inspect the already established manager workspace. Calling its
        # construction helper during preview would write AGENTS/skill files.
        parent = self.store.root / "manager-workspaces"
        path = parent / hashlib.sha256(session["channel_id"].encode()).hexdigest()[:24]
        if (parent.is_symlink() or path.is_symlink() or not path.is_dir()
                or not path.resolve().is_relative_to(self.store.root.resolve())):
            raise ValueError("the original private steward workspace is unavailable")
        return path.resolve()

    def _goal(self, goal_id: str) -> dict[str, Any] | None:
        goal = load_goal_from_registry(self.controller.registry_path, goal_id)
        if goal is None:
            return None
        return {"goal_id": goal["id"], "goal_instance_id": goal.get("goal_instance_id"),
                "workspace_path": str(Path(goal["repo"]).resolve()),
                "creation_operation_id": goal.get("creation_operation_id"),
                "registered_agents": registered_agent_ids_for_goal(goal)}

    def prepare(self, binding_id: str, *, session_id: str | None = None,
                expected_plan_id: str | None = None, execute: bool = False) -> dict[str, Any]:
        session = self._session(binding_id, session_id)
        saved = session["steward_context"]
        # The local owner is a real web origin, not a fabricated provider event.
        observed = self.bindings.resolve(binding_id=binding_id, session=session,
            include_observation=True, origin="web", source_ref=saved["source_ref"],
            sender_ref=saved["operator_ref"], private_human_message=False, session_context=saved)
        with self.controller.lock, exclusive_file_lock(self.bindings.path, operation="prepare_native_steward"):
            current = self.bindings.read()
            binding = next((row for row in current["bindings"] if row["binding_id"] == binding_id), None)
            if binding != observed["binding"]:
                raise ValueError("the original conversation grant changed before preparation")
            session = self._session(binding_id, session["session_id"])
            workspace = self._workspace(session)
            goal_id = f"steward-agent-{binding_id}"
            params = {"binding": binding, "binding_revision": current["revision"], "session": session,
                "observation": observed["observation"], "workspace_path": str(workspace),
                "queued_turn_count": len(self.store.queued_turns(session["session_id"])),
                "runtime_profile": {key: session.get(key) for key in
                    ("manager_runtime_profile", "manager_runtime_sandbox", "manager_runtime_standing_grant")},
                "goal": self._goal(goal_id)}
            plan = self.bindings._core("collaboration.steward.managed_mode.plan", params)
            if not execute:
                return {"ok": True, "applied": False, "plan": plan}
            # Lost-response recovery reads the exact native lifetime/reference;
            # it never creates another Goal or reuses an unrelated existing one.
            if plan["already_prepared"]:
                return {"ok": True, "applied": True, "idempotent": True, "plan": plan,
                        "actor": binding["managed_actor"], "scheduling_enabled": False}
            if expected_plan_id != plan["plan_id"]:
                raise ValueError("review the current steward preparation preview before applying")
            from ...bootstrap import bootstrap_project
            from ...configure_goal import configure_goal
            from ...control_plane.coordination.new_goal_storage_transport import initialize_goal_storage_target

            if params["goal"] is None:
                created = bootstrap_project(project=workspace, registry_path=self.controller.registry_path,
                    runtime_root=self.controller.coordination_runtime_root, goal_id=goal_id,
                    objective="Maintain the original private steward's authorized monitors and periodic reports; preserve original requests, scope and delivery evidence.",
                    domain="personal-steward", role="primary", parent_goal_id=None, state_file=None,
                    goal_doc=None, adapter_kind="generic_project_goal_v0", adapter_status="connected",
                    next_probe=None, spawn_allowed=False, max_children=0, allowed_domains=[], write_scope=[],
                    preserve_todos=True, display_name="Steward monitoring", force=False, dry_run=False,
                    sync_global=False, creation_operation_id=plan["creation_operation_id"])
                if not created.get("ok"):
                    raise ValueError("native steward Goal creation did not complete")
            else:
                # Native creation can publish registration before initializing
                # storage. Its existing owner recovers that same creation;
                # bootstrap itself correctly rejects every registered id.
                goal = load_goal_from_registry(self.controller.registry_path, goal_id)
                initialize_goal_storage_target(self.controller.coordination_runtime_root, goal,
                                               registry_path=self.controller.registry_path)
            configure_goal(registry_path=self.controller.registry_path, goal_id=goal_id,
                           registered_agents=[plan["agent_id"]], execute=True)
            # A queued user request wins this idle boundary. Preserve the
            # native creation receipt for recovery; do not publish a new host.
            session = self._session(binding_id, session["session_id"])
            if self._workspace(session) != workspace:
                raise ValueError("the original private steward workspace changed")
            result = self.bindings._core("collaboration.conversation.binding", {
                **params, "current": current, "expected_revision": current["revision"],
                "operation": "bind_managed_actor", "binding_id": binding_id,
                "observation": self.bindings.observe(binding["transport_ref"]),
                "session": session, "queued_turn_count": len(self.store.queued_turns(session["session_id"])),
                "goal": self._goal(goal_id)})
            if result["changed"]:
                _atomic_write_json(self.bindings.path, result["state"])
            readback = self.bindings.read()
            if readback != result["state"]:
                raise OSError("managed steward identity publication did not verify")
            actor = next(row["managed_actor"] for row in readback["bindings"] if row["binding_id"] == binding_id)
            return {"ok": True, "applied": True, "idempotent": False, "plan": plan, "actor": actor,
                    "scheduling_enabled": False, "model_invoked": False}

    def health(self, binding_id: str) -> dict[str, Any]:
        """Metadata projection only; it neither verifies grants nor enables work."""
        try:
            binding = next(row for row in self.bindings.read()["bindings"] if row["binding_id"] == binding_id)
            actor = binding.get("managed_actor")
            if actor is None:
                return {"available": True, "prepared": False, "scheduling_enabled": False}
            goal = self._goal(actor["goal_id"])
            session = self._session(binding_id, actor["session_id"])
            if (not goal or goal["goal_instance_id"] != actor["goal_instance_id"]
                    or goal["creation_operation_id"] != actor["creation_operation_id"]
                    or actor["agent_id"] not in goal["registered_agents"]
                    or session.get("status") == "closed"
                    or goal["workspace_path"] != str(self._workspace(session))):
                raise ValueError("native steward identity unavailable")
            from .steward_monitor import StewardMonitor
            from ...control_plane.scheduler.monitor_todo import monitor_cadence_delta
            monitor = StewardMonitor(self.controller).current(actor)
            native_goal = load_goal_from_registry(self.controller.registry_path, actor["goal_id"])
            active = bool(native_goal and native_goal.get("status") == "active" and not goal_is_stopped(native_goal))
            cadence = monitor_cadence_delta(monitor.get("cadence")) if monitor else None
            from .steward_reports import StewardReports
            return {"available": True, "prepared": True,
                    "scheduling_enabled": bool(active and monitor and monitor.get("status") == "open"),
                    "goal_active": active, "report": StewardReports(self.controller).health(binding_id),
                    "monitor": ({"todo_id": monitor["todo_id"], "status": monitor["status"],
                        "cadence_minutes": int(cadence.total_seconds() / 60) if cadence else None,
                        "next_due_at": monitor.get("next_due_at"), "last_checked_at": monitor.get("last_checked_at")}
                        if monitor else None),
                    "goal_id": actor["goal_id"], "agent_id": actor["agent_id"], "session_id": actor["session_id"]}
        except (KeyError, StopIteration, OSError, ValueError):
            return {"available": False, "error_code": "managed_steward_identity_unavailable"}


class ManagedTaskValidationUnavailable(ValueError):
    """A native selection has no usable caller-declared independent validator."""


class ManagedSessionTurn:
    """Thin local-owner entry to the existing Turn worker and task validator.

    No identity preparation, new Session, calendar or task policy lives here.
    Attached and provider-bound conversations retain their original owners.
    """
    def __init__(self, controller: Any) -> None:
        self.controller, self.store = controller, controller.store
        self.validation_basis: dict[str, Any] | None = None
        self.task_validator = None
        self.validation_unavailable = False

    def _request(self, session_id: str, agent_id: str, operation_id: str,
                 todo_id: str | None = None) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", operation_id):
            raise ValueError("operation_id must be a bounded public token")
        session = self.store.load_session(session_id)
        if not session:
            raise KeyError("chat session was not found")
        goal = StewardManagedMode(self.controller, None)._goal(session.get("goal_id"))
        if not goal:
            raise ValueError("the original registered Goal is unavailable")
        actor = {"schema_version": "loopx_managed_session_actor_v0", "goal_id": session["goal_id"],
            "goal_instance_id": session.get("goal_instance_id"), "session_id": session_id,
            "agent_id": agent_id, "workspace_path": goal["workspace_path"]}
        return {"operation": "native_turn", "host": "codex-app-server", "actor": actor,
            "turn_instance_id": "managed-session." + operation_id, "todo_id": todo_id,
            "work_kind": "native_turn", "validation_command": None}

    def admit(self, request: dict[str, Any], *, turn: dict[str, Any] | None = None) -> dict[str, Any]:
        from .conversation_bindings import ChatConversationBindings
        if self.controller.closed.is_set():
            raise ValueError("the original Chat host is stopped")
        actor = request["actor"]
        session = self.store.load_session(actor["session_id"])
        goal = load_goal_from_registry(self.controller.registry_path, actor["goal_id"])
        return ChatConversationBindings._core("collaboration.session.managed_turn.admit", {
            "session": session, "request": request, "turn": turn,
            "goal": StewardManagedMode(self.controller, None)._goal(actor["goal_id"]),
            "goal_active": bool(goal and goal.get("status") == "active" and not goal_is_stopped(goal)),
            "queued_turn_count": len(self.store.queued_turns(actor["session_id"]))})

    def inspect(self, session_id: str, *, agent_id: str | None = None,
                operation_id: str | None = None) -> dict[str, Any]:
        session = self.store.load_session(session_id)
        if not session:
            raise KeyError("chat session was not found")
        goal = StewardManagedMode(self.controller, None)._goal(session.get("goal_id"))
        agents = goal["registered_agents"] if goal else []
        result: dict[str, Any] = {"ok": True, "session_id": session_id, "registered_agents": agents,
            "available": False, "turn": None}
        if operation_id is not None:
            if not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", operation_id):
                raise ValueError("invalid operation identity")
            # Readback of an accepted/unknown request is not another execution.
            result["turn"] = self.store.turn_for_client(session_id, "managed." + operation_id)
        try:
            self.admit(self._request(session_id, agent_id or next(iter(agents), ""), "preview"))
            result["available"] = True
        except ValueError as exc:
            result["reason"] = str(exc)
        return result

    def submit(self, session_id: str, *, agent_id: str, operation_id: str,
               todo_id: str | None = None) -> tuple[dict[str, Any], bool]:
        from ...attached_session import managed_session_goal_commit
        controller = self.controller
        with controller._session_adapter_lock(session_id):
            request = self._request(session_id, agent_id, operation_id, todo_id)
            existing = self.store.turn_for_client(session_id, "managed." + operation_id)
            if existing is not None:
                if existing.get("loopx_request") != request:
                    raise ValueError("managed Turn request identity conflict; inspect the original operation")
                return existing, False  # Failed/unknown work is never replayed.
            commit = managed_session_goal_commit(store=self.store,
                registry_path=controller.registry_path, session_id=session_id)
            with commit():
                admitted = self.admit(request)
                accepted = self.store.accept_managed_turn(session_id, client_turn_id="managed." + operation_id,
                    message="Advance the currently admitted native LoopX work.", display_message="推进一次 LoopX 任务。",
                    origin="native_loopx", loopx_execution=True, loopx_request=request)
            try:
                session = self.store.load_session(session_id)
                adapter = controller._ensure_adapter_locked(session, work_dir=Path(admitted["workspace_path"]),
                    objective="Advance this Goal's authorized work.", accepted_turn_id=accepted.turn["turn_id"])
                if Path(adapter.session.work_dir).resolve() != Path(admitted["workspace_path"]).resolve():
                    raise ValueError("the original managed conversation workspace changed")
                controller._start_accepted_turn_worker(session_id=session_id, turn_id=accepted.turn["turn_id"],
                    message=accepted.turn["message"], attachments=[], adapter=adapter, loopx_execution=True)
            except Exception as exc:
                controller._fail_queue_preparation(session_id, accepted.turn["turn_id"], exc)
                raise
            return accepted.turn, accepted.created

    def _validation_source(self, request: dict[str, Any], todo_id: str) -> dict[str, Any]:
        from ...control_plane.todos.list_readback import list_goal_todos
        readback = list_goal_todos(registry_path=self.controller.registry_path, goal_id=request["actor"]["goal_id"],
            todo_id=todo_id, role="agent", runtime_root_arg=str(self.controller.coordination_runtime_root))
        rows = [readback["todo"]] if readback.get("todo") else []
        if len(rows) != 1 or rows[0].get("completion_validation_required") is not True:
            raise ManagedTaskValidationUnavailable("the selected task needs its existing independent argv validation declaration")
        return rows[0]

    def prepare_validation(self, request: dict[str, Any], native_request: dict[str, Any]) -> None:
        from ...control_plane.turn_driver.driver import selected_turn_todo
        from ...control_plane.turn_driver.command_validation import build_loopx_turn_command_validator
        from ...control_plane.todos.completion_validation_store import read_completion_validation_declaration
        selected = selected_turn_todo(native_request.get("turn_envelope") or {})
        if not selected.get("todo_id"):
            raise ManagedTaskValidationUnavailable("select an existing native task with independent validation")
        self.validation_basis = self._validation_source(request, selected["todo_id"])
        declaration = read_completion_validation_declaration(runtime_root=self.controller.coordination_runtime_root,
            goal_id=request["actor"]["goal_id"], todo_id=selected["todo_id"],
            expected_digest=self.validation_basis["completion_validation_sha256"])
        if not declaration or not declaration.get("validation_command_argv"):
            raise ManagedTaskValidationUnavailable("the selected task requires its existing argv validation declaration")
        self.task_validator = build_loopx_turn_command_validator(declaration["validation_command_argv"],
            project=Path(request["actor"]["workspace_path"]), timeout_seconds=declaration.get("validation_timeout_seconds") or 20)

    def validator(self, request: dict[str, Any], *, turn_id: str):
        """Resolve only the native selection's immutable, caller-declared argv.

        No browser or model argument may install a host validation command.
        The captured canonical digest and fresh source must agree before and
        after the existing command validator reads the task's real artifact.
        """
        from ...control_plane.turn_driver.driver import selected_turn_todo

        def validate(plan, result):
            actor = request["actor"]
            selected = selected_turn_todo(plan.get("turn_envelope") or {})
            basis = self.validation_basis
            if not basis or not self.task_validator or basis["todo_id"] != selected["todo_id"]:
                raise ValueError("the original task validation source was not captured before model work")

            def current():
                self.admit(request, turn=self.store.load_turn(actor["session_id"], turn_id))
                fresh = self._validation_source(request, selected["todo_id"])
                if any(fresh.get(key) != basis.get(key) for key in
                       ("completion_validation_sha256", "completion_validation_revision")):
                    raise ValueError("the selected task's validation declaration changed")

            current()
            receipt = self.task_validator(plan, result)
            current()
            return receipt

        return validate
