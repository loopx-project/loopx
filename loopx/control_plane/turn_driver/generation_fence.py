"""Generation fence prevents stale executors from committing effects.

The generation counter is embedded in turn_instance_id format ({uuid}.g{N}).
Executors validate their generation matches controller before acquiring resources.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from .dual_session import (
    ControllerSession,
    ExecutorSession,
    load_controller_session,
)


class GenerationValidationPhase(StrEnum):
    """Settlement phases where generation fence is validated."""
    EXECUTOR_CREATION = "executor_creation"
    SETTLEMENT_PREPARE = "settlement_prepare"
    SETTLEMENT_CHECKPOINT = "settlement_checkpoint"


class GenerationFenceViolation(Exception):
    """Stale executor attempted to proceed with outdated generation."""

    def __init__(self, executor_gen: int, controller_gen: int, phase: str) -> None:
        self.executor_generation = executor_gen
        self.controller_generation = controller_gen
        self.validation_phase = phase
        super().__init__(
            f"generation fence violation at {phase}: "
            f"executor generation {executor_gen} != "
            f"controller generation {controller_gen}"
        )


class GenerationFenceValidator:
    """Validates executor generation against controller.

    REMOVED: Per-phase caching (_validated_phases) has been eliminated.
    Caching validation results creates a TOCTOU window where controller
    generation could advance between cached check and subsequent use.
    Each validation now performs fresh atomic load+compare under lock.
    """

    def __init__(self, executor: ExecutorSession, controller: ControllerSession):
        self.executor = executor
        self.controller = controller

    def validate(self, phase: GenerationValidationPhase) -> None:
        """Validate generation fence at specific settlement phase.

        Always performs fresh validation by atomically loading controller
        state and comparing generation under exclusive file lock.

        LOCK RATIONALE: Without the lock, a TOCTOU race exists:
        1. Process A loads controller (generation=N)
        2. Process B increments generation to N+1
        3. Process A compares stale generation N against executor
        4. Stale executor passes validation and commits duplicate effects

        The exclusive_file_lock ensures load+compare is atomic with respect
        to any concurrent generation increments, preventing stale executors
        from proceeding.

        Raises:
            GenerationFenceViolation: If executor generation is stale
            RuntimeError: If controller session file is missing
        """
        from pathlib import Path
        import json
        import hashlib
        from ...file_lock import exclusive_file_lock

        runtime_root = Path(self.controller.session_config.runtime_root)

        # Compute session file path (must match dual_session.py logic)
        lineage_content = (
            f"{self.controller.goal_id}\0"
            f"{self.controller.agent_id}\0"
            f"{self.controller.todo_id}"
        ).encode("utf-8")
        lineage_digest = hashlib.sha256(lineage_content).hexdigest()[:16]
        controller_path = (
            runtime_root / "goals" / self.controller.goal_id /
            "sessions" / f"{lineage_digest}.controller.json"
        )

        if not controller_path.exists():
            raise RuntimeError("controller session disappeared during execution")

        # Atomic load + compare under exclusive lock (minimal critical section)
        with exclusive_file_lock(controller_path):
            data = json.loads(controller_path.read_text(encoding="utf-8"))
            current_controller = ControllerSession.from_dict(data)

            if self.executor.generation != current_controller.generation:
                raise GenerationFenceViolation(
                    self.executor.generation,
                    current_controller.generation,
                    phase.value,
                )

            # Update cached controller reference with validated state
            self.controller = current_controller


def parse_generation_from_turn_instance_id(turn_instance_id: str) -> int | None:
    """Extract generation counter from turn_instance_id format {uuid}.g{N}.

    Returns:
        Generation number or None if format doesn't match
    """
    if not turn_instance_id or ".g" not in turn_instance_id:
        return None

    parts = turn_instance_id.rsplit(".g", 1)
    if len(parts) != 2:
        return None

    try:
        return int(parts[1])
    except ValueError:
        return None


def build_turn_instance_id_with_generation(base_uuid: str, generation: int) -> str:
    """Build turn_instance_id embedding generation counter."""
    return f"{base_uuid}.g{generation}"


def validate_executor_generation_at_creation(
    executor: ExecutorSession,
    controller: ControllerSession,
) -> None:
    """Validate generation fence when executor is created (fail-fast).

    Prevents stale executor from even acquiring lane fence.

    Raises:
        GenerationFenceViolation: If executor generation is stale
    """
    validator = GenerationFenceValidator(executor, controller)
    validator.validate(GenerationValidationPhase.EXECUTOR_CREATION)


def validate_executor_generation_before_settlement(
    executor: ExecutorSession,
    controller: ControllerSession,
    validator: GenerationFenceValidator | None = None,
) -> GenerationFenceValidator:
    """Validate generation fence before settlement prepare phase.

    Returns validator instance for subsequent checkpoint validations.

    Raises:
        GenerationFenceViolation: If executor generation is stale
    """
    if validator is None:
        validator = GenerationFenceValidator(executor, controller)

    validator.validate(GenerationValidationPhase.SETTLEMENT_PREPARE)
    return validator


def validate_executor_generation_at_checkpoint(
    validator: GenerationFenceValidator,
) -> None:
    """Re-validate generation fence before each effect commit.

    Catches mid-settlement generation advancement by controller.

    Raises:
        GenerationFenceViolation: If executor generation became stale
    """
    validator.validate(GenerationValidationPhase.SETTLEMENT_CHECKPOINT)
