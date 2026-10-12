from __future__ import annotations

import threading
import time
from typing import Any, Mapping

import pytest

from loopx.capabilities.explore.adaptive_replay_planner import (
    FailureReplayObservation,
    ReplayPositionCost,
    plan_adaptive_replay_points,
)
from loopx.capabilities.explore.counterfactual_runtime import (
    CounterfactualCandidate,
    CounterfactualChildCandidate,
    CounterfactualDecision,
    CounterfactualHypothesis,
    ValidationTarget,
    run_counterfactual_child_group,
)
from loopx.capabilities.explore.replay_runtime import (
    AdapterStateLease,
    CaptureReplayStateRequest,
    EquivalenceCheck,
    EquivalenceReport,
    ForkReplayChildRequest,
    ReplayChildConcurrency,
    ReplayChildExecutionFailure,
    ReplayChildExecutionResult,
    ReplayChildFailureStage,
    ReplayChildLease,
    ReplayChildTask,
    ReplayFidelity,
    ReplayPointLifecycle,
    ReplayPointRegistry,
    ReplayRiskAssessment,
    ReplayRiskDisposition,
    ReplayRiskRejectedError,
    RestoreReceipt,
    RestoreReplayStateRequest,
    fidelity_meets,
)
from loopx.capabilities.explore.trace_runtime import (
    AgentCursor,
    TraceEvent,
    TraceEventKind,
    TraceLog,
)


class SyntheticReplayError(RuntimeError):
    pass


class SyntheticAgentStateStore:
    def __init__(self, *, fail_capture: bool = False) -> None:
        self.fail_capture = fail_capture
        self.captured: list[object] = []
        self.restored: list[object] = []
        self.released: list[object] = []

    def capture_agent_state(self, cursor: object) -> object:
        if self.fail_capture:
            raise SyntheticReplayError("agent capture failed")
        key = object()
        self.captured.append(key)
        return key

    def restore_agent_state(self, state_key: object) -> None:
        assert state_key in self.captured
        self.restored.append(state_key)

    def release_agent_state(self, state_key: object) -> None:
        assert state_key in self.captured
        self.released.append(state_key)


class SyntheticReplayAdapter:
    def __init__(
        self,
        *,
        fidelity: ReplayFidelity = ReplayFidelity.SEMANTIC_EQUIVALENT,
        fail_capture: bool = False,
        fail_release: bool = False,
        equivalent: bool = True,
        restored_fidelity: ReplayFidelity | None = None,
        observed_digest: str = "public-safe-state-v1",
        captured_digest: str = "public-safe-state-v1",
    ) -> None:
        self.fidelity = fidelity
        self.fail_capture = fail_capture
        self.fail_release = fail_release
        self.equivalent = equivalent
        self.restored_fidelity = restored_fidelity or fidelity
        self.observed_digest = observed_digest
        self.captured_digest = captured_digest
        self.capture_requests: list[CaptureReplayStateRequest] = []
        self.active_bindings: set[object] = set()
        self.release_calls: list[object] = []
        self.restore_calls: list[RestoreReplayStateRequest] = []

    def capture_replay_state(
        self, request: CaptureReplayStateRequest
    ) -> AdapterStateLease:
        self.capture_requests.append(request)
        if self.fail_capture:
            raise SyntheticReplayError("adapter capture failed")
        if self.fidelity == ReplayFidelity.NON_REPLAYABLE:
            return AdapterStateLease(
                binding_key=None,
                fidelity=self.fidelity,
                projection_digest=None,
            )
        binding = object()
        self.active_bindings.add(binding)
        return AdapterStateLease(
            binding_key=binding,
            fidelity=self.fidelity,
            projection_digest=self.captured_digest,
        )

    def restore_replay_state(
        self,
        binding_key: object,
        request: RestoreReplayStateRequest,
    ) -> RestoreReceipt:
        assert binding_key in self.active_bindings
        self.restore_calls.append(request)
        return RestoreReceipt(
            achieved_fidelity=self.restored_fidelity,
            restored_projection_digest=self.observed_digest,
        )

    def validate_replay_equivalence(
        self,
        binding_key: object,
        receipt: RestoreReceipt,
    ) -> EquivalenceReport:
        assert binding_key in self.active_bindings
        return EquivalenceReport(
            achieved_fidelity=receipt.achieved_fidelity,
            equivalent=self.equivalent,
            expected_digest="public-safe-state-v1",
            observed_digest=receipt.restored_projection_digest,
            checks=(
                EquivalenceCheck(
                    check_id="synthetic-state-equivalence",
                    passed=self.equivalent,
                ),
            )
            if receipt.achieved_fidelity == ReplayFidelity.SEMANTIC_EQUIVALENT
            else (),
        )

    def release_replay_state(self, binding_key: object) -> None:
        assert binding_key in self.active_bindings
        self.release_calls.append(binding_key)
        if self.fail_release:
            raise SyntheticReplayError("adapter release failed")
        self.active_bindings.remove(binding_key)


class SyntheticChildAgentStateStore(SyntheticAgentStateStore):
    def __init__(self) -> None:
        super().__init__()
        self.child_keys: list[object] = []

    def fork_agent_state(self, state_key: object, child_id: str) -> object:
        assert state_key in self.captured
        assert child_id
        child_key = object()
        self.captured.append(child_key)
        self.child_keys.append(child_key)
        return child_key


class SyntheticChildReplayAdapter(SyntheticReplayAdapter):
    def __init__(
        self,
        *,
        reuse_child_key: bool = False,
        fail_child_release_id: str | None = None,
        concurrency: ReplayChildConcurrency = ReplayChildConcurrency.THREAD_SAFE,
        reuse_worker_identity: bool = False,
    ) -> None:
        super().__init__()
        self.reuse_child_key = reuse_child_key
        self.fail_child_release_id = fail_child_release_id
        self.concurrency = ReplayChildConcurrency(concurrency)
        self.reuse_worker_identity = reuse_worker_identity
        self.shared_worker_identity = object()
        self.shared_child_key = object()
        self.child_requests: list[ForkReplayChildRequest] = []
        self.active_children: set[object] = set()
        self.child_ids: dict[object, str] = {}
        self.released_children: list[object] = []

    def fork_replay_child(
        self,
        binding_key: object,
        request: ForkReplayChildRequest,
    ) -> ReplayChildLease:
        assert binding_key in self.active_bindings
        self.child_requests.append(request)
        child_key = self.shared_child_key if self.reuse_child_key else object()
        self.active_children.add(child_key)
        self.child_ids[child_key] = request.child_id
        return ReplayChildLease(
            child_key=child_key,
            concurrency=self.concurrency,
            worker_identity=(
                self.shared_worker_identity
                if self.concurrency == ReplayChildConcurrency.PROCESS_ISOLATED
                and self.reuse_worker_identity
                else object()
                if self.concurrency == ReplayChildConcurrency.PROCESS_ISOLATED
                else None
            ),
            receipt=RestoreReceipt(
                achieved_fidelity=ReplayFidelity.SEMANTIC_EQUIVALENT,
                restored_projection_digest="public-safe-state-v1",
                prefix_reused=False,
            ),
        )

    def validate_replay_child_equivalence(
        self,
        child_key: object,
        receipt: RestoreReceipt,
    ) -> EquivalenceReport:
        assert child_key in self.active_children
        return EquivalenceReport(
            achieved_fidelity=receipt.achieved_fidelity,
            equivalent=True,
            expected_digest="public-safe-state-v1",
            observed_digest=receipt.restored_projection_digest,
            checks=(EquivalenceCheck("isolated-child-state", True),),
        )

    def release_replay_child(self, child_key: object) -> None:
        assert child_key in self.active_children
        if self.child_ids[child_key] == self.fail_child_release_id:
            raise SyntheticReplayError("child release failed")
        self.active_children.remove(child_key)
        del self.child_ids[child_key]
        self.released_children.append(child_key)


