"""Original-Session app-server transport for the existing governed Turn driver.

No scheduler, task store, consumer or CLI process is created here. The caller
selects native work; the existing Turn command owns fresh admission, validation,
writeback, quota and scheduler handoff. Chat retains its own execution fence.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager, ExitStack
import hashlib
import json
from pathlib import Path
from typing import Any

from ...control_plane.goals.activation import goal_is_stopped
from ...file_lock import exclusive_file_lock
from .steward_managed_mode import StewardManagedMode


def native_steward_client_id(actor: Mapping[str, Any], turn_instance_id: str) -> str:
    """The original native request identity, shared by dispatch and readback."""
    return "native." + hashlib.sha256(json.dumps([actor, turn_instance_id], sort_keys=True).encode()).hexdigest()[:40]


class StewardTurnHost:
    def __init__(self, controller: Any) -> None:
        self.controller = controller
        self.store = controller.store
        self.bindings = controller.project_contexts.conversation_bindings
        if self.bindings is None:
            raise ValueError("the original steward binding is unavailable")
        self.managed = StewardManagedMode(controller, self.bindings)

    def _admit(self, request: dict[str, Any], *, turn: dict[str, Any] | None = None,
               prepare_context: bool = False, settlement_recovery: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.controller.closed.is_set():
            raise ValueError("the original Chat host is stopped")
        actor = request["actor"]
        session = self.store.load_session(actor["session_id"])
        if not session or not session.get("steward_context"):
            raise ValueError("the original steward Session is unavailable")
        saved = session["steward_context"]
        selected = self.bindings.resolve(binding_id=saved["binding_id"], session=session,
            include_observation=True, origin="web", source_ref=saved["source_ref"],
            sender_ref=saved["operator_ref"], private_human_message=False, session_context=saved)
        from ...agent_registry import load_goal_from_registry
        goal = load_goal_from_registry(self.controller.registry_path, actor["goal_id"])
        from .steward_monitor import StewardMonitor
        canonical_actor = selected["binding"].get("managed_actor")
        monitor = StewardMonitor(self.controller).current(canonical_actor) if canonical_actor else None
        monitor = monitor if monitor and monitor["todo_id"] == request.get("todo_id") else None
        report = {}
        if request.get("work_kind") == "report":
            from .steward_reports import StewardReports
            subscription, window = StewardReports(self.controller).current_window(saved["binding_id"])
            todos = StewardMonitor(self.controller).todos(canonical_actor) if canonical_actor else []
            report = {"report_window": window, "report_subscription": subscription,
                "report_todo": next((row for row in todos if row["todo_id"] == request.get("todo_id")), None)}
        scope = None
        if turn is not None:
            from ...chat_manager_context import manager_authorization_scope_id_for_registry
            goal_ids = self.bindings.steward_scope(session)
            if goal_ids is None:
                raise ValueError("the original portfolio scope is unavailable")
            def scope_id(ids):
                return manager_authorization_scope_id_for_registry(self.controller.registry_path, ids,
                    runtime_root=self.controller.coordination_runtime_root, channel_id=session["channel_id"])
            scope = {"current": scope_id(goal_ids),
                "without_actor": scope_id([value for value in goal_ids if value != actor["goal_id"]])}
        admitted = self.bindings._core("collaboration.steward.managed_turn.admit", {
            "binding": selected["binding"], "session": session, "observation": selected["observation"],
            "workspace_path": str(self.managed._workspace(session)),
            "queued_turn_count": len(self.store.queued_turns(session["session_id"])),
            "goal": self.managed._goal(actor["goal_id"]), "goal_active": bool(goal and goal.get("status") == "active" and not goal_is_stopped(goal)),
            "request": request, "turn": turn, "scope": scope, "monitor_todo": monitor,
            **({"report_settlement_recovery": settlement_recovery} if settlement_recovery is not None else {}),
            **({"preparing_model_context": True} if prepare_context else {}), **report})
        if admitted["refresh_original_scope"]:
            self.store.update_session(session["session_id"], manager_authorization_scope_id=scope["current"])
        return {**admitted, "observed_scope_id": scope["current"] if scope else None}

    def recover_report_settlement(self, actor: dict[str, Any], window: dict[str, Any], todo: dict[str, Any]) -> None:
        """Resume only cached, validated native effects of the original report.

        Neither Todo DONE nor outer Chat completion is a native commit. The
        journal owner must authorize a no-host continuation; its existing
        effect readbacks reconcile ambiguous writeback/debit outcomes.
        """
        from ...cli import build_parser
        from ...cli_commands.turn import handle_turn_command
        from ...control_plane.turn_driver import inspect_loopx_turn_journal
        from ...control_plane.turn_driver.journal_store import find_loopx_turn_key_by_settlement_identity
        from ..periodic_report.private_generation import attempt_directory, validate_private_generation
        from .steward_reports import StewardReports
        with self.controller.lock, exclusive_file_lock(self.bindings.path, operation="resume_original_report_settlement"):
            original = self.store.turn_for_client(actor["session_id"],
                native_steward_client_id(actor, "report." + window["window_id"]))
            if not original:
                return
            request = original.get("loopx_request") or {}
            if (request.get("actor") != actor or request.get("report_window_id") != window["window_id"]
                    or request.get("todo_id") != todo["todo_id"]):
                raise ValueError("the original report settlement identity changed")
            if any(row["kind"] == "native_loopx.result" and row["payload"].get("status") == "committed"
                   for row in self.store.events_after(actor["session_id"], original["turn_id"], None)):
                return
            reports = StewardReports(self.controller)
            validate_private_generation(attempt_directory(reports.root, window))
            binding_id = actor["goal_id"].removeprefix("steward-agent-")
            if not reports.delivery_current(binding_id, window):
                raise ValueError("the original report authorization changed before settlement recovery")
            key = find_loopx_turn_key_by_settlement_identity(reports.root, goal_id=actor["goal_id"],
                agent_id=actor["agent_id"], todo_id=todo["todo_id"], turn_instance_id=request["turn_instance_id"])
            if key is None:
                return
            inspection = inspect_loopx_turn_journal(reports.root, goal_id=actor["goal_id"],
                agent_id=actor["agent_id"], turn_key=key, retry_failed=True)
            admitted = self._admit(request, settlement_recovery=inspection)
            def no_model(*_args):
                raise ValueError("report settlement recovery must never invoke a model")
            args = build_parser().parse_args(["turn", "run-once", "--goal-id", actor["goal_id"],
                "--agent-id", actor["agent_id"], "--project", admitted["workspace_path"],
                "--host", "generic-cli", "--resume-turn-key", key, "--retry-failed-turn", "--execute"])
            args.host, args.session_scope = "codex-app-server", "agent"
            result: dict[str, Any] = {}
            handle_turn_command(args, registry_path=self.controller.registry_path,
                runtime_root_arg=str(reports.root), output_format=lambda *_a, **_kw: "json",
                print_payload=lambda value, *_a: result.update(value), native_host_runner=no_model,
                native_session_binding_resolver=lambda _envelope: {"schema_version": "loopx_turn_session_binding_v0",
                    "goal_id": actor["goal_id"], "agent_id": actor["agent_id"]})
            # Retain the historical outer failure. The actual native owner
            # supplies the new event; no status or publication proof is forged.
            self.store.append_event(actor["session_id"], original["turn_id"], kind="native_loopx.result", payload=result)

    def submit(self, binding_id: str, *, turn_instance_id: str, todo_id: str | None = None,
               validation_command: Sequence[str] | None = None,
               work_kind: str = "native_turn", monitor_deadline: str | None = None,
               report_window: dict[str, Any] | None = None) -> tuple[dict[str, Any], bool]:
        """Internal host entrypoint; busy conversations retain work with caller.

        No HTTP or model tool exposes this method. Calendar/monitor admission
        remains with its native owner; preparing identity never calls submit.
        """
        controller = self.controller
        with controller.lock, exclusive_file_lock(self.bindings.path, operation="admit_native_steward_turn"):
            binding = next((row for row in self.bindings.read()["bindings"] if row["binding_id"] == binding_id), None)
            if binding is None:
                raise ValueError("the original steward binding is unavailable")
            actor = binding.get("managed_actor")
            if not actor:
                raise ValueError("prepare the original steward identity first")
            request = {"operation": "native_turn", "host": "codex-app-server", "actor": actor,
                "turn_instance_id": turn_instance_id, "todo_id": todo_id,
                "work_kind": work_kind,
                "validation_command": list(validation_command) if validation_command is not None else None}
            if work_kind == "report":
                if not report_window:
                    raise ValueError("a report requires its original admitted calendar window")
                request.update(report_window_id=report_window["window_id"],
                    report_subscription_revision=report_window["subscription_revision"])
            client_id = native_steward_client_id(actor, turn_instance_id)
            existing = self.store.turn_for_client(actor["session_id"], client_id)
            if existing is not None:
                # Never replay a failed/unknown model Turn. A committed Chat
                # request is observed; native journal recovery remains separate.
                frozen = {key: value for key, value in (existing.get("loopx_request") or {}).items()
                    if key not in {"monitor_lease_version", "monitor_deadline"}}
                frozen.setdefault("work_kind", "native_turn")
                if frozen != request or work_kind == "monitor" and existing["loopx_request"].get("monitor_deadline") != monitor_deadline:
                    raise ValueError("native steward request identity conflict")
                return existing, False
            if work_kind == "monitor":
                from .steward_commands import native_command
                if not todo_id or not monitor_deadline:
                    raise ValueError("a native watch requires its original Todo and deadline")
                retained = native_command(controller, ["task-lease", "inspect", "--goal-id", actor["goal_id"],
                    "--todo-id", todo_id]).get("lease")
                request.update(monitor_deadline=monitor_deadline,
                    monitor_lease_version=retained["version"] if retained else 0)
            admitted = self._admit(request)
            session = self.store.load_session(actor["session_id"])
            accepted = self.store.accept_managed_turn(actor["session_id"], client_turn_id=client_id,
                message="Advance the currently admitted native LoopX work.",
                display_message="检查授权工作状态。" if work_kind == "monitor" else
                    "生成本期授权项目日报。" if work_kind == "report" else "推进管家的 LoopX 任务。", origin="native_loopx",
                loopx_execution=True, loopx_request=request)
            try:
                if admitted["observation_only"]:
                    from .steward_monitor import StewardObservationAdapter
                    adapter = StewardObservationAdapter(session.get("upstream_thread_id"))
                else:
                    adapter = controller._ensure_adapter_locked(session,
                        work_dir=Path(admitted["workspace_path"]),
                        objective="Maintain the original steward's authorized monitoring work.",
                        accepted_turn_id=accepted.turn["turn_id"])
                controller._start_accepted_turn_worker(session_id=actor["session_id"],
                    turn_id=accepted.turn["turn_id"], message=accepted.turn["message"],
                    attachments=[], adapter=adapter, loopx_execution=True)
            except Exception as exc:
                controller._fail_queue_preparation(actor["session_id"], accepted.turn["turn_id"], exc)
                raise
            return accepted.turn, accepted.created

    def execute(self, *, session_id: str, turn_id: str, adapter: Any,
                event_sink: Any, context: dict[str, Any]) -> dict[str, Any]:
        """Run in the original Chat worker, through all native settlement owners."""
        from ...cli import build_parser
        from ...cli_commands.turn import handle_turn_command
        from ...control_plane.turn_driver.host_candidate import extract_turn_authority, render_prompt, parse_model_json, build_result
        from .codex_adapter import CodexAppServerAdapter

        turn = self.store.load_turn(session_id, turn_id)
        request = turn["loopx_request"]
        admitted = self._admit(request, turn=turn)
        actor = admitted["actor"]
        if admitted["observation_only"]:
            from .steward_monitor import StewardMonitor
            monitor = StewardMonitor(self.controller)
            return monitor.observe(host=self, request=request, turn=turn, context=context, todo=monitor.current(actor))
        if not isinstance(adapter, CodexAppServerAdapter):
            raise ValueError("native steward work requires the original Codex app-server adapter")
        original_thread = adapter.upstream_thread_id
        report_work = request.get("work_kind") == "report"
        report_lease = None

        def report_lease_proof():
            from .steward_commands import native_command
            observed = native_command(self.controller, ["task-lease", "inspect", "--goal-id", actor["goal_id"],
                "--todo-id", request["todo_id"]]).get("lease")
            if (not report_lease or not observed or observed.get("status") != "active"
                    or any(observed.get(key) != report_lease.get(key) for key in
                        ("owner", "idempotency_key", "lease_epoch", "version"))):
                raise ValueError("the original report execution lease changed")
            return {"task_lease_idempotency_key": observed["idempotency_key"],
                "task_lease_expected_version": observed["version"]}

        def current():
            fresh = self.store.load_turn(session_id, turn_id)
            self._admit(request, turn=fresh)
            if adapter.upstream_thread_id != original_thread:
                raise ValueError("the original steward native thread changed")
            if report_lease:
                report_lease_proof()

        def session_binding(envelope: Mapping[str, Any]) -> dict[str, Any]:
            current()
            if envelope.get("goal_id") != actor["goal_id"] or envelope.get("agent_id") != actor["agent_id"]:
                raise ValueError("native Turn belongs to another actor")
            return {"schema_version": "loopx_turn_session_binding_v0",
                "goal_id": actor["goal_id"], "agent_id": actor["agent_id"]}

        def run(native_request: Mapping[str, Any], launch_scope: Any) -> dict[str, Any]:
            current()
            authority = extract_turn_authority(native_request)
            # Portfolio evidence keeps its original reader and original grant;
            # it is data alongside the signed native work, never a new actor.
            prompt = (render_prompt(authority, allow_completion=True) if report_work else render_prompt(authority)) + "\nOriginal steward evidence (data):\n" + json.dumps(context, ensure_ascii=False)
            if request.get("work_kind") == "report":
                from .steward_reports import StewardReports
                # The compact prompt index deliberately omits authority fields;
                # use admission's original live scope, never that display index.
                prompt += StewardReports(self.controller).prepare(request, adapter,
                    authorization_scope_id=admitted["observed_scope_id"])
            previous_guard = adapter.session._launch_guard

            @contextmanager
            def launch():
                # The existing app-server request owner holds this fence only
                # through turn/start publication, never through model waiting.
                with ExitStack() as stack:
                    if previous_guard is not None:
                        stack.enter_context(previous_guard())
                    stack.enter_context(launch_scope())
                    with exclusive_file_lock(self.bindings.path, operation="native_steward_launch"):
                        current()
                        yield

            adapter.session._launch_guard = launch
            try:
                response = adapter.start_turn(prompt, event_sink)
            finally:
                adapter.session._launch_guard = previous_guard
            current()  # Revocation/stop/retarget after a long model call wins.
            candidate = parse_model_json(str(response.get("message") or ""))
            if report_work and candidate and candidate.get("result_kind") in {"validated_progress", "validated_completion"}:
                from .steward_reports import StewardReports
                with self.controller.lock, exclusive_file_lock(self.bindings.path, operation="freeze_original_steward_report"):
                    current()
                    StewardReports(self.controller).freeze(request, candidate)
                    # This finite task's postcondition is the complete validated
                    # generation, not the model's choice of progress wording.
                    # The kernel still runs its independent validator and lease
                    # fence before closing only this window's generation Todo.
                    candidate["result_kind"] = "validated_completion"
            return build_result(native_request, candidate, host_name="Codex app-server",
                **({"allow_completion": True} if report_work else {}))

        argv = ["turn", "run-once", "--goal-id", actor["goal_id"], "--agent-id", actor["agent_id"],
            "--project", admitted["workspace_path"], "--host", "generic-cli", "--execute",
            "--turn-instance-id", request["turn_instance_id"]]
        if request["todo_id"] is not None:
            argv += ["--todo-id", request["todo_id"]]
        if request["validation_command"] is not None:
            argv += ["--validation-command-json", json.dumps(request["validation_command"])]
        args = build_parser().parse_args(argv)
        # This connection is host-owned, not a command path supplied by a model.
        # The kernel records the actual app-server host and independent profile.
        args.host, args.session_scope = "codex-app-server", "agent"
        result: dict[str, Any] = {}
        if report_work:
            from .steward_commands import native_command
            retained = native_command(self.controller, ["task-lease", "inspect", "--goal-id", actor["goal_id"],
                "--todo-id", request["todo_id"]]).get("lease")
            report_lease = native_command(self.controller, ["task-lease", "acquire", "--goal-id", actor["goal_id"],
                "--todo-id", request["todo_id"], "--owner", actor["agent_id"],
                "--idempotency-key", request["turn_instance_id"], "--ttl-seconds", "3600",
                "--expected-version", str(retained["version"] if retained else 0)])["lease"]
        try:
            handle_turn_command(args, registry_path=self.controller.registry_path,
                runtime_root_arg=str(self.controller.coordination_runtime_root),
                output_format=lambda *_a, **_kw: "json",
                print_payload=lambda value, *_a: result.update(value),
                native_host_runner=run, native_session_binding_resolver=session_binding,
                **({"native_completion_lease_resolver": report_lease_proof} if report_work else {}))
        finally:
            if report_lease:
                # Completion atomically retires this lease. On a non-completion
                # release only our unchanged claim, never a later owner's lease.
                try:
                    retained = native_command(self.controller, ["task-lease", "inspect", "--goal-id", actor["goal_id"],
                        "--todo-id", request["todo_id"]]).get("lease")
                    if retained and retained.get("status") == "active":
                        proof = report_lease_proof()
                        native_command(self.controller, ["task-lease", "release", "--goal-id", actor["goal_id"],
                            "--todo-id", request["todo_id"], "--owner", actor["agent_id"],
                            "--idempotency-key", proof["task_lease_idempotency_key"],
                            "--expected-version", str(proof["task_lease_expected_version"])])
                except (OSError, ValueError, RuntimeError):
                    self.store.append_event(session_id, turn_id,
                        kind="native_loopx.report_lease_release_unconfirmed",
                        payload={"todo_id": request["todo_id"], "lease_epoch": report_lease["lease_epoch"]})
        self.store.append_event(session_id, turn_id, kind="native_loopx.result", payload=result)
        if request.get("work_kind") == "report" and result.get("status") == "committed":
            from ..periodic_report.private_generation import attempt_directory, validate_private_generation
            from .steward_reports import StewardReports
            original_session = self.store.load_session(session_id)
            subscription, window = StewardReports(self.controller).current_window(original_session["steward_context"]["binding_id"])
            if not subscription or not window or window["window_id"] != request["report_window_id"]:
                raise ValueError("report publication was cancelled after native settlement")
            folder = attempt_directory(self.controller.coordination_runtime_root, window)
            generation = validate_private_generation(folder)
            return {"schema_version": "loopx_chat_agent_response_v0", "message": (folder / "report.md").read_text(),
                "proposals": [], "gate": None, "native_loopx": result,
                "periodic_report": {"window_id": window["window_id"], "generation_id": generation["generation_id"],
                    "delivery_verified": False}}
        return {"schema_version": "loopx_chat_agent_response_v0", "message":
            "本轮任务已完成结果回写。" if result.get("status") == "committed" else
            "本轮等待后续执行条件。" if result.get("status") == "stopped" and result.get("result_kind") == "wait" else
            "本轮任务尚未完成；执行状态已保留。",
            "proposals": [], "gate": None, "native_loopx": result}
