"""Property tests for C3/C4 dual-session binding using Hypothesis.

Stateful tests simulate controller crashes, executor retries, and session config
drift to verify single-debit guarantees and stale executor rejection.
"""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pytest
from hypothesis import assume, given, settings, strategies as st
from hypothesis.stateful import Bundle, RuleBasedStateMachine, rule

from .dual_session import (
    ControllerSession,
    ExecutorAttemptOutcome,
    ExecutorSession,
    ReconciliationDecision,
    SessionConfig,
    bind_controller_executor_sessions,
    load_controller_session,
    load_executor_session,
    save_controller_session,
    save_executor_session,
)
from .generation_fence import (
    GenerationFenceValidator,
    GenerationFenceViolation,
    GenerationValidationPhase,
    parse_generation_from_turn_instance_id,
    validate_executor_generation_at_creation,
)


# Strategies for generating test data
@st.composite
def session_config_strategy(draw: st.DrawFn) -> SessionConfig:
    """Generate valid SessionConfig instances."""
    profile_digest = draw(st.text(min_size=64, max_size=64, alphabet="0123456789abcdef"))
    runtime_root = draw(st.text(min_size=1, max_size=100))
    return SessionConfig(profile_digest=profile_digest, runtime_root=runtime_root)


@st.composite
def goal_lineage_strategy(draw: st.DrawFn) -> tuple[str, str, str]:
    """Generate goal/agent/todo lineage."""
    goal_id = draw(st.text(min_size=10, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz"))
    agent_id = draw(st.text(min_size=10, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz"))
    todo_id = draw(st.text(min_size=10, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz"))
    return goal_id, agent_id, todo_id


class DualSessionStateMachine(RuleBasedStateMachine):
    """Stateful testing of dual-session binding lifecycle."""

    def __init__(self) -> None:
        super().__init__()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.temp_dir.name)
        self.goal_id = "test-goal"
        self.agent_id = "test-agent"
        self.todo_id = "test-todo"
        self.profile_digest = "a" * 64
        self.controller: ControllerSession | None = None
        self.executors: list[ExecutorSession] = []
        self.committed_effects: set[str] = set()

    def teardown(self) -> None:
        self.temp_dir.cleanup()

    @rule()
    def create_controller(self) -> None:
        """Initialize controller session."""
        controller, executor = bind_controller_executor_sessions(
            self.runtime_root,
            self.goal_id,
            self.agent_id,
            self.todo_id,
            self.profile_digest,
        )
        self.controller = controller
        self.executors.append(executor)
        assert controller.generation == 0
        assert executor.generation == 0

    @rule()
    def simulate_executor_success(self) -> None:
        """Simulate successful executor commit."""
        assume(self.controller is not None)
        assume(len(self.executors) > 0)

        executor = self.executors[-1]
        effect_id = f"effect-{executor.turn_instance_id}"

        # Validate generation fence
        try:
            validate_executor_generation_at_creation(executor, self.controller)
        except GenerationFenceViolation:
            # Stale executor correctly rejected
            return

        # Commit effect
        self.committed_effects.add(effect_id)
        self.controller.record_executor_attempt(
            executor.generation,
            ExecutorAttemptOutcome.INVOKED,
            executor.turn_instance_id,
        )

    @rule()
    def simulate_executor_crash(self) -> None:
        """Simulate executor crash without observable outcome."""
        assume(self.controller is not None)
        assume(len(self.executors) > 0)

        executor = self.executors[-1]
        self.controller.record_executor_attempt(
            executor.generation,
            ExecutorAttemptOutcome.UNKNOWN,
            executor.turn_instance_id,
        )

    @rule()
    def simulate_controller_crash_and_recovery(self) -> None:
        """Simulate controller crash, then recovery with generation increment."""
        assume(self.controller is not None)

        # Controller crashes and increments generation on recovery
        self.controller.increment_generation(self.runtime_root)
        save_controller_session(self.controller, self.runtime_root)

        # Create new executor with new generation
        _, new_executor = bind_controller_executor_sessions(
            self.runtime_root,
            self.goal_id,
            self.agent_id,
            self.todo_id,
            self.profile_digest,
            existing_controller=self.controller,
        )
        self.executors.append(new_executor)

    @rule()
    def check_reconciliation_decision(self) -> None:
        """Verify controller makes correct recovery decision."""
        assume(self.controller is not None)

        decision = self.controller.decide_reconciliation()

        if not self.controller.observing_attempts:
            assert decision == ReconciliationDecision.AUTOMATIC_RETRY
        else:
            latest = self.controller.observing_attempts[-1]
            if latest.outcome == ExecutorAttemptOutcome.UNKNOWN:
                assert decision == ReconciliationDecision.BLOCKED_UNKNOWN_OUTCOME
            elif latest.outcome == ExecutorAttemptOutcome.INVOKED:
                assert decision == ReconciliationDecision.COMMITTED
            else:
                assert decision == ReconciliationDecision.AUTOMATIC_RETRY

    @rule()
    def verify_single_debit_invariant(self) -> None:
        """Verify at most one effect committed per generation."""
        assume(self.controller is not None)

        # Count committed effects per generation
        generation_commits: dict[int, int] = {}
        for attempt in self.controller.observing_attempts:
            if attempt.outcome == ExecutorAttemptOutcome.INVOKED:
                gen = attempt.generation
                generation_commits[gen] = generation_commits.get(gen, 0) + 1

        # Assert single debit: at most one commit per generation
        for gen, count in generation_commits.items():
            assert count <= 1, f"Generation {gen} has {count} commits (expected ≤1)"


TestDualSession = DualSessionStateMachine.TestCase


@given(goal_lineage=goal_lineage_strategy(), config=session_config_strategy())
@settings(max_examples=50)
def test_session_binding_creates_valid_sessions(
    goal_lineage: tuple[str, str, str],
    config: SessionConfig,
) -> None:
    """Property: bind_controller_executor_sessions creates valid linked sessions."""
    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_root = Path(temp_dir)
        goal_id, agent_id, todo_id = goal_lineage

        controller, executor = bind_controller_executor_sessions(
            runtime_root,
            goal_id,
            agent_id,
            todo_id,
            config.profile_digest,
        )

        # Verify controller properties
        assert controller.session_id
        assert controller.goal_id == goal_id
        assert controller.agent_id == agent_id
        assert controller.todo_id == todo_id
        assert controller.generation == 0
        assert controller.session_config.profile_digest == config.profile_digest

        # Verify executor binds to controller
        assert executor.controller_session_id == controller.session_id
        assert executor.generation == controller.generation
        assert executor.session_config.profile_digest == config.profile_digest

        # Verify turn_instance_id embeds generation
        generation = parse_generation_from_turn_instance_id(executor.turn_instance_id)
        assert generation == executor.generation


@given(st.integers(min_value=0, max_value=100))
@settings(max_examples=50)
def test_stale_executor_rejected_by_generation_fence(generation_delta: int) -> None:
    """Property: Stale executor (older generation) is rejected by fence."""
    assume(generation_delta > 0)

    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_root = Path(temp_dir)
        goal_id = "test-goal"
        agent_id = "test-agent"
        todo_id = "test-todo"
        profile_digest = "a" * 64

        # Create initial binding
        controller, stale_executor = bind_controller_executor_sessions(
            runtime_root, goal_id, agent_id, todo_id, profile_digest
        )

        # Advance controller generation
        for _ in range(generation_delta):
            controller.increment_generation(runtime_root)
        save_controller_session(controller, runtime_root)

        # Stale executor should be rejected
        with pytest.raises(GenerationFenceViolation) as exc_info:
            validate_executor_generation_at_creation(stale_executor, controller)

        assert exc_info.value.executor_generation < exc_info.value.controller_generation


def test_generation_fence_validator_caches_per_phase() -> None:
    """Verify GenerationFenceValidator caches validation per phase."""
    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_root = Path(temp_dir)
        profile_digest = "a" * 64

        controller, executor = bind_controller_executor_sessions(
            runtime_root, "goal1", "agent1", "todo1", profile_digest
        )

        validator = GenerationFenceValidator(executor, controller)

        # First validation should succeed
        validator.validate(GenerationValidationPhase.EXECUTOR_CREATION)
        assert GenerationValidationPhase.EXECUTOR_CREATION in validator._validated_phases

        # Advance controller generation
        controller.increment_generation(runtime_root)
        save_controller_session(controller, runtime_root)

        # Cached phase should not re-validate (no exception)
        validator.validate(GenerationValidationPhase.EXECUTOR_CREATION)

        # Different phase should re-validate and fail
        with pytest.raises(GenerationFenceViolation):
            validator.validate(GenerationValidationPhase.SETTLEMENT_PREPARE)


def test_session_config_drift_detection() -> None:
    """Verify session config drift (profile_digest change) is detected."""
    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_root = Path(temp_dir)
        goal_id = "test-goal"
        agent_id = "test-agent"
        todo_id = "test-todo"
        profile_digest_1 = "a" * 64
        profile_digest_2 = "b" * 64

        # Create initial controller
        controller, _ = bind_controller_executor_sessions(
            runtime_root, goal_id, agent_id, todo_id, profile_digest_1
        )

        # Attempt binding with different profile_digest should fail
        with pytest.raises(ValueError, match="session config drift"):
            bind_controller_executor_sessions(
                runtime_root,
                goal_id,
                agent_id,
                todo_id,
                profile_digest_2,
                existing_controller=controller,
            )


def test_controller_reconciliation_unknown_outcome_blocks_retry() -> None:
    """Verify unknown outcome blocks automatic retry."""
    controller = ControllerSession(
        session_id=str(uuid.uuid4()),
        goal_id="goal1",
        agent_id="agent1",
        todo_id="todo1",
        generation=0,
        session_config=SessionConfig("a" * 64, "/tmp"),
    )

    # Record unknown outcome
    controller.record_executor_attempt(
        0, ExecutorAttemptOutcome.UNKNOWN, "turn-1"
    )

    decision = controller.decide_reconciliation()
    assert decision == ReconciliationDecision.BLOCKED_UNKNOWN_OUTCOME


def test_controller_reconciliation_observable_failure_allows_retry() -> None:
    """Verify observable failure allows automatic retry."""
    controller = ControllerSession(
        session_id=str(uuid.uuid4()),
        goal_id="goal1",
        agent_id="agent1",
        todo_id="todo1",
        generation=0,
        session_config=SessionConfig("a" * 64, "/tmp"),
    )

    # Record observable failure
    controller.record_executor_attempt(
        0, ExecutorAttemptOutcome.FAILED, "turn-1"
    )

    decision = controller.decide_reconciliation()
    assert decision == ReconciliationDecision.AUTOMATIC_RETRY


def test_turn_instance_id_generation_parsing() -> None:
    """Verify generation extraction from turn_instance_id."""
    # Valid format
    tid = "550e8400-e29b-41d4-a716-446655440000.g5"
    assert parse_generation_from_turn_instance_id(tid) == 5

    # No generation suffix
    tid = "550e8400-e29b-41d4-a716-446655440000"
    assert parse_generation_from_turn_instance_id(tid) is None

    # Invalid format
    tid = "invalid.g"
    assert parse_generation_from_turn_instance_id(tid) is None

    # Multiple dots
    tid = "uuid.part1.g10"
    assert parse_generation_from_turn_instance_id(tid) == 10
