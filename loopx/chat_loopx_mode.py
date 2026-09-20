"""Owner-local conversation control over native continuation and scoped delegation.

The host binds the sender and execution configuration; models cannot select
identities, commands or filesystem roots. Task acceptance remains with Delegations.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading
import time

from .agent_registry import load_goal_from_registry, registered_agent_ids_for_goal
from .chat_codex_goal import CodexGoalDriver, validate_goal_chat
from .control_plane.effect_runtime import effect_runtime_result
from .file_lock import exclusive_file_lock, LockAcquisitionPolicy
from .orchestration import (
    compact_orchestration_policy,
    normalize_subagent_execution_config,
)

TOOL = {
    "type": "function",
    "name": "loopx_collaboration",
    "description": "In explicitly enabled LoopX mode, read authorized member bindings, delegate work, "
    "and read independently accepted results. The host fixes your identity and scope. "
    "Choose the work order yourself. An accepted result requires canonical task completion. "
    "Use action=bindings first; start requires binding_id, stable operation_id and brief with "
    "schema_version=collaboration_brief_v0, purpose, context, constraints (strings), inputs "
    "(relative ref, description, optional sha256), acceptance (strings), return_requirement. "
    "A version-bound input adds delegation={operation_id,ref,relation} and sha256; relation is "
    "responds_to, revises or uses. Copying inputs needs existing workspace authority. "
    "Action=adopt(operation_id,consumer_operation_id) records your decision after both results "
    "are accepted and the consumer has that exact uses input. Reading alone is not adoption. "
    "Read/wait/resume use the original operation_id. Running is not failure; do not duplicate it. "
    "After context loss, action=operations recovers this requester's durable work. Follow "
    "next_cursor for more; unavailable means reconcile, not redispatch. Action=inspect with binding_id checks the actual Turn/profile before new dispatch; unknown availability is not readiness.",
    "inputSchema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": ["bindings", "operations", "inspect", "start", "read", "wait", "resume", "adopt", "messages"],
            },
            "binding_id": {"type": "string"},
            "operation_id": {"type": "string"},
            "consumer_operation_id": {"type": "string"},
            "brief": {"type": "object"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "cursor": {"type": "string"},
        },
        "required": ["action"],
    },
}

GUIDANCE = (
    "This is explicitly enabled LoopX conversation execution. The host binds your coordinator "
    "identity and authorized execution catalog. Use loopx_context_read for fresh Goal facts and "
    "loopx_collaboration to organize actual member work. Decide questions, ordering and recovery "
    "yourself; no business-phase script is provided. Members may delegate through the same "
    "service when authorized. Only current accepted results returned by that service establish "
    "member completion. Synthesize their actual artifacts and report remaining gaps here. "
    "Read action=messages between work steps for owner inbox additions. "
    "Do not claim the whole canonical Goal is complete. Keep independent analysis substantive. "
    "Before completing or blocking, return a self-contained report with substantive accepted findings, "
    "exact artifact hashes and remaining gaps; do not require readers to reconstruct earlier streamed replies. "
    "A missing report-writing grant must not prevent returning the complete analysis in this conversation. "
    "Pause/block when tools, authorization or evidence are insufficient."
)


class ChatLoopXMode:
    def __init__(self, controller):
        self.controller = controller
        self.store = controller.store
        self.locks: dict[str, threading.RLock] = {}
        self.lock = threading.Lock()

    def _lock(self, session_id):
        with self.lock:
            return self.locks.setdefault(session_id, threading.RLock())

    def _session(self, session_id):
        session = self.store.load_session(session_id)
        if not session or session.get("status") == "closed":
            raise ValueError("conversation unavailable")
        validate_goal_chat(session, [])
        return session

    def _goal(self, session):
        goal = load_goal_from_registry(
            self.controller.registry_path, session["goal_id"]
        )
        if not goal:
            raise ValueError("Goal unavailable")
        return goal

    @staticmethod
    def _goal_execution_config(goal) -> str | None:
        return str(
            compact_orchestration_policy(goal.get("spawn_policy")).get(
                "execution_config"
            )
            or ""
        ) or None

    @staticmethod
    def _unfinished_native_goal(session) -> bool:
        return (session.get("native_goal") or {}).get("status") not in {
            None,
            "absent",
            "complete",
        }

    def _execution(self, session, settings):
        from .collaboration_mcp import Delegations

        goal = self._goal(session)
        workspace = Path(goal["repo"]).resolve()
        goal_execution_config = self._goal_execution_config(goal)
        stored_config_raw = str(
            settings.get("execution_config_ref")
            or settings.get("execution_config")
            or ""
        ).strip()
        stored_config = (
            normalize_subagent_execution_config(stored_config_raw)
            if stored_config_raw
            else None
        )
        # Before the Goal registry became the sole configuration owner, an
        # unfinished native Goal pinned this pointer in its Session. Preserve
        # that exact execution identity only until the run reaches a terminal
        # state; new and completed runs must use the Goal-owned pointer.
        execution_config = goal_execution_config or (
            stored_config if self._unfinished_native_goal(session) else None
        )
        if not execution_config:
            raise ValueError(
                "configure delegation bindings in Goal sub-agent settings first"
            )
        if (
            stored_config
            and goal_execution_config
            and stored_config != goal_execution_config
        ):
            raise ValueError(
                "Goal execution bindings changed; reopen settings before continuing"
            )
        relative = Path(execution_config)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.parts[:2] != (".loopx", "config")
        ):
            raise ValueError(
                "select an execution binding JSON file under .loopx/config/"
            )
        path = (workspace / relative).resolve()
        if (
            not path.is_relative_to(workspace / ".loopx" / "config")
            or not path.is_file()
            or path.stat().st_size > 1_000_000
        ):
            raise ValueError("execution binding file unavailable")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if settings.get("config_digest") and settings["config_digest"] != digest:
            raise ValueError("execution bindings changed; reconcile before continuing")
        service = Delegations(
            self.store.root.parent,
            self.controller.registry_path,
            session["goal_id"],
            settings["agent_id"],
            path,
        )
        directory = service.directory()
        if not directory["bindings"]:
            raise ValueError(
                "the selected coordinator has no authorized member bindings"
            )
        return service, directory, digest, execution_config

    def snapshot(self, session_id):
        session = self._session(session_id)
        goal = self._goal(session)
        mode = session.get("loopx_mode") or {}
        settings = mode.get("settings") or {}
        execution_config = self._goal_execution_config(goal)
        if not execution_config and self._unfinished_native_goal(session):
            legacy_config = str(
                settings.get("execution_config_ref")
                or settings.get("execution_config")
                or ""
            ).strip()
            if legacy_config:
                try:
                    execution_config = normalize_subagent_execution_config(
                        legacy_config
                    )
                except ValueError:
                    execution_config = None
        native = session.get("native_goal") or {"status": "absent"}
        busy_turn = session.get("active_turn_id")
        active = (
            busy_turn
            if busy_turn
            and (self.store.load_turn(session_id, busy_turn) or {}).get(
                "loopx_execution"
            )
            else None
        )
        deliveries = session.get("loopx_deliveries") or []
        # Refresh unfinished observations even while the coordinator is paused:
        # delegated members have their own lifecycles. Accepted labels remain
        # explicitly last-read observations, never canonical Goal settlement.
        if deliveries and not active:
            try:
                service, _, _, _ = self._execution(session, settings)
                deliveries = [
                    {**row, "status": service.read(row["operation_id"])["status"]}
                    for row in deliveries
                ]
            except (ValueError, KeyError, OSError):
                deliveries = [{**row, "status": "unavailable"} for row in deliveries]
        return {
            "ok": True,
            "session_id": session_id,
            "enabled": mode.get("enabled") is True,
            "settings": {
                **{
                    key: settings.get(key)
                    for key in ("agent_id", "token_budget")
                },
                "execution_config": execution_config or None,
            },
            "native": native,
            "active_turn_id": active,
            "conversation_busy": bool(busy_turn),
            "registered_agents": registered_agent_ids_for_goal(goal),
            "members": mode.get("members") or [],
            "paused": mode.get("paused") is True,
            "recovery_required": bool(
                active and session_id not in self.controller.adapters
            ),
            "deliveries": deliveries,
            "ingress": [
                {key: row.get(key) for key in ("client_ingress_id", "mode", "status")}
                for row in self.store.loopx_ingress(session_id)[-20:]
            ],
        }

    def read_team(self, session_id, body):
        """Owner readback stays available while paused; no model Turn is submitted."""
        operation = body.get("operation")
        fields = {
            "inspect": {"operation", "binding_id"},
            "operations": {"operation", "limit", "cursor"},
            "read": {"operation", "operation_id"},
        }
        if operation not in fields or set(body) - fields[operation]:
            raise ValueError("invalid team readback request")
        session = self._session(session_id)
        settings = (session.get("loopx_mode") or {}).get("settings") or {}
        if not settings.get("agent_id"):
            raise ValueError("configure a coordinator identity before team readback")
        service, _, _, _ = self._execution(session, settings)
        if operation == "read":
            from .control_plane.collaboration.peers import require_operation_id

            result = service.read(require_operation_id(body.get("operation_id")))
        elif operation == "inspect":
            result = service.inspect(body.get("binding_id", ""))
        else:
            result = service.operations(limit=body.get("limit", 10), cursor=body.get("cursor"))
        return {"ok": True, **result}

    def apply(self, session_id, body, *, work_dir, objective):
        if body.get("operation") in {"inspect", "operations", "read"}:
            return self.read_team(session_id, body)
        if set(body) - {
            "operation",
            "settings",
            "operation_id",
            "message",
            "delivery_mode",
        }:
            raise ValueError("unknown LoopX mode input")
        with self._lock(session_id):
            if body.get("operation") == "message":
                return self.message(session_id, body)
            session = self._session(session_id)
            operation = body.get("operation")
            mode = session.get("loopx_mode") or {}
            operation_id = str(body.get("operation_id") or "")
            if operation in {"start", "resume"}:
                from .control_plane.collaboration.peers import require_operation_id

                require_operation_id(operation_id)
                existing = self.store.turn_for_client(session_id, operation_id)
                if existing:
                    prior_request = existing.get("loopx_request") or {}
                    prior_settings = prior_request.get("settings") or {}
                    expected = body.get("settings", prior_settings)
                    if (
                        not isinstance(expected, dict)
                        or prior_request.get("operation") != operation
                        or not existing.get("loopx_execution")
                        or any(
                            expected.get(k) != prior_settings.get(k)
                            for k in ("agent_id", "token_budget")
                        )
                    ):
                        raise ValueError("execution operation identity conflict")
                    return {**self.snapshot(session_id), "turn_id": existing["turn_id"]}
            if operation in {"pause", "exit"}:
                effect_runtime_result(
                    "collaboration.chat_mode",
                    {
                        "session": session,
                        "origin": "web",
                        "operation": operation,
                        "settings": {},
                        "native": session.get("native_goal") or {},
                    },
                )
                if session.get("active_turn_id"):
                    turn = self.store.load_turn(session_id, session["active_turn_id"])
                    if not turn or not turn.get("loopx_execution"):
                        raise ValueError(
                            "the active turn is an ordinary conversation; use its interrupt control"
                        )
                self.store.update_session(
                    session_id,
                    loopx_mode={**mode, "enabled": operation != "exit", "paused": True},
                )
                if session.get("active_turn_id"):
                    self.controller.interrupt_turn(
                        session_id=session_id, turn_id=session["active_turn_id"]
                    )
                adapter = self.controller.adapters.get(session_id)
                if adapter:
                    driver = CodexGoalDriver(adapter.session)
                    driver.pause()
                    self.store.update_session(
                        session_id, native_goal=driver.compact(driver.read())
                    )
                return self.snapshot(session_id)
            settings = body.get("settings", mode.get("settings") or {})
            if not isinstance(settings, dict) or (
                "settings" in body
                and set(settings) - {"agent_id", "token_budget", "execution_config"}
            ):
                raise ValueError("invalid LoopX mode settings")
            # An unfinished native Goal keeps its original execution identity.
            prior = mode.get("settings") or {}
            if (
                session.get("native_goal", {}).get("status")
                not in {None, "absent", "complete"}
                and prior
            ):
                if any(
                    prior.get(k) != settings.get(k)
                    for k in ("agent_id",)
                ):
                    raise ValueError(
                        "an unfinished Goal cannot change coordinator or execution bindings"
                    )
                settings = {
                    **settings,
                    "execution_config_ref": (
                        prior.get("execution_config_ref")
                        or prior.get("execution_config")
                    ),
                    "config_digest": prior.get("config_digest"),
                }
            if not settings.get("agent_id"):
                raise ValueError("select a registered coordinator identity")
            service, directory, digest, execution_config = self._execution(
                session, settings
            )
            settings = {
                "agent_id": settings["agent_id"],
                "token_budget": settings.get("token_budget"),
                "execution_config_ref": execution_config,
                "config_digest": digest,
            }
            goal = self._goal(session)
            effect_runtime_result(
                "collaboration.chat_mode",
                {
                    "session": session,
                    "origin": "web",
                    "operation": operation,
                    "settings": settings,
                    "native": session.get("native_goal") or {},
                    "registered_agents": registered_agent_ids_for_goal(goal),
                    "goal_active": goal.get("status") not in {"stopped", "archived"},
                    "execution_binding_valid": True,
                },
            )
            new_mode = {**mode, "settings": settings, "members": directory["bindings"]}
            if operation == "configure":
                self.store.update_session(session_id, loopx_mode=new_mode)
                return self.snapshot(session_id)
            self.store.update_session(
                session_id, loopx_mode={**new_mode, "enabled": True, "paused": False}
            )
            try:
                command = f"/goal {operation} --tokens {settings['token_budget']}"
                if operation == "start":
                    command += " " + objective[:3000]
                turn, _ = self.controller.submit_turn(
                    session_id=session_id,
                    client_turn_id=operation_id,
                    message=command,
                    attachments=[],
                    work_dir=work_dir,
                    objective=objective,
                    loopx_execution=True,
                    loopx_request={"operation": operation, "settings": settings},
                )
            except Exception:
                self.store.update_session(session_id, loopx_mode=mode)
                raise
            return {**self.snapshot(session_id), "turn_id": turn["turn_id"]}

    def recover(self, session_id, adapter):
        driver = CodexGoalDriver(adapter.session)
        driver.pause()
        self.store.update_session(session_id, native_goal=driver.compact(driver.read()))
        for row in self.store.loopx_ingress(session_id):
            if row["status"] == "delivering":
                self.store.update_ingress_receipt(session_id, row["client_ingress_id"], status="uncertain")

    def activate_tools(self, session, *, work_dir, objective):
        """Explicit enable upgrades an idle executor, retaining local conversation history.

        Codex cannot add dynamic tools to a resumed thread. An unfinished native
        Goal must retain that exact thread, so it is never replaced here.
        """
        current = self.controller._ensure_adapter_locked(
            session, work_dir=work_dir, objective=objective
        )
        native = CodexGoalDriver(current.session).read()
        if native and native["status"] != "complete":
            raise ValueError(
                "the existing native Goal is unfinished; retain it or explicitly create a new conversation"
            )
        history = [
            {
                "role": "assistant" if row["role"] == "agent" else "user",
                "content": row["text"],
            }
            for row in self.store.messages(session["session_id"])
            if row["role"] in {"agent", "user"}
        ]
        replacement = self.controller._start_adapter(
            agent_id=session["agent_id"],
            work_dir=work_dir,
            goal_id=session["goal_id"],
            objective=objective,
            history=history,
            project_coordination=True,
            loopx_tools=True,
            executor_model={
                "model": current.session.model,
                "reasoning_effort": current.session.reasoning_effort,
            },
        )
        try:
            updated = self.store.update_session(
                session["session_id"],
                loopx_tools=True,
                upstream_thread_id=replacement.upstream_thread_id,
                upstream_mode="chat",
                native_goal={"status": "absent"},
                loopx_executor={
                    "model": replacement.session.model,
                    "reasoning_effort": replacement.session.reasoning_effort,
                },
            )
        except Exception:
            replacement.close_session()
            raise
        with self.controller.lock:
            self.controller.adapters[session["session_id"]] = replacement
        current.close_session()
        return updated

    def message(self, session_id, body):
        session = self._session(session_id)
        effect_runtime_result(
            "collaboration.chat_mode",
            {
                "session": session,
                "origin": "web",
                "operation": "message",
                "settings": {},
                "delivery_mode": body.get("delivery_mode"),
                "turn": self.store.load_turn(session_id, session["active_turn_id"])
                if session.get("active_turn_id")
                else {},
            },
        )
        message = body.get("message")
        if not isinstance(message, str) or not message.strip() or len(message) > 12000:
            raise ValueError("a bounded message is required")
        identity = body.get("operation_id", "")
        if body["delivery_mode"] == "steer":
            receipt, created = self.controller.steer_active_turn(
                session_id=session_id, client_ingress_id=identity, message=message
            )
            return {
                "ok": True,
                "created": created,
                "delivery_mode": "steer",
                "status": "delivered",
                "turn_id": receipt["turn_id"],
            }
        pending = [
            row
            for row in self.store.loopx_ingress(session_id)
            if row["status"] == "pending"
        ]
        if len(pending) >= 20 and not any(
            row["client_ingress_id"] == identity
            for row in self.store.loopx_ingress(session_id)
        ):
            raise ValueError("conversation queue is full")
        receipt, _ = self.store.create_ingress_receipt(
            session_id,
            client_ingress_id=identity,
            mode="loopx_" + body["delivery_mode"],
            message=message,
        )
        self.store.append_message(
            session_id,
            role="user",
            text=message,
            origin=receipt["mode"],
            message_id="loopx-" + identity,
        )
        return {
            "ok": True,
            "status": receipt["status"],
            "delivery_mode": body["delivery_mode"],
        }

    def deliver_queue(self, session_id, adapter, upstream_turn_id):
        if (self._session(session_id).get("loopx_mode") or {}).get("paused"):
            return
        pending = [
            row
            for row in self.store.loopx_ingress(session_id)
            if row["mode"] == "loopx_queue" and row["status"] == "pending"
        ][:20]
        if not pending:
            return
        for row in pending:
            self.store.update_ingress_receipt(
                session_id,
                row["client_ingress_id"],
                status="delivering",
                active_turn_id=upstream_turn_id,
            )
        try:
            adapter.session.steer(
                "Owner messages queued for this new turn:\n"
                + json.dumps([row["message"] for row in pending], ensure_ascii=False),
                expected_turn_id=upstream_turn_id,
            )
        except Exception:
            for row in pending:
                self.store.update_ingress_receipt(
                    session_id, row["client_ingress_id"], status="uncertain"
                )
            raise
        for row in pending:
            self.store.update_ingress_receipt(
                session_id, row["client_ingress_id"], status="delivered"
            )

    def prepare(self, session_id, turn_id, adapter, read_handler, emit):
        session = self._session(session_id)
        mode = session.get("loopx_mode") or {}
        settings = mode.get("settings") or {}
        if not mode.get("enabled") or mode.get("paused"):
            raise ValueError("conversation execution is paused")
        service, _, _, _ = self._execution(session, settings)
        # Multiple conversations cannot run under the same configured sender.
        identity = hashlib.sha256(
            json.dumps([session["goal_id"], settings["agent_id"]]).encode()
        ).hexdigest()
        lock = exclusive_file_lock(
            self.store.root / "coordinators" / identity,
            policy=LockAcquisitionPolicy.SINGLE_FLIGHT,
        )
        lock.__enter__()
        adapter.goal_driver.turn_start_handler = lambda upstream: self.deliver_queue(
            session_id, adapter, upstream
        )

        def dispatch(name, arguments):
            if name != TOOL["name"]:
                return read_handler(name, arguments)
            current = self._session(session_id)
            current_mode = current.get("loopx_mode") or {}
            if (
                not current_mode.get("enabled")
                or current_mode.get("paused")
                or current.get("active_turn_id") != turn_id
                or adapter.goal_driver.stopped.is_set()
            ):
                return {"ok": False, "error": "conversation_execution_inactive"}
            self._execution(
                current, settings
            )  # registration/config revocation on every call
            if not isinstance(arguments, dict) or set(arguments) - {
                "action",
                "binding_id",
                "operation_id",
                "consumer_operation_id",
                "brief",
                "limit",
                "cursor",
            }:
                raise ValueError("invalid collaboration arguments")
            action = arguments.get("action")
            if action != "operations" and ("limit" in arguments or "cursor" in arguments):
                raise ValueError("pagination is only valid for operations")
            if action == "operations" and set(arguments) - {"action", "limit", "cursor"}:
                raise ValueError("operations reads a page; use read to select an operation")
            if action != "adopt" and "consumer_operation_id" in arguments:
                raise ValueError("consumer_operation_id is only valid for adopt")
            operation_id = arguments.get("operation_id", "")
            if action == "messages":
                rows = [
                    r
                    for r in self.store.loopx_ingress(session_id)
                    if r["mode"] == "loopx_inbox" and r["status"] == "pending"
                ][:20]
                for row in rows:
                    self.store.update_ingress_receipt(
                        session_id, row["client_ingress_id"], status="delivered"
                    )
                result = {
                    "messages": [r["message"] for r in rows],
                    "note": "Read delivery is not work adoption.",
                }
            elif action == "bindings":
                result = service.directory()
            elif action == "inspect":
                if set(arguments) != {"action", "binding_id"}:
                    raise ValueError("inspect requires only binding_id")
                result = service.inspect(arguments["binding_id"])
            elif action == "operations":
                result = service.operations(limit=arguments.get("limit", 20), cursor=arguments.get("cursor"))
            elif action == "adopt":
                if set(arguments) != {"action", "operation_id", "consumer_operation_id"}:
                    raise ValueError("adopt requires source and consumer operation ids only")
                result = service.adopt_result(operation_id, arguments["consumer_operation_id"])
            elif action == "start":
                result = service.start(
                    arguments.get("binding_id", ""),
                    operation_id,
                    arguments.get("brief", {}),
                )
            elif action in {"read", "wait", "resume"}:
                result = (
                    service.resume(operation_id)
                    if action == "resume"
                    else service.read(operation_id)
                )
                if action == "wait":
                    emit("agent.phase", {"label": "LoopX 正在等待成员返回"})
                    for _ in range(5):
                        if (
                            result["status"] in {"accepted", "rejected"}
                            or result["recovery_required"]
                            or adapter.goal_driver.stopped.is_set()
                        ):
                            break
                        time.sleep(1)
                        result = service.read(operation_id)
            else:
                raise ValueError("unsupported collaboration action")
            if "status" in result:
                summary = {
                    key: result.get(key)
                    for key in ("operation_id", "agent_id", "todo_id", "status")
                }
                rows = [
                    r
                    for r in self._session(session_id).get("loopx_deliveries", [])
                    if r.get("operation_id") != operation_id
                ]
                self.store.update_session(
                    session_id, loopx_deliveries=(rows + [summary])[-20:]
                )
                emit("collaboration.execution", summary)
            return {"ok": True, **result}

        def tool(name, arguments):
            try:
                # Dispatch admission and pause share the same fence. Already
                # launched children remain independently governed.
                with self._lock(session_id):
                    return dispatch(name, arguments)
            except (ValueError, KeyError, OSError):
                return {
                    "ok": False,
                    "error": "collaboration_request_rejected",
                    "next_action": "Check the registered identity, binding, operation id and unchanged execution configuration.",
                }

        adapter.session.read_tool_handler = tool
        return lock


def handle_loopx_request(handler, session_id: str, *, apply: bool = False) -> None:
    try:
        if not handler._require_loopback_origin():
            return
        session = handler.server.chat_store.load_session(session_id)
        if not session:
            raise ValueError("conversation unavailable")
        service = handler.server.runtime_controller.loopx_mode
        if apply:
            context = handler._session_context(session)
            result = service.apply(session_id, handler._read_json(), work_dir=context["project"],
                                   objective=str(context["objective"] or context["title"]))
        else:
            result = service.snapshot(session_id)
        handler._send_json(result)
    except ValueError as exc:
        handler._send_error(str(exc), status=409, error_code="loopx_mode_unavailable")
    except Exception:
        handler._send_error("LoopX mode could not access its configured executor or bindings. Check the local configuration and reconnect the conversation.", status=409, error_code="loopx_mode_unavailable")
