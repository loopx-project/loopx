from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, SupportsInt, TypedDict, cast

if TYPE_CHECKING:
    from .effect_program import ReceiptBoundReplayPhase
    from .settlement import QuotaSettlementReadback
    from ..scheduler.arbitration import SchedulerArbitration
    from ..scheduler.execution_context import SchedulerExecutionContextResolution
    from ..work_items.interaction_contract import InteractionContractPacket


JsonMap = dict[str, object]
JsonMapView = Mapping[str, object]
HostObservationResolver = Callable[..., Mapping[str, object]]
BoundedResearchFrontierProjector = Callable[..., Mapping[str, object] | None]
ProgressReviewContextReader = Callable[[dict[str, object], Path], dict[str, object] | None]
OperatorInboxUrgencyProjector = Callable[..., dict[str, object]]
SchedulerStateObserver = Callable[[Mapping[str, object]], None]


class ReceiptBoundGuardKwargs(TypedDict):
    receipt_bound_replan_guard_scoped: bool
    receipt_bound_replan_execution_pending: bool
    receipt_bound_guard_recorded_at: str | None
    receipt_bound_guard_turn_instance_id: str | None
    receipt_bound_replan_review: Mapping[str, object] | None
    receipt_bound_successor_replan: bool


class BuildInteractionContractFn(Protocol):
    def __call__(
        self,
        payload: dict[str, object],
        *,
        available_capabilities: object = None,
        scheduler_execution_context: Mapping[str, object]
        | SchedulerExecutionContextResolution
        | None = None,
        turn_instance_id: str | None = None,
        runtime_root: str | None = None,
        registry_path: str | None = None,
    ) -> InteractionContractPacket: ...


class AttachSettlementProgressFn(Protocol):
    def __call__(
        self,
        payload: dict[str, object],
        readback: QuotaSettlementReadback,
        *,
        registry_path: Path | None = None,
        runtime_root: Path | None = None,
        goal_ref: Mapping[str, object] | None = None,
    ) -> None: ...


class ReadHeartbeatSettlementFn(Protocol):
    def __call__(
        self,
        runtime_root: Path,
        *,
        goal_id: str,
        agent_id: str | None,
        todo_id: str | None,
        turn_instance_id: str | None,
        replan_obligation_id: str | None = None,
        registry_path: Path | None = None,
        goal_ref: Mapping[str, object] | None = None,
        tolerate_malformed_history: bool = False,
    ) -> QuotaSettlementReadback | None: ...


class ApplyWaitRecoveryFn(Protocol):
    def __call__(
        self,
        payload: dict[str, object],
        *,
        registry_path: Path,
        runtime_root: Path,
        goal_id: str,
        agent_id: str,
        todo_id: str,
        turn_instance_id: str,
        available_capabilities: list[str] | None,
        scheduler_execution_context: Mapping[str, object]
        | SchedulerExecutionContextResolution
        | None,
        monitor_phase: str | None = None,
        settlement_progress: dict[str, object] | None = None,
        replay_phase: ReceiptBoundReplayPhase | None = None,
        heartbeat_receipt: Mapping[str, object] | None = None,
    ) -> bool: ...


class ApplyUnsettledRecoveryFn(Protocol):
    def __call__(
        self,
        payload: dict[str, object],
        *,
        registry_path: Path,
        runtime_root: Path,
        goal_id: str,
        agent_id: str | None,
        current_turn_instance_id: str | None,
        available_capabilities: list[str] | None,
        project_scheduler_hint: Callable[..., dict[str, object]],
        scheduler_execution_context: Mapping[str, object]
        | SchedulerExecutionContextResolution
        | None,
        goal_ref: Mapping[str, object] | None = None,
        owner_action_id: str | None = None,
        inspect_current_execution_loss: bool = False,
    ) -> bool: ...


class BuildSchedulerHintFn(Protocol):
    def __call__(
        self,
        payload: dict[str, object],
        *,
        user_action_required: bool = False,
        arbitration: SchedulerArbitration | None = None,
        include_detail: bool = False,
        codex_app_scheduler_state: dict[str, object] | None = None,
        available_capabilities: object = None,
        codex_app_current_rrule: object = None,
        codex_app_automation_id: object = None,
        scheduler_execution_context: Mapping[str, object]
        | SchedulerExecutionContextResolution
        | None = None,
    ) -> dict[str, object]: ...


class LoadAppSchedulerStateFn(Protocol):
    def __call__(
        self,
        runtime_root: Path,
        *,
        goal_id: str,
        agent_id: str | None,
        surface: str,
    ) -> dict[str, object] | None: ...


class ReconcileRetainedSelectionFn(Protocol):
    def __call__(
        self,
        *,
        retained_todo_id: str,
        projected_todo_id: str | None,
        effective_action: str,
        replan_obligation_id: str | None,
    ) -> Mapping[str, object]: ...


def as_json_map(value: object) -> dict[str, object] | None:
    """Narrow a dynamic object to its exact mutable dict without copying."""

    return cast(dict[str, object], value) if isinstance(value, dict) else None


def optional_json_map(value: Mapping[str, object] | None) -> dict[str, object] | None:
    """Return a dict view when given a mapping, keeping existing dict identity."""

    if value is None:
        return None
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return dict(value)


def mapping_or_empty(value: object) -> Mapping[str, object]:
    """Return a read-only Mapping view for a mapping candidate, else empty."""

    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def require_mapping(value: object, *, label: str = "mapping") -> Mapping[str, object]:
    """Return a validated Mapping view or fail closed with TypeError on bad types."""

    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    raise TypeError(f"Expected a mapping for {label}, got {type(value).__name__}")


def as_object_list(value: object) -> list[object] | None:
    """Narrow a dynamic object to its mutable list without copying."""

    return cast(list[object], value) if isinstance(value, list) else None


def non_negative_int(value: object) -> int:
    """Convert an integer-like count matching `max(0, int(value or 0))`."""

    if not value:
        return 0
    if isinstance(value, (int, float, str, bytes, bytearray)):
        return max(0, int(value))
    if isinstance(value, SupportsInt):
        return max(0, int(value))
    raise TypeError(
        f"int() argument must be a string, a bytes-like object or a real number, not {type(value).__name__!r}"
    )


def resolve_root_pair(registry_path: Path, runtime_root: Path) -> tuple[str, str]:
    """Resolve registry and runtime-root paths once per route-binding pass."""

    return (
        str(registry_path.expanduser().resolve()),
        str(runtime_root.expanduser().resolve()),
    )
