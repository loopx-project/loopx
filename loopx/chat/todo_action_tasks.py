"""Task assignment, session admission, claim, and recovery workflows for Todo actions."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path

from ..control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable,
    read_canonical_todos_if_promoted,
)
from ..control_plane.coordination.shadow_management import shadow_maintenance_lock_target
from ..control_plane.goals.activation import goal_is_stopped
from ..control_plane.todos.mutation_api import update_goal_todo
from ..control_plane.work_items.task_lease import lease_is_active
from ..file_lock import exclusive_cross_runtime_file_lock
from .action_store_common import _opaque_id, is_str_dict, require_str_dict
from .todo_action_contracts import (
    ChatTodoActionHost,
    GoalCommitSubmitController,
    TodoSessionStoreProtocol,
    as_dict_list,
    require_str,
    resolve_session_store,
    resolve_turn_loader,
    task_turn_dispatched,
    todo_checkpoints,
)
from .todo_source import (
    ChatTodoSource,
    ChatTodoSourceChanged,
    resolve_chat_todo_source,
    task_start_claim_operation_id,
)


def read_task_start(
    chat: TodoSessionStoreProtocol,
    *,
    goal_id: str,
    agent_id: str,
    channel_id: str,
    client_turn_id: str,
    checkpoint: Mapping[str, object] | None,
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    """Read the persisted Task Session and Turn for creation or recovery."""
    if checkpoint is not None:
        sid = _opaque_id(checkpoint.get("session_id"), field="session_id")
        session = chat.load_session(sid)
        if (
            session is None
            or session.get("goal_id") != goal_id
            or session.get("agent_id") != agent_id
            or session.get("channel_id") != channel_id
        ):
            raise ValueError("The original Task Session is unavailable")
        return session, chat.turn_for_client(sid, client_turn_id)

    # A process may have persisted the Session before the proposal's
    # checkpoint. Include closed Sessions so retry cannot replace one.
    sessions = [
        dict(candidate)
        for candidate in chat.session_candidates(
            goal_id=goal_id,
            agent_id=agent_id,
            channel_id=channel_id,
        )
    ]
    matches: list[tuple[dict[str, object], dict[str, object]]] = []
    for session in sessions:
        turn = chat.turn_for_client(str(session["session_id"]), client_turn_id)
        if turn is not None:
            matches.append((session, turn))
    if len(matches) == 1:
        return matches[0]
    if len(sessions) > 1:
        raise ValueError("The original Task Session is ambiguous")
    return (sessions[0] if sessions else None), None


def read_confirmed_task(
    host: ChatTodoActionHost,
    proposal_id: str,
    parameters: Mapping[str, object],
    todo_id: str,
    steps: Mapping[str, object],
    *,
    execution: Mapping[str, object] | None = None,
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    """Load the confirmed Task Session and Turn matching the reviewed request."""
    chat = resolve_session_store(host.runtime_controller, host.chat_store)
    if chat is None:
        raise ValueError("Chat Runtime Controller is unavailable")
    active_execution = execution if execution is not None else steps.get("execution_started")
    execution_map = active_execution if isinstance(active_execution, Mapping) else None
    opened = steps.get("task_session_opened")
    checkpoint = execution_map if execution_map is not None else (opened if isinstance(opened, Mapping) else None)
    session, turn = read_task_start(
        chat,
        goal_id=str(parameters["goal_id"]),
        agent_id=str(parameters.get("endpoint_id") or parameters["agent_id"]),
        channel_id=f"task.{todo_id}",
        client_turn_id=f"task-start-{proposal_id}",
        checkpoint=checkpoint,
    )
    if execution_map is not None and (turn is None or turn.get("turn_id") != execution_map.get("turn_id")):
        raise ValueError("The original Task Turn is unavailable")
    if turn is not None and turn.get("message") != str(parameters["text"]):
        raise ValueError("The original Task Turn request changed")
    return session, turn


def pending_applied_task_receipt(
    host: ChatTodoActionHost,
    proposal: Mapping[str, object],
) -> dict[str, object] | None:
    """Find the exact accepted Turn whose worker has yet to start."""
    raw_params = proposal.get("normalized_parameters")
    parameters = raw_params if isinstance(raw_params, Mapping) else {}
    raw_receipt = proposal.get("receipt")
    receipt = raw_receipt if isinstance(raw_receipt, Mapping) else {}
    raw_context = proposal.get("context")
    context = raw_context if isinstance(raw_context, Mapping) else {}
    if (
        proposal.get("status") != "applied"
        or proposal.get("action_kind") != "todo.create"
        or parameters.get("start_execution") is not True
        or proposal.get("permission_classification") != "durable_write"
        or context.get("binding_id")
        or receipt.get("outcome") != "task_execution_started"
        or receipt.get("projection_verified") is not True
    ):
        return None
    resources = receipt.get("resource_ids")
    canonical = receipt.get("canonical_receipt")
    if not is_str_dict(resources) or not is_str_dict(canonical):
        return None
    goal_id = parameters.get("goal_id")
    todo_id = resources.get("todo_id")
    sid = resources.get("session_id")
    tid = resources.get("turn_id")
    if (
        not isinstance(goal_id, str) or not goal_id
        or not isinstance(todo_id, str) or not todo_id
        or not isinstance(sid, str) or not sid
        or not isinstance(tid, str) or not tid
        or resources.get("goal_id") != goal_id
        or canonical.get("goal_id") != goal_id
        or canonical.get("todo_id") != todo_id
        or canonical.get("status") != "applied"
        or canonical.get("preview_id") != proposal.get("expected_state_fingerprint")
    ):
        return None
    chat = resolve_turn_loader(host.runtime_controller, host.chat_store)
    if chat is None:
        return None
    try:
        turn = chat.load_turn(sid, tid)
    except (OSError, ValueError):
        return None
    if (
        turn
        and turn.get("session_id") == sid
        and turn.get("turn_id") == tid
        and turn.get("client_turn_id") == f"task-start-{proposal['proposal_id']}"
        and turn.get("status") == "queued"
        and not (turn.get("started_at") or turn.get("upstream_turn_id"))
    ):
        return resources
    return None


def project_task_dispatch(
    host: ChatTodoActionHost,
    proposal: Mapping[str, object],
) -> dict[str, object]:
    """Annotate an applied task proposal when its queued Turn awaits worker dispatch."""
    base = dict(proposal)
    if pending_applied_task_receipt(host, proposal) is not None:
        return {**base, "task_execution_dispatch_pending": True}
    return base


def apply_created_todo_assignment(
    host: ChatTodoActionHost,
    proposal_id: str,
    *,
    goal_id: str,
    todo_id: str,
    binding: Mapping[str, object],
    steps: Mapping[str, object],
    apply_mutation: Callable[[ChatTodoSource, str], dict[str, object]],
) -> dict[str, object]:
    """Assign or claim a newly created Todo with CAS basis recovery."""
    binding_dict = dict(binding)

    def observe() -> tuple[ChatTodoSource, dict[str, object]]:
        source = resolve_chat_todo_source(host, goal_id)
        source.require_review(binding_dict)
        authority = read_canonical_todos_if_promoted(runtime_root=source.runtime_root, goal_id=goal_id)
        source.require_created_todo(binding_dict, authority)
        return source, authority

    source, _ = observe()
    raw_basis = steps.get("todo_assignment_basis", binding_dict)
    basis_dict = require_str_dict(raw_basis, error="Todo assignment basis is malformed")
    revision = str(basis_dict["provider_revision"])
    try:
        return apply_mutation(source, revision)
    except LocalCoordinationAuthorityUnavailable as error:
        if error.code != "provider_revision_mismatch":
            raise

    # Only a definitive stale-revision rejection permits a new CAS basis.
    # Preserve it before dispatch so a lost response replays the same intent.
    source, authority = observe()
    current_revision = source.current_assignment_revision(binding_dict, authority, todo_id)
    host.store.save_checkpoint(
        proposal_id,
        step="todo_assignment_basis",
        receipt={"provider_revision": current_revision},
    )
    return apply_mutation(source, current_revision)


@contextmanager
def confirmed_task_commit(
    host: ChatTodoActionHost,
    *,
    goal_id: str,
    todo_id: str,
    endpoint: str,
    project: Path,
    claim: Mapping[str, object],
    source: ChatTodoSource,
    binding: Mapping[str, object],
) -> Iterator[None]:
    """Fence acceptance with the same registry and canonical writers as Stop and Todo edits."""
    invoked = host.registry_path.resolve()
    runtime = source.runtime_root
    targets: tuple[Path, ...] = (shadow_maintenance_lock_target(runtime, goal_id), source.registry_path)
    if source.registry_path != invoked:
        targets += (invoked,)
    binding_dict = dict(binding)
    with ExitStack() as locks:
        for target in targets:
            locks.enter_context(exclusive_cross_runtime_file_lock(target, operation="confirm_task_admission"))
        current_source = resolve_chat_todo_source(host, goal_id)
        current_source.require_review(binding_dict)
        goal = current_source.goal
        if goal.get("status", "active") not in {"active", "ready"} or goal_is_stopped(goal):
            raise ValueError("The Task's Goal is no longer active")
        if Path(str(goal.get("repo") or ".")).expanduser().resolve() != project:
            raise ValueError("The reviewed Task's project root changed")
        owner = claim["claimed_by"]
        if host._resolve_goal_agent(goal_id, endpoint, goal=goal) != owner:
            raise ValueError("The reviewed Agent endpoint now belongs to another Goal Agent")
        authority = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal_id, include_leases=True)
        current_source.require_created_todo(binding_dict, authority)
        todo = next((row for row in as_dict_list(authority.get("todos")) if row.get("todo_id") == todo_id), None)
        if (
            todo is None or todo.get("role") != "agent" or todo.get("status") != "open"
            or todo.get("claimed_by") != owner or authority.get("handoff_mode") != claim.get("handoff_mode")
        ):
            raise ValueError("The reviewed Task assignment changed before launch")
        if authority.get("handoff_mode") == "hard_lease":
            lease = next((row for row in as_dict_list(authority.get("leases")) if row.get("todo_id") == todo_id), None)
            original = claim.get("lease")
            if (
                not lease or not is_str_dict(original) or not lease_is_active(lease)
                or any(lease.get(key) != original.get(key) for key in ("owner", "idempotency_key", "lease_epoch"))
            ):
                raise ValueError("The reviewed Task execution lease changed before launch")
        yield


def _claim_task_for_dispatch(
    host: ChatTodoActionHost,
    proposal_id: str,
    *,
    goal_id: str,
    todo_id: str,
    owner: str,
    source: ChatTodoSource,
    binding: Mapping[str, object],
    steps: Mapping[str, object],
    applied_execution: Mapping[str, object] | None,
) -> dict[str, object]:
    def apply_claim(current_source: ChatTodoSource, revision: str) -> dict[str, object]:
        return update_goal_todo(
            registry_path=current_source.registry_path,
            runtime_root_arg=str(current_source.runtime_root),
            goal_id=goal_id,
            todo_id=todo_id,
            claimed_by=owner,
            agent_id=owner,
            claim_only=True,
            manage_execution=True,
            claim_operation_id=task_start_claim_operation_id(proposal_id),
            dry_run=False,
            claim_expected_provider_revision=revision,
            claim_expected_registry_sha256=current_source.registry_sha256,
            routing_registry_witness=current_source.routing_registry_witness,
        )

    if applied_execution is None:
        claim = apply_created_todo_assignment(
            host, proposal_id, goal_id=goal_id, todo_id=todo_id,
            binding=binding, steps=steps, apply_mutation=apply_claim,
        )
    else:
        # Replay the accepted claim using the same intent. A stale
        # basis cannot authorize another claim on an applied card.
        basis_dict = require_str_dict(steps.get("todo_assignment_basis", binding), error="Todo assignment basis is malformed")
        claim = apply_claim(source, str(basis_dict["provider_revision"]))
    if claim.get("handoff_mode") == "soft_claim" and claim.get("status") in {"recovered", "replayed"}:
        readback = update_goal_todo(
            registry_path=source.registry_path,
            runtime_root_arg=str(source.runtime_root),
            goal_id=goal_id,
            todo_id=todo_id,
            claimed_by=owner,
            agent_id=owner,
            claim_only=True,
            manage_execution=True,
            dry_run=True,
        )
        if readback.get("changed"):
            raise ValueError("The reviewed Task assignment changed before launch")
    if applied_execution is None and not is_str_dict(steps.get("todo_assigned")):
        host.store.save_checkpoint(
            proposal_id, step="todo_assigned", receipt={"todo_id": todo_id, "agent_id": owner},
        )
    return claim


def _dispatch_confirmed_task_turn(
    host: ChatTodoActionHost,
    proposal_id: str,
    parameters: Mapping[str, object],
    todo_id: str,
    steps: Mapping[str, object],
    *,
    source: ChatTodoSource,
    binding: Mapping[str, object],
    session: dict[str, object] | None,
    opened: object,
    execution: Mapping[str, object] | None,
    applied_execution: Mapping[str, object] | None,
) -> tuple[dict[str, object], bool]:
    controller = host.runtime_controller
    if controller is None:
        raise ValueError("Chat Runtime Controller is unavailable")
    if session is not None and session.get("status") == "closed":
        raise ValueError("The original Task Session is closed")
    goal_id = str(parameters["goal_id"])
    owner = str(parameters["agent_id"])
    endpoint = str(parameters.get("endpoint_id") or owner)
    message = str(parameters["text"])
    goal = source.goal
    if goal.get("status", "active") not in {"active", "ready"} or goal_is_stopped(goal):
        raise ValueError("The Task's Goal is no longer active")
    project = Path(str(goal.get("repo") or ".")).expanduser().resolve()
    if not project.is_dir():
        raise ValueError("the Goal project root is unavailable")
    host._agent_eligibility(endpoint, project=project)
    if parameters.get("endpoint_id") and host._resolve_goal_agent(goal_id, endpoint, goal=goal) != owner:
        raise ValueError("The reviewed Agent endpoint now belongs to another Goal Agent")
    claim = _claim_task_for_dispatch(
        host, proposal_id, goal_id=goal_id, todo_id=todo_id, owner=owner,
        source=source, binding=binding, steps=steps, applied_execution=applied_execution,
    )
    active_session = session
    if active_session is None:
        active_session, _ = controller.open_session(
            goal_id=goal_id, agent_id=endpoint, work_dir=project, objective=message,
            mode="resume_latest", channel_id=f"task.{todo_id}", agent_goal_id=goal_id,
        )
    sid = _opaque_id(active_session.get("session_id"), field="session_id")
    if opened is None and execution is None:
        host.store.save_checkpoint(proposal_id, step="task_session_opened", receipt={"session_id": sid})
    if not isinstance(controller, GoalCommitSubmitController):
        raise ValueError("Chat Runtime Controller is unavailable")
    return controller.submit_turn(
        session_id=sid,
        client_turn_id=f"task-start-{proposal_id}",
        message=message,
        work_dir=project,
        objective=message,
        _goal_commit=lambda: confirmed_task_commit(
            host, goal_id=goal_id, todo_id=todo_id, endpoint=endpoint,
            project=project, claim=claim, source=source, binding=binding,
        ),
    )


def start_confirmed_task(
    host: ChatTodoActionHost,
    proposal_id: str,
    parameters: Mapping[str, object],
    todo_id: str,
    steps: Mapping[str, object],
    *,
    source: ChatTodoSource,
    binding: Mapping[str, object],
    applied_execution: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Claim an accepted Todo, open its Session, and submit or replay its execution Turn."""
    opened = steps.get("task_session_opened")
    raw_exec = applied_execution if applied_execution is not None else steps.get("execution_started")
    execution = raw_exec if isinstance(raw_exec, Mapping) else None
    session, turn = read_confirmed_task(host, proposal_id, parameters, todo_id, steps, execution=execution)

    # An already dispatched or terminal Turn is readback. Only work that
    # still needs dispatch passes through the current Goal and claim gates.
    if turn is not None and task_turn_dispatched(turn):
        active_turn, created = turn, False
    else:
        active_turn, created = _dispatch_confirmed_task_turn(
            host, proposal_id, parameters, todo_id, steps, source=source, binding=binding,
            session=session, opened=opened, execution=execution, applied_execution=applied_execution,
        )

    identity: dict[str, object] = {
        "session_id": _opaque_id(active_turn.get("session_id"), field="session_id"),
        "turn_id": _opaque_id(active_turn.get("turn_id"), field="turn_id"),
    }
    if execution is None:
        host.store.save_checkpoint(proposal_id, step="execution_started", receipt=identity)
    return {**identity, "status": str(active_turn.get("status") or "accepted"), "created": created}