class SyntheticRiskAwareChildAdapter(SyntheticChildReplayAdapter):
    def __init__(
        self,
        *,
        deny_intent: bool = False,
        deny_outcome: bool = False,
    ) -> None:
        super().__init__()
        self.deny_intent = deny_intent
        self.deny_outcome = deny_outcome
        self.intent_calls: list[Mapping[str, Any]] = []
        self.outcome_calls: list[Any] = []

    def assess_replay_child_intent(
        self,
        child_key: object,
        intent: Mapping[str, Any],
    ) -> ReplayRiskAssessment:
        assert child_key in self.active_children
        self.intent_calls.append(intent)
        denied = self.deny_intent or intent.get("operation") == "denied-candidate"
        return ReplayRiskAssessment(
            disposition=(
                ReplayRiskDisposition.DENY if denied else ReplayRiskDisposition.ALLOW
            ),
            policy_id="synthetic-intent-policy",
            sanitized_reason="candidate intent rejected" if denied else None,
        )

    def assess_replay_child_outcome(
        self,
        child_key: object,
        intent: Mapping[str, Any],
        outcome: Any,
    ) -> ReplayRiskAssessment:
        assert child_key in self.active_children
        self.outcome_calls.append(outcome)
        return ReplayRiskAssessment(
            disposition=(
                ReplayRiskDisposition.DENY
                if self.deny_outcome
                else ReplayRiskDisposition.ALLOW
            ),
            policy_id="synthetic-outcome-policy",
            sanitized_reason=(
                "candidate outcome rejected" if self.deny_outcome else None
            ),
        )


def _agent_cursor(index: int = 1) -> AgentCursor:
    return AgentCursor(
        history_index=index,
        history_digest=f"history-{index}",
        context_digest=f"context-{index}",
    )


def test_trace_is_addressable_and_public_safe() -> None:
    trace = TraceLog("trace-1", "branch-main")
    first = TraceEvent(
        event_id="event-1",
        trace_id="trace-1",
        branch_id="branch-main",
        sequence=0,
        parent_event_id=None,
        kind=TraceEventKind.ACTION_INTENT,
        action_id="action-1",
        public_payload={"operation": "bounded_probe", "arguments": [1, 2]},
    )
    trace.append(first)
    assert trace.events == (first,)
    assert first.to_record()["public_payload"]["arguments"] == [1, 2]

    with pytest.raises(ValueError, match="contiguous"):
        trace.append(
            TraceEvent(
                event_id="event-gap",
                trace_id="trace-1",
                branch_id="branch-main",
                sequence=2,
                parent_event_id="event-1",
                kind=TraceEventKind.ACTION_OUTCOME,
            )
        )
    with pytest.raises(ValueError, match="must not enter"):
        TraceEvent(
            event_id="event-sensitive",
            trace_id="trace-1",
            branch_id="branch-main",
            sequence=1,
            parent_event_id="event-1",
            kind=TraceEventKind.ENVIRONMENT_OBSERVATION,
            public_payload={"handle": object()},
        )
    with pytest.raises(ValueError, match="absolute paths"):
        TraceEvent(
            event_id="event-path",
            trace_id="trace-1",
            branch_id="branch-main",
            sequence=1,
            parent_event_id="event-1",
            kind=TraceEventKind.ENVIRONMENT_OBSERVATION,
            public_payload={"evidence_ref": r"D:\private\raw.png"},
        )


def test_replay_point_atomically_binds_cursor_and_adapter_state() -> None:
    trace = TraceLog("trace-2", "branch-main")
    trace.append(
        TraceEvent(
            event_id="event-before",
            trace_id="trace-2",
            branch_id="branch-main",
            sequence=0,
            parent_event_id=None,
            kind=TraceEventKind.ACTION_OUTCOME,
            action_id="prepare-section",
            public_payload={"status": "ok"},
        )
    )
    agent_states = SyntheticAgentStateStore()
    adapter = SyntheticReplayAdapter()
    registry = ReplayPointRegistry(
        id_factory=lambda: "replay-before-first-effect",
        event_id_factory=lambda: "event-replay-captured",
    )

    point = registry.capture_at_head(
        trace,
        _agent_cursor(4),
        agent_state_store=agent_states,
        adapter=adapter,
    )

    assert point.cursor.after_event_id == "event-before"
    assert point.cursor.next_sequence == 1
    assert point.cursor.agent_cursor.history_index == 4
    assert point.fidelity == ReplayFidelity.SEMANTIC_EQUIVALENT
    assert point.lifecycle == ReplayPointLifecycle.READY
    assert adapter.capture_requests[0].cursor == point.cursor
    assert trace.events[-1].replay_point_id == point.replay_point_id
    public_record = registry.records()[0]
    assert "binding_key" not in public_record
    assert "agent_state_key" not in public_record

    released = registry.release(point.replay_point_id)
    assert released.lifecycle == ReplayPointLifecycle.RELEASED
    assert adapter.active_bindings == set()
    assert agent_states.released == agent_states.captured
    assert registry.release(point.replay_point_id) == released


def test_capture_failure_leaves_no_partial_replay_point() -> None:
    trace = TraceLog("trace-3", "branch-main")
    agent_states = SyntheticAgentStateStore()
    adapter = SyntheticReplayAdapter(fail_capture=True)
    registry = ReplayPointRegistry(
        id_factory=lambda: "replay-failed",
        event_id_factory=lambda: "event-never-visible",
    )

    with pytest.raises(SyntheticReplayError, match="adapter capture failed"):
        registry.capture_at_head(
            trace,
            _agent_cursor(),
            agent_state_store=agent_states,
            adapter=adapter,
        )

    assert registry.records() == ()
    assert trace.events == ()
    assert agent_states.released == agent_states.captured


def test_non_replayable_marker_has_no_adapter_binding() -> None:
    trace = TraceLog("trace-4", "branch-main")
    agent_states = SyntheticAgentStateStore()
    adapter = SyntheticReplayAdapter(fidelity=ReplayFidelity.NON_REPLAYABLE)
    registry = ReplayPointRegistry(
        id_factory=lambda: "replay-marker",
        event_id_factory=lambda: "event-marker",
    )

    point = registry.capture_at_head(
        trace,
        _agent_cursor(),
        agent_state_store=agent_states,
        adapter=adapter,
    )
    assert point.fidelity == ReplayFidelity.NON_REPLAYABLE
    assert fidelity_meets(ReplayFidelity.EXACT, ReplayFidelity.SEMANTIC_EQUIVALENT)
    assert not fidelity_meets(
        ReplayFidelity.BEST_EFFORT,
        ReplayFidelity.SEMANTIC_EQUIVALENT,
    )

    registry.release(point.replay_point_id)
    assert adapter.release_calls == []
    assert agent_states.released == agent_states.captured


