"""Typed contracts and pure boundary helpers for Chat Todo/Task actions."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import NoReturn, Protocol, runtime_checkable

from ..chat_store import TERMINAL_TURN_STATES
from ..control_plane.collaboration.action_store_common import is_str_dict, require_str_dict

CANONICAL_UPDATE_BASIS_SCHEMA_VERSION = "loopx_chat_canonical_update_basis_v0"
CANONICAL_TERMINAL_BASIS_SCHEMA_VERSION = "loopx_chat_canonical_terminal_basis_v0"

MONITOR_OUTCOME_BY_OPERATION: dict[str, str] = {
    "pause": "monitor_paused",
    "resume": "monitor_resumed",
    "edit": "monitor_updated",
    "stop": "monitor_stopped",
}


class ActionStoreProtocol(Protocol):
    """Minimal persistence contract implemented by ChatActionStore."""

    def apply(
        self,
        proposal_id: str,
        *,
        current_state_fingerprint: str,
        receipt: Mapping[str, object],
    ) -> dict[str, object]: ...

    def save_checkpoint(
        self,
        proposal_id: str,
        *,
        step: str,
        receipt: Mapping[str, object],
    ) -> dict[str, object]: ...

    def mark_failed(
        self,
        proposal_id: str,
        *,
        error_code: str,
        message: str,
        details: Mapping[str, object] | None = None,
    ) -> dict[str, object]: ...


@runtime_checkable
class TodoTurnLoaderProtocol(Protocol):
    """Read-only turn lookup interface for pending task execution checks."""

    def load_turn(self, session_id: str, turn_id: str) -> dict[str, object] | None: ...


@runtime_checkable
class TodoSessionStoreProtocol(TodoTurnLoaderProtocol, Protocol):
    """Session and client-turn lookup interface for task recovery."""

    def load_session(self, session_id: str) -> dict[str, object] | None: ...

    def turn_for_client(self, session_id: str, client_turn_id: str) -> dict[str, object] | None: ...

    def session_candidates(
        self,
        *,
        goal_id: str | None,
        agent_id: str,
        channel_id: str | None = None,
    ) -> Sequence[Mapping[str, object]]: ...


class TodoRuntimeControllerProtocol(Protocol):
    """Runtime controller interface matching ChatActionService's host contract."""

    def capabilities(self) -> Sequence[Mapping[str, object]]: ...

    def open_session(
        self,
        *,
        goal_id: str,
        goal_instance_id: str | None = None,
        agent_id: str,
        work_dir: Path,
        objective: str,
        mode: str,
        session_id: str | None = None,
        channel_id: str,
        agent_goal_id: str,
        managed_agent_id: str = ...,
    ) -> tuple[dict[str, object], bool]: ...

    def submit_turn(
        self,
        *,
        session_id: str,
        client_turn_id: str,
        message: str,
        work_dir: Path,
        objective: str,
        display_message: str = ...,
    ) -> tuple[dict[str, object], bool]: ...

    def resume_session_queue(self, *, session_id: str, work_dir: Path, objective: str) -> object: ...


@runtime_checkable
class GoalCommitSubmitController(Protocol):
    """Extended runtime controller contract supporting transactional _goal_commit."""

    def submit_turn(
        self,
        *,
        session_id: str,
        client_turn_id: str,
        message: str,
        work_dir: Path,
        objective: str,
        _goal_commit: Callable[[], AbstractContextManager[None]] | None = ...,
    ) -> tuple[dict[str, object], bool]: ...


class TodoUpdateRunner(Protocol):
    """Signature shared by todo, monitor, and gate mutation runners."""

    def __call__(
        self,
        parameters: dict[str, object],
        *,
        dry_run: bool,
        basis: dict[str, object] | None = None,
        operation_id: str | None = None,
    ) -> dict[str, object]: ...


