from __future__ import annotations

import threading
import time

import pytest

from loopx.capabilities.explore.counterfactual_runtime import (
    CounterfactualCandidate,
    CounterfactualDecision,
    CounterfactualHypothesis,
    ValidationOutcome,
    ValidationTarget,
    run_counterfactual_suffix_replay,
)
from loopx.capabilities.explore.replay_runtime import (
    AdapterStateLease,
    CaptureReplayStateRequest,
    EquivalenceCheck,
    EquivalenceReport,
    ReplayFidelity,
    ReplayPointLifecycle,
    ReplayPointRegistry,
    RestoreReceipt,
    RestoreReplayStateRequest,
)
from loopx.capabilities.explore.replay_metrics import (
    summarize_counterfactual_results,
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
        prefix_reused: bool | None = None,
    ) -> None:
        self.fidelity = fidelity
        self.fail_capture = fail_capture
        self.fail_release = fail_release
        self.equivalent = equivalent
        self.restored_fidelity = restored_fidelity or fidelity
        self.observed_digest = observed_digest
        self.prefix_reused = prefix_reused
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
            projection_digest="public-safe-state-v1",
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
            prefix_reused=self.prefix_reused,
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


def _agent_cursor(index: int = 1) -> AgentCursor:
    return AgentCursor(
        history_index=index,
        history_digest=f"history-{index}",
        context_digest=f"context-{index}",
    )


def _capture_point(
    *,
    trace_id: str = "trace-counterfactual",
    adapter: SyntheticReplayAdapter | None = None,
    point_id: str = "replay-before-change",
    event_id: str = "event-point",
) -> tuple[
    TraceLog, SyntheticAgentStateStore, SyntheticReplayAdapter, ReplayPointRegistry
]:
    trace = TraceLog(trace_id, "branch-main")
    trace.append(
        TraceEvent(
            event_id="event-prefix",
            trace_id=trace_id,
            branch_id="branch-main",
            sequence=0,
            parent_event_id=None,
            kind=TraceEventKind.ACTION_OUTCOME,
            action_id="prepare-stable-prefix",
            public_payload={"status": "ok"},
        )
    )
    agent_states = SyntheticAgentStateStore()
    replay_adapter = adapter or SyntheticReplayAdapter()
    registry = ReplayPointRegistry(
        id_factory=lambda: point_id,
        event_id_factory=lambda: event_id,
    )
    registry.capture_at_head(
        trace,
        _agent_cursor(6),
        agent_state_store=agent_states,
        adapter=replay_adapter,
    )
    trace.append(
        TraceEvent(
            event_id="event-first-failure",
            trace_id=trace_id,
            branch_id="branch-main",
            sequence=2,
            parent_event_id=event_id,
            kind=TraceEventKind.ACTION_OUTCOME,
            action_id="first-affected-action",
            public_payload={"status": "failed"},
        )
    )
    return trace, agent_states, replay_adapter, registry


def _hypothesis() -> CounterfactualHypothesis:
    return CounterfactualHypothesis(
        hypothesis_id="optional-argument-fix",
        first_affected_event_id="event-first-failure",
        first_affected_sequence=2,
        fix_set=(
            ValidationTarget("target-created", baseline_passed=False),
            ValidationTarget("dependent-update", baseline_passed=False),
        ),
        guard_set=(
            ValidationTarget("constraint-health", baseline_passed=True),
            ValidationTarget("resource-cleanup", baseline_passed=True),
        ),
    )


def test_counterfactual_promotes_only_when_fixes_and_guards_pass() -> None:
    trace, agent_states, _, registry = _capture_point()
    result = run_counterfactual_suffix_replay(
        registry,
        trace=trace,
        hypothesis=_hypothesis(),
        candidate=CounterfactualCandidate(
            candidate_id="use-none",
            public_change_set={"optional_argument": "none"},
        ),
        execute_suffix=lambda request: {
            "target-created": request.public_change_set["optional_argument"] == "none",
            "dependent-update": True,
            "constraint-health": True,
            "resource-cleanup": True,
        },
    )

    assert result.decision == CounterfactualDecision.PROMOTED
    assert result.promoted is True
    assert result.failed_fixes == ()
    assert result.regressed_guards == ()
    assert len(agent_states.restored) == 1


def test_counterfactual_rejects_functional_fix_with_cleanup_regression() -> None:
    trace, _, _, registry = _capture_point()
    result = run_counterfactual_suffix_replay(
        registry,
        trace=trace,
        hypothesis=_hypothesis(),
        candidate=CounterfactualCandidate(
            candidate_id="leaky-fix",
            public_change_set={"optional_argument": "none"},
        ),
        execute_suffix=lambda _: {
            "target-created": True,
            "dependent-update": True,
            "constraint-health": True,
            "resource-cleanup": False,
        },
    )

    assert result.decision == CounterfactualDecision.REJECTED
    assert result.regressed_guards == ("resource-cleanup",)
    assert "guard" in result.reason