def test_failed_release_is_retryable_without_double_releasing_agent_state() -> None:
    trace = TraceLog("trace-5", "branch-main")
    agent_states = SyntheticAgentStateStore()
    adapter = SyntheticReplayAdapter(fail_release=True)
    registry = ReplayPointRegistry(
        id_factory=lambda: "replay-retry-release",
        event_id_factory=lambda: "event-retry-release",
    )
    point = registry.capture_at_head(
        trace,
        _agent_cursor(),
        agent_state_store=agent_states,
        adapter=adapter,
    )

    with pytest.raises(SyntheticReplayError, match="adapter release failed"):
        registry.release(point.replay_point_id)
    assert registry.get(point.replay_point_id).lifecycle == ReplayPointLifecycle.FAILED
    assert len(agent_states.released) == 1

    adapter.fail_release = False
    released = registry.release(point.replay_point_id)
    assert released.lifecycle == ReplayPointLifecycle.RELEASED
    assert len(agent_states.released) == 1
    assert len(adapter.release_calls) == 2


def test_released_point_can_be_replaced_on_a_verified_recovery_branch() -> None:
    point_ids = iter(("replay-dirty", "replay-recovered"))
    event_ids = iter(("event-dirty", "event-recovered"))
    registry = ReplayPointRegistry(
        id_factory=lambda: next(point_ids),
        event_id_factory=lambda: next(event_ids),
    )
    agent_states = SyntheticAgentStateStore()
    old_adapter = SyntheticReplayAdapter()
    old_trace = TraceLog("trace-recovery", "branch-failed")
    old_point = registry.capture_at_head(
        old_trace,
        _agent_cursor(2),
        agent_state_store=agent_states,
        adapter=old_adapter,
    )
    registry.release(old_point.replay_point_id)

    recovery_trace = TraceLog("trace-recovery", "branch-recovery-1")
    recovery_trace.append(
        TraceEvent(
            event_id="event-prefix-rebuilt",
            trace_id="trace-recovery",
            branch_id="branch-recovery-1",
            sequence=0,
            parent_event_id=None,
            kind=TraceEventKind.ACTION_OUTCOME,
            causation_id=old_point.created_event_id,
            public_payload={"operation": "prefix_reconstructed"},
        )
    )
    new_adapter = SyntheticReplayAdapter()
    replacement = registry.capture_at_head(
        recovery_trace,
        _agent_cursor(3),
        agent_state_store=agent_states,
        adapter=new_adapter,
        replaces_replay_point_id=old_point.replay_point_id,
    )

    assert replacement.replaces_replay_point_id == old_point.replay_point_id
    assert replacement.cursor.branch_id == "branch-recovery-1"
    assert registry.get(old_point.replay_point_id).lifecycle == (
        ReplayPointLifecycle.RELEASED
    )
    assert registry.records()[1]["replaces_replay_point_id"] == "replay-dirty"
    assert (
        recovery_trace.events[-1].public_payload["replaces_replay_point_id"]
        == "replay-dirty"
    )
    metrics = registry.metrics()
    assert metrics["replacement_capture_attempt_count"] == 1
    assert metrics["replacement_capture_success_count"] == 1
    assert metrics["replacement_capture_failure_count"] == 0


def test_replacement_requires_release_and_compensates_digest_mismatch() -> None:
    point_ids = iter(("replay-old", "replay-too-soon", "replay-mismatch"))
    event_ids = iter(("event-old", "event-too-soon", "event-mismatch"))
    registry = ReplayPointRegistry(
        id_factory=lambda: next(point_ids),
        event_id_factory=lambda: next(event_ids),
    )
    old_states = SyntheticAgentStateStore()
    old_adapter = SyntheticReplayAdapter()
    old_point = registry.capture_at_head(
        TraceLog("trace-replacement", "branch-old"),
        _agent_cursor(),
        agent_state_store=old_states,
        adapter=old_adapter,
    )
    recovery_trace = TraceLog("trace-replacement", "branch-recovery")
    rejected_states = SyntheticAgentStateStore()
    rejected_adapter = SyntheticReplayAdapter()

    with pytest.raises(ValueError, match="requires the replaced point to be released"):
        registry.capture_at_head(
            recovery_trace,
            _agent_cursor(),
            agent_state_store=rejected_states,
            adapter=rejected_adapter,
            replaces_replay_point_id=old_point.replay_point_id,
        )
    assert rejected_states.captured == []
    assert rejected_adapter.capture_requests == []

    registry.release(old_point.replay_point_id)
    mismatch_states = SyntheticAgentStateStore()
    mismatch_adapter = SyntheticReplayAdapter(captured_digest="different-state")
    with pytest.raises(ValueError, match="projection does not match"):
        registry.capture_at_head(
            recovery_trace,
            _agent_cursor(),
            agent_state_store=mismatch_states,
            adapter=mismatch_adapter,
            replaces_replay_point_id=old_point.replay_point_id,
        )

    assert registry.records() == (registry.get(old_point.replay_point_id).to_record(),)
    assert recovery_trace.events == ()
    assert mismatch_states.released == mismatch_states.captured
    assert mismatch_adapter.active_bindings == set()
    metrics = registry.metrics()
    assert metrics["replacement_capture_attempt_count"] == 2
    assert metrics["replacement_capture_success_count"] == 0
    assert metrics["replacement_capture_failure_count"] == 2
    assert metrics["replacement_projection_mismatch_count"] == 1


@pytest.mark.parametrize("fail_release", [False, True])
def test_capture_commit_failure_reports_compensation_and_orphan_state(
    fail_release: bool,
) -> None:
    trace = TraceLog("trace-compensation", "branch-main")
    trace.append(
        TraceEvent(
            event_id="duplicate-event-id",
            trace_id="trace-compensation",
            branch_id="branch-main",
            sequence=0,
            parent_event_id=None,
            kind=TraceEventKind.ACTION_OUTCOME,
            public_payload={"status": "ok"},
        )
    )
    agent_states = SyntheticAgentStateStore()
    adapter = SyntheticReplayAdapter(fail_release=fail_release)
    registry = ReplayPointRegistry(
        id_factory=lambda: "replay-compensation",
        event_id_factory=lambda: "duplicate-event-id",
    )

    with pytest.raises(ValueError, match="event_id is duplicated") as raised:
        registry.capture_at_head(
            trace,
            _agent_cursor(),
            agent_state_store=agent_states,
            adapter=adapter,
        )

    metrics = registry.metrics()
    assert metrics["capture_failure_count"] == 1
    assert metrics["capture_compensation_count"] == 1
    assert metrics["capture_compensation_failure_count"] == int(fail_release)
    assert metrics["orphaned_adapter_binding_count"] == int(fail_release)
    assert metrics["orphaned_agent_state_count"] == 0
    assert agent_states.released == agent_states.captured
    if fail_release:
        assert isinstance(raised.value.__cause__, SyntheticReplayError)
        assert len(adapter.active_bindings) == 1
    else:
        assert raised.value.__cause__ is None
        assert adapter.active_bindings == set()


