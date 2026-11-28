"""Execution orchestration for native Steward Turn workers."""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
import json
from pathlib import Path
from typing import Protocol, cast

from ...control_plane.goals.first_party_host_admission import FirstPartyHostRuntimeRejected
from ...file_lock import exclusive_file_lock
from .steward_turn_common import (
    StewardTurnHostProtocol,
    as_dict,
    as_dict_list,
    as_mapping,
    as_optional_dict,
    native_steward_client_id,
    steward_monitor_for,
)
from .steward_turn_context import (
    ExecutionReportServiceProtocol,
    build_steward_turn_prompt,
    finalize_steward_execution_response,
)


class _GuardOwnerProtocol(Protocol):
    _launch_guard: Callable[[], AbstractContextManager[None]] | None


class _TurnAdapterProtocol(Protocol):
    def start_turn(
        self,
        message: str,
        event_sink: Callable[[str, dict[str, object]], None] | None = None,
    ) -> dict[str, object]: ...

    def start_native_task_turn(
        self,
        message: str,
        event_sink: Callable[[str, dict[str, object]], None] | None,
        policy: dict[str, object],
    ) -> dict[str, object]: ...


def _resolve_turn_selection_recovery(
    host: StewardTurnHostProtocol,
    *,
    session_id: str,
    turn_id: str,
    turn: Mapping[str, object],
    request: Mapping[str, object],
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    actor = as_mapping(request["actor"])
    turn_instance_id = str(request["turn_instance_id"])
    original_client = (
        "managed." + turn_instance_id.removeprefix("managed-session.")
        if actor.get("schema_version") == "loopx_managed_session_actor_v0"
        else native_steward_client_id(actor, turn_instance_id)
    )
    original = host.store.turn_for_client(session_id, original_client) if request.get("work_kind") == "report" else None
    selection_recovery = (
        host.report_selection_recovery(original) if original and original.get("turn_id") != turn_id else None
    )
    resumed_client = (
        original_client + ".admission-resume"
        if actor.get("schema_version") == "loopx_managed_session_actor_v0"
        else native_steward_client_id(actor, turn_instance_id + ".admission-resume")
    )
    if selection_recovery and turn.get("client_turn_id") == resumed_client:
        retry_client = (
            original_client + ".admission-retry"
            if actor.get("schema_version") == "loopx_managed_session_actor_v0"
            else native_steward_client_id(actor, turn_instance_id + ".admission-retry")
        )
        retry = host.store.turn_for_client(session_id, retry_client)
        if retry is None:
            raise ValueError("the original admission probe is unavailable")
        if original is None:
            raise ValueError("the original report Turn is unavailable")
        selection_recovery = host.report_selection_recovery(original, probe_turn=retry)
        if selection_recovery is None:
            raise ValueError("the original admission probe has no canonical journal")
    return original, selection_recovery


def _finalize_candidate_and_build_result(
    host: StewardTurnHostProtocol,
    *,
    native_request: Mapping[str, object],
    response: Mapping[str, object],
    actor: Mapping[str, object],
    request: Mapping[str, object],
    context: Mapping[str, object],
    adapter: object,
    reports: ExecutionReportServiceProtocol | None,
    governance_work: bool,
    report_work: bool,
    managed_turn_active: bool,
    steward_advancement: bool,
    steward_actions_state: dict[str, object],
    current_fn: Callable[[dict[str, object] | None], None],
    normalize_advancement: Callable[[Mapping[str, object]], dict[str, object]],
    normalize_report: Callable[[Mapping[str, object]], dict[str, object]],
) -> dict[str, object]:
    from ...control_plane.turn_driver.host_candidate import build_result, parse_model_json
    from .antigravity_adapter import AntigravityAdapter

    candidate = parse_model_json(str(response.get("message") or ""))
    kernel_candidate = as_dict(candidate) if isinstance(candidate, Mapping) else None
    if steward_advancement and not governance_work and kernel_candidate is not None:
        steward_actions_state.update(host._normalize_steward_actions(kernel_candidate, context))
        kernel_candidate.pop("context_handoff", None)
        kernel_candidate.pop("steward_user_actions", None)
        kernel_candidate.pop("steward_owner_report", None)
        kernel_candidate = normalize_advancement(kernel_candidate)
    elif governance_work and kernel_candidate is not None:
        kernel_candidate.pop("context_handoff", None)
        kernel_candidate.pop("steward_user_actions", None)
        kernel_candidate.pop("steward_owner_report", None)
        kernel_candidate = normalize_advancement(kernel_candidate)
    if (
        governance_work
        and kernel_candidate
        and kernel_candidate.get("result_kind") not in {"replan_required", "wait", "user_action_required", "iteration_failed"}
    ):
        kernel_candidate = {
            "result_kind": "wait",
            "classification": "report_replan_not_settled",
            "next_action": "Retain the original report until its native semantic replan is settled.",
        }
    if (
        report_work
        and not governance_work
        and kernel_candidate
        and kernel_candidate.get("result_kind") in {"validated_progress", "validated_completion"}
    ):
        observed = host._observe(actor)
        with host.controller.lock, host._report_fence(request):
            current_fn(observed)
            assert reports is not None
            reports.freeze(request, kernel_candidate)
            # This finite task's postcondition is the complete validated
            # generation, not the model's choice of progress wording.
            # The kernel still runs its independent validator and lease
            # fence before closing only this window's generation Todo.
            kernel_candidate = normalize_report(kernel_candidate)
    built = build_result(
        native_request,
        kernel_candidate,
        host_name="Antigravity" if isinstance(adapter, AntigravityAdapter) else "Codex app-server",
        allow_completion=bool((report_work or managed_turn_active or steward_advancement) and not governance_work),
    )
    return as_dict(built)


def _append_native_result_with_readback(
    host: StewardTurnHostProtocol,
    *,
    session_id: str,
    turn_id: str,
    result: Mapping[str, object],
) -> None:
    try:
        host.store.append_event(session_id, turn_id, kind="native_loopx.result", payload=dict(result))
    except OSError:
        # The native transaction already owns execution and settlement.
        # A lost Chat append acknowledgment must not discard its answer
        # when this exact committed event is independently readable.
        # Reuse the store's ordered readback without re-entering native work.
        if result.get("ok") is not True or result.get("status") != "committed":
            raise
        recorded = [row for row in host.store.events_after(session_id, turn_id, None) if row.get("kind") == "native_loopx.result"]
        if len(recorded) != 1 or recorded[0].get("payload") != result:
            raise


def execute_steward_turn(
    host: StewardTurnHostProtocol,
    *,
    session_id: str,
    turn_id: str,
    adapter: object,
    event_sink: object,
    context: dict[str, object],
    normalize_advancement: Callable[[Mapping[str, object]], dict[str, object]],
    normalize_report: Callable[[Mapping[str, object]], dict[str, object]],
    is_replan_deferral: Callable[[Mapping[str, object]], bool],
) -> dict[str, object]:
    """Run in the original Chat worker, through all native settlement owners."""
    from ...cli_runtime import _build_selected_parser
    from ...cli_commands.turn import handle_turn_command
    from ...control_plane.turn_driver.host_candidate import build_result, extract_turn_authority
    from .antigravity_adapter import ANTIGRAVITY_IDLE_TIMEOUT_SEC, AntigravityAdapter
    from .codex_adapter import CodexAppServerAdapter
    from .steward_managed_mode import ManagedSessionTurn, ManagedTaskValidationUnavailable
    from .steward_reports import report_service

    turn_raw = host.store.load_turn(session_id, turn_id)
    if turn_raw is None:
        raise ValueError("the accepted Turn is unavailable")
    turn = as_dict(turn_raw)
    request = as_dict(turn["loopx_request"])
    original, selection_recovery = _resolve_turn_selection_recovery(
        host, session_id=session_id, turn_id=turn_id, turn=turn, request=request
    )
    admitted = host._admit(request, turn=turn, selection_recovery=selection_recovery)
    actor = as_mapping(admitted["actor"])
    local_session = actor.get("schema_version") == "loopx_managed_session_actor_v0"
    report_work = request.get("work_kind") == "report"
    steward_advancement = not local_session and not report_work
    managed_turn = ManagedSessionTurn(host.controller) if local_session and not report_work else None
    if admitted["observation_only"]:
        monitor = steward_monitor_for(host.controller)
        todo = next(
            (row for row in as_dict_list(monitor.todos(dict(actor))) if row.get("todo_id") == request.get("todo_id")),
            None,
        )
        if todo is None:
            raise ValueError("the original steward monitor Todo is unavailable")
        return as_dict(monitor.observe(host=host, request=request, turn=turn, context=context, todo=todo))
    if local_session and not isinstance(adapter, CodexAppServerAdapter):
        raise ValueError("native steward work requires the original Codex app-server adapter")
    if not local_session and not isinstance(adapter, (CodexAppServerAdapter, AntigravityAdapter)):
        raise ValueError("native steward work requires the original Codex app-server or Antigravity adapter")

    original_thread = getattr(adapter, "upstream_thread_id", None)
    reports_pair = report_service(host.controller, dict(actor)) if report_work else (None, None)
    reports = cast(ExecutionReportServiceProtocol | None, reports_pair[0])
    report_owner_key: str | None = reports_pair[1]
    report_context = ""
    governance_work = False
    report_launch_rejected = False
    managed_answer = ""
    managed_lease: dict[str, object] | None = None
    managed_lease_request: dict[str, object] | None = None
    preparation_status: str | None = None
    report_lease: dict[str, object] | None = None
    steward_actions_state: dict[str, object] = {
        "context_handoff": None,
        "user_actions": [],
        "owner_report": None,
        "validation_error": None,
    }
    managed_leases = ExitStack()

    def report_lease_proof() -> dict[str, object]:
        return host._task_lease_proof(request, report_lease)

    def current(observed: dict[str, object] | None = None) -> None:
        fresh = host.store.load_turn(session_id, turn_id)
        host._admit(request, turn=fresh, observed=observed, selection_recovery=selection_recovery)
        if getattr(adapter, "upstream_thread_id", None) != original_thread:
            raise ValueError("the original steward native thread changed")
        if report_lease:
            report_lease_proof()
        if managed_lease and managed_lease_request is not None:
            host._task_lease_proof(managed_lease_request, managed_lease)

    def session_binding(envelope: Mapping[str, object]) -> dict[str, object]:
        current(None)
        identity = envelope.get("agent_identity") if envelope.get("schema_version") == "loopx_turn_envelope_v1" else envelope
        if (
            envelope.get("goal_id") != actor["goal_id"]
            or not isinstance(identity, Mapping)
            or identity.get("agent_id") != actor["agent_id"]
        ):
            raise ValueError("native Turn belongs to another actor")
        return {
            "schema_version": "loopx_turn_session_binding_v0",
            "goal_id": actor["goal_id"],
            "agent_id": actor["agent_id"],
        }

    def prepare_native_task(decision: Mapping[str, object]) -> str | None:
        nonlocal managed_lease, managed_lease_request, preparation_status
        from ...control_plane.turn_driver.codex_execution_preparation import compact_execution_preparation_request

        preparation = as_optional_dict(compact_execution_preparation_request(decision))
        if preparation is None:
            return None
        todo_id = str(preparation["todo_id"])
        if (
            preparation["goal_id"] != actor["goal_id"]
            or preparation["agent_id"] != actor["agent_id"]
            or preparation.get("turn_instance_id", request["turn_instance_id"]) != request["turn_instance_id"]
            or (request["todo_id"] is not None and todo_id != request["todo_id"])
        ):
            raise ValueError("native task preparation belongs to another Turn or Todo")
        current(None)
        if managed_turn is not None:
            try:
                managed_turn.prepare_validation(dict(request), {"work_contract": decision})
            except ManagedTaskValidationUnavailable:
                managed_turn.validation_unavailable = True
                preparation_status = "stopped"
                raise
            except ValueError:
                preparation_status = "failed"
                raise
        managed_lease_request = {**request, "todo_id": todo_id}
        managed_lease = managed_leases.enter_context(
            host._task_execution_lease(managed_lease_request, turn_id, claim_args=cast(list[str], preparation["claim_args"]))
        )
        return todo_id

    def run(native_request: Mapping[str, object], launch_scope: Callable[[], AbstractContextManager[None]]) -> dict[str, object]:
        nonlocal managed_lease, managed_lease_request, managed_answer
        current(None)
        authority = as_mapping(extract_turn_authority(native_request))
        if governance_work and not authority.get("replan_action_packet"):
            return as_dict(
                build_result(
                    native_request,
                    {
                        "result_kind": "wait",
                        "classification": "report_replan_not_admitted",
                        "next_action": "Retain the original report; its native governance route is no longer admitted.",
                    },
                )
            )
        if managed_turn and not governance_work:
            raw_work_identity = authority.get("_work_identity")
            checked_identity = (
                {str(k): str(v) for k, v in as_mapping(raw_work_identity).items()}
                if isinstance(raw_work_identity, Mapping)
                else None
            )
            try:
                managed_turn.prepare_validation(
                    dict(request),
                    dict(native_request),
                    _checked_work_identity=checked_identity,
                )
            except ManagedTaskValidationUnavailable:
                managed_turn.validation_unavailable = True
                return as_dict(
                    build_result(
                        native_request,
                        {
                            "result_kind": "user_action_required",
                            "classification": "independent_task_validation_unavailable",
                            "summary": "The selected task has no usable independent argv validation declaration.",
                            "next_action": "Configure the original task's independent validation before executing it.",
                        },
                        host_name="Codex app-server",
                    )
                )
            validation_basis = as_mapping(managed_turn.validation_basis)
            selected_request = {**request, "todo_id": validation_basis["todo_id"]}
            if managed_lease is None:
                managed_lease_request = selected_request
                managed_lease = managed_leases.enter_context(host._task_execution_lease(managed_lease_request, turn_id))
            elif managed_lease_request is None or selected_request["todo_id"] != managed_lease_request["todo_id"]:
                raise ValueError("native task preparation no longer matches the selected Todo")
            host._task_lease_proof(selected_request, managed_lease)

        prompt = build_steward_turn_prompt(
            host,
            actor=actor,
            session_id=session_id,
            request=request,
            authority=authority,
            context=context,
            report_work=report_work,
            governance_work=governance_work,
            managed_turn_active=managed_turn is not None,
            steward_advancement=steward_advancement,
            report_context=report_context,
        )
        if steward_advancement and not governance_work:
            selected_todo_id = as_mapping(authority.get("_work_identity")).get("todo_id") or request.get("todo_id")
            if selected_todo_id and managed_lease is None:
                managed_lease_request = {**request, "todo_id": selected_todo_id}
                managed_lease = managed_leases.enter_context(host._task_execution_lease(managed_lease_request, turn_id))

        raw_owner = getattr(adapter, "session", None) if isinstance(adapter, CodexAppServerAdapter) else adapter
        guard_owner = cast(_GuardOwnerProtocol, raw_owner)
        previous_guard = guard_owner._launch_guard

        @contextmanager
        def launch() -> Iterator[None]:
            nonlocal report_launch_rejected
            observed = host._observe(actor)
            with ExitStack() as stack:
                try:
                    if previous_guard is not None:
                        stack.enter_context(previous_guard())
                    stack.enter_context(launch_scope())
                    stack.enter_context(
                        nullcontext()
                        if actor.get("schema_version") == "loopx_managed_session_actor_v0" or host.bindings is None
                        else exclusive_file_lock(host.bindings.path, operation="native_steward_launch")
                    )
                    current(observed)
                except FirstPartyHostRuntimeRejected as exc:
                    if not report_work or exc.code != "stale_goal_authority":
                        raise
                    if not local_session and not governance_work and original and original.get("turn_id") == turn_id:
                        report_launch_rejected = True
                    from ...control_plane.turn_driver.host_failure import BuiltInHostError

                    raise BuiltInHostError(str(exc), failure_kind="contract_rejected") from exc
                yield

        guard_owner._launch_guard = launch
        try:
            typed_adapter = cast(_TurnAdapterProtocol, adapter)
            sink_fn = cast(Callable[[str, dict[str, object]], None] | None, event_sink)
            if managed_turn and not governance_work:
                from ...control_plane.effect_runtime import effect_runtime_result

                validation_basis = as_mapping(managed_turn.validation_basis)
                policy = as_dict(
                    effect_runtime_result(
                        "collaboration.session.managed_turn.sandbox_policy",
                        {
                            "host": request["host"],
                            "actor": actor,
                            "authority": authority,
                            "validation": validation_basis["plan"],
                            "task_lease": managed_lease,
                        },
                    )
                )
                response = as_mapping(typed_adapter.start_native_task_turn(prompt, sink_fn, policy))
            else:
                invocation_sink = (
                    (
                        lambda kind, payload: sink_fn(kind, payload)
                        if kind not in {"answer.delta", "answer.final", "turn.native_result"}
                        else None
                    )
                    if (governance_work and sink_fn is not None)
                    else sink_fn
                )
                if (report_work or steward_advancement or governance_work) and isinstance(adapter, AntigravityAdapter):
                    response = as_mapping(
                        adapter.start_turn(
                            prompt,
                            invocation_sink,
                            isolated_cascade=True,
                            raw_prompt=True,
                            idle_timeout_sec=(
                                adapter.idle_timeout_sec
                                if adapter.idle_timeout_sec is not None
                                else ANTIGRAVITY_IDLE_TIMEOUT_SEC
                            ),
                        )
                    )
                else:
                    response = as_mapping(typed_adapter.start_turn(prompt, invocation_sink))
        finally:
            guard_owner._launch_guard = previous_guard
        current(None)
        built = _finalize_candidate_and_build_result(
            host,
            native_request=native_request,
            response=response,
            actor=actor,
            request=request,
            context=context,
            adapter=adapter,
            reports=reports,
            governance_work=governance_work,
            report_work=report_work,
            managed_turn_active=managed_turn is not None,
            steward_advancement=steward_advancement,
            steward_actions_state=steward_actions_state,
            current_fn=current,
            normalize_advancement=normalize_advancement,
            normalize_report=normalize_report,
        )
        if managed_turn or steward_advancement:
            managed_answer = str(built.get("summary") or "")
        return built

    argv = [
        "turn", "run-once", "--goal-id", str(actor["goal_id"]), "--agent-id", str(actor["agent_id"]),
        "--project", str(admitted["workspace_path"]), "--host", "generic-cli", "--execute",
    ]
    if request.get("resume_turn_key"):
        argv += ["--resume-turn-key", str(request["resume_turn_key"]), "--retry-failed-turn"]
    else:
        argv += ["--turn-instance-id", str(request["turn_instance_id"])]
    if request.get("todo_id") is not None and not request.get("resume_turn_key"):
        argv += ["--todo-id", str(request["todo_id"])]
    if request.get("validation_command") is not None:
        argv += ["--validation-command-json", json.dumps(request["validation_command"])]
    args = _build_selected_parser("turn").parse_args(argv)
    args.host, args.session_scope = "codex-app-server", "agent"
    result: dict[str, object] = {}

    def retain_report_lease() -> bool:
        failure = as_mapping(result.get("host_failure"))
        return report_launch_rejected and failure.get("kind") == "contract_rejected" and failure.get("attempt") == 1

    with (
        managed_leases,
        (
            host._task_execution_lease(
                request,
                turn_id,
                admission_retry=bool(selection_recovery),
                retain_for_retry=retain_report_lease,
            )
            if report_work
            else nullcontext(None)
        ) as entered_report_lease,
    ):
        report_lease = as_optional_dict(entered_report_lease)
        if not report_work and request.get("todo_id") and not request.get("resume_turn_key"):
            from .steward_commands import native_command

            selected = as_mapping(
                native_command(
                    host.controller,
                    ["todo", "list", "--goal-id", str(actor["goal_id"]), "--todo-id", str(request["todo_id"]), "--agent-id", str(actor["agent_id"])],
                )
            )
            if selected.get("matched") is True:
                managed_lease_request = dict(request)
                managed_lease = managed_leases.enter_context(host._task_execution_lease(request, turn_id))
        if selection_recovery and "probe" not in selection_recovery:
            governance_work = True

            def probe_governance(todo_id: object) -> dict[str, object]:
                governance_args = type(args)(**vars(args))
                governance_args.todo_id, governance_args.validation_command_json = todo_id, None
                governance_args.turn_instance_id = str(request["turn_instance_id"]) + ".replan"
                observed_gov: dict[str, object] = {}
                handle_turn_command(
                    governance_args,
                    registry_path=cast(Path, host.controller.registry_path),
                    runtime_root_arg=str(host.controller.coordination_runtime_root),
                    output_format=lambda *_a, **_kw: "json",
                    print_payload=lambda value, *_a: observed_gov.update(as_mapping(value)),
                    native_host_runner=run,
                    native_session_binding_resolver=session_binding,
                )
                return observed_gov

            governance = probe_governance(request["todo_id"])
            if (
                governance.get("error_code") == "turn_todo_selection_deferred"
                and as_mapping(governance.get("selection_rejection")).get("reason_code") == "autonomous_replan"
            ):
                governance = probe_governance(None)
            host.store.append_event(session_id, turn_id, kind="native_loopx.replan_result", payload=governance)
            if governance.get("status") != "committed" or governance.get("result_kind") != "replan_required":
                assert original is not None
                probe = host.report_selection_recovery(original, probe_turn=host.store.load_turn(session_id, turn_id))
                try:
                    if probe is None or "probe" not in probe:
                        raise ValueError("no canonical admission probe")
                    host._admit(request, turn=host.store.load_turn(session_id, turn_id), selection_recovery=probe)
                except (OSError, ValueError, RuntimeError):
                    retained = as_dict(as_mapping(as_mapping(selection_recovery["original"]).get("response")).get("native_loopx"))
                    host.store.append_event(session_id, turn_id, kind="native_loopx.result", payload=retained)
                    return {
                        "schema_version": "loopx_chat_agent_response_v0",
                        "message": "原生重规划尚未完成；保留原报告及执行证据。",
                        "proposals": [],
                        "gate": None,
                        "native_loopx": retained,
                    }
                selection_recovery = probe
            governance_work = False
        if report_work:
            assert reports is not None
            current(None)
            report_context = reports.prepare(request, adapter, authorization_scope_id=admitted.get("observed_scope_id"))
            current(None)

        def steward_validator(_plan: Mapping[str, object], candidate_result: Mapping[str, object]) -> dict[str, object]:
            if steward_actions_state.get("validation_error"):
                return {
                    "status": "failed",
                    "validator_kind": "callback",
                    "summary": str(steward_actions_state["validation_error"]),
                    "recovery_kind": "repair_required",
                }
            if candidate_result.get("result_kind") in {"validated_progress", "validated_completion"}:
                return {
                    "status": "passed",
                    "validator_kind": "callback",
                    "summary": "steward advancement actions passed validation",
                }
            return {
                "status": "passed",
                "validator_kind": "callback",
                "summary": "steward advancement non-completion result recorded",
            }

        def managed_completion_lease_proof() -> dict[str, object]:
            return host._task_lease_proof(managed_lease_request or request, managed_lease)

        task_validator: Callable[[Mapping[str, object], Mapping[str, object]], Mapping[str, object]] | None = None
        completion_lease_resolver: Callable[[], Mapping[str, object]] | None = None
        if report_work:
            task_validator = host._report_validator(request)
            completion_lease_resolver = report_lease_proof
        elif managed_turn is not None:
            task_validator = managed_turn.validator(dict(request), turn_id=turn_id)
            completion_lease_resolver = managed_completion_lease_proof
        elif steward_advancement:
            task_validator = steward_validator
            completion_lease_resolver = managed_completion_lease_proof

        def run_primary_turn() -> None:
            result.clear()
            handle_turn_command(
                args,
                registry_path=cast(Path, host.controller.registry_path),
                runtime_root_arg=str(host.controller.coordination_runtime_root),
                output_format=lambda *_a, **_kw: "json",
                print_payload=lambda value, *_a: result.update(as_mapping(value)),
                native_host_runner=run,
                native_session_binding_resolver=session_binding,
                native_execution_preparer=prepare_native_task if not report_work else None,
                native_completion_lease_resolver=completion_lease_resolver,
                native_task_validator=task_validator,
            )

        run_primary_turn()
        if (
            steward_advancement
            and request.get("todo_id")
            and not request.get("resume_turn_key")
            and is_replan_deferral(result)
        ):
            governance_args = type(args)(**vars(args))
            governance_args.todo_id, governance_args.validation_command_json = None, None
            governance_args.turn_instance_id = str(request["turn_instance_id"]) + ".replan"
            adv_governance: dict[str, object] = {}
            governance_work = True
            try:
                handle_turn_command(
                    governance_args,
                    registry_path=cast(Path, host.controller.registry_path),
                    runtime_root_arg=str(host.controller.coordination_runtime_root),
                    output_format=lambda *_a, **_kw: "json",
                    print_payload=lambda value, *_a: adv_governance.update(as_mapping(value)),
                    native_host_runner=run,
                    native_session_binding_resolver=session_binding,
                )
            finally:
                governance_work = False
            host.store.append_event(session_id, turn_id, kind="native_loopx.replan_result", payload=adv_governance)
            replan_cleared = (
                adv_governance.get("status") == "committed" and adv_governance.get("result_kind") == "replan_required"
            ) or (
                adv_governance.get("status") == "stopped"
                and as_mapping(adv_governance.get("host_result")).get("classification") == "report_replan_not_admitted"
                and host._cleared_replan_guard(actor) is not None
            )
            if replan_cleared:
                run_primary_turn()
            elif isinstance(adv_governance.get("effects"), Mapping):
                gov_effects = as_mapping(adv_governance["effects"])
                merged_effects = dict(as_mapping(result.get("effects")))
                for key in ("host_invoked", "state_written", "quota_spent", "scheduler_acknowledged"):
                    if gov_effects.get(key) is True:
                        merged_effects[key] = True
                result["effects"] = merged_effects
    if preparation_status is not None:
        result["status"] = preparation_status
        if preparation_status == "stopped":
            result.update(ok=True, result_kind="user_action_required")
    _append_native_result_with_readback(host, session_id=session_id, turn_id=turn_id, result=result)
    return finalize_steward_execution_response(
        host,
        actor=actor,
        session_id=session_id,
        turn_id=turn_id,
        request=request,
        selection_recovery=selection_recovery,
        result=result,
        reports=reports,
        report_owner_key=report_owner_key,
        local_session=local_session,
        steward_advancement=steward_advancement,
        steward_actions_state=steward_actions_state,
        managed_turn_validation_unavailable=bool(managed_turn and managed_turn.validation_unavailable),
        managed_answer=managed_answer,
        managed_lease_request=managed_lease_request,
    )