@pytest.mark.parametrize(
    ("outcomes", "expected_missing", "expected_failed"),
    [
        (
            {
                "target-created": False,
                "dependent-update": True,
                "constraint-health": True,
                "resource-cleanup": True,
            },
            (),
            ("target-created",),
        ),
        (
            {
                "target-created": True,
                "constraint-health": True,
                "resource-cleanup": True,
            },
            ("dependent-update",),
            (),
        ),
    ],
)
def test_counterfactual_rejects_failed_or_missing_fix_evidence(
    outcomes: dict[str, bool],
    expected_missing: tuple[str, ...],
    expected_failed: tuple[str, ...],
) -> None:
    trace, _, _, registry = _capture_point()
    result = run_counterfactual_suffix_replay(
        registry,
        trace=trace,
        hypothesis=_hypothesis(),
        candidate=CounterfactualCandidate(
            candidate_id="incomplete-fix",
            public_change_set={"optional_argument": "none"},
        ),
        execute_suffix=lambda _: outcomes,
    )
    assert result.decision == CounterfactualDecision.REJECTED
    assert result.missing_validations == expected_missing
    assert result.failed_fixes == expected_failed


def test_best_effort_replay_is_observed_but_never_promoted() -> None:
    adapter = SyntheticReplayAdapter(
        fidelity=ReplayFidelity.BEST_EFFORT,
        equivalent=False,
    )
    trace, agent_states, _, registry = _capture_point(adapter=adapter)
    result = run_counterfactual_suffix_replay(
        registry,
        trace=trace,
        hypothesis=_hypothesis(),
        candidate=CounterfactualCandidate(
            candidate_id="unverified",
            public_change_set={"optional_argument": "none"},
        ),
        execute_suffix=lambda _: {
            "target-created": True,
            "dependent-update": True,
            "constraint-health": True,
            "resource-cleanup": True,
        },
    )

    assert result.decision == CounterfactualDecision.OBSERVED_ONLY
    assert result.restore.promotion_eligible is False
    assert len(agent_states.restored) == 1
    metrics = registry.metrics()
    assert metrics["replay_point_selection_count"] == 1
    assert metrics["replay_point_fidelity_fallback_count"] == 1


def test_exact_restore_rejects_mismatched_state_digest_before_suffix() -> None:
    adapter = SyntheticReplayAdapter(
        fidelity=ReplayFidelity.EXACT,
        observed_digest="different-state",
    )
    trace, agent_states, _, registry = _capture_point(adapter=adapter)
    suffix_called = False

    def execute_suffix(_: object) -> dict[str, bool]:
        nonlocal suffix_called
        suffix_called = True
        return {}

    with pytest.raises(ValueError, match="exact replay requires equal"):
        run_counterfactual_suffix_replay(
            registry,
            trace=trace,
            hypothesis=_hypothesis(),
            candidate=CounterfactualCandidate(
                candidate_id="bad-exact",
                public_change_set={"optional_argument": "none"},
            ),
            execute_suffix=execute_suffix,
        )
    assert suffix_called is False
    assert agent_states.restored == []


def test_semantic_restore_requires_verified_equivalence_before_suffix() -> None:
    adapter = SyntheticReplayAdapter(equivalent=False)
    trace, agent_states, _, registry = _capture_point(adapter=adapter)
    suffix_called = False

    def execute_suffix(_: object) -> dict[str, bool]:
        nonlocal suffix_called
        suffix_called = True
        return {}

    with pytest.raises(ValueError, match="requires verified state equivalence"):
        run_counterfactual_suffix_replay(
            registry,
            trace=trace,
            hypothesis=_hypothesis(),
            candidate=CounterfactualCandidate(
                candidate_id="bad-semantic",
                public_change_set={"optional_argument": "none"},
            ),
            execute_suffix=execute_suffix,
        )
    assert suffix_called is False
    assert agent_states.restored == []


def test_public_validation_summary_rejects_adapter_owned_path() -> None:
    with pytest.raises(ValueError, match="absolute paths"):
        ValidationOutcome(
            validation_id="unsafe-summary",
            passed=False,
            public_summary=r"D:\private\raw-state.json",
        )


def test_replay_point_selection_uses_nearest_trusted_point_before_effect() -> None:
    replay_ids = iter(("replay-early", "replay-nearest"))
    event_ids = iter(("event-early-point", "event-nearest-point"))
    trace = TraceLog("trace-counterfactual", "branch-main")
    trace.append(
        TraceEvent(
            event_id="event-prefix",
            trace_id="trace-counterfactual",
            branch_id="branch-main",
            sequence=0,
            parent_event_id=None,
            kind=TraceEventKind.ACTION_OUTCOME,
            public_payload={"status": "ok"},
        )
    )
    registry = ReplayPointRegistry(
        id_factory=lambda: next(replay_ids),
        event_id_factory=lambda: next(event_ids),
    )
    registry.capture_at_head(
        trace,
        _agent_cursor(4),
        agent_state_store=SyntheticAgentStateStore(),
        adapter=SyntheticReplayAdapter(),
    )
    trace.append(
        TraceEvent(
            event_id="event-stable-middle",
            trace_id="trace-counterfactual",
            branch_id="branch-main",
            sequence=2,
            parent_event_id="event-early-point",
            kind=TraceEventKind.ACTION_OUTCOME,
            public_payload={"status": "ok"},
        )
    )
    late_states = SyntheticAgentStateStore()
    late_adapter = SyntheticReplayAdapter()
    registry.capture_at_head(
        trace,
        _agent_cursor(8),
        agent_state_store=late_states,
        adapter=late_adapter,
    )

    selected = registry.select_before(
        trace_id="trace-counterfactual",
        branch_id="branch-main",
        first_affected_sequence=4,
        minimum_fidelity=ReplayFidelity.SEMANTIC_EQUIVALENT,
    )
    assert selected.replay_point_id == "replay-nearest"
    assert selected.cursor.next_sequence == 3
    metrics = registry.metrics()
    assert metrics["replay_point_selection_count"] == 1
    assert metrics["replay_point_fidelity_fallback_count"] == 0
    assert metrics["replay_point_fidelity_filtered_selection_count"] == 0