def test_isolated_child_sessions_overlap_only_with_distinct_runtime_keys() -> None:
    trace = TraceLog("trace-child-parallel", "branch-main")
    agent_states = SyntheticChildAgentStateStore()
    adapter = SyntheticChildReplayAdapter()
    registry = ReplayPointRegistry(
        id_factory=lambda: "replay-child-parallel",
        event_id_factory=lambda: "event-child-parallel",
    )
    point = registry.capture_at_head(
        trace,
        _agent_cursor(),
        agent_state_store=agent_states,
        adapter=adapter,
    )
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    active = 0
    max_active = 0
    runtime_keys: list[object] = []

    def execute(context: object) -> str:
        nonlocal active, max_active
        assert context.isolated is True
        assert context.adapter_child_key is not None
        runtime_keys.append(context.adapter_child_key)
        with lock:
            active += 1
            max_active = max(max_active, active)
        barrier.wait(timeout=2)
        time.sleep(0.02)
        with lock:
            active -= 1
        return context.child_id

    results = registry.run_child_group_from(
        point.replay_point_id,
        (
            ReplayChildTask(
                "child-a",
                "candidate-a",
                ReplayFidelity.SEMANTIC_EQUIVALENT,
                execute,
            ),
            ReplayChildTask(
                "child-b",
                "candidate-b",
                ReplayFidelity.SEMANTIC_EQUIVALENT,
                execute,
            ),
        ),
        parallel=True,
    )

    assert [result.outcome for result in results] == ["child-a", "child-b"]
    assert all(result.isolated and result.parallel for result in results)
    assert max_active == 2
    assert len(runtime_keys) == 2
    assert runtime_keys[0] is not runtime_keys[1]
    assert adapter.active_children == set()
    assert len(adapter.released_children) == 2
    assert all(key in agent_states.released for key in agent_states.child_keys)
    metrics = registry.metrics()
    assert metrics["child_parallel_group_count"] == 1
    assert metrics["child_group_success_count"] == 1
    assert metrics["child_session_fork_success_count"] == 2
    assert metrics["child_session_release_success_count"] == 2
    registry.release(point.replay_point_id)


def test_process_isolated_children_parallelize_with_distinct_worker_identities() -> (
    None
):
    trace = TraceLog("trace-process-workers", "branch-main")
    agent_states = SyntheticChildAgentStateStore()
    adapter = SyntheticChildReplayAdapter(
        concurrency=ReplayChildConcurrency.PROCESS_ISOLATED
    )
    registry = ReplayPointRegistry(
        id_factory=lambda: "replay-process-workers",
        event_id_factory=lambda: "event-process-workers",
    )
    point = registry.capture_at_head(
        trace,
        _agent_cursor(),
        agent_state_store=agent_states,
        adapter=adapter,
    )
    barrier = threading.Barrier(2)

    def execute(context: object) -> str:
        barrier.wait(timeout=2)
        return context.child_id

    results = registry.run_child_group_from(
        point.replay_point_id,
        (
            ReplayChildTask(
                "process-a",
                "candidate-a",
                ReplayFidelity.SEMANTIC_EQUIVALENT,
                execute,
            ),
            ReplayChildTask(
                "process-b",
                "candidate-b",
                ReplayFidelity.SEMANTIC_EQUIVALENT,
                execute,
            ),
        ),
        parallel=True,
    )

    assert all(result.isolated and result.parallel for result in results)
    metrics = registry.metrics()
    assert metrics["child_parallel_group_count"] == 1
    assert metrics["child_process_isolated_parallel_group_count"] == 1
    assert metrics["child_parallel_downgrade_group_count"] == 0
    registry.release(point.replay_point_id)


def test_process_isolated_children_downgrade_when_worker_identity_is_reused() -> None:
    trace = TraceLog("trace-reused-worker", "branch-main")
    agent_states = SyntheticChildAgentStateStore()
    adapter = SyntheticChildReplayAdapter(
        concurrency=ReplayChildConcurrency.PROCESS_ISOLATED,
        reuse_worker_identity=True,
    )
    registry = ReplayPointRegistry(
        id_factory=lambda: "replay-reused-worker",
        event_id_factory=lambda: "event-reused-worker",
    )
    point = registry.capture_at_head(
        trace,
        _agent_cursor(),
        agent_state_store=agent_states,
        adapter=adapter,
    )
    active = 0
    max_active = 0

    def execute(context: object) -> str:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        time.sleep(0.01)
        active -= 1
        return context.child_id

    results = registry.run_child_group_from(
        point.replay_point_id,
        (
            ReplayChildTask(
                "same-worker-a",
                "candidate-a",
                ReplayFidelity.SEMANTIC_EQUIVALENT,
                execute,
            ),
            ReplayChildTask(
                "same-worker-b",
                "candidate-b",
                ReplayFidelity.SEMANTIC_EQUIVALENT,
                execute,
            ),
        ),
        parallel=True,
    )

    assert all(result.isolated and not result.parallel for result in results)
    assert max_active == 1
    metrics = registry.metrics()
    assert metrics["child_parallel_group_count"] == 0
    assert metrics["child_process_isolated_parallel_group_count"] == 0
    assert metrics["child_parallel_downgrade_group_count"] == 1
    registry.release(point.replay_point_id)


def test_process_isolated_child_lease_requires_worker_identity() -> None:
    with pytest.raises(TypeError, match="requires an opaque worker_identity"):
        ReplayChildLease(
            child_key=object(),
            receipt=RestoreReceipt(
                achieved_fidelity=ReplayFidelity.SEMANTIC_EQUIVALENT
            ),
            concurrency=ReplayChildConcurrency.PROCESS_ISOLATED,
        )


def test_child_group_uses_serial_fallback_without_two_sided_isolation() -> None:
    trace = TraceLog("trace-child-fallback", "branch-main")
    agent_states = SyntheticAgentStateStore()
    adapter = SyntheticReplayAdapter()
    registry = ReplayPointRegistry(
        id_factory=lambda: "replay-child-fallback",
        event_id_factory=lambda: "event-child-fallback",
    )
    point = registry.capture_at_head(
        trace,
        _agent_cursor(),
        agent_state_store=agent_states,
        adapter=adapter,
    )
    lock = threading.Lock()
    active = 0
    max_active = 0

    def execute(context: object) -> str:
        nonlocal active, max_active
        assert context.isolated is False
        assert context.adapter_child_key is None
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return context.child_id

    results = registry.run_child_group_from(
        point.replay_point_id,
        (
            ReplayChildTask(
                "fallback-a",
                "candidate-a",
                ReplayFidelity.SEMANTIC_EQUIVALENT,
                execute,
            ),
            ReplayChildTask(
                "fallback-b",
                "candidate-b",
                ReplayFidelity.SEMANTIC_EQUIVALENT,
                execute,
            ),
        ),
        parallel=True,
    )

    assert [result.outcome for result in results] == [
        "fallback-a",
        "fallback-b",
    ]
    assert all(not result.isolated and not result.parallel for result in results)
    assert max_active == 1
    metrics = registry.metrics()
    assert metrics["child_serial_fallback_group_count"] == 1
    assert metrics["child_parallel_group_count"] == 0
    registry.release(point.replay_point_id)