class ChatTodoActionHost(Protocol):
    """Host capabilities required by ChatTodoActionMixin."""

    @property
    def store(self) -> ActionStoreProtocol: ...

    @property
    def registry_path(self) -> Path: ...

    @property
    def chat_store(self) -> TodoTurnLoaderProtocol | None: ...

    @property
    def runtime_controller(self) -> TodoRuntimeControllerProtocol | None: ...

    @property
    def runtime_root_override(self) -> str | None: ...

    def _goal_state_fingerprint(self, goal_id: str) -> str: ...

    def _agent_eligibility(
        self,
        agent_id: str,
        *,
        project: Path | None = None,
        requires_tools: bool = True,
        require_healthy: bool = True,
    ) -> dict[str, object]: ...

    def _resolve_goal_agent(
        self,
        goal_id: str,
        endpoint_id: str,
        *,
        goal: dict[str, object] | None = None,
    ) -> str: ...

    def _turn_from_receipt(self, receipt: object) -> dict[str, object] | None: ...

    def _run_monitor_update(
        self,
        parameters: dict[str, object],
        *,
        dry_run: bool,
        basis: dict[str, object] | None = None,
        operation_id: str | None = None,
    ) -> dict[str, object]: ...


class TodoActionServiceSelf(ChatTodoActionHost, Protocol):
    """Combined host and Todo mixin surface for internal method delegation."""

    def _run_todo_update(
        self,
        parameters: dict[str, object],
        *,
        dry_run: bool,
        basis: dict[str, object] | None = None,
        operation_id: str | None = None,
    ) -> dict[str, object]: ...

    def _run_gate_resolve(
        self,
        parameters: dict[str, object],
        *,
        dry_run: bool,
        basis: dict[str, object] | None = None,
        operation_id: str | None = None,
    ) -> dict[str, object]: ...

    def _apply_reviewed_todo_edit(
        self,
        proposal_id: str,
        proposal: Mapping[str, object],
        parameters: dict[str, object],
    ) -> dict[str, object]: ...


def raise_protected_action_gate(
    action_kind: str,
    *,
    gate: Mapping[str, object],
    cause: Exception | None = None,
) -> NoReturn:
    """Raise ProtectedActionGate via lazy import to avoid module cycles."""
    from ..chat_actions import ProtectedActionGate

    error = ProtectedActionGate(action_kind, gate=gate)
    if cause is not None:
        raise error from cause
    raise error


def decision_readback(result: Mapping[str, object]) -> dict[str, object]:
    """Carry the canonical outcome and dependent effect, never a local guess."""
    resume = result.get("unblock_resume")
    return {
        "decision_outcome": result.get("decision_outcome"),
        "unblock_resume_state": resume.get("state") if isinstance(resume, Mapping) else None,
    }


def todo_checkpoints(proposal: Mapping[str, object]) -> dict[str, object]:
    """Extract persisted step checkpoints from a proposal payload."""
    checkpoint = proposal.get("checkpoint")
    steps = checkpoint.get("steps") if is_str_dict(checkpoint) else None
    return steps if is_str_dict(steps) else {}


def parse_reviewed_terminal_options(
    basis: Mapping[str, object] | None,
    operation_id: str | None,
) -> tuple[str | None, dict[str, object] | None]:
    """Validate and unpack reviewed terminal completion options."""
    if basis is None:
        return None, None
    if basis.get("schema_version") != CANONICAL_TERMINAL_BASIS_SCHEMA_VERSION:
        raise ValueError("unsupported canonical terminal review basis; regenerate preview")
    return operation_id, {
        "provider_revision": basis["provider_revision"],
        "registry_sha256": basis["registry_sha256"],
    }


def reviewed_terminal_options(
    basis: Mapping[str, object] | None,
    operation_id: str | None,
) -> dict[str, object]:
    """Return kwargs mapping for complete_goal_todo calls."""
    completion_turn_key, terminal_review_basis = parse_reviewed_terminal_options(basis, operation_id)
    if terminal_review_basis is None:
        return {}
    return {
        "completion_turn_key": completion_turn_key,
        "terminal_review_basis": terminal_review_basis,
    }