def test_replay_point_selection_skips_nearer_ineligible_fidelity() -> None:
    replay_ids = iter(("replay-trusted-early", "replay-best-effort-near"))
    event_ids = iter(("event-trusted-early", "event-best-effort-near"))
    trace = TraceLog("trace-fidelity-selection", "branch-main")
    trace.append(
        TraceEvent(
            event_id="event-prefix",
            trace_id=trace.trace_id,
            branch_id=trace.branch_id,
            sequence=0,
            parent_event_id=None,
            kind=TraceEventKind.ACTION_OUTCOME,
            public_payload={"status": "ok"},
        )
    )
    registry = ReplayPointRegistry(
        id_factory=lambda: next(replay_ids),
        event_id_factory=lambda: next(event_ids),
    )
    early = registry.capture_at_head(
        trace,
        _agent_cursor(2),
        agent_state_store=SyntheticAgentStateStore(),
        adapter=SyntheticReplayAdapter(),
    )
    trace.append(
        TraceEvent(
            event_id="event-stable-middle",
            trace_id=trace.trace_id,
            branch_id=trace.branch_id,
            sequence=2,
            parent_event_id="event-trusted-early",
            kind=TraceEventKind.ACTION_OUTCOME,
            public_payload={"status": "ok"},
        )
    )
    registry.capture_at_head(
        trace,
        _agent_cursor(4),
        agent_state_store=SyntheticAgentStateStore(),
        adapter=SyntheticReplayAdapter(
            fidelity=ReplayFidelity.BEST_EFFORT,
        ),
    )

    selected = registry.select_before(
        trace_id=trace.trace_id,
        branch_id=trace.branch_id,
        first_affected_sequence=4,
        minimum_fidelity=ReplayFidelity.SEMANTIC_EQUIVALENT,
    )

    assert selected.replay_point_id == early.replay_point_id
    metrics = registry.metrics()
    assert metrics["replay_point_selection_count"] == 1
    assert metrics["replay_point_fidelity_fallback_count"] == 0
    assert metrics["replay_point_fidelity_filtered_selection_count"] == 1


def test_counterfactual_rejects_unaddressable_first_effect() -> None:
    trace, _, _, registry = _capture_point()
    invalid = CounterfactualHypothesis(
        hypothesis_id="bad-address",
        first_affected_event_id="missing-event",
        first_affected_sequence=2,
        fix_set=(ValidationTarget("fix", baseline_passed=False),),
        guard_set=(ValidationTarget("guard", baseline_passed=True),),
    )
    with pytest.raises(KeyError, match="unknown trace event_id"):
        run_counterfactual_suffix_replay(
            registry,
            trace=trace,
            hypothesis=invalid,
            candidate=CounterfactualCandidate(
                candidate_id="candidate",
                public_change_set={"change": "bounded"},
            ),
            execute_suffix=lambda _: {"fix": True, "guard": True},
        )


def test_structural_suffix_must_restore_shared_prefix_before_next_candidate() -> None:
    adapter = SyntheticReplayAdapter(prefix_reused=True)
    trace, _, _, registry = _capture_point(adapter=adapter)
    shared_structure = ["captured-prefix"]
    hypothesis = CounterfactualHypothesis(
        hypothesis_id="structural-terminal-feature",
        first_affected_event_id="event-first-failure",
        first_affected_sequence=2,
        fix_set=(ValidationTarget("feature-created", baseline_passed=False),),
        guard_set=(
            ValidationTarget("allowed-feature-type", baseline_passed=True),
            ValidationTarget("prefix-rolled-back", baseline_passed=True),
        ),
    )

    def execute_suffix(request: object) -> dict[str, bool]:
        assert shared_structure == ["captured-prefix"]
        feature_type = str(request.public_change_set["feature_type"])
        shared_structure.append(feature_type)
        created = shared_structure[-1] == feature_type
        allowed = feature_type == "accepted-terminal"
        shared_structure.pop()
        return {
            "feature-created": created,
            "allowed-feature-type": allowed,
            "prefix-rolled-back": shared_structure == ["captured-prefix"],
        }

    promoted = run_counterfactual_suffix_replay(
        registry,
        trace=trace,
        hypothesis=hypothesis,
        candidate=CounterfactualCandidate(
            candidate_id="accepted-structure",
            public_change_set={"feature_type": "accepted-terminal"},
        ),
        execute_suffix=execute_suffix,
    )
    rejected = run_counterfactual_suffix_replay(
        registry,
        trace=trace,
        hypothesis=hypothesis,
        candidate=CounterfactualCandidate(
            candidate_id="wrong-structure",
            public_change_set={"feature_type": "wrong-terminal"},
        ),
        execute_suffix=execute_suffix,
    )

    assert promoted.decision == CounterfactualDecision.PROMOTED
    assert rejected.decision == CounterfactualDecision.REJECTED
    assert rejected.regressed_guards == ("allowed-feature-type",)
    assert shared_structure == ["captured-prefix"]
    assert registry.metrics()["prefix_reuse_verified_count"] == 2