def test_isolated_child_group_downgrades_parallel_without_adapter_grant() -> None:
    trace = TraceLog("trace-child-downgrade", "branch-main")
    agent_states = SyntheticChildAgentStateStore()
    adapter = SyntheticChildReplayAdapter(
        concurrency=ReplayChildConcurrency.SERIAL_ONLY
    )
    registry = ReplayPointRegistry(
        id_factory=lambda: "replay-child-downgrade",
        event_id_factory=lambda: "event-child-downgrade",
    )
    point = registry.capture_at_head(
        trace,
        _agent_cursor(),
        agent_state_store=agent_states,
        adapter=adapter,
    )
    active = 0
    max_active = 0

    def execute(context: object) -> str:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        time.sleep(0.01)
        active -= 1
        return context.child_id

    results = registry.run_child_group_from(
        point.replay_point_id,
        (
            ReplayChildTask(
                "serial-a",
                "candidate-a",
                ReplayFidelity.SEMANTIC_EQUIVALENT,
                execute,
            ),
            ReplayChildTask(
                "serial-b",
                "candidate-b",
                ReplayFidelity.SEMANTIC_EQUIVALENT,
                execute,
            ),
        ),
        parallel=True,
    )

    assert [result.outcome for result in results] == ["serial-a", "serial-b"]
    assert all(result.isolated and not result.parallel for result in results)
    assert max_active == 1
    metrics = registry.metrics()
    assert metrics["child_parallel_group_count"] == 0
    assert metrics["child_parallel_downgrade_group_count"] == 1
    registry.release(point.replay_point_id)


def test_isolated_child_group_rejects_reused_adapter_child_key() -> None:
    trace = TraceLog("trace-child-duplicate", "branch-main")
    agent_states = SyntheticChildAgentStateStore()
    adapter = SyntheticChildReplayAdapter(reuse_child_key=True)
    registry = ReplayPointRegistry(
        id_factory=lambda: "replay-child-duplicate",
        event_id_factory=lambda: "event-child-duplicate",
    )
    point = registry.capture_at_head(
        trace,
        _agent_cursor(),
        agent_state_store=agent_states,
        adapter=adapter,
    )

    with pytest.raises(ValueError, match="child keys must be unique"):
        registry.run_child_group_from(
            point.replay_point_id,
            (
                ReplayChildTask(
                    "duplicate-a",
                    "candidate-a",
                    ReplayFidelity.SEMANTIC_EQUIVALENT,
                    lambda _: None,
                ),
                ReplayChildTask(
                    "duplicate-b",
                    "candidate-b",
                    ReplayFidelity.SEMANTIC_EQUIVALENT,
                    lambda _: None,
                ),
            ),
            parallel=True,
        )

    assert adapter.active_children == set()
    assert registry.get(point.replay_point_id).lifecycle == ReplayPointLifecycle.READY
    metrics = registry.metrics()
    assert metrics["child_group_failure_count"] == 1
    assert metrics["child_session_fork_failure_count"] == 1
    registry.release(point.replay_point_id)


def test_isolated_child_execution_failure_releases_child_and_keeps_parent_ready() -> (
    None
):
    trace = TraceLog("trace-child-execution-failure", "branch-main")
    agent_states = SyntheticChildAgentStateStore()
    adapter = SyntheticChildReplayAdapter()
    registry = ReplayPointRegistry(
        id_factory=lambda: "replay-child-execution-failure",
        event_id_factory=lambda: "event-child-execution-failure",
    )
    point = registry.capture_at_head(
        trace,
        _agent_cursor(),
        agent_state_store=agent_states,
        adapter=adapter,
    )

    def fail_after_fork(_: object) -> None:
        raise SyntheticReplayError("isolated suffix failed")

    with pytest.raises(SyntheticReplayError, match="isolated suffix failed"):
        registry.run_child_group_from(
            point.replay_point_id,
            (
                ReplayChildTask(
                    "failing-child",
                    "candidate-failing-child",
                    ReplayFidelity.SEMANTIC_EQUIVALENT,
                    fail_after_fork,
                ),
            ),
        )

    assert adapter.active_children == set()
    assert registry.get(point.replay_point_id).lifecycle == ReplayPointLifecycle.READY
    metrics = registry.metrics()
    assert metrics["child_group_failure_count"] == 1
    assert metrics["child_session_fork_success_count"] == 1
    assert metrics["child_session_fork_failure_count"] == 0
    assert metrics["child_session_release_success_count"] == 1
    registry.release(point.replay_point_id)


def test_parallel_child_group_is_all_settled_when_one_suffix_fails() -> None:
    trace = TraceLog("trace-child-all-settled", "branch-main")
    agent_states = SyntheticChildAgentStateStore()
    adapter = SyntheticChildReplayAdapter()
    registry = ReplayPointRegistry(
        id_factory=lambda: "replay-child-all-settled",
        event_id_factory=lambda: "event-child-all-settled",
    )
    point = registry.capture_at_head(
        trace,
        _agent_cursor(),
        agent_state_store=agent_states,
        adapter=adapter,
    )
    barrier = threading.Barrier(2)
    completed: list[str] = []

    def fail(_: object) -> None:
        barrier.wait(timeout=2)
        completed.append("failed")
        raise SyntheticReplayError("parallel child failed")

    def succeed(_: object) -> str:
        barrier.wait(timeout=2)
        time.sleep(0.02)
        completed.append("succeeded")
        return "ok"

    with pytest.raises(SyntheticReplayError, match="parallel child failed"):
        registry.run_child_group_from(
            point.replay_point_id,
            (
                ReplayChildTask(
                    "all-settled-fail",
                    "candidate-fail",
                    ReplayFidelity.SEMANTIC_EQUIVALENT,
                    fail,
                ),
                ReplayChildTask(
                    "all-settled-success",
                    "candidate-success",
                    ReplayFidelity.SEMANTIC_EQUIVALENT,
                    succeed,
                ),
            ),
            parallel=True,
        )

    assert sorted(completed) == ["failed", "succeeded"]
    assert adapter.active_children == set()
    assert registry.get(point.replay_point_id).lifecycle == ReplayPointLifecycle.READY
    metrics = registry.metrics()
    assert metrics["child_group_failure_count"] == 1
    assert metrics["child_session_release_success_count"] == 2
    assert metrics["child_session_fork_failure_count"] == 0
    registry.release(point.replay_point_id)


