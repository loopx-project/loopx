"""Domain-neutral trace and replay-point contracts for Explore Episode V2.

The core owns addressable execution lineage and agent-history cursors.  An
adapter owns every environment-specific state object behind an opaque runtime
binding.  Public records deliberately exclude both agent-state keys and
adapter binding keys.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, Sequence

from .replay_metrics import ReplayMetricLedger
from .trace_runtime import (
    AgentCursor,
    TraceCursor,
    TraceEvent,
    TraceEventKind,
    TraceLog,
    freeze_public_mapping,
    require_nonempty_text as _required_text,
)


class ReplayFidelity(str, Enum):
    EXACT = "exact"
    SEMANTIC_EQUIVALENT = "semantic_equivalent"
    BEST_EFFORT = "best_effort"
    NON_REPLAYABLE = "non_replayable"


class ReplayChildConcurrency(str, Enum):
    """Adapter-declared safety boundary for an isolated child lease."""

    SERIAL_ONLY = "serial_only"
    THREAD_SAFE = "thread_safe"
    PROCESS_ISOLATED = "process_isolated"


class ReplayRiskDisposition(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class ReplayChildFailureStage(str, Enum):
    INTENT = "intent"
    SUFFIX = "suffix"


_FIDELITY_RANK = {
    ReplayFidelity.NON_REPLAYABLE: 0,
    ReplayFidelity.BEST_EFFORT: 1,
    ReplayFidelity.SEMANTIC_EQUIVALENT: 2,
    ReplayFidelity.EXACT: 3,
}


def fidelity_meets(
    actual: ReplayFidelity,
    required: ReplayFidelity,
) -> bool:
    """Return whether ``actual`` is at least as trustworthy as ``required``."""

    return (
        _FIDELITY_RANK[ReplayFidelity(actual)]
        >= _FIDELITY_RANK[ReplayFidelity(required)]
    )


class ReplayPointLifecycle(str, Enum):
    READY = "ready"
    RELEASING = "releasing"
    RELEASED = "released"
    FAILED = "failed"


REPLAYABLE_EPISODE_V2_MODE = "replayable-v2"
_V2_PROTOCOL_METHODS = (
    "capture_replay_state",
    "restore_replay_state",
    "validate_replay_equivalence",
    "release_replay_state",
)
_ISOLATED_CHILD_PROTOCOL_METHODS = (
    "fork_replay_child",
    "validate_replay_child_equivalence",
    "release_replay_child",
)
_CHILD_RISK_PROTOCOL_METHODS = (
    "assess_replay_child_intent",
    "assess_replay_child_outcome",
)


def replayable_episode_v2_mode(adapter: Any) -> str | None:
    """Detect the optional V2 protocol and reject partial implementations."""

    declared = [hasattr(adapter, name) for name in _V2_PROTOCOL_METHODS]
    implemented = [
        callable(getattr(adapter, name, None)) for name in _V2_PROTOCOL_METHODS
    ]
    if any(declared) and not all(implemented):
        missing = [
            name
            for name, available in zip(_V2_PROTOCOL_METHODS, implemented)
            if not available
        ]
        raise ValueError(
            "replayable Episode V2 adapters must implement all protocol methods; "
            f"missing callable(s): {', '.join(missing)}"
        )
    return REPLAYABLE_EPISODE_V2_MODE if all(implemented) else None


def isolated_replay_child_mode(
    adapter: Any,
    agent_state_store: Any,
) -> str | None:
    """Detect the optional two-sided child isolation contract."""

    declared = [hasattr(adapter, name) for name in _ISOLATED_CHILD_PROTOCOL_METHODS]
    implemented = [
        callable(getattr(adapter, name, None))
        for name in _ISOLATED_CHILD_PROTOCOL_METHODS
    ]
    if any(declared) and not all(implemented):
        missing = [
            name
            for name, available in zip(
                _ISOLATED_CHILD_PROTOCOL_METHODS,
                implemented,
            )
            if not available
        ]
        raise ValueError(
            "isolated replay child adapters must implement all protocol "
            f"methods; missing callable(s): {', '.join(missing)}"
        )
    if not all(implemented):
        return None
    if not callable(getattr(agent_state_store, "fork_agent_state", None)):
        return None
    return "isolated-child-v0"


def replay_child_risk_mode(adapter: Any) -> str | None:
    """Detect the optional two-stage child risk interception contract."""

    declared = [hasattr(adapter, name) for name in _CHILD_RISK_PROTOCOL_METHODS]
    implemented = [
        callable(getattr(adapter, name, None)) for name in _CHILD_RISK_PROTOCOL_METHODS
    ]
    if any(declared) and not all(implemented):
        missing = [
            name
            for name, available in zip(
                _CHILD_RISK_PROTOCOL_METHODS,
                implemented,
            )
            if not available
        ]
        raise ValueError(
            "replay child risk adapters must implement both protocol "
            f"methods; missing callable(s): {', '.join(missing)}"
        )
    return "child-risk-v0" if all(implemented) else None


@dataclass(frozen=True)
class CaptureReplayStateRequest:
    replay_point_id: str
    cursor: TraceCursor


@dataclass(frozen=True)
class RestoreReplayStateRequest:
    replay_point_id: str
    target_branch_id: str
    minimum_fidelity: ReplayFidelity


@dataclass(frozen=True)
class ForkReplayChildRequest:
    replay_point_id: str
    child_id: str
    target_branch_id: str
    minimum_fidelity: ReplayFidelity

    def __post_init__(self) -> None:
        for field_name in (
            "replay_point_id",
            "child_id",
            "target_branch_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "minimum_fidelity",
            ReplayFidelity(self.minimum_fidelity),
        )
        if self.minimum_fidelity == ReplayFidelity.NON_REPLAYABLE:
            raise ValueError("isolated replay children require replayable fidelity")


@dataclass(frozen=True)
class AdapterStateLease:
    """Runtime-only Adapter binding; ``binding_key`` is never serialized."""

    binding_key: object | None
    fidelity: ReplayFidelity
    projection_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "fidelity", ReplayFidelity(self.fidelity))
        if self.fidelity == ReplayFidelity.NON_REPLAYABLE:
            if self.binding_key is not None:
                raise ValueError("non_replayable leases must not expose a binding_key")
        elif self.binding_key is None:
            raise ValueError("replayable leases require an opaque binding_key")
        if self.projection_digest is not None:
            object.__setattr__(
                self,
                "projection_digest",
                _required_text(self.projection_digest, "projection_digest"),
            )


@dataclass(frozen=True)
class RestoreReceipt:
    achieved_fidelity: ReplayFidelity
    restored_projection_digest: str | None = None
    prefix_reused: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "achieved_fidelity",
            ReplayFidelity(self.achieved_fidelity),
        )
        if self.restored_projection_digest is not None:
            object.__setattr__(
                self,
                "restored_projection_digest",
                _required_text(
                    self.restored_projection_digest,
                    "restored_projection_digest",
                ),
            )
        if self.prefix_reused is not None and not isinstance(
            self.prefix_reused,
            bool,
        ):
            raise TypeError("prefix_reused must be bool or None")


@dataclass(frozen=True)
class ReplayChildLease:
    """Runtime-only isolated environment child and its restore receipt."""

    child_key: object
    receipt: RestoreReceipt
    concurrency: ReplayChildConcurrency = ReplayChildConcurrency.SERIAL_ONLY
    worker_identity: object | None = None

    def __post_init__(self) -> None:
        if self.child_key is None:
            raise TypeError("ReplayChildLease requires an opaque child_key")
        if not isinstance(self.receipt, RestoreReceipt):
            raise TypeError("ReplayChildLease receipt must be a RestoreReceipt")
        object.__setattr__(
            self,
            "concurrency",
            ReplayChildConcurrency(self.concurrency),
        )
        if (
            self.concurrency == ReplayChildConcurrency.PROCESS_ISOLATED
            and self.worker_identity is None
        ):
            raise TypeError(
                "process-isolated ReplayChildLease requires an opaque worker_identity"
            )


@dataclass(frozen=True)
class ReplayRiskAssessment:
    disposition: ReplayRiskDisposition
    policy_id: str
    sanitized_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "disposition",
            ReplayRiskDisposition(self.disposition),
        )
        object.__setattr__(
            self,
            "policy_id",
            _required_text(self.policy_id, "policy_id"),
        )
        if self.sanitized_reason is not None:
            frozen = freeze_public_mapping(
                {"sanitized_reason": self.sanitized_reason},
                field_path="replay_risk_assessment",
            )
            object.__setattr__(
                self,
                "sanitized_reason",
                frozen["sanitized_reason"],
            )

    def to_record(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition.value,
            "policy_id": self.policy_id,
            "sanitized_reason": self.sanitized_reason,
        }


class ReplayRiskRejectedError(RuntimeError):
    def __init__(self, stage: str, assessment: ReplayRiskAssessment) -> None:
        self.stage = _required_text(stage, "stage")
        self.assessment = assessment
        super().__init__(f"replay child {self.stage} denied by {assessment.policy_id}")


@dataclass(frozen=True)
class EquivalenceCheck:
    check_id: str
    passed: bool
    public_summary: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "check_id", _required_text(self.check_id, "check_id"))
        object.__setattr__(self, "passed", bool(self.passed))
        if self.public_summary is not None:
            frozen = freeze_public_mapping(
                {"public_summary": self.public_summary},
                field_path="equivalence_check",
            )
            object.__setattr__(
                self,
                "public_summary",
                frozen["public_summary"],
            )


@dataclass(frozen=True)
class EquivalenceReport:
    achieved_fidelity: ReplayFidelity
    equivalent: bool
    expected_digest: str | None = None
    observed_digest: str | None = None
    checks: tuple[EquivalenceCheck, ...] = ()
    sanitized_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "achieved_fidelity",
            ReplayFidelity(self.achieved_fidelity),
        )
        object.__setattr__(self, "equivalent", bool(self.equivalent))
        for field_name in ("expected_digest", "observed_digest", "sanitized_reason"):
            value = getattr(self, field_name)
            if value is not None:
                if field_name == "sanitized_reason":
                    frozen = freeze_public_mapping(
                        {field_name: value},
                        field_path="equivalence_report",
                    )
                    object.__setattr__(self, field_name, frozen[field_name])
                else:
                    object.__setattr__(
                        self,
                        field_name,
                        _required_text(value, field_name),
                    )
        normalized_checks = tuple(self.checks)
        if not all(isinstance(check, EquivalenceCheck) for check in normalized_checks):
            raise TypeError("checks must contain EquivalenceCheck values")
        object.__setattr__(self, "checks", normalized_checks)


@dataclass(frozen=True)
class ReplayRestoreResult:
    replay_point_id: str
    target_branch_id: str
    achieved_fidelity: ReplayFidelity
    equivalent: bool
    promotion_eligible: bool
    prefix_reused: bool | None
    report: EquivalenceReport
    restore_minutes: float
    equivalence_minutes: float
    suffix_minutes: float
    replay_wall_minutes: float

    def to_record(self) -> dict[str, Any]:
        return {
            "replay_point_id": self.replay_point_id,
            "target_branch_id": self.target_branch_id,
            "achieved_fidelity": self.achieved_fidelity.value,
            "equivalent": self.equivalent,
            "promotion_eligible": self.promotion_eligible,
            "prefix_reused": self.prefix_reused,
            "expected_digest": self.report.expected_digest,
            "observed_digest": self.report.observed_digest,
            "checks": [
                {
                    "check_id": check.check_id,
                    "passed": check.passed,
                    "public_summary": check.public_summary,
                }
                for check in self.report.checks
            ],
            "sanitized_reason": self.report.sanitized_reason,
            "restore_minutes": self.restore_minutes,
            "equivalence_minutes": self.equivalence_minutes,
            "suffix_minutes": self.suffix_minutes,
            "replay_wall_minutes": self.replay_wall_minutes,
        }


@dataclass(frozen=True)
class ReplayChildExecutionContext:
    child_id: str
    restore: ReplayRestoreResult
    isolated: bool
    adapter_child_key: object | None = field(repr=False, compare=False)


@dataclass(frozen=True)
class ReplayChildTask:
    child_id: str
    target_branch_id: str
    minimum_fidelity: ReplayFidelity
    execute: Callable[[ReplayChildExecutionContext], Any] = field(
        repr=False,
        compare=False,
    )
    intent: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "child_id",
            _required_text(self.child_id, "child_id"),
        )
        object.__setattr__(
            self,
            "target_branch_id",
            _required_text(self.target_branch_id, "target_branch_id"),
        )
        object.__setattr__(
            self,
            "minimum_fidelity",
            ReplayFidelity(self.minimum_fidelity),
        )
        if self.minimum_fidelity == ReplayFidelity.NON_REPLAYABLE:
            raise ValueError("ReplayChildTask requires replayable fidelity")
        if not callable(self.execute):
            raise TypeError("ReplayChildTask execute must be callable")
        object.__setattr__(
            self,
            "intent",
            freeze_public_mapping(self.intent, field_path="replay_child_intent"),
        )


@dataclass(frozen=True)
class ReplayChildExecutionResult:
    child_id: str
    restore: ReplayRestoreResult
    outcome: Any
    isolated: bool
    parallel: bool
    intent_assessment: ReplayRiskAssessment | None = None
    outcome_assessment: ReplayRiskAssessment | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "child_id": self.child_id,
            "restore": self.restore.to_record(),
            "isolated": self.isolated,
            "parallel": self.parallel,
            "intent_assessment": (
                self.intent_assessment.to_record()
                if self.intent_assessment is not None
                else None
            ),
            "outcome_assessment": (
                self.outcome_assessment.to_record()
                if self.outcome_assessment is not None
                else None
            ),
        }


@dataclass(frozen=True)
class ReplayChildExecutionFailure:
    child_id: str
    restore: ReplayRestoreResult
    stage: ReplayChildFailureStage
    error_type: str
    isolated: bool
    parallel: bool
    risk_assessment: ReplayRiskAssessment | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "child_id", _required_text(self.child_id, "child_id"))
        object.__setattr__(self, "stage", ReplayChildFailureStage(self.stage))
        object.__setattr__(
            self,
            "error_type",
            _required_text(self.error_type, "error_type"),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "child_id": self.child_id,
            "restore": self.restore.to_record(),
            "stage": self.stage.value,
            "error_type": self.error_type,
            "isolated": self.isolated,
            "parallel": self.parallel,
            "risk_assessment": (
                self.risk_assessment.to_record()
                if self.risk_assessment is not None
                else None
            ),
        }


class AgentStateStore(Protocol):
    def capture_agent_state(self, cursor: TraceCursor) -> object: ...

    def restore_agent_state(self, state_key: object) -> None: ...

    def release_agent_state(self, state_key: object) -> None: ...


class IsolatedAgentStateStore(AgentStateStore, Protocol):
    def fork_agent_state(self, state_key: object, child_id: str) -> object: ...


class ReplayAdapterV2(Protocol):
    def capture_replay_state(
        self,
        request: CaptureReplayStateRequest,
    ) -> AdapterStateLease: ...

    def restore_replay_state(
        self,
        binding_key: object,
        request: RestoreReplayStateRequest,
    ) -> RestoreReceipt: ...

    def validate_replay_equivalence(
        self,
        binding_key: object,
        receipt: RestoreReceipt,
    ) -> EquivalenceReport: ...

    def release_replay_state(self, binding_key: object) -> None: ...


class IsolatedReplayChildAdapter(ReplayAdapterV2, Protocol):
    def fork_replay_child(
        self,
        binding_key: object,
        request: ForkReplayChildRequest,
    ) -> ReplayChildLease: ...

    def validate_replay_child_equivalence(
        self,
        child_key: object,
        receipt: RestoreReceipt,
    ) -> EquivalenceReport: ...

    def release_replay_child(self, child_key: object) -> None: ...


class RiskAwareReplayChildAdapter(IsolatedReplayChildAdapter, Protocol):
    def assess_replay_child_intent(
        self,
        child_key: object,
        intent: Mapping[str, Any],
    ) -> ReplayRiskAssessment: ...

    def assess_replay_child_outcome(
        self,
        child_key: object,
        intent: Mapping[str, Any],
        outcome: Any,
    ) -> ReplayRiskAssessment: ...


@dataclass(frozen=True)
class ReplayPoint:
    replay_point_id: str
    cursor: TraceCursor
    fidelity: ReplayFidelity
    captured_projection_digest: str | None
    lifecycle: ReplayPointLifecycle
    created_event_id: str
    replaces_replay_point_id: str | None = None

    def to_record(self) -> dict[str, Any]:
        """Return the public-safe record; runtime keys intentionally do not exist."""

        return {
            "replay_point_id": self.replay_point_id,
            "trace_id": self.cursor.trace_id,
            "branch_id": self.cursor.branch_id,
            "after_event_id": self.cursor.after_event_id,
            "next_sequence": self.cursor.next_sequence,
            "agent_history_index": self.cursor.agent_cursor.history_index,
            "agent_history_digest": self.cursor.agent_cursor.history_digest,
            "agent_context_digest": self.cursor.agent_cursor.context_digest,
            "fidelity": self.fidelity.value,
            "captured_projection_digest": self.captured_projection_digest,
            "lifecycle": self.lifecycle.value,
            "created_event_id": self.created_event_id,
            "replaces_replay_point_id": self.replaces_replay_point_id,
        }


def _validate_equivalence_contract(
    point: ReplayPoint,
    receipt: RestoreReceipt,
    report: EquivalenceReport,
    minimum: ReplayFidelity,
) -> bool:
    if report.achieved_fidelity != receipt.achieved_fidelity:
        raise ValueError("restore receipt and equivalence report fidelity disagree")
    if (
        receipt.restored_projection_digest is not None
        and report.observed_digest is not None
        and receipt.restored_projection_digest != report.observed_digest
    ):
        raise ValueError(
            "restore receipt and equivalence report observed digests disagree"
        )
    if (
        point.captured_projection_digest is not None
        and report.expected_digest != point.captured_projection_digest
    ):
        raise ValueError(
            "equivalence report expected digest does not match the captured ReplayPoint"
        )
    if report.equivalent and any(not check.passed for check in report.checks):
        raise ValueError("equivalence report cannot pass while a declared check fails")
    if report.achieved_fidelity == ReplayFidelity.EXACT:
        if (
            report.expected_digest is None
            or report.observed_digest is None
            or report.expected_digest != report.observed_digest
        ):
            raise ValueError("exact replay requires equal non-empty state digests")
    trusted_fidelity = fidelity_meets(
        report.achieved_fidelity,
        ReplayFidelity.SEMANTIC_EQUIVALENT,
    )
    if (
        report.achieved_fidelity == ReplayFidelity.SEMANTIC_EQUIVALENT
        and not report.checks
    ):
        raise ValueError(
            "semantic replay requires at least one explicit equivalence check"
        )
    promotion_eligible = bool(
        report.equivalent
        and trusted_fidelity
        and fidelity_meets(report.achieved_fidelity, minimum)
    )
    if trusted_fidelity and not report.equivalent:
        raise ValueError("exact or semantic replay requires verified state equivalence")
    return promotion_eligible


@dataclass
class _RuntimeReplayBinding:
    point: ReplayPoint
    agent_state_key: object
    adapter_binding_key: object | None
    agent_state_store: AgentStateStore
    adapter: ReplayAdapterV2
    agent_released: bool = False
    adapter_released: bool = False
    replay_lock: threading.Lock = field(default_factory=threading.Lock)


class ReplayPointRegistry:
    """Atomically bind a trace cursor to Core and Adapter runtime state."""

    def __init__(
        self,
        *,
        id_factory: Callable[[], str] | None = None,
        event_id_factory: Callable[[], str] | None = None,
        metrics: ReplayMetricLedger | None = None,
    ) -> None:
        self._id_factory = id_factory or (lambda: f"replay-{uuid.uuid4().hex}")
        self._event_id_factory = event_id_factory or (
            lambda: f"event-{uuid.uuid4().hex}"
        )
        self._entries: dict[str, _RuntimeReplayBinding] = {}
        self._lock = threading.RLock()
        self._metrics = metrics or ReplayMetricLedger()

    def capture_at_head(
        self,
        trace: TraceLog,
        agent_cursor: AgentCursor,
        *,
        agent_state_store: AgentStateStore,
        adapter: ReplayAdapterV2,
        replaces_replay_point_id: str | None = None,
    ) -> ReplayPoint:
        if replayable_episode_v2_mode(adapter) is None:
            raise TypeError(
                "adapter does not implement the Replayable Episode V2 protocol"
            )
        capture_started = time.perf_counter()
        self._metrics.increment("capture_attempt_count")
        replay_point_id = _required_text(self._id_factory(), "replay_point_id")
        event_id = _required_text(self._event_id_factory(), "event_id")
        if replaces_replay_point_id is not None:
            replaces_replay_point_id = _required_text(
                replaces_replay_point_id,
                "replaces_replay_point_id",
            )
            self._metrics.increment("replacement_capture_attempt_count")
        agent_state_key: object | None = None
        lease: AdapterStateLease | None = None
        with trace.locked_cursor(agent_cursor) as cursor:
            with self._lock:
                if replay_point_id in self._entries:
                    raise ValueError(
                        f"replay_point_id is duplicated: {replay_point_id!r}"
                    )
            try:
                replaced_point: ReplayPoint | None = None
                if replaces_replay_point_id is not None:
                    with self._lock:
                        try:
                            replaced_point = self._entries[
                                replaces_replay_point_id
                            ].point
                        except KeyError as error:
                            raise KeyError(
                                "unknown replaces_replay_point_id: "
                                f"{replaces_replay_point_id}"
                            ) from error
                    if replaced_point.lifecycle != ReplayPointLifecycle.RELEASED:
                        raise ValueError(
                            "a replacement ReplayPoint requires the replaced point "
                            "to be released"
                        )
                    if replaced_point.cursor.trace_id != cursor.trace_id:
                        raise ValueError(
                            "a replacement ReplayPoint must remain in the same trace"
                        )
                    if replaced_point.cursor.branch_id == cursor.branch_id:
                        raise ValueError(
                            "a replacement ReplayPoint must be captured on a new branch"
                        )
                agent_state_key = agent_state_store.capture_agent_state(cursor)
                if agent_state_key is None:
                    raise TypeError(
                        "capture_agent_state must return an opaque state key"
                    )
                lease = adapter.capture_replay_state(
                    CaptureReplayStateRequest(
                        replay_point_id=replay_point_id,
                        cursor=cursor,
                    )
                )
                if not isinstance(lease, AdapterStateLease):
                    raise TypeError(
                        "capture_replay_state must return an AdapterStateLease"
                    )
                if (
                    replaced_point is not None
                    and replaced_point.captured_projection_digest is not None
                    and lease.projection_digest is not None
                    and replaced_point.captured_projection_digest
                    != lease.projection_digest
                ):
                    self._metrics.increment("replacement_projection_mismatch_count")
                    raise ValueError(
                        "replacement ReplayPoint projection does not match the "
                        "released point"
                    )
                event = TraceEvent(
                    event_id=event_id,
                    trace_id=trace.trace_id,
                    branch_id=trace.branch_id,
                    sequence=cursor.next_sequence,
                    parent_event_id=cursor.after_event_id,
                    kind=TraceEventKind.LIFECYCLE,
                    replay_point_id=replay_point_id,
                    public_payload={
                        "operation": "replay_point_captured",
                        "fidelity": lease.fidelity.value,
                        "cursor_sequence": cursor.next_sequence,
                        "replaces_replay_point_id": replaces_replay_point_id,
                    },
                )
                point = ReplayPoint(
                    replay_point_id=replay_point_id,
                    cursor=cursor,
                    fidelity=lease.fidelity,
                    captured_projection_digest=lease.projection_digest,
                    lifecycle=ReplayPointLifecycle.READY,
                    created_event_id=event.event_id,
                    replaces_replay_point_id=replaces_replay_point_id,
                )
                entry = _RuntimeReplayBinding(
                    point=point,
                    agent_state_key=agent_state_key,
                    adapter_binding_key=lease.binding_key,
                    agent_state_store=agent_state_store,
                    adapter=adapter,
                    adapter_released=lease.binding_key is None,
                )
                with self._lock:
                    if replay_point_id in self._entries:
                        raise ValueError(
                            f"replay_point_id is duplicated: {replay_point_id!r}"
                        )
                    trace._validate_append_unlocked(event)
                    trace._append_unlocked(event)
                    self._entries[replay_point_id] = entry
                self._metrics.increment("capture_success_count")
                if replaces_replay_point_id is not None:
                    self._metrics.increment("replacement_capture_success_count")
                return point
            except Exception as error:
                self._metrics.increment("capture_failure_count")
                if replaces_replay_point_id is not None:
                    self._metrics.increment("replacement_capture_failure_count")
                cleanup_error: Exception | None = None
                compensation_needed = bool(
                    (lease is not None and lease.binding_key is not None)
                    or agent_state_key is not None
                )
                if compensation_needed:
                    self._metrics.increment("capture_compensation_count")
                if lease is not None and lease.binding_key is not None:
                    try:
                        adapter.release_replay_state(lease.binding_key)
                    except Exception as caught:
                        cleanup_error = caught
                        self._metrics.increment("orphaned_adapter_binding_count")
                if agent_state_key is not None:
                    try:
                        agent_state_store.release_agent_state(agent_state_key)
                    except Exception as caught:
                        cleanup_error = cleanup_error or caught
                        self._metrics.increment("orphaned_agent_state_count")
                if cleanup_error is not None:
                    self._metrics.increment("capture_compensation_failure_count")
                    raise error from cleanup_error
                raise
            finally:
                self._metrics.add_seconds(
                    "capture_minutes",
                    time.perf_counter() - capture_started,
                )

    def get(self, replay_point_id: str) -> ReplayPoint:
        with self._lock:
            try:
                return self._entries[replay_point_id].point
            except KeyError as error:
                raise KeyError(f"unknown replay_point_id: {replay_point_id}") from error

    def records(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(entry.point.to_record() for entry in self._entries.values())

    def metrics(self) -> dict[str, Any]:
        payload = self._metrics.snapshot()
        with self._lock:
            payload.update(
                {
                    "active_replay_point_count": sum(
                        entry.point.lifecycle != ReplayPointLifecycle.RELEASED
                        for entry in self._entries.values()
                    ),
                    "active_adapter_binding_count": sum(
                        not entry.adapter_released for entry in self._entries.values()
                    ),
                    "failed_replay_point_count": sum(
                        entry.point.lifecycle == ReplayPointLifecycle.FAILED
                        for entry in self._entries.values()
                    ),
                }
            )
        return payload

    def select_before(
        self,
        *,
        trace_id: str,
        branch_id: str,
        first_affected_sequence: int,
        minimum_fidelity: ReplayFidelity,
    ) -> ReplayPoint:
        """Choose the nearest ready point before the first affected event.

        A point meeting the requested fidelity wins.  If none does, the
        nearest best-effort point may still support an observed-only replay.
        Non-replayable markers are never selected.
        """

        trace_id = _required_text(trace_id, "trace_id")
        branch_id = _required_text(branch_id, "branch_id")
        affected_sequence = int(first_affected_sequence)
        minimum = ReplayFidelity(minimum_fidelity)
        with self._lock:
            candidates = [
                entry.point
                for entry in self._entries.values()
                if entry.point.lifecycle == ReplayPointLifecycle.READY
                and entry.point.cursor.trace_id == trace_id
                and entry.point.cursor.branch_id == branch_id
                and entry.point.cursor.next_sequence <= affected_sequence
                and entry.point.fidelity != ReplayFidelity.NON_REPLAYABLE
            ]
        if not candidates:
            raise LookupError(
                "no replayable ReplayPoint exists before the affected event"
            )
        trusted = [
            point for point in candidates if fidelity_meets(point.fidelity, minimum)
        ]
        pool = trusted or candidates
        selected = max(pool, key=lambda point: point.cursor.next_sequence)
        self._metrics.increment("replay_point_selection_count")
        if not trusted:
            self._metrics.increment("replay_point_fidelity_fallback_count")
        elif any(
            point.cursor.next_sequence > selected.cursor.next_sequence
            and not fidelity_meets(point.fidelity, minimum)
            for point in candidates
        ):
            self._metrics.increment("replay_point_fidelity_filtered_selection_count")
        return selected

    def run_from(
        self,
        replay_point_id: str,
        *,
        target_branch_id: str,
        minimum_fidelity: ReplayFidelity,
        execute: Callable[[ReplayRestoreResult], Any],
    ) -> tuple[ReplayRestoreResult, Any]:
        """Restore and execute one suffix while holding the point's serial lock."""

        target_branch_id = _required_text(target_branch_id, "target_branch_id")
        minimum = ReplayFidelity(minimum_fidelity)
        replay_started = time.perf_counter()
        self._metrics.increment("replay_attempt_count")
        try:
            with self._lock:
                try:
                    entry = self._entries[replay_point_id]
                except KeyError as error:
                    raise KeyError(
                        f"unknown replay_point_id: {replay_point_id}"
                    ) from error
                if entry.point.lifecycle != ReplayPointLifecycle.READY:
                    raise RuntimeError(
                        "ReplayPoint must be ready before restore: "
                        f"{entry.point.lifecycle.value}"
                    )
                if entry.point.fidelity == ReplayFidelity.NON_REPLAYABLE:
                    raise RuntimeError("non_replayable ReplayPoints cannot be restored")
                if entry.adapter_binding_key is None:
                    raise RuntimeError("replayable ReplayPoint has no Adapter binding")

            with entry.replay_lock:
                with self._lock:
                    if entry.point.lifecycle != ReplayPointLifecycle.READY:
                        raise RuntimeError(
                            "ReplayPoint stopped being ready before restore: "
                            f"{entry.point.lifecycle.value}"
                        )
                request = RestoreReplayStateRequest(
                    replay_point_id=replay_point_id,
                    target_branch_id=target_branch_id,
                    minimum_fidelity=minimum,
                )
                restore_attempted = False
                suffix_attempted = False
                suffix_succeeded = False
                stage_error: BaseException | None = None
                finalization_error: BaseException | None = None
                restored_state: ReplayRestoreResult | None = None
                suffix_started: float | None = None
                suffix_minutes = 0.0
                outcomes: list[object] = []
                try:
                    restore_started = time.perf_counter()
                    self._metrics.increment("restore_attempt_count")
                    restore_attempted = True
                    try:
                        receipt = entry.adapter.restore_replay_state(
                            entry.adapter_binding_key,
                            request,
                        )
                        if not isinstance(receipt, RestoreReceipt):
                            raise TypeError(
                                "restore_replay_state must return a RestoreReceipt"
                            )
                        if receipt.achieved_fidelity == ReplayFidelity.NON_REPLAYABLE:
                            raise RuntimeError(
                                "Adapter reported non_replayable while restoring a ReplayPoint"
                            )
                        if not fidelity_meets(
                            entry.point.fidelity,
                            receipt.achieved_fidelity,
                        ):
                            raise ValueError(
                                "restore achieved a fidelity stronger than the captured "
                                "ReplayPoint"
                            )
                    except Exception:
                        self._metrics.increment("restore_failure_count")
                        raise
                    else:
                        self._metrics.increment("restore_success_count")
                        if receipt.prefix_reused is True:
                            self._metrics.increment("prefix_reuse_verified_count")
                        elif receipt.prefix_reused is False:
                            self._metrics.increment("prefix_reconstruction_count")
                        else:
                            self._metrics.increment("prefix_reuse_unknown_count")
                    finally:
                        restore_minutes = (
                            time.perf_counter() - restore_started
                        ) / 60.0
                        self._metrics.add_seconds(
                            "restore_minutes",
                            time.perf_counter() - restore_started,
                        )

                    equivalence_started = time.perf_counter()
                    self._metrics.increment("equivalence_attempt_count")
                    try:
                        report = entry.adapter.validate_replay_equivalence(
                            entry.adapter_binding_key,
                            receipt,
                        )
                        if not isinstance(report, EquivalenceReport):
                            raise TypeError(
                                "validate_replay_equivalence must return an "
                                "EquivalenceReport"
                            )
                        promotion_eligible = _validate_equivalence_contract(
                            entry.point,
                            receipt,
                            report,
                            minimum,
                        )
                        trusted_fidelity = fidelity_meets(
                            report.achieved_fidelity,
                            ReplayFidelity.SEMANTIC_EQUIVALENT,
                        )
                    except Exception:
                        self._metrics.increment("equivalence_failure_count")
                        raise
                    else:
                        if report.equivalent and trusted_fidelity:
                            self._metrics.increment("equivalence_verified_count")
                        else:
                            self._metrics.increment("equivalence_unverified_count")
                    finally:
                        equivalence_minutes = (
                            time.perf_counter() - equivalence_started
                        ) / 60.0
                        self._metrics.add_seconds(
                            "equivalence_minutes",
                            time.perf_counter() - equivalence_started,
                        )
                    self._metrics.observe_fidelity(report.achieved_fidelity.value)
                    entry.agent_state_store.restore_agent_state(entry.agent_state_key)
                    restored_state = ReplayRestoreResult(
                        replay_point_id=replay_point_id,
                        target_branch_id=target_branch_id,
                        achieved_fidelity=report.achieved_fidelity,
                        equivalent=report.equivalent,
                        promotion_eligible=promotion_eligible,
                        prefix_reused=receipt.prefix_reused,
                        report=report,
                        restore_minutes=restore_minutes,
                        equivalence_minutes=equivalence_minutes,
                        suffix_minutes=0.0,
                        replay_wall_minutes=0.0,
                    )
                    suffix_started = time.perf_counter()
                    self._metrics.increment("suffix_attempt_count")
                    suffix_attempted = True
                    outcomes.append(execute(restored_state))
                    suffix_succeeded = True
                except BaseException as error:
                    stage_error = error
                finally:
                    if restore_attempted:
                        finalizer = getattr(
                            entry.adapter,
                            "finalize_replay_suffix",
                            None,
                        )
                        if callable(finalizer):
                            self._metrics.increment("suffix_finalization_attempt_count")
                            try:
                                finalizer(
                                    entry.adapter_binding_key,
                                    request,
                                    suffix_succeeded=suffix_succeeded,
                                )
                            except BaseException as error:
                                finalization_error = error
                                self._metrics.increment(
                                    "suffix_finalization_failure_count"
                                )
                            else:
                                self._metrics.increment(
                                    "suffix_finalization_success_count"
                                )
                    if suffix_started is not None:
                        suffix_elapsed = time.perf_counter() - suffix_started
                        suffix_minutes = suffix_elapsed / 60.0
                        self._metrics.add_seconds("suffix_minutes", suffix_elapsed)
                if finalization_error is not None:
                    if stage_error is None or suffix_attempted:
                        self._metrics.increment("suffix_failure_count")
                    with self._lock:
                        entry.point = replace(
                            entry.point,
                            lifecycle=ReplayPointLifecycle.FAILED,
                        )
                    self._metrics.increment("replay_point_quarantine_count")
                    if stage_error is not None:
                        raise stage_error from finalization_error
                    raise finalization_error
                if stage_error is not None:
                    if suffix_attempted and isinstance(stage_error, Exception):
                        self._metrics.increment("suffix_failure_count")
                    raise stage_error
                assert restored_state is not None
                self._metrics.increment("suffix_success_count")
                replay_wall_minutes = (time.perf_counter() - replay_started) / 60.0
                restore = replace(
                    restored_state,
                    suffix_minutes=suffix_minutes,
                    replay_wall_minutes=replay_wall_minutes,
                )
                self._metrics.increment("replay_success_count")
                return restore, outcomes[0]
        except Exception:
            self._metrics.increment("replay_failure_count")
            raise
        finally:
            self._metrics.add_seconds(
                "replay_wall_minutes",
                time.perf_counter() - replay_started,
            )

    def run_child_group_from(
        self,
        replay_point_id: str,
        tasks: Sequence[ReplayChildTask],
        *,
        parallel: bool = False,
        max_workers: int | None = None,
    ) -> tuple[ReplayChildExecutionResult, ...]:
        from .child_replay_runtime import run_child_group_from

        return run_child_group_from(
            self,
            replay_point_id,
            tasks,
            parallel=parallel,
            max_workers=max_workers,
        )

    def run_child_group_settled_from(
        self,
        replay_point_id: str,
        tasks: Sequence[ReplayChildTask],
        *,
        parallel: bool = False,
        max_workers: int | None = None,
    ) -> tuple[ReplayChildExecutionResult | ReplayChildExecutionFailure, ...]:
        """Preserve safe sibling results when isolated candidates fail safely."""

        from .child_replay_runtime import run_child_group_from

        return run_child_group_from(
            self,
            replay_point_id,
            tasks,
            parallel=parallel,
            max_workers=max_workers,
            settle_failures=True,
        )

    def release(self, replay_point_id: str) -> ReplayPoint:
        release_started = time.perf_counter()
        self._metrics.increment("release_attempt_count")
        try:
            with self._lock:
                try:
                    entry = self._entries[replay_point_id]
                except KeyError as error:
                    raise KeyError(
                        f"unknown replay_point_id: {replay_point_id}"
                    ) from error
                if entry.point.lifecycle == ReplayPointLifecycle.RELEASED:
                    self._metrics.increment("release_success_count")
                    return entry.point
                entry.point = replace(
                    entry.point,
                    lifecycle=ReplayPointLifecycle.RELEASING,
                )

            adapter_error: Exception | None = None
            agent_error: Exception | None = None
            with entry.replay_lock:
                if not entry.adapter_released and entry.adapter_binding_key is not None:
                    try:
                        entry.adapter.release_replay_state(entry.adapter_binding_key)
                        entry.adapter_released = True
                    except Exception as error:
                        adapter_error = error
                if not entry.agent_released:
                    try:
                        entry.agent_state_store.release_agent_state(
                            entry.agent_state_key
                        )
                        entry.agent_released = True
                    except Exception as error:
                        agent_error = error

            with self._lock:
                entry.point = replace(
                    entry.point,
                    lifecycle=(
                        ReplayPointLifecycle.RELEASED
                        if entry.adapter_released and entry.agent_released
                        else ReplayPointLifecycle.FAILED
                    ),
                )
                point = entry.point
            if adapter_error is not None:
                if agent_error is not None:
                    raise adapter_error from agent_error
                raise adapter_error
            if agent_error is not None:
                raise agent_error
            self._metrics.increment("release_success_count")
            return point
        except Exception:
            self._metrics.increment("release_failure_count")
            raise
        finally:
            self._metrics.add_seconds(
                "release_minutes",
                time.perf_counter() - release_started,
            )
