"""Native monitor Todo + the existing Chat host, without a second scheduler.

The Todo owns enable/pause, cadence and observation state. The existing host's
cheap wake pump checks its due projection; the original Chat worker performs
the observation and the native monitor-poll owner settles it without a model
or quota spend. Reports remain a separate periodic-report integration.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import subprocess as subprocess

from ...control_plane.scheduler.monitor_todo import monitor_todo_is_due
from ...control_plane.todos.list_readback import list_goal_todos
from ...file_lock import exclusive_file_lock
from .github_pr_evidence import observe_github_repository_prs
from .steward_commands import NativeStewardCommandError, native_command
from .steward_managed_mode import StewardManagedMode
from .steward_monitor_config import configure_steward_monitor
from .steward_monitor_pump import (
    pump_managed_session_monitors as pump_managed_session_monitors,
    pump_steward_monitors as pump_steward_monitors,
)
from .steward_monitor_snapshots import commit_signal_snapshot, fence_observed_signal_scope
from .steward_monitor_signals import (
    SignalMonitorCollection,
    _accepts_backfill_comments as _accepts_backfill_comments,
    _github_repo_slug as _github_repo_slug,
    _signal_scope_uses_stalled_age as _signal_scope_uses_stalled_age,
    collect_signal_monitor,
    rearm_exhausted_followup_if_needed,
)
from .steward_monitor_turns import (
    ADVANCEMENT_ADMISSION_RETRY_SUFFIX as ADVANCEMENT_ADMISSION_RETRY_SUFFIX,
    ADVANCEMENT_PRECOMMIT_RETRY_COOLDOWN_SECONDS as ADVANCEMENT_PRECOMMIT_RETRY_COOLDOWN_SECONDS,
    ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX as ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX,
    FOLLOWUP_TARGET_PREFIX as FOLLOWUP_TARGET_PREFIX,
    STEWARD_BACKGROUND_CHANNEL_PREFIX as STEWARD_BACKGROUND_CHANNEL_PREFIX,
    STEWARD_BACKGROUND_SESSION_PREFIX as STEWARD_BACKGROUND_SESSION_PREFIX,
    TARGET as TARGET,
    StewardControllerProtocol,
    StewardHostProtocol as StewardHostProtocol,
    StewardObservationAdapter as StewardObservationAdapter,
    StewardTodoRecord,
    _GOVERNANCE_TASK_CONTEXT_KEYS as _GOVERNANCE_TASK_CONTEXT_KEYS,
    _coerce_dict,
    actor_steward_binding_id as actor_steward_binding_id,
    advancement_turn_id as advancement_turn_id,
    ensure_steward_background_session as ensure_steward_background_session,
    governance_turn_prompt_authority as governance_turn_prompt_authority,
    is_exhausted_zero_write_followup_todo as is_exhausted_zero_write_followup_todo,
    load_steward_monitor_turn_by_id as load_steward_monitor_turn_by_id,
    lookup_steward_monitor_turn as lookup_steward_monitor_turn,
    monitor_turn_id as monitor_turn_id,
    normalize_steward_monitor_scope as normalize_steward_monitor_scope,
    read_steward_monitor_scope as read_steward_monitor_scope,
    resolve_advancement_turn_id as resolve_advancement_turn_id,
    steward_background_channel_id as steward_background_channel_id,
    steward_background_session_id as steward_background_session_id,
    steward_monitor_delivery_dir as steward_monitor_delivery_dir,
    steward_monitor_scope_path as steward_monitor_scope_path,
    steward_monitor_snapshot_path as steward_monitor_snapshot_path,
    write_steward_monitor_scope as write_steward_monitor_scope,
)


def _observe_github_repository_prs(
    repo_slug: str,
    *,
    backfill_comments: bool = False,
    select_comment_backfill: Callable[[Sequence[Mapping[str, object]], float], set[int]] | None = None,
    select_review_backfill: Callable[[Sequence[Mapping[str, object]], float], set[int]] | None = None,
) -> dict[str, object]:
    """Read bounded public/authorized open PR signal metadata without side effects."""
    raw = observe_github_repository_prs(
        repo_slug,
        runner=subprocess.run,
        backfill_comments=backfill_comments,
        select_comment_backfill=select_comment_backfill,
        select_review_backfill=select_review_backfill,
    )
    return _coerce_dict(raw)


class StewardMonitor:
    """Coordinate native Steward portfolio, signal, and session monitor execution."""

    def __init__(self, controller: StewardControllerProtocol) -> None:
        self.controller = controller
        self.bindings = controller.project_contexts.conversation_bindings
        self.managed = StewardManagedMode(controller, self.bindings)

    def todos(self, actor: Mapping[str, object]) -> list[StewardTodoRecord]:
        result = list_goal_todos(
            registry_path=self.controller.registry_path,
            runtime_root_arg=str(self.controller.coordination_runtime_root),
            goal_id=str(actor["goal_id"]),
            agent_id=str(actor["agent_id"]),
            role="agent",
        )
        raw_todos = result["todos"]
        if not isinstance(raw_todos, Sequence) or isinstance(raw_todos, (str, bytes)):
            raise TypeError("todos payload must be a sequence")
        return [
            StewardTodoRecord({str(k): v for k, v in row.items()})
            for row in raw_todos
            if isinstance(row, Mapping)
        ]

    def current(
        self,
        actor: Mapping[str, object],
        *,
        todos: Sequence[StewardTodoRecord] | None = None,
    ) -> StewardTodoRecord | None:
        source = self.todos(actor) if todos is None else todos
        rows = [row for row in source if row.get("target_key") == TARGET]
        if len(rows) > 1:
            raise ValueError("the original native monitor is ambiguous")
        return rows[0] if rows else None

    def due_monitors(
        self,
        actor: Mapping[str, object],
        *,
        todos: Sequence[StewardTodoRecord] | None = None,
    ) -> list[StewardTodoRecord]:
        source = self.todos(actor) if todos is None else todos
        self.current(actor, todos=source)
        rows = [
            row
            for row in source
            if row.get("task_class") == "continuous_monitor"
            and row.get("claimed_by") == actor["agent_id"]
            and isinstance(row.get("target_key"), str)
            and row["target_key"]
            and not row["target_key"].startswith("managed-session-watch.")
            and not row["target_key"].startswith(FOLLOWUP_TARGET_PREFIX)
            and monitor_todo_is_due(row)
        ]
        return sorted(
            rows,
            key=lambda row: (str(row.get("next_due_at") or ""), str(row["todo_id"])),
        )

    def runnable_advancement_todos(
        self,
        actor: Mapping[str, object],
        *,
        todos: Sequence[StewardTodoRecord] | None = None,
    ) -> list[StewardTodoRecord]:
        source = self.todos(actor) if todos is None else todos
        rows = [
            row
            for row in source
            if row.get("status") == "open"
            and row.get("task_class") == "advancement_task"
            and row.get("action_kind") != "generate_periodic_report"
            and row.get("claimed_by") == actor["agent_id"]
            and isinstance(row.get("target_key"), str)
            and row["target_key"].startswith(FOLLOWUP_TARGET_PREFIX)
            and row.get("resume_ready") is not False
        ]
        prio = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}
        return sorted(
            rows,
            key=lambda row: (
                prio.get(str(row.get("priority") or "P1"), 9),
                str(row.get("created_at") or ""),
                str(row["todo_id"]),
            ),
        )

    def _acquire(
        self,
        actor: Mapping[str, object],
        todo: Mapping[str, object],
        key: str,
        *,
        expected_version: int | None = None,
    ) -> dict[str, object]:
        route = [
            "--goal-id",
            str(actor["goal_id"]),
            "--todo-id",
            str(todo["todo_id"]),
        ]
        if expected_version is None:
            retained = native_command(
                self.controller, ["task-lease", "inspect", *route]
            ).get("lease")
            expected_version = (
                int(retained["version"]) if isinstance(retained, Mapping) else 0
            )
        return native_command(
            self.controller,
            [
                "task-lease",
                "acquire",
                *route,
                "--owner",
                str(actor["agent_id"]),
                "--idempotency-key",
                key,
                "--ttl-seconds",
                "600",
                "--expected-version",
                str(expected_version),
            ],
        )

    def _release(
        self,
        actor: Mapping[str, object],
        todo: Mapping[str, object],
        lease: Mapping[str, object],
    ) -> None:
        native_command(
            self.controller,
            [
                "task-lease",
                "release",
                "--goal-id",
                str(actor["goal_id"]),
                "--todo-id",
                str(todo["todo_id"]),
                "--owner",
                str(actor["agent_id"]),
                "--idempotency-key",
                str(lease["idempotency_key"]),
                "--expected-version",
                str(lease["version"]),
            ],
        )

    def _commit_poll(
        self,
        host: StewardHostProtocol,
        request: Mapping[str, object],
        turn: Mapping[str, object],
        context: Mapping[str, object],
        argv: list[str],
        *,
        signal_snapshot: dict[str, object] | None = None,
    ) -> dict[str, object]:
        # Configuration and revocation use this original binding fence too.
        # Keep the final scoped admission and native write in one ordering;
        # data collected under an earlier scope cannot become a new result.
        actor = _coerce_dict(request["actor"])
        request_copy = _coerce_dict(request)
        session_id = str(turn["session_id"])
        turn_id = str(turn["turn_id"])
        if actor.get("schema_version") == "loopx_managed_session_actor_v0":
            from ...attached_session import managed_session_goal_commit

            commit = managed_session_goal_commit(
                store=self.controller.store,
                registry_path=self.controller.registry_path,
                session_id=session_id,
            )
            with self.controller.lock, commit():
                loaded_turn = self.controller.store.load_turn(session_id, turn_id)
                host._admit(
                    request_copy,
                    turn=_coerce_dict(loaded_turn) if loaded_turn is not None else None,
                )
                return self._native_poll(argv)
        observed = host._observe(actor)
        with (
            self.controller.lock,
            exclusive_file_lock(self.bindings.path, operation="commit_steward_monitor"),
        ):
            loaded_turn = self.controller.store.load_turn(session_id, turn_id)
            admitted = host._admit(
                request_copy,
                observed=observed,
                turn=_coerce_dict(loaded_turn) if loaded_turn is not None else None,
            )
            if (
                not context.get("authorization_scope_id")
                or admitted["observed_scope_id"] != context["authorization_scope_id"]
            ):
                raise ValueError("the portfolio scope changed after observation")
            if signal_snapshot is None:
                return self._native_poll(argv)
            root = self.controller.coordination_runtime_root
            with fence_observed_signal_scope(root, signal_snapshot):
                return commit_signal_snapshot(
                    root,
                    self.controller.registry_path,
                    signal_snapshot,
                    agent_id=str(actor["agent_id"]),
                    turn_instance_id=str(request["turn_instance_id"]),
                    poll=lambda: self._native_poll(
                        argv, observed_task_repository=str(signal_snapshot.get("task_repository") or ""),
                    ),
                )

    def _native_poll(
        self, argv: Sequence[str], *, observed_task_repository: str | None = None,
    ) -> dict[str, object]:
        """Use the native poll owner; a Chat observation is no heartbeat task."""
        from ...cli_runtime import _build_selected_parser
        from ...cli_commands.lark_inbox import (
            build_lark_operator_inbox_urgency_projector,
        )
        from ...cli_commands.quota_context import prepare_quota_command_context
        from ...control_plane.goals.first_party_host_admission import (
            capture_first_party_host_goal_ref,
        )
        from ...quota import record_quota_monitor_poll
        from ..explore.composition_frontier import project_live_explore_composition_frontier

        args = _build_selected_parser("quota").parse_args(["--format", "json", *argv])
        admission = prepare_quota_command_context(
            args,
            registry_path=self.controller.registry_path,
            runtime_root_arg=str(self.controller.coordination_runtime_root),
            operator_inbox_urgency_projector_factory=(
                build_lark_operator_inbox_urgency_projector
            ),
            force_projection_refresh=True,
        )
        goal_ref = capture_first_party_host_goal_ref(
            registry_path=self.controller.registry_path, goal_id=args.goal_id
        )
        result = record_quota_monitor_poll(
            admission.status_payload,
            registry_path=self.controller.registry_path,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            available_capabilities=args.available_capabilities,
            scheduler_execution_context=admission.scheduler_context,
            operator_inbox_urgency_projector=admission.operator_inbox_urgency_projector,
            bounded_research_frontier_projector=(
                project_live_explore_composition_frontier
            ),
            status_reloader=lambda: prepare_quota_command_context(
                args,
                registry_path=self.controller.registry_path,
                runtime_root_arg=str(self.controller.coordination_runtime_root),
                operator_inbox_urgency_projector_factory=(
                    build_lark_operator_inbox_urgency_projector
                ),
                force_projection_refresh=True,
            ).status_payload,
            todo_id=args.todo_id,
            target_key=args.target_key,
            observed_task_repository=observed_task_repository,
            turn_instance_id=args.turn_instance_id,
            result_hash=args.result_hash,
            material_change=args.material_change,
            next_agent_todo=getattr(args, "next_agent_todo", None),
            next_action_kind=getattr(args, "next_action_kind", None),
            next_task_repository=getattr(args, "next_task_repository", None),
            next_required_capabilities=getattr(
                args, "next_required_capabilities", None
            ),
            next_continuation_policy=getattr(args, "next_continuation_policy", None),
            next_target_key=getattr(args, "next_target_key", None),
            next_claimed_by=getattr(args, "next_claimed_by", None),
            next_agent_priority=getattr(args, "next_agent_priority", None),
            task_lease_idempotency_key=args.task_lease_idempotency_key,
            task_lease_expected_version=args.task_lease_expected_version,
            source="adapter",
            goal_ref=goal_ref,
            execute=True,
        )
        if result.get("ok") is not True:
            raise NativeStewardCommandError(result)
        return _coerce_dict(result)

    def _clear_next_check_hint(self, binding_id: str) -> None:
        from .steward_monitor_pump import clear_steward_monitor_check_hints

        clear_steward_monitor_check_hints(self.controller, binding_id)

    def configure(
        self,
        binding_id: str,
        *,
        operation: str,
        cadence_minutes: int = 30,
        expected_plan_id: str | None = None,
        execute: bool = False,
    ) -> dict[str, object]:
        return configure_steward_monitor(
            self.controller,
            managed=self.managed,
            binding_id=binding_id,
            operation=operation,
            cadence_minutes=cadence_minutes,
            expected_plan_id=expected_plan_id,
            execute=execute,
            read_todos=self.todos,
            current_monitor=self.current,
            acquire_lease=self._acquire,
            release_lease=self._release,
            clear_next_check_hint=self._clear_next_check_hint,
            run_command=native_command,
        )

    def _session_scope_for_actor(self, actor: Mapping[str, object]) -> list[str] | None:
        loaded = self.controller.store.load_session(str(actor["session_id"]))
        if loaded is None:
            raise ValueError("the original steward Session is unavailable")
        return self.bindings.steward_scope(_coerce_dict(loaded))

    def _collect_signal_monitor(
        self,
        actor: Mapping[str, object],
        context: Mapping[str, object],
        todo: Mapping[str, object],
    ) -> SignalMonitorCollection:
        return collect_signal_monitor(
            self.controller,
            actor=actor,
            context=context,
            todo=todo,
            session_scope_for_actor=self._session_scope_for_actor,
            read_todos=self.todos,
            observe_github_prs=_observe_github_repository_prs,
        )

    def _rearm_exhausted_followup_if_needed(
        self,
        actor: Mapping[str, object],
        monitor_todo: Mapping[str, object],
        poll_turn_instance_id: str,
    ) -> bool:
        return rearm_exhausted_followup_if_needed(
            self.controller,
            actor=actor,
            monitor_todo=monitor_todo,
            poll_turn_instance_id=poll_turn_instance_id,
            read_todos=self.todos,
            acquire_lease=self._acquire,
            release_lease=self._release,
            run_command=native_command,
        )

    def observe(
        self,
        *,
        host: StewardHostProtocol,
        request: Mapping[str, object],
        turn: Mapping[str, object],
        context: Mapping[str, object],
        todo: Mapping[str, object] | None,
    ) -> dict[str, object]:
        if todo is None:
            raise ValueError("the native steward monitor is unavailable")
        actor = _coerce_dict(request["actor"])
        goal_id = str(actor["goal_id"])
        agent_id = str(actor["agent_id"])
        turn_instance_id = str(request["turn_instance_id"])
        route = [
            "--goal-id",
            goal_id,
            "--agent-id",
            agent_id,
            "--todo-id",
            str(todo["todo_id"]),
            "--turn-instance-id",
            turn_instance_id,
            "--available-capability",
            "filesystem",
            "--available-capability",
            "external_evidence_poll",
            "--runtime-profile",
            "codex_app_server",
        ]
        host._admit(_coerce_dict(request), turn=_coerce_dict(turn))
        successor_args: list[str] = []
        signal_snapshot = None
        if actor.get("schema_version") == "loopx_managed_session_actor_v0":
            from .conversation_bindings import ChatConversationBindings

            observation = ChatConversationBindings._core(
                "collaboration.session.monitor.observation",
                {
                    "goal": self.managed._goal(goal_id),
                    "actor": actor,
                    "todos": self.todos(actor),
                    "previous_hash": todo.get("result_hash"),
                },
            )
        elif todo.get("target_key") != TARGET:
            observation, successor_args, signal_snapshot = self._collect_signal_monitor(
                actor, context, todo
            )
        else:
            observation = self.bindings._core(
                "collaboration.steward.monitor.observation",
                {
                    "context": _coerce_dict(context),
                    "goal_ids": self._session_scope_for_actor(actor),
                    "actor_goal_id": goal_id,
                    "previous_hash": todo.get("result_hash"),
                },
            )
        # Freeze admission's generation so a pause/re-enable cannot commit stale observations.
        raw_recovery = request.get("monitor_restart_recovery")
        lease_key = (
            "monitor-recovery." + str(raw_recovery["original_turn_id"])
            if isinstance(raw_recovery, Mapping)
            else turn_instance_id
        )
        raw_version = request.get("monitor_lease_version")
        expected_version = int(raw_version) if isinstance(raw_version, int) else None
        raw_lease = self._acquire(
            actor, todo, lease_key, expected_version=expected_version
        )["lease"]
        if not isinstance(raw_lease, Mapping):
            raise ValueError("monitor task lease unavailable")
        lease = _coerce_dict(raw_lease)
        try:
            result = self._commit_poll(
                host,
                request,
                turn,
                context,
                [
                    "quota",
                    "monitor-poll",
                    *route,
                    "--target-key",
                    str(todo["target_key"]),
                    "--result-hash",
                    str(observation["result_hash"]),
                    "--task-lease-idempotency-key",
                    lease_key,
                    "--task-lease-expected-version",
                    str(lease["version"]),
                    "--execute",
                    *(["--material-change"] if observation["material_change"] else []),
                    *successor_args,
                ],
                **({"signal_snapshot": signal_snapshot} if signal_snapshot is not None else {}),
            )
        finally:
            self._release(actor, todo, lease)
        self.controller.store.append_event(
            str(turn["session_id"]),
            str(turn["turn_id"]),
            kind="native_loopx.monitor_poll",
            payload={"observation": observation, "receipt": result},
        )
        rearmed_followup = False
        if (
            actor.get("schema_version") != "loopx_managed_session_actor_v0"
            and todo.get("target_key") != TARGET
            and not successor_args
        ):
            rearmed_followup = self._rearm_exhausted_followup_if_needed(
                actor, todo, turn_instance_id
            )
        if successor_args or rearmed_followup:
            self._clear_next_check_hint(goal_id.removeprefix("steward-agent-"))
        return {
            "schema_version": "loopx_chat_agent_response_v0",
            "message": "已检查授权 Goal 状态；覆盖缺口和原始请求继续保留。",
            "proposals": [],
            "gate": None,
            "native_loopx": {
                "ok": True,
                "status": "observed",
                "model_invoked": False,
                "quota_spent": False,
                "monitor": observation,
                "receipt": result,
            },
        }