def test_child_release_failure_quarantines_parent_without_duplicate_cleanup() -> None:
    trace = TraceLog("trace-child-release-failure", "branch-main")
    agent_states = SyntheticChildAgentStateStore()
    adapter = SyntheticChildReplayAdapter(
        fail_child_release_id="leaky-child",
    )
    registry = ReplayPointRegistry(
        id_factory=lambda: "replay-child-release-failure",
        event_id_factory=lambda: "event-child-release-failure",
    )
    point = registry.capture_at_head(
        trace,
        _agent_cursor(),
        agent_state_store=agent_states,
        adapter=adapter,
    )

    with pytest.raises(SyntheticReplayError, match="child release failed"):
        registry.run_child_group_from(
            point.replay_point_id,
            (
                ReplayChildTask(
                    "leaky-child",
                    "candidate-leaky-child",
                    ReplayFidelity.SEMANTIC_EQUIVALENT,
                    lambda _: "suffix-finished",
                ),
            ),
        )

    assert registry.get(point.replay_point_id).lifecycle == ReplayPointLifecycle.FAILED
    assert len(adapter.active_children) == 1
    assert agent_states.child_keys[0] in agent_states.released
    metrics = registry.metrics()
    assert metrics["child_session_release_attempt_count"] == 1
    assert metrics["child_session_release_failure_count"] == 1
    assert metrics["replay_point_quarantine_count"] == 1

    adapter.fail_child_release_id = None
    adapter.release_replay_child(next(iter(adapter.active_children)))
    registry.release(point.replay_point_id)


def test_intent_denial_blocks_suffix_without_quarantining_parent() -> None:
    trace = TraceLog("trace-intent-denied", "branch-main")
    agent_states = SyntheticChildAgentStateStore()
    adapter = SyntheticRiskAwareChildAdapter(deny_intent=True)
    registry = ReplayPointRegistry(
        id_factory=lambda: "replay-intent-denied",
        event_id_factory=lambda: "event-intent-denied",
    )
    point = registry.capture_at_head(
        trace,
        _agent_cursor(),
        agent_state_store=agent_states,
        adapter=adapter,
    )
    executed = False

    def execute(_: object) -> None:
        nonlocal executed
        executed = True

    with pytest.raises(ReplayRiskRejectedError, match="intent denied") as raised:
        registry.run_child_group_from(
            point.replay_point_id,
            (
                ReplayChildTask(
                    "intent-denied",
                    "candidate-intent-denied",
                    ReplayFidelity.SEMANTIC_EQUIVALENT,
                    execute,
                    intent={"operation": "bounded-candidate"},
                ),
            ),
        )

    assert raised.value.stage == "intent"
    assert executed is False
    assert len(adapter.intent_calls) == 1
    assert adapter.outcome_calls == []
    assert adapter.active_children == set()
    assert registry.get(point.replay_point_id).lifecycle == ReplayPointLifecycle.READY
    metrics = registry.metrics()
    assert metrics["child_intent_assessment_count"] == 1
    assert metrics["child_intent_denied_count"] == 1
    assert metrics["child_outcome_assessment_count"] == 0
    assert metrics["replay_point_quarantine_count"] == 0
    registry.release(point.replay_point_id)


def test_outcome_denial_cleans_child_then_quarantines_parent() -> None:
    trace = TraceLog("trace-outcome-denied", "branch-main")
    agent_states = SyntheticChildAgentStateStore()
    adapter = SyntheticRiskAwareChildAdapter(deny_outcome=True)
    registry = ReplayPointRegistry(
        id_factory=lambda: "replay-outcome-denied",
        event_id_factory=lambda: "event-outcome-denied",
    )
    point = registry.capture_at_head(
        trace,
        _agent_cursor(),
        agent_state_store=agent_states,
        adapter=adapter,
    )

    with pytest.raises(ReplayRiskRejectedError, match="outcome denied") as raised:
        registry.run_child_group_from(
            point.replay_point_id,
            (
                ReplayChildTask(
                    "outcome-denied",
                    "candidate-outcome-denied",
                    ReplayFidelity.SEMANTIC_EQUIVALENT,
                    lambda _: {"candidate_result": "unsafe"},
                    intent={"operation": "bounded-candidate"},
                ),
            ),
        )

    assert raised.value.stage == "outcome"
    assert len(adapter.intent_calls) == 1
    assert len(adapter.outcome_calls) == 1
    assert adapter.active_children == set()
    assert registry.get(point.replay_point_id).lifecycle == ReplayPointLifecycle.FAILED
    metrics = registry.metrics()
    assert metrics["child_intent_allowed_count"] == 1
    assert metrics["child_outcome_denied_count"] == 1
    assert metrics["child_session_release_success_count"] == 1
    assert metrics["replay_point_quarantine_count"] == 1
    registry.release(point.replay_point_id)


def test_allowed_risk_assessments_are_recorded_without_runtime_handles() -> None:
    trace = TraceLog("trace-risk-allowed", "branch-main")
    agent_states = SyntheticChildAgentStateStore()
    adapter = SyntheticRiskAwareChildAdapter()
    registry = ReplayPointRegistry(
        id_factory=lambda: "replay-risk-allowed",
        event_id_factory=lambda: "event-risk-allowed",
    )
    point = registry.capture_at_head(
        trace,
        _agent_cursor(),
        agent_state_store=agent_states,
        adapter=adapter,
    )

    result = registry.run_child_group_from(
        point.replay_point_id,
        (
            ReplayChildTask(
                "risk-allowed",
                "candidate-risk-allowed",
                ReplayFidelity.SEMANTIC_EQUIVALENT,
                lambda _: "safe-result",
                intent={"operation": "bounded-candidate"},
            ),
        ),
    )[0]

    record = result.to_record()
    assert record["intent_assessment"]["disposition"] == "allow"
    assert record["outcome_assessment"]["disposition"] == "allow"
    assert "adapter_child_key" not in record
    metrics = registry.metrics()
    assert metrics["child_intent_allowed_count"] == 1
    assert metrics["child_outcome_allowed_count"] == 1
    registry.release(point.replay_point_id)


def test_partial_child_risk_protocol_is_rejected_before_fork() -> None:
    class PartialRiskAdapter(SyntheticChildReplayAdapter):
        def assess_replay_child_intent(
            self,
            child_key: object,
            intent: Mapping[str, Any],
        ) -> ReplayRiskAssessment:
            raise AssertionError("must not be called")

    trace = TraceLog("trace-partial-risk", "branch-main")
    agent_states = SyntheticChildAgentStateStore()
    adapter = PartialRiskAdapter()
    registry = ReplayPointRegistry(
        id_factory=lambda: "replay-partial-risk",
        event_id_factory=lambda: "event-partial-risk",
    )
    point = registry.capture_at_head(
        trace,
        _agent_cursor(),
        agent_state_store=agent_states,
        adapter=adapter,
    )

    with pytest.raises(ValueError, match="must implement both protocol methods"):
        registry.run_child_group_from(
            point.replay_point_id,
            (
                ReplayChildTask(
                    "partial-risk",
                    "candidate-partial-risk",
                    ReplayFidelity.SEMANTIC_EQUIVALENT,
                    lambda _: None,
                ),
            ),
        )

    assert adapter.child_requests == []
    assert registry.get(point.replay_point_id).lifecycle == ReplayPointLifecycle.READY
    registry.release(point.replay_point_id)


def test_child_intent_rejects_sensitive_public_metadata() -> None:
    with pytest.raises(ValueError, match="sensitive state"):
        ReplayChildTask(
            "sensitive-intent",
            "candidate-sensitive-intent",
            ReplayFidelity.SEMANTIC_EQUIVALENT,
            lambda _: None,
            intent={"password": "must-not-cross-core-boundary"},
        )