def test_optional_suffix_finalizer_recovers_partial_failure_before_retry() -> None:
    class FinalizingAdapter(SyntheticReplayAdapter):
        def __init__(self) -> None:
            super().__init__(prefix_reused=True)
            self.shared_structure = ["captured-prefix"]
            self.pending_suffix = False
            self.finalizer_success_flags: list[bool] = []

        def restore_replay_state(
            self,
            binding_key: object,
            request: RestoreReplayStateRequest,
        ) -> RestoreReceipt:
            assert self.shared_structure == ["captured-prefix"]
            return super().restore_replay_state(binding_key, request)

        def finalize_replay_suffix(
            self,
            binding_key: object,
            request: RestoreReplayStateRequest,
            *,
            suffix_succeeded: bool,
        ) -> None:
            assert binding_key in self.active_bindings
            assert request.replay_point_id == "replay-before-change"
            if self.pending_suffix:
                self.shared_structure.pop()
                self.pending_suffix = False
            self.finalizer_success_flags.append(suffix_succeeded)

    adapter = FinalizingAdapter()
    trace, _, _, registry = _capture_point(adapter=adapter)
    hypothesis = _hypothesis()

    def failing_suffix(_: object) -> dict[str, bool]:
        adapter.shared_structure.append("partial-terminal-feature")
        adapter.pending_suffix = True
        raise SyntheticReplayError("suffix failed after partial mutation")

    with pytest.raises(SyntheticReplayError, match="partial mutation"):
        run_counterfactual_suffix_replay(
            registry,
            trace=trace,
            hypothesis=hypothesis,
            candidate=CounterfactualCandidate(
                candidate_id="partial-failure",
                public_change_set={"optional_argument": "none"},
            ),
            execute_suffix=failing_suffix,
        )
    assert adapter.shared_structure == ["captured-prefix"]

    def successful_retry(_: object) -> dict[str, bool]:
        adapter.shared_structure.append("accepted-terminal-feature")
        adapter.pending_suffix = True
        return {
            "target-created": True,
            "dependent-update": True,
            "constraint-health": True,
            "resource-cleanup": True,
        }

    result = run_counterfactual_suffix_replay(
        registry,
        trace=trace,
        hypothesis=hypothesis,
        candidate=CounterfactualCandidate(
            candidate_id="retry-after-cleanup",
            public_change_set={"optional_argument": "none"},
        ),
        execute_suffix=successful_retry,
    )
    metrics = registry.metrics()
    assert result.decision == CounterfactualDecision.PROMOTED
    assert adapter.shared_structure == ["captured-prefix"]
    assert adapter.finalizer_success_flags == [False, True]
    assert metrics["suffix_failure_count"] == 1
    assert metrics["suffix_success_count"] == 1
    assert metrics["suffix_finalization_attempt_count"] == 2
    assert metrics["suffix_finalization_success_count"] == 2
    assert metrics["suffix_finalization_failure_count"] == 0


def test_finalizer_failure_quarantines_point_and_preserves_suffix_error() -> None:
    class CleanupFailure(SyntheticReplayError):
        pass

    class FailingFinalizerAdapter(SyntheticReplayAdapter):
        def __init__(self) -> None:
            super().__init__(prefix_reused=True)
            self.pending_suffix = False

        def finalize_replay_suffix(
            self,
            binding_key: object,
            request: RestoreReplayStateRequest,
            *,
            suffix_succeeded: bool,
        ) -> None:
            assert binding_key in self.active_bindings
            assert request.replay_point_id == "replay-before-change"
            assert suffix_succeeded is False
            assert self.pending_suffix is True
            raise CleanupFailure("partial suffix cleanup failed")

    adapter = FailingFinalizerAdapter()
    trace, _, _, registry = _capture_point(adapter=adapter)

    def failing_suffix(_: object) -> dict[str, bool]:
        adapter.pending_suffix = True
        raise SyntheticReplayError("original suffix failure")

    with pytest.raises(SyntheticReplayError, match="original suffix failure") as raised:
        run_counterfactual_suffix_replay(
            registry,
            trace=trace,
            hypothesis=_hypothesis(),
            candidate=CounterfactualCandidate(
                candidate_id="dirty-failure",
                public_change_set={"optional_argument": "none"},
            ),
            execute_suffix=failing_suffix,
        )

    assert isinstance(raised.value.__cause__, CleanupFailure)
    assert registry.get("replay-before-change").lifecycle == ReplayPointLifecycle.FAILED
    with pytest.raises(LookupError, match="no replayable ReplayPoint"):
        run_counterfactual_suffix_replay(
            registry,
            trace=trace,
            hypothesis=_hypothesis(),
            candidate=CounterfactualCandidate(
                candidate_id="must-not-retry",
                public_change_set={"optional_argument": "none"},
            ),
            execute_suffix=lambda _: {},
        )

    released = registry.release("replay-before-change")
    metrics = registry.metrics()
    assert released.lifecycle == ReplayPointLifecycle.RELEASED
    assert metrics["suffix_finalization_failure_count"] == 1
    assert metrics["replay_point_quarantine_count"] == 1
    assert metrics["release_success_count"] == 1