def recover_applied_task(
    host: ChatTodoActionHost,
    proposal: Mapping[str, object],
) -> dict[str, object] | None:
    """Resume execution dispatch for an already applied task proposal."""
    execution = pending_applied_task_receipt(host, proposal)
    if execution is None:
        return host._turn_from_receipt(proposal.get("receipt"))

    parameters = require_str_dict(proposal.get("normalized_parameters"), error="typed Chat action proposal is malformed")
    goal_id = require_str(parameters.get("goal_id"), error="goal_id is missing from applied task")
    todo_id = require_str(execution.get("todo_id"), error="todo_id is missing from execution receipt")
    agent_id = require_str(parameters.get("agent_id"), error="agent_id is missing from applied task")
    source = resolve_chat_todo_source(host, goal_id)
    steps = todo_checkpoints(proposal)
    raw_created = steps.get("todo_created", {})
    raw_receipt = require_str_dict(proposal.get("receipt"), error="Todo execution receipt is malformed")
    canonical = require_str_dict(
        raw_receipt.get("canonical_receipt"),
        error="The accepted Task's creation checkpoint changed",
    )
    if not is_str_dict(raw_created) or (
        raw_created and (raw_created.get("todo_id") != todo_id or raw_created.get("canonical_receipt") != canonical)
    ):
        raise ValueError("The accepted Task's creation checkpoint changed")
    authority = read_canonical_todos_if_promoted(runtime_root=source.runtime_root, goal_id=goal_id)
    if "source" in raw_created:
        raw_binding = raw_created["source"]
        if not is_str_dict(raw_binding):
            raise ValueError("Todo creation checkpoint source is malformed")
        binding = raw_binding
    else:
        binding = source.recover_legacy_creation(
            todo_id=todo_id,
            preview_id=str(proposal.get("expected_state_fingerprint") or ""),
            receipt=canonical,
            authority=authority,
            assignment_pending=False,
        )
    source.require_created_todo(binding, authority)
    if not source.has_managed_task_claim(
        str(proposal["proposal_id"]), todo_id, agent_id, str(authority["source_authority"]),
    ):
        raise ChatTodoSourceChanged("The saved Task start claim is unavailable.")
    return start_confirmed_task(
        host, str(proposal["proposal_id"]), parameters, todo_id, steps,
        source=source, binding=binding, applied_execution=execution,
    )


__all__ = [
    "apply_created_todo_assignment",
    "confirmed_task_commit",
    "pending_applied_task_receipt",
    "project_task_dispatch",
    "read_confirmed_task",
    "read_task_start",
    "recover_applied_task",
    "start_confirmed_task",
]