def _planning_trace(event_count: int = 10) -> TraceLog:
    trace = TraceLog("trace-adaptive-planning", "branch-main")
    for sequence in range(event_count):
        trace.append(
            TraceEvent(
                f"planning-event-{sequence}",
                trace.trace_id,
                trace.branch_id,
                sequence,
                f"planning-event-{sequence - 1}" if sequence else None,
                TraceEventKind.ACTION_OUTCOME,
                {"status": "observed", "ordinal": sequence},
            )
        )
    return trace


def test_adaptive_planner_prefers_frequent_early_failure_boundary() -> None:
    trace = _planning_trace()
    candidates = (
        ReplayPositionCost(
            "planning-event-1",
            2,
            ReplayFidelity.SEMANTIC_EQUIVALENT,
            capture_minutes=0.2,
            restore_minutes=1.0,
        ),
        ReplayPositionCost(
            "planning-event-5",
            6,
            ReplayFidelity.SEMANTIC_EQUIVALENT,
            capture_minutes=0.2,
            restore_minutes=1.0,
        ),
        ReplayPositionCost(
            "planning-event-7",
            8,
            ReplayFidelity.BEST_EFFORT,
            capture_minutes=0.1,
            restore_minutes=0.5,
        ),
    )
    observations = (
        FailureReplayObservation(
            3,
            10,
            10.0,
            ReplayFidelity.SEMANTIC_EQUIVALENT,
            weight=6.0,
        ),
        FailureReplayObservation(
            9,
            10,
            10.0,
            ReplayFidelity.SEMANTIC_EQUIVALENT,
            weight=1.0,
        ),
    )

    proposals = plan_adaptive_replay_points(trace, candidates, observations)

    assert [proposal.next_sequence for proposal in proposals] == [2, 6]
    assert proposals[0].eligible_observation_weight == 7.0
    assert proposals[1].eligible_observation_weight == 1.0
    assert proposals[0].score_minutes > proposals[1].score_minutes
    assert all(proposal.next_sequence != 8 for proposal in proposals)


def test_adaptive_planner_moves_later_when_late_failures_dominate() -> None:
    trace = _planning_trace()
    candidates = (
        ReplayPositionCost(
            "planning-event-1",
            2,
            ReplayFidelity.SEMANTIC_EQUIVALENT,
            capture_minutes=0.2,
            restore_minutes=1.0,
        ),
        ReplayPositionCost(
            "planning-event-5",
            6,
            ReplayFidelity.SEMANTIC_EQUIVALENT,
            capture_minutes=0.2,
            restore_minutes=1.0,
        ),
    )
    observations = (
        FailureReplayObservation(
            3,
            10,
            10.0,
            ReplayFidelity.SEMANTIC_EQUIVALENT,
            weight=1.0,
        ),
        FailureReplayObservation(
            9,
            10,
            10.0,
            ReplayFidelity.SEMANTIC_EQUIVALENT,
            weight=4.0,
        ),
    )

    proposals = plan_adaptive_replay_points(trace, candidates, observations)

    assert proposals[0].next_sequence == 6
    assert proposals[0].expected_suffix_events == 4.0
    assert proposals[0].score_minutes > proposals[1].score_minutes


def test_adaptive_planner_filters_costly_and_invalid_positions() -> None:
    trace = _planning_trace()
    costly = ReplayPositionCost(
        "planning-event-3",
        4,
        ReplayFidelity.EXACT,
        capture_minutes=20.0,
        restore_minutes=1.0,
    )
    observation = FailureReplayObservation(
        5,
        10,
        10.0,
        ReplayFidelity.SEMANTIC_EQUIVALENT,
    )

    assert (
        plan_adaptive_replay_points(
            trace,
            (costly,),
            (observation,),
            minimum_score_minutes=0.0,
        )
        == ()
    )
    with pytest.raises(ValueError, match="does not match next_sequence"):
        plan_adaptive_replay_points(
            trace,
            (
                ReplayPositionCost(
                    "planning-event-1",
                    5,
                    ReplayFidelity.EXACT,
                    0.1,
                    0.1,
                ),
            ),
            (observation,),
        )


@pytest.mark.parametrize(
    "invalid_minutes",
    [True, False, float("nan"), float("inf"), float("-inf"), 10**400],
)
def test_replay_cost_rejects_non_finite_or_non_numeric_minutes(invalid_minutes) -> None:
    with pytest.raises(ValueError, match="capture_minutes must be a finite non-negative number"):
        ReplayPositionCost(
            "planning-event-1",
            2,
            ReplayFidelity.SEMANTIC_EQUIVALENT,
            capture_minutes=invalid_minutes,
            restore_minutes=0.0,
        )