def test_release_waits_for_active_suffix_before_adapter_cleanup() -> None:
    trace, _, adapter, registry = _capture_point()
    suffix_started = threading.Event()
    allow_suffix_finish = threading.Event()

    def execute_suffix(_: object) -> dict[str, bool]:
        suffix_started.set()
        assert allow_suffix_finish.wait(timeout=3.0)
        assert adapter.active_bindings
        return {
            "target-created": True,
            "dependent-update": True,
            "constraint-health": True,
            "resource-cleanup": True,
        }

    replay_thread = threading.Thread(
        target=lambda: run_counterfactual_suffix_replay(
            registry,
            trace=trace,
            hypothesis=_hypothesis(),
            candidate=CounterfactualCandidate(
                candidate_id="serial-suffix",
                public_change_set={"optional_argument": "none"},
            ),
            execute_suffix=execute_suffix,
        )
    )
    replay_thread.start()
    assert suffix_started.wait(timeout=3.0)
    release_thread = threading.Thread(
        target=lambda: registry.release("replay-before-change")
    )
    release_thread.start()
    time.sleep(0.05)
    assert release_thread.is_alive()
    assert adapter.release_calls == []

    allow_suffix_finish.set()
    replay_thread.join(timeout=3.0)
    release_thread.join(timeout=3.0)
    assert not replay_thread.is_alive()
    assert not release_thread.is_alive()
    assert adapter.active_bindings == set()


def test_hypothesis_rejects_invalid_baseline_fix_or_guard_sets() -> None:
    with pytest.raises(ValueError, match="fix_set targets must fail"):
        CounterfactualHypothesis(
            hypothesis_id="invalid",
            first_affected_event_id="event-1",
            first_affected_sequence=1,
            fix_set=(ValidationTarget("not-a-failure", baseline_passed=True),),
            guard_set=(ValidationTarget("guard", baseline_passed=True),),
        )


def test_replay_runtime_and_counterfactual_metrics_are_public_and_recomputable() -> (
    None
):
    promoted_trace, _, _, promoted_registry = _capture_point(
        trace_id="trace-metrics-promoted",
        adapter=SyntheticReplayAdapter(prefix_reused=True),
        point_id="replay-metrics-promoted",
        event_id="event-metrics-promoted",
    )
    promoted = run_counterfactual_suffix_replay(
        promoted_registry,
        trace=promoted_trace,
        hypothesis=_hypothesis(),
        candidate=CounterfactualCandidate(
            candidate_id="candidate-promoted",
            public_change_set={"optional_argument": "none"},
        ),
        execute_suffix=lambda _: {
            "target-created": True,
            "dependent-update": True,
            "constraint-health": True,
            "resource-cleanup": True,
        },
    )
    runtime_before_release = promoted_registry.metrics()
    assert runtime_before_release["capture_success_count"] == 1
    assert runtime_before_release["replay_success_count"] == 1
    assert runtime_before_release["restore_success_count"] == 1
    assert runtime_before_release["prefix_reuse_verified_count"] == 1
    assert runtime_before_release["prefix_reconstruction_count"] == 0
    assert runtime_before_release["prefix_reuse_unknown_count"] == 0
    assert runtime_before_release["equivalence_verified_count"] == 1
    assert runtime_before_release["suffix_success_count"] == 1
    assert runtime_before_release["active_adapter_binding_count"] == 1
    assert runtime_before_release["restored_fidelity_counts"] == {
        "semantic_equivalent": 1
    }

    rejected_trace, _, _, rejected_registry = _capture_point(
        trace_id="trace-metrics-rejected",
        adapter=SyntheticReplayAdapter(prefix_reused=False),
        point_id="replay-metrics-rejected",
        event_id="event-metrics-rejected",
    )
    rejected = run_counterfactual_suffix_replay(
        rejected_registry,
        trace=rejected_trace,
        hypothesis=_hypothesis(),
        candidate=CounterfactualCandidate(
            candidate_id="candidate-rejected",
            public_change_set={"optional_argument": "none"},
        ),
        execute_suffix=lambda _: {
            "target-created": True,
            "dependent-update": True,
            "constraint-health": True,
            "resource-cleanup": False,
        },
    )
    summary = summarize_counterfactual_results(
        [promoted, rejected],
        fresh_compute_minutes_by_candidate={
            "candidate-promoted": 1.0,
            "candidate-rejected": 1.0,
        },
    )
    assert summary["counterfactual_attempt_count"] == 2
    assert summary["promotion_count"] == 1
    assert summary["rejection_count"] == 1
    assert summary["guard_regression_count"] == 1
    assert summary["addressed_prefix_event_count"] == 2
    assert summary["avoided_prefix_event_count"] == 2
    assert summary["verified_reused_prefix_event_count"] == 1
    assert summary["prefix_reuse_counts"] == {
        "reconstructed": 1,
        "verified_reuse": 1,
    }
    assert summary["replay_distance_event_count"] == 2
    assert summary["fresh_compute_sample_count"] == 2
    assert summary["estimated_fresh_compute_minutes"] == 2.0
    assert summary["estimated_avoided_compute_minutes"] > 1.9
    assert summary["promoted_avoided_compute_minutes"] > 0.9

    promoted_registry.release("replay-metrics-promoted")
    runtime_after_release = promoted_registry.metrics()
    assert runtime_after_release["release_success_count"] == 1
    assert runtime_after_release["active_replay_point_count"] == 0
    assert runtime_after_release["active_adapter_binding_count"] == 0


