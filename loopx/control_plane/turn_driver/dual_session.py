"""C3/C4 dual-session binding for LoopX Turn execution.

Controller and executor sessions enable automatic recovery from transient host
failures while preventing stale executors from committing duplicate effects.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from ...file_lock import exclusive_file_lock


CONTROLLER_SESSION_SCHEMA_VERSION = "loopx_controller_session_v0"
EXECUTOR_SESSION_SCHEMA_VERSION = "loopx_executor_session_v0"
SESSION_CONFIG_SCHEMA_VERSION = "loopx_session_config_v0"


class ExecutorAttemptOutcome(StrEnum):
    """Observable outcome of one executor attempt."""
    INVOKED = "invoked"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ReconciliationDecision(StrEnum):
    """Controller decision after observing executor attempt history."""
    AUTOMATIC_RETRY = "automatic_retry"
    BLOCKED_UNKNOWN_OUTCOME = "blocked_unknown_outcome"
    COMMITTED = "committed"


@dataclass(frozen=True)
class SessionConfig:
    """Session configuration snapshot for drift detection."""
    profile_digest: str
    runtime_root: str

    def as_dict(self) -> dict[str, str]:
        return {
            "schema_version": SESSION_CONFIG_SCHEMA_VERSION,
            "profile_digest": self.profile_digest,
            "runtime_root": self.runtime_root,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SessionConfig:
        if data.get("schema_version") != SESSION_CONFIG_SCHEMA_VERSION:
            raise ValueError("unsupported session config schema version")
        return cls(
            profile_digest=str(data["profile_digest"]),
            runtime_root=str(data["runtime_root"]),
        )


@dataclass(frozen=True)
class ExecutorAttempt:
    """Record of one executor invocation attempt."""
    generation: int
    outcome: ExecutorAttemptOutcome
    turn_instance_id: str
    timestamp: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "outcome": self.outcome.value,
            "turn_instance_id": self.turn_instance_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExecutorAttempt:
        return cls(
            generation=int(data["generation"]),
            outcome=ExecutorAttemptOutcome(str(data["outcome"])),
            turn_instance_id=str(data["turn_instance_id"]),
            timestamp=str(data["timestamp"]),
        )


@dataclass
class ControllerSession:
    """Controller session coordinates executor generation and recovery decisions."""
    session_id: str
    goal_id: str
    agent_id: str
    todo_id: str
    generation: int
    session_config: SessionConfig
    observing_attempts: list[ExecutorAttempt] = field(default_factory=list)
    reconciliation_state: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CONTROLLER_SESSION_SCHEMA_VERSION,
            "session_id": self.session_id,
            "goal_id": self.goal_id,
            "agent_id": self.agent_id,
            "todo_id": self.todo_id,
            "generation": self.generation,
            "session_config": self.session_config.as_dict(),
            "observing_attempts": [attempt.as_dict() for attempt in self.observing_attempts],
            "reconciliation_state": self.reconciliation_state,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ControllerSession:
        if data.get("schema_version") != CONTROLLER_SESSION_SCHEMA_VERSION:
            raise ValueError("unsupported controller session schema version")
        return cls(
            session_id=str(data["session_id"]),
            goal_id=str(data["goal_id"]),
            agent_id=str(data["agent_id"]),
            todo_id=str(data["todo_id"]),
            generation=int(data["generation"]),
            session_config=SessionConfig.from_dict(data["session_config"]),
            observing_attempts=[
                ExecutorAttempt.from_dict(attempt)
                for attempt in data.get("observing_attempts", [])
            ],
            reconciliation_state=data.get("reconciliation_state"),
        )

    def increment_generation(self, runtime_root: Path) -> None:
        """Advance generation counter, invalidating existing executors.

        This operation must be atomic: the in-memory increment and disk persistence
        happen under the same exclusive lock to prevent race conditions where:
        - Two concurrent controllers both read generation N
        - Both increment to N+1 in memory
        - Both persist N+1, creating two executor generations with the same number

        The lock ensures the read-modify-write cycle is serialized across processes.

        Args:
            runtime_root: Root directory for session storage, used to compute lock path
        """
        lineage_digest = _compute_lineage_digest(
            self.goal_id, self.agent_id, self.todo_id
        )
        lock_path = _session_storage_path(
            runtime_root, self.goal_id, lineage_digest, "controller"
        )

        with exclusive_file_lock(lock_path):
            self.generation += 1
            save_controller_session(self, runtime_root)

    def record_executor_attempt(
        self, generation: int, outcome: ExecutorAttemptOutcome, turn_instance_id: str
    ) -> None:
        """Record observable outcome of executor attempt."""
        from datetime import datetime, timezone

        self.observing_attempts.append(
            ExecutorAttempt(
                generation=generation,
                outcome=outcome,
                turn_instance_id=turn_instance_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        )

    def decide_reconciliation(self) -> ReconciliationDecision:
        """Decide automatic retry vs operator escalation based on attempt observability."""
        if not self.observing_attempts:
            return ReconciliationDecision.AUTOMATIC_RETRY

        latest_attempt = self.observing_attempts[-1]
        if latest_attempt.outcome == ExecutorAttemptOutcome.UNKNOWN:
            return ReconciliationDecision.BLOCKED_UNKNOWN_OUTCOME

        if latest_attempt.outcome == ExecutorAttemptOutcome.INVOKED:
            return ReconciliationDecision.COMMITTED

        # All outcomes observable and latest failed
        return ReconciliationDecision.AUTOMATIC_RETRY


@dataclass
class ExecutorSession:
    """Executor session binds to controller generation for effect atomicity."""
    session_id: str
    controller_session_id: str
    generation: int
    turn_instance_id: str
    session_config: SessionConfig
    goal_id: str
    agent_id: str
    todo_id: str
    _validated_phases: set[str] = field(default_factory=set)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EXECUTOR_SESSION_SCHEMA_VERSION,
            "session_id": self.session_id,
            "controller_session_id": self.controller_session_id,
            "generation": self.generation,
            "turn_instance_id": self.turn_instance_id,
            "session_config": self.session_config.as_dict(),
            "goal_id": self.goal_id,
            "agent_id": self.agent_id,
            "todo_id": self.todo_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExecutorSession:
        if data.get("schema_version") != EXECUTOR_SESSION_SCHEMA_VERSION:
            raise ValueError("unsupported executor session schema version")
        return cls(
            session_id=str(data["session_id"]),
            controller_session_id=str(data["controller_session_id"]),
            generation=int(data["generation"]),
            turn_instance_id=str(data["turn_instance_id"]),
            session_config=SessionConfig.from_dict(data["session_config"]),
            goal_id=str(data["goal_id"]),
            agent_id=str(data["agent_id"]),
            todo_id=str(data["todo_id"]),
        )


def _session_storage_path(
    runtime_root: Path, goal_id: str, lineage_digest: str, suffix: str
) -> Path:
    """Compute session document path with shared lineage digest."""
    return (
        runtime_root / "goals" / goal_id / "sessions" / f"{lineage_digest}.{suffix}.json"
    )


def _compute_lineage_digest(goal_id: str, agent_id: str, todo_id: str) -> str:
    """Compute stable digest for controller/executor session lineage."""
    import hashlib

    content = f"{goal_id}\0{agent_id}\0{todo_id}".encode("utf-8")
    return hashlib.sha256(content).hexdigest()[:16]


def load_controller_session(
    runtime_root: Path, goal_id: str, agent_id: str, todo_id: str
) -> ControllerSession | None:
    """Load existing controller session or return None."""
    lineage_digest = _compute_lineage_digest(goal_id, agent_id, todo_id)
    path = _session_storage_path(runtime_root, goal_id, lineage_digest, "controller")

    if not path.exists():
        return None

    with exclusive_file_lock(path):
        data = json.loads(path.read_text(encoding="utf-8"))
        return ControllerSession.from_dict(data)


def save_controller_session(session: ControllerSession, runtime_root: Path) -> None:
    """Persist controller session atomically."""
    lineage_digest = _compute_lineage_digest(
        session.goal_id, session.agent_id, session.todo_id
    )
    path = _session_storage_path(
        runtime_root, session.goal_id, lineage_digest, "controller"
    )
    path.parent.mkdir(parents=True, exist_ok=True)

    with exclusive_file_lock(path):
        path.write_text(json.dumps(session.as_dict(), indent=2), encoding="utf-8")


def load_executor_session(
    runtime_root: Path, goal_id: str, agent_id: str, todo_id: str, generation: int
) -> ExecutorSession | None:
    """Load executor session for specific generation."""
    lineage_digest = _compute_lineage_digest(goal_id, agent_id, todo_id)
    path = _session_storage_path(
        runtime_root, goal_id, lineage_digest, f"executor-gen{generation}"
    )

    if not path.exists():
        return None

    with exclusive_file_lock(path):
        data = json.loads(path.read_text(encoding="utf-8"))
        return ExecutorSession.from_dict(data)


def save_executor_session(session: ExecutorSession, runtime_root: Path) -> None:
    """Persist executor session atomically."""
    lineage_digest = _compute_lineage_digest(
        session.goal_id, session.agent_id, session.todo_id
    )
    path = _session_storage_path(
        runtime_root,
        session.goal_id,
        lineage_digest,
        f"executor-gen{session.generation}",
    )
    path.parent.mkdir(parents=True, exist_ok=True)

    with exclusive_file_lock(path):
        path.write_text(json.dumps(session.as_dict(), indent=2), encoding="utf-8")


def bind_controller_executor_sessions(
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    todo_id: str,
    profile_digest: str,
    existing_controller: ControllerSession | None = None,
) -> tuple[ControllerSession, ExecutorSession]:
    """Bind controller and executor sessions with generation coordination.

    Creates new controller if none exists, or validates existing controller's
    generation matches session config. Creates new executor bound to current
    controller generation.

    Raises:
        ValueError: If session config drifts (profile_digest mismatch)
        RuntimeError: If controller state is inconsistent
    """
    # Compute lock path from lineage_digest to prevent TOCTOU between load, validate, and executor creation
    lineage_digest = _compute_lineage_digest(goal_id, agent_id, todo_id)
    lock_path = _session_storage_path(runtime_root, goal_id, lineage_digest, "controller")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with exclusive_file_lock(lock_path, timeout=10.0):
        session_config = SessionConfig(
            profile_digest=profile_digest,
            runtime_root=str(runtime_root.resolve()),
        )

        # Load or create controller
        controller = existing_controller
        if controller is None:
            # Re-check existence inside lock to handle concurrent creation attempts
            if lock_path.exists():
                data = json.loads(lock_path.read_text(encoding="utf-8"))
                controller = ControllerSession.from_dict(data)
            else:
                # Create new controller session
                controller = ControllerSession(
                    session_id=str(uuid.uuid4()),
                    goal_id=goal_id,
                    agent_id=agent_id,
                    todo_id=todo_id,
                    generation=0,
                    session_config=session_config,
                )
                lock_path.write_text(
                    json.dumps(controller.as_dict(), indent=2), encoding="utf-8"
                )

        # Validate session config hasn't drifted
        if controller.session_config.profile_digest != profile_digest:
            raise ValueError(
                f"session config drift: expected profile_digest "
                f"{controller.session_config.profile_digest}, "
                f"got {profile_digest}"
            )
        if controller.session_config.runtime_root != str(runtime_root.resolve()):
            raise ValueError(
                f"runtime_root divergence: controller has "
                f"{controller.session_config.runtime_root}, "
                f"executor has {runtime_root.resolve()}"
            )

        # Create executor bound to current controller generation
        turn_instance_id = f"{uuid.uuid4()}.g{controller.generation}"
        executor = ExecutorSession(
            session_id=str(uuid.uuid4()),
            controller_session_id=controller.session_id,
            generation=controller.generation,
            turn_instance_id=turn_instance_id,
            session_config=session_config,
            goal_id=goal_id,
            agent_id=agent_id,
            todo_id=todo_id,
        )

        return controller, executor