def parse_reviewed_update_options(
    basis: Mapping[str, object] | None,
    operation_id: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Validate and unpack reviewed non-terminal update CAS options."""
    if basis is None:
        return None, None, None
    if basis.get("schema_version") != CANONICAL_UPDATE_BASIS_SCHEMA_VERSION:
        raise ValueError("unsupported canonical update review basis; regenerate preview")
    return (
        operation_id,
        str(basis["provider_revision"]),
        str(basis["registry_sha256"]),
    )


def reviewed_update_options(
    basis: Mapping[str, object] | None,
    operation_id: str | None,
) -> dict[str, object]:
    """Return kwargs mapping for update_goal_todo calls."""
    update_op, provider_revision, registry_sha256 = parse_reviewed_update_options(basis, operation_id)
    if provider_revision is None:
        return {}
    return {
        "update_operation_id": update_op,
        "update_expected_provider_revision": provider_revision,
        "update_expected_registry_sha256": registry_sha256,
    }


def task_turn_dispatched(turn: Mapping[str, object]) -> bool:
    """Return whether a task Turn has already been dispatched or terminated."""
    return turn.get("status") in TERMINAL_TURN_STATES or bool(
        turn.get("started_at") or turn.get("upstream_turn_id")
    )


def as_optional_str(value: object) -> str | None:
    """Preserve None while normalizing non-None scalar inputs to str."""
    return None if value is None else str(value)


def as_optional_int(value: object, *, field: str) -> int | None:
    """Validate optional integer parameter without accepting bool."""
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValueError(f"{field} must be an integer")


def as_optional_str_list(value: object, *, field: str) -> list[str] | None:
    """Normalize optional string sequence parameter to a concrete list."""
    if value is None:
        return None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(item) for item in value]
    raise ValueError(f"{field} must be a sequence of strings")


def as_dict_list(value: object) -> list[dict[str, object]]:
    """Filter a sequence payload into typed string-keyed row dictionaries."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [row for row in value if is_str_dict(row)]


def require_str(value: object, *, error: str) -> str:
    """Require a non-empty string value or raise ValueError."""
    if not isinstance(value, str) or not value:
        raise ValueError(error)
    return value


def resolve_turn_loader(
    controller: object,
    chat_store: TodoTurnLoaderProtocol | None,
) -> TodoTurnLoaderProtocol | None:
    """Resolve the active turn loader from controller.store or chat_store."""
    raw_store = getattr(controller, "store", None)
    candidate = raw_store if raw_store is not None else chat_store
    return candidate if isinstance(candidate, TodoTurnLoaderProtocol) else None


def resolve_session_store(
    controller: object,
    chat_store: TodoTurnLoaderProtocol | None,
) -> TodoSessionStoreProtocol | None:
    """Resolve the full session store from controller.store or chat_store."""
    raw_store = getattr(controller, "store", None)
    candidate = raw_store if raw_store is not None else chat_store
    return candidate if isinstance(candidate, TodoSessionStoreProtocol) else None


__all__ = [
    "ActionStoreProtocol",
    "CANONICAL_TERMINAL_BASIS_SCHEMA_VERSION",
    "CANONICAL_UPDATE_BASIS_SCHEMA_VERSION",
    "ChatTodoActionHost",
    "GoalCommitSubmitController",
    "MONITOR_OUTCOME_BY_OPERATION",
    "TodoActionServiceSelf",
    "TodoRuntimeControllerProtocol",
    "TodoSessionStoreProtocol",
    "TodoTurnLoaderProtocol",
    "TodoUpdateRunner",
    "as_dict_list",
    "as_optional_int",
    "as_optional_str",
    "as_optional_str_list",
    "decision_readback",
    "is_str_dict",
    "parse_reviewed_terminal_options",
    "parse_reviewed_update_options",
    "raise_protected_action_gate",
    "require_str",
    "require_str_dict",
    "resolve_session_store",
    "resolve_turn_loader",
    "reviewed_terminal_options",
    "reviewed_update_options",
    "task_turn_dispatched",
    "todo_checkpoints",
]