def test_full_scope_composes_adaptive_children_risk_and_promotion() -> None:
    trace = TraceLog("trace-full-scope", "branch-main")
    prefix = TraceEvent(
        event_id="event-prefix",
        trace_id=trace.trace_id,
        branch_id=trace.branch_id,
        sequence=0,
        parent_event_id=None,
        kind=TraceEventKind.ACTION_OUTCOME,
        action_id="action-prefix",
        public_payload={"operation": "stable-prefix"},
    )
    trace.append(prefix)
    agent_states = SyntheticChildAgentStateStore()
    adapter = SyntheticRiskAwareChildAdapter()
    registry = ReplayPointRegistry(
        id_factory=lambda: "replay-full-scope",
        event_id_factory=lambda: "event-replay-full-scope",
    )
    point = registry.capture_at_head(
        trace,
        _agent_cursor(),
        agent_state_store=agent_states,
        adapter=adapter,
    )
    first_effect = TraceEvent(
        event_id="event-first-effect",
        trace_id=trace.trace_id,
        branch_id=trace.branch_id,
        sequence=2,
        parent_event_id="event-replay-full-scope",
        kind=TraceEventKind.ACTION_INTENT,
        action_id="action-change",
        public_payload={"operation": "candidate-change"},
    )
    trace.append(first_effect)
    trace.append(
        TraceEvent(
            event_id="event-validation",
            trace_id=trace.trace_id,
            branch_id=trace.branch_id,
            sequence=3,
            parent_event_id=first_effect.event_id,
            kind=TraceEventKind.ACTION_OUTCOME,
            action_id="action-change",
            causation_id=first_effect.event_id,
            public_payload={"operation": "candidate-validation"},
        )
    )
    trace.append(
        TraceEvent(
            event_id="event-terminal",
            trace_id=trace.trace_id,
            branch_id=trace.branch_id,
            sequence=4,
            parent_event_id="event-validation",
            kind=TraceEventKind.ENVIRONMENT_OBSERVATION,
            public_payload={"operation": "terminal-observation"},
        )
    )

    proposals = plan_adaptive_replay_points(
        trace,
        (
            ReplayPositionCost(
                after_event_id=None,
                next_sequence=0,
                achievable_fidelity=ReplayFidelity.SEMANTIC_EQUIVALENT,
                capture_minutes=0.0,
                restore_minutes=0.0,
            ),
            ReplayPositionCost(
                after_event_id=prefix.event_id,
                next_sequence=point.cursor.next_sequence,
                achievable_fidelity=point.fidelity,
                capture_minutes=0.01,
                restore_minutes=0.01,
            ),
            ReplayPositionCost(
                after_event_id=first_effect.event_id,
                next_sequence=3,
                achievable_fidelity=ReplayFidelity.EXACT,
                capture_minutes=0.0,
                restore_minutes=0.0,
            ),
        ),
        (
            FailureReplayObservation(
                first_effect_sequence=first_effect.sequence,
                terminal_sequence=len(trace.events),
                full_replay_minutes=10.0,
                minimum_fidelity=ReplayFidelity.SEMANTIC_EQUIVALENT,
            ),
        ),
    )
    assert proposals[0].next_sequence == point.cursor.next_sequence

    hypothesis = CounterfactualHypothesis(
        hypothesis_id="hypothesis-full-scope",
        first_affected_event_id=first_effect.event_id,
        first_affected_sequence=first_effect.sequence,
        fix_set=(ValidationTarget("target-fix", baseline_passed=False),),
        guard_set=(ValidationTarget("stable-guard", baseline_passed=True),),
    )
    denied_executed = False

    def denied_suffix(_: object, __: object) -> None:
        nonlocal denied_executed
        denied_executed = True

    group = run_counterfactual_child_group(
        registry,
        trace=trace,
        hypothesis=hypothesis,
        candidates=(
            CounterfactualChildCandidate(
                CounterfactualCandidate(
                    "candidate-promoted",
                    {"variant": "promoted"},
                ),
                "branch-promoted",
                lambda _context, _request: {
                    "target-fix": True,
                    "stable-guard": True,
                },
                intent={"operation": "bounded-candidate"},
            ),
            CounterfactualChildCandidate(
                CounterfactualCandidate(
                    "candidate-regressed",
                    {"variant": "regressed"},
                ),
                "branch-regressed",
                lambda _context, _request: {
                    "target-fix": True,
                    "stable-guard": False,
                },
                intent={"operation": "bounded-candidate"},
            ),
            CounterfactualChildCandidate(
                CounterfactualCandidate(
                    "candidate-denied",
                    {"variant": "denied"},
                ),
                "branch-denied",
                denied_suffix,
                intent={"operation": "denied-candidate"},
            ),
        ),
        parallel=True,
    )
    decisions = {
        candidate.candidate_id: candidate.replay_result.decision
        for candidate in group.candidates
        if candidate.replay_result is not None
    }
    assert decisions == {
        "candidate-promoted": CounterfactualDecision.PROMOTED,
        "candidate-regressed": CounterfactualDecision.REJECTED,
    }
    evaluated = [
        candidate.execution_result
        for candidate in group.candidates
        if candidate.execution_result is not None
    ]
    assert all(result.parallel for result in evaluated)
    assert all(result.intent_assessment is not None for result in evaluated)
    assert all(result.outcome_assessment is not None for result in evaluated)
    denied = group.candidates[2]
    assert denied.execution_failure is not None
    assert denied.execution_failure.stage == ReplayChildFailureStage.INTENT
    assert denied_executed is False
    record = group.to_record()
    assert record["candidate_count"] == 3
    assert record["evaluated_count"] == 2
    assert record["execution_failure_count"] == 1
    assert registry.get(point.replay_point_id).lifecycle == ReplayPointLifecycle.READY
    metrics = registry.metrics()
    assert metrics["child_parallel_group_count"] == 1
    assert metrics["child_group_settled_count"] == 1
    assert metrics["child_intent_allowed_count"] == 2
    assert metrics["child_intent_denied_count"] == 1
    assert metrics["child_outcome_allowed_count"] == 2
    registry.release(point.replay_point_id)


def test_settled_child_group_preserves_safe_sibling_after_intent_denial() -> None:
    trace = TraceLog("trace-child-settled", "branch-main")
    agent_states = SyntheticChildAgentStateStore()
    adapter = SyntheticRiskAwareChildAdapter()
    registry = ReplayPointRegistry(
        id_factory=lambda: "replay-child-settled",
        event_id_factory=lambda: "event-child-settled",
    )
    point = registry.capture_at_head(
        trace,
        _agent_cursor(),
        agent_state_store=agent_states,
        adapter=adapter,
    )
    executed: list[str] = []

    results = registry.run_child_group_settled_from(
        point.replay_point_id,
        (
            ReplayChildTask(
                "settled-safe",
                "branch-settled-safe",
                ReplayFidelity.SEMANTIC_EQUIVALENT,
                lambda _: executed.append("safe") or "safe-outcome",
                intent={"operation": "bounded-candidate"},
            ),
            ReplayChildTask(
                "settled-denied",
                "branch-settled-denied",
                ReplayFidelity.SEMANTIC_EQUIVALENT,
                lambda _: executed.append("denied"),
                intent={"operation": "denied-candidate"},
            ),
        ),
        parallel=True,
    )

    safe, denied = results
    assert isinstance(safe, ReplayChildExecutionResult)
    assert safe.outcome == "safe-outcome"
    assert isinstance(denied, ReplayChildExecutionFailure)
    assert denied.stage == ReplayChildFailureStage.INTENT
    assert denied.risk_assessment is not None
    assert denied.risk_assessment.disposition == ReplayRiskDisposition.DENY
    assert executed == ["safe"]
    assert adapter.active_children == set()
    assert registry.get(point.replay_point_id).lifecycle == ReplayPointLifecycle.READY
    record = denied.to_record()
    assert record["error_type"] == "ReplayRiskRejectedError"
    assert "adapter_child_key" not in record
    metrics = registry.metrics()
    assert metrics["child_group_settled_count"] == 1
    assert metrics["child_group_failure_count"] == 0
    assert metrics["child_candidate_success_count"] == 1
    assert metrics["child_candidate_failure_count"] == 1
    registry.release(point.replay_point_id)


def test_settled_child_group_still_quarantines_unsafe_outcome() -> None:
    trace = TraceLog("trace-child-settled-unsafe", "branch-main")
    agent_states = SyntheticChildAgentStateStore()
    adapter = SyntheticRiskAwareChildAdapter(deny_outcome=True)
    registry = ReplayPointRegistry(
        id_factory=lambda: "replay-child-settled-unsafe",
        event_id_factory=lambda: "event-child-settled-unsafe",
    )
    point = registry.capture_at_head(
        trace,
        _agent_cursor(),
        agent_state_store=agent_states,
        adapter=adapter,
    )

    with pytest.raises(ReplayRiskRejectedError) as denied:
        registry.run_child_group_settled_from(
            point.replay_point_id,
            (
                ReplayChildTask(
                    "settled-unsafe",
                    "branch-settled-unsafe",
                    ReplayFidelity.SEMANTIC_EQUIVALENT,
                    lambda _: "unsafe-outcome",
                    intent={"operation": "bounded-candidate"},
                ),
            ),
        )

    assert denied.value.stage == "outcome"
    assert adapter.active_children == set()
    assert registry.get(point.replay_point_id).lifecycle == ReplayPointLifecycle.FAILED
    metrics = registry.metrics()
    assert metrics["child_group_settled_count"] == 0
    assert metrics["child_group_failure_count"] == 1
    assert metrics["replay_point_quarantine_count"] == 1
    registry.release(point.replay_point_id)