def test_base_exception_during_restore_or_suffix_finalizes_with_failed_flag() -> None:
    class _FinalizerSpyAdapter(SyntheticReplayAdapter):
        def __init__(self) -> None:
            super().__init__(prefix_reused=True)
            self.finalizer_flags: list[bool] = []

        def finalize_replay_suffix(
            self,
            binding_key: object,
            request: RestoreReplayStateRequest,
            *,
            suffix_succeeded: bool,
        ) -> None:
            assert binding_key in self.active_bindings
            assert request.replay_point_id == "replay-before-change"
            self.finalizer_flags.append(suffix_succeeded)

    # 1) KeyboardInterrupt inside restore_agent_state must pass suffix_succeeded=False and propagate.
    adapter_restore = _FinalizerSpyAdapter()
    trace_restore, store_restore, _, reg_restore = _capture_point(
        adapter=adapter_restore
    )
    orig_restore = store_restore.restore_agent_state
    interrupt_restore = True

    def _interrupting_agent_restore(state_key: object) -> None:
        if interrupt_restore:
            raise KeyboardInterrupt("aborted agent state restore")
        orig_restore(state_key)

    setattr(store_restore, "restore_agent_state", _interrupting_agent_restore)
    suffix_calls = 0

    def _count_suffix(_: object) -> dict[str, bool]:
        nonlocal suffix_calls
        suffix_calls += 1
        return {
            "target-created": True,
            "dependent-update": True,
            "constraint-health": True,
            "resource-cleanup": True,
        }

    with pytest.raises(KeyboardInterrupt, match="aborted agent state restore"):
        run_counterfactual_suffix_replay(
            reg_restore,
            trace=trace_restore,
            hypothesis=_hypothesis(),
            candidate=CounterfactualCandidate(
                candidate_id="interrupt-agent-restore",
                public_change_set={"optional_argument": "none"},
            ),
            execute_suffix=_count_suffix,
        )

    assert suffix_calls == 0
    assert adapter_restore.finalizer_flags == [False]
    assert reg_restore.get("replay-before-change").lifecycle == ReplayPointLifecycle.READY

    # 2) KeyboardInterrupt inside suffix execution must also pass suffix_succeeded=False and propagate.
    def _interrupting_suffix(_: object) -> dict[str, bool]:
        nonlocal suffix_calls
        suffix_calls += 1
        raise KeyboardInterrupt("aborted suffix execution")

    with pytest.raises(KeyboardInterrupt, match="aborted suffix execution"):
        interrupt_restore = False
        run_counterfactual_suffix_replay(
            reg_restore,
            trace=trace_restore,
            hypothesis=_hypothesis(),
            candidate=CounterfactualCandidate(
                candidate_id="interrupt-suffix",
                public_change_set={"optional_argument": "none"},
            ),
            execute_suffix=_interrupting_suffix,
        )

    assert suffix_calls == 1
    assert adapter_restore.finalizer_flags == [False, False]
    assert reg_restore.get("replay-before-change").lifecycle == ReplayPointLifecycle.READY

    # Point remains healthy and succeeds once no longer interrupted.
    promoted = run_counterfactual_suffix_replay(
        reg_restore,
        trace=trace_restore,
        hypothesis=_hypothesis(),
        candidate=CounterfactualCandidate(
            candidate_id="retry-after-interrupt",
            public_change_set={"optional_argument": "none"},
        ),
        execute_suffix=_count_suffix,
    )
    assert promoted.decision == CounterfactualDecision.PROMOTED
    assert suffix_calls == 2
    assert adapter_restore.finalizer_flags == [False, False, True]


