"""Native Goal/Agent preparation for the original private steward.

The typed conversation owner fixes identity and consent. Existing Goal creation
and configuration perform durable effects. No model, monitor, scheduler or
second Chat Session is started here; preparation is distinct from activation.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ...agent_registry import load_goal_from_registry, registered_agent_ids_for_goal
from ...chat_store import _atomic_write_json
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
            return {"available": True, "prepared": True, "scheduling_enabled": False,
                    "goal_id": actor["goal_id"], "agent_id": actor["agent_id"], "session_id": actor["session_id"]}
        except (KeyError, StopIteration, OSError, ValueError):
            return {"available": False, "error_code": "managed_steward_identity_unavailable"}
