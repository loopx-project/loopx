"""Typed Chat actions for canonical Todo lifecycle transitions."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from .todo_action_contracts import (
    ChatTodoActionHost,
    TodoActionServiceSelf,
    TodoSessionStoreProtocol,
    decision_readback,
    reviewed_terminal_options,
    reviewed_update_options,
    task_turn_dispatched,
    todo_checkpoints,
)
from .todo_action_create import apply_todo_create
from .todo_action_tasks import (
    apply_created_todo_assignment,
    confirmed_task_commit,
    pending_applied_task_receipt,
    project_task_dispatch as project_task_dispatch_record,
    read_confirmed_task,
    read_task_start,
    recover_applied_task,
    start_confirmed_task,
)
from .todo_action_updates import (
    apply_reviewed_todo_edit,
    apply_todo_update,
    canonical_update_basis,
    run_gate_resolve,
    run_todo_update,
)
from .todo_source import ChatTodoSource


def _decision_readback(result: Mapping[str, object]) -> dict[str, object]:
    """Carry the canonical outcome and dependent effect, never a local guess."""
    return decision_readback(result)


class ChatTodoActionMixin:
    """Keep Todo preview/apply parity separate from general orchestration."""

    @staticmethod
    def _todo_checkpoints(proposal: Mapping[str, object]) -> dict[str, object]:
        return todo_checkpoints(proposal)

    def _apply_todo_create(
        self: ChatTodoActionHost,
        proposal_id: str,
        proposal: Mapping[str, object],
        parameters: Mapping[str, object],
    ) -> dict[str, object]:
        return apply_todo_create(self, proposal_id, proposal, parameters)

    def _apply_created_todo_assignment(
        self: ChatTodoActionHost,
        proposal_id: str,
        *,
        goal_id: str,
        todo_id: str,
        binding: Mapping[str, object],
        steps: Mapping[str, object],
        apply: Callable[[ChatTodoSource, str], dict[str, object]],
    ) -> dict[str, object]:
        return apply_created_todo_assignment(
            self,
            proposal_id,
            goal_id=goal_id,
            todo_id=todo_id,
            binding=binding,
            steps=steps,
            apply_mutation=apply,
        )

    @contextmanager
    def _confirmed_task_commit(
        self: ChatTodoActionHost,
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
        with confirmed_task_commit(
            self,
            goal_id=goal_id,
            todo_id=todo_id,
            endpoint=endpoint,
            project=project,
            claim=claim,
            source=source,
            binding=binding,
        ):
            yield

    @staticmethod
    def _read_task_start(
        chat: TodoSessionStoreProtocol,
        *,
        goal_id: str,
        agent_id: str,
        channel_id: str,
        client_turn_id: str,
        checkpoint: Mapping[str, object] | None,
    ) -> tuple[dict[str, object] | None, dict[str, object] | None]:
        return read_task_start(
            chat,
            goal_id=goal_id,
            agent_id=agent_id,
            channel_id=channel_id,
            client_turn_id=client_turn_id,
            checkpoint=checkpoint,
        )

    def _pending_applied_task_receipt(
        self: ChatTodoActionHost,
        proposal: Mapping[str, object],
    ) -> dict[str, object] | None:
        """Find the exact accepted Turn whose worker has yet to start."""
        return pending_applied_task_receipt(self, proposal)

    def project_task_dispatch(
        self: ChatTodoActionHost,
        proposal: Mapping[str, object],
    ) -> dict[str, object]:
        return project_task_dispatch_record(self, proposal)

    def _recover_applied_task(
        self: ChatTodoActionHost,
        proposal: Mapping[str, object],
    ) -> dict[str, object] | None:
        return recover_applied_task(self, proposal)

    def _read_confirmed_task(
        self: ChatTodoActionHost,
        proposal_id: str,
        parameters: Mapping[str, object],
        todo_id: str,
        steps: Mapping[str, object],
        *,
        execution: Mapping[str, object] | None = None,
    ) -> tuple[dict[str, object] | None, dict[str, object] | None]:
        return read_confirmed_task(self, proposal_id, parameters, todo_id, steps, execution=execution)

    @staticmethod
    def _task_turn_dispatched(turn: Mapping[str, object]) -> bool:
        return task_turn_dispatched(turn)

    def _start_confirmed_task(
        self: ChatTodoActionHost,
        proposal_id: str,
        parameters: Mapping[str, object],
        todo_id: str,
        steps: Mapping[str, object],
        *,
        source: ChatTodoSource,
        binding: Mapping[str, object],
        applied_execution: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        return start_confirmed_task(
            self,
            proposal_id,
            parameters,
            todo_id,
            steps,
            source=source,
            binding=binding,
            applied_execution=applied_execution,
        )

    def _run_todo_update(
        self: ChatTodoActionHost,
        parameters: dict[str, object],
        *,
        dry_run: bool,
        basis: dict[str, object] | None = None,
        operation_id: str | None = None,
    ) -> dict[str, object]:
        return run_todo_update(self, parameters, dry_run=dry_run, basis=basis, operation_id=operation_id)

    def _run_gate_resolve(
        self: ChatTodoActionHost,
        parameters: dict[str, object],
        *,
        dry_run: bool,
        basis: dict[str, object] | None = None,
        operation_id: str | None = None,
    ) -> dict[str, object]:
        return run_gate_resolve(self, parameters, dry_run=dry_run, basis=basis, operation_id=operation_id)

    @staticmethod
    def _reviewed_terminal_options(
        basis: Mapping[str, object] | None,
        operation_id: str | None,
    ) -> dict[str, object]:
        return reviewed_terminal_options(basis, operation_id)

    @staticmethod
    def _reviewed_update_options(
        basis: Mapping[str, object] | None,
        operation_id: str | None,
    ) -> dict[str, object]:
        return reviewed_update_options(basis, operation_id)

    def _canonical_update_basis(
        self: ChatTodoActionHost,
        goal_id: str,
        *,
        completion_todo_id: str | None = None,
        decision: bool = False,
    ) -> dict[str, object] | None:
        return canonical_update_basis(
            self,
            goal_id,
            completion_todo_id=completion_todo_id,
            decision=decision,
        )

    def _apply_reviewed_todo_edit(
        self: TodoActionServiceSelf,
        proposal_id: str,
        proposal: Mapping[str, object],
        parameters: dict[str, object],
    ) -> dict[str, object]:
        """Recover the canonical operation before testing present-day freshness."""
        return apply_reviewed_todo_edit(self, proposal_id, proposal, parameters)

    def _apply_todo_update(
        self: TodoActionServiceSelf,
        proposal_id: str,
        proposal: Mapping[str, object],
        parameters: dict[str, object],
    ) -> dict[str, object]:
        return apply_todo_update(self, proposal_id, proposal, parameters)