@pytest.mark.parametrize("interrupt_stage", ["restore_agent_state", "execute"])
def test_base_exception_and_failing_finalizer_quarantines_point_with_cause(
    interrupt_stage: str,
) -> None:
    class _FailingFinalizerSyntheticAdapter(SyntheticReplayAdapter):
        def finalize_replay_suffix(
            self,
            binding_key: object,
            request: RestoreReplayStateRequest,
            *,
            suffix_succeeded: bool,
        ) -> None:
            assert binding_key in self.active_bindings
            assert request.replay_point_id == "replay-before-change"
            assert suffix_succeeded is False
            raise RuntimeError("finalizer failed during unwind")

    adapter = _FailingFinalizerSyntheticAdapter(prefix_reused=True)
    trace, store, _, registry = _capture_point(adapter=adapter)
    if interrupt_stage == "restore_agent_state":

        def _interrupt_agent_restore(_state_key: object) -> None:
            raise KeyboardInterrupt("aborted during agent restore")

        setattr(store, "restore_agent_state", _interrupt_agent_restore)

    def _suffix(_: object) -> dict[str, bool]:
        if interrupt_stage == "execute":
            raise KeyboardInterrupt("aborted during suffix execute")
        return {
            "target-created": True,
            "dependent-update": True,
            "constraint-health": True,
            "resource-cleanup": True,
        }

    with pytest.raises(KeyboardInterrupt, match="aborted during") as exc_info:
        run_counterfactual_suffix_replay(
            registry,
            trace=trace,
            hypothesis=_hypothesis(),
            candidate=CounterfactualCandidate(
                candidate_id=f"interrupt-plus-finalizer-{interrupt_stage}",
                public_change_set={"optional_argument": "none"},
            ),
            execute_suffix=_suffix,
        )

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert "finalizer failed during unwind" in str(exc_info.value.__cause__)
    assert registry.get("replay-before-change").lifecycle == ReplayPointLifecycle.FAILED
    metrics = registry.metrics()
    assert metrics["replay_point_quarantine_count"] == 1
    assert metrics["suffix_finalization_failure_count"] == 1
    assert metrics["replay_failure_count"] == 0
    assert metrics["suffix_failure_count"] == (1 if interrupt_stage == "execute" else 0)
    assert len(adapter.active_bindings) == 1

    released = registry.release("replay-before-change")
    assert released.lifecycle == ReplayPointLifecycle.RELEASED
    assert adapter.active_bindings == set()
    after_release = registry.metrics()
    assert after_release["release_success_count"] == 1
    assert after_release["failed_replay_point_count"] == 0


def test_suffix_minutes_includes_finalizer_duration_for_existing_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [time.perf_counter()]
    monkeypatch.setattr(time, "perf_counter", lambda: clock[0])

    class _TimedFinalizerAdapter(SyntheticReplayAdapter):
        def __init__(self, *, fail_restore: bool = False) -> None:
            super().__init__(prefix_reused=True)
            self.fail_restore = fail_restore

        def restore_replay_state(
            self,
            binding_key: object,
            request: RestoreReplayStateRequest,
        ) -> RestoreReceipt:
            if self.fail_restore:
                raise SyntheticReplayError("restore failed before suffix")
            return super().restore_replay_state(binding_key, request)

        def finalize_replay_suffix(
            self,
            binding_key: object,
            request: RestoreReplayStateRequest,
            *,
            suffix_succeeded: bool,
        ) -> None:
            assert binding_key in self.active_bindings
            assert request.replay_point_id == "replay-before-change"
            clock[0] += 60.0

    # 1) Non-SPI adapter with finalize_replay_suffix: 10s execute + 60s finalizer = 70s.
    finalizer_trace, _, _, finalizer_registry = _capture_point(
        adapter=_TimedFinalizerAdapter()
    )

    def _ten_second_suffix(_: object) -> dict[str, bool]:
        clock[0] += 10.0
        return {
            "target-created": True,
            "dependent-update": True,
            "constraint-health": True,
            "resource-cleanup": True,
        }

    with_finalizer = run_counterfactual_suffix_replay(
        finalizer_registry,
        trace=finalizer_trace,
        hypothesis=_hypothesis(),
        candidate=CounterfactualCandidate(
            candidate_id="timed-with-finalizer",
            public_change_set={"optional_argument": "none"},
        ),
        execute_suffix=_ten_second_suffix,
    )
    assert with_finalizer.restore.suffix_minutes == pytest.approx(70.0 / 60.0)
    assert finalizer_registry.metrics()["suffix_minutes"] == pytest.approx(
        round(70.0 / 60.0, 6)
    )
    assert summarize_counterfactual_results([with_finalizer])[
        "suffix_minutes"
    ] == pytest.approx(round(70.0 / 60.0, 6))

    # 2) Adapter without finalize_replay_suffix: only 10s execute is recorded.
    plain_trace, _, _, plain_registry = _capture_point(
        adapter=SyntheticReplayAdapter(prefix_reused=True)
    )
    without_finalizer = run_counterfactual_suffix_replay(
        plain_registry,
        trace=plain_trace,
        hypothesis=_hypothesis(),
        candidate=CounterfactualCandidate(
            candidate_id="timed-without-finalizer",
            public_change_set={"optional_argument": "none"},
        ),
        execute_suffix=_ten_second_suffix,
    )
    assert without_finalizer.restore.suffix_minutes == pytest.approx(10.0 / 60.0)
    assert plain_registry.metrics()["suffix_minutes"] == pytest.approx(
        round(10.0 / 60.0, 6)
    )

    # 3) Early restore failure before execute: finalizer cost stays out of suffix_minutes.
    failed_trace, _, _, failed_registry = _capture_point(
        adapter=_TimedFinalizerAdapter(fail_restore=True)
    )
    with pytest.raises(SyntheticReplayError, match="restore failed before suffix"):
        run_counterfactual_suffix_replay(
            failed_registry,
            trace=failed_trace,
            hypothesis=_hypothesis(),
            candidate=CounterfactualCandidate(
                candidate_id="timed-early-restore-fail",
                public_change_set={"optional_argument": "none"},
            ),
            execute_suffix=_ten_second_suffix,
        )
    assert failed_registry.metrics()["suffix_minutes"] == 0.0


@pytest.mark.parametrize("stage_kind", ["restore_agent_state", "execute"])
def test_exception_priority_prefers_finalizer_base_exception_and_keeps_double_exception_order(
    stage_kind: str,
) -> None:
    class _ConfiguredFinalizerAdapter(SyntheticReplayAdapter):
        def __init__(self, *, finalizer_exc: BaseException) -> None:
            super().__init__(prefix_reused=True)
            self.finalizer_exc = finalizer_exc

        def finalize_replay_suffix(
            self,
            binding_key: object,
            request: RestoreReplayStateRequest,
            *,
            suffix_succeeded: bool,
        ) -> None:
            assert binding_key in self.active_bindings
            assert request.replay_point_id == "replay-before-change"
            assert suffix_succeeded is False
            raise self.finalizer_exc

    # 1) Ordinary stage Exception + KeyboardInterrupt in finalizer must raise KeyboardInterrupt
    #    (not Exception) chained from the stage exception and quarantine the point.
    ki_adapter = _ConfiguredFinalizerAdapter(
        finalizer_exc=KeyboardInterrupt("aborted during cleanup")
    )
    ki_trace, ki_store, _, ki_registry = _capture_point(adapter=ki_adapter)
    if stage_kind == "restore_agent_state":

        def _fail_agent_restore(_state_key: object) -> None:
            raise ValueError("stage restore error")

        setattr(ki_store, "restore_agent_state", _fail_agent_restore)

    def _fail_suffix(_: object) -> dict[str, bool]:
        if stage_kind == "execute":
            raise ValueError("stage suffix error")
        return {
            "target-created": True,
            "dependent-update": True,
            "constraint-health": True,
            "resource-cleanup": True,
        }

    with pytest.raises(KeyboardInterrupt, match="aborted during cleanup") as ki_exc:
        run_counterfactual_suffix_replay(
            ki_registry,
            trace=ki_trace,
            hypothesis=_hypothesis(),
            candidate=CounterfactualCandidate(
                candidate_id=f"stage-exc-finalizer-ki-{stage_kind}",
                public_change_set={"optional_argument": "none"},
            ),
            execute_suffix=_fail_suffix,
        )

    assert isinstance(ki_exc.value.__cause__, ValueError)
    assert ki_registry.get("replay-before-change").lifecycle == ReplayPointLifecycle.FAILED
    ki_metrics = ki_registry.metrics()
    assert ki_metrics["replay_point_quarantine_count"] == 1
    assert ki_metrics["suffix_finalization_failure_count"] == 1
    assert ki_metrics["replay_failure_count"] == 0
    assert ki_metrics["suffix_failure_count"] == (1 if stage_kind == "execute" else 0)
    assert ki_registry.release("replay-before-change").lifecycle == ReplayPointLifecycle.RELEASED

    # 2) Double-Exception parity: stage Exception + finalizer Exception still raises
    #    the stage Exception chained from the finalizer Exception and quarantines.
    dbl_adapter = _ConfiguredFinalizerAdapter(
        finalizer_exc=RuntimeError("finalizer runtime failure")
    )
    dbl_trace, dbl_store, _, dbl_registry = _capture_point(adapter=dbl_adapter)
    if stage_kind == "restore_agent_state":

        def _fail_dbl_restore(_state_key: object) -> None:
            raise ValueError("stage restore error")

        setattr(dbl_store, "restore_agent_state", _fail_dbl_restore)

    with pytest.raises(ValueError, match="stage ") as dbl_exc:
        run_counterfactual_suffix_replay(
            dbl_registry,
            trace=dbl_trace,
            hypothesis=_hypothesis(),
            candidate=CounterfactualCandidate(
                candidate_id=f"stage-exc-finalizer-exc-{stage_kind}",
                public_change_set={"optional_argument": "none"},
            ),
            execute_suffix=_fail_suffix,
        )

    assert isinstance(dbl_exc.value.__cause__, RuntimeError)
    assert dbl_registry.get("replay-before-change").lifecycle == ReplayPointLifecycle.FAILED
    dbl_metrics = dbl_registry.metrics()
    assert dbl_metrics["replay_point_quarantine_count"] == 1
    assert dbl_metrics["suffix_finalization_failure_count"] == 1
    assert dbl_metrics["replay_failure_count"] == 1
    assert dbl_metrics["suffix_failure_count"] == (1 if stage_kind == "execute" else 0)
    assert dbl_registry.release("replay-before-change").lifecycle == ReplayPointLifecycle.RELEASED
