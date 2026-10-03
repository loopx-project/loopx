from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from ...file_lock import (
    LockAcquireTimeoutError,
    LockAcquisitionPolicy,
    exclusive_cross_runtime_file_lock,
    exclusive_file_lock,
)
from ..effect_program import TurnProviderStepKind
from ..effect_runtime import effect_runtime_result
from ..projects.registry_codec import (
    SOURCE_SESSION_PROFILE_ID,
    load_project_registry,
)
from ..todos.active_state_editing import fsync_state_directory
from .source_session_registry_state import (
    alias_digest,
    canonical_digest,
    current_goal_ref,
    guard_path,
    lifetime_root,
    write_journal,
)


_GATE_SCHEMA = "loopx_source_turn_effect_gate_v1"
_ADMISSION_SCHEMA = "loopx_source_turn_effect_admission_v1"
SOURCE_TURN_EFFECT_HOLD_SCHEMA_VERSION = "loopx_source_turn_effect_hold_v1"
_PENDING_RECOVERY_ACTIONS = {
    "executor_active": "Wait for this Turn to finish, then retry recreate-goal.",
    "goal_ref_mismatch": "Repair the admission GoalRef before retrying recreate-goal.",
    "journal_effect_conflict": "Repair this Turn journal before retrying recreate-goal.",
    "journal_identity_invalid": "Repair this Turn identity before retrying recreate-goal.",
    "journal_unreadable": "Repair this Turn journal before retrying recreate-goal.",
    "provider_readback_required": (
        "Resume this Turn with provider readback, then retry recreate-goal."
    ),
    "turn_tail_conflict": "Repair this Turn tail before retrying recreate-goal.",
    "turn_tail_recovery_required": (
        "Resume this Turn to finish its settlement tail, then retry recreate-goal."
    ),
}

SourceAdmissionFactory = Callable[[], Mapping[str, Any]]
JournalPersist = Callable[[Mapping[str, Any]], None]


class SourceTurnEffectRejected(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(f"source Turn effect rejected: {code}")
        self.code = code


@dataclass(frozen=True, slots=True)
class SourceTurnEffect:
    goal_ref: Mapping[str, str]
    turn_key: str
    step_kind: TurnProviderStepKind
    effect_ref: str
    journal_path: Path

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": _ADMISSION_SCHEMA,
            "goal_ref": dict(self.goal_ref),
            "turn_key": self.turn_key,
            "step_kind": self.step_kind.value,
            "effect_ref": self.effect_ref,
        }


@dataclass(frozen=True, slots=True)
class SourceTurnEffectDrainResult:
    pending_effects: list[dict[str, str]]
    released_count: int

    @property
    def changed(self) -> bool:
        return self.released_count > 0


def _gate_root(registry_path: Path, goal_id: str) -> Path:
    return lifetime_root(registry_path) / "turn-settlement" / alias_digest(goal_id)


def source_turn_effect_gate_path(registry_path: Path, goal_id: str) -> Path:
    return _gate_root(registry_path, goal_id) / "gate.json"


def _admission_directory(registry_path: Path, goal_id: str) -> Path:
    return _gate_root(registry_path, goal_id) / "admissions"


def _admission_path(
    registry_path: Path,
    goal_id: str,
    effect: SourceTurnEffect,
) -> Path:
    digest = canonical_digest(effect.payload()).removeprefix("sha256:")
    return _admission_directory(registry_path, goal_id) / f"{digest}.json"


def _read_object(path: Path, *, label: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _read_gate(registry_path: Path, goal_id: str) -> dict[str, Any] | None:
    return _read_object(
        source_turn_effect_gate_path(registry_path, goal_id),
        label="source Turn effect gate",
    )


def _read_admission(
    registry_path: Path,
    goal_id: str,
    effect: SourceTurnEffect,
) -> dict[str, Any] | None:
    return _read_object(
        _admission_path(registry_path, goal_id, effect),
        label="source Turn effect admission",
    )


def _source_state(
    registry_path: Path,
    goal_id: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    registry = load_project_registry(registry_path)
    if registry.get("profile_id") != SOURCE_SESSION_PROFILE_ID:
        raise SourceTurnEffectRejected("unsupported_profile")
    active_goal_ref, _goal = current_goal_ref(registry, goal_id=goal_id)
    return registry, active_goal_ref


def _require_canonical_journal_path(
    registry: Mapping[str, Any],
    *,
    goal_id: str,
    effect: SourceTurnEffect,
) -> None:
    runtime_root = Path(str(registry.get("common_runtime_root") or ""))
    if not runtime_root.is_absolute() or runtime_root.resolve() != runtime_root:
        raise ValueError("source-session common_runtime_root must be absolute")
    digest = effect.turn_key.removeprefix("sha256:")
    expected = runtime_root / "goals" / goal_id / "turns" / f"{digest}.json"
    if effect.journal_path.resolve() != expected:
        raise SourceTurnEffectRejected("journal_path_mismatch")


def _decision(method: str, facts: Mapping[str, Any]) -> dict[str, Any]:
    decision = effect_runtime_result(method, dict(facts))
    if not isinstance(decision, dict):
        raise RuntimeError("source Turn effect decision must be an object")
    if decision.get("kind") == "reject":
        raise SourceTurnEffectRejected(str(decision.get("code") or "invalid"))
    if decision.get("kind") not in {"commit", "replay"}:
        raise RuntimeError("source Turn effect decision kind is unsupported")
    return decision


def _write_decision_payload(
    path: Path,
    decision: Mapping[str, Any],
    field: str,
) -> None:
    payload = decision.get(field)
    if not isinstance(payload, dict):
        raise RuntimeError(f"source Turn effect decision omitted {field}")
    write_journal(path, payload)


def _remove_admission(path: Path) -> None:
    path.unlink(missing_ok=True)
    fsync_state_directory(path)


def prepare_source_turn_effect(
    *,
    registry_path: Path,
    goal_id: str,
    effect: SourceTurnEffect,
    source_admission: SourceAdmissionFactory,
    persist_journal: JournalPersist,
) -> None:
    target = guard_path(registry_path, goal_id)
    with exclusive_cross_runtime_file_lock(
        target,
        operation="source_turn_effect_admit",
    ):
        registry, active_goal_ref = _source_state(registry_path, goal_id)
        _require_canonical_journal_path(
            registry,
            goal_id=goal_id,
            effect=effect,
        )
        admission_path = _admission_path(registry_path, goal_id, effect)
        decision = _decision(
            "goal.source_session.turn_effect.admit",
            {
                "profile_id": SOURCE_SESSION_PROFILE_ID,
                "requested_goal_ref": dict(effect.goal_ref),
                "current_goal_ref": active_goal_ref,
                "gate": _read_gate(registry_path, goal_id),
                "admission": effect.payload(),
                "existing_admission": _read_admission(
                    registry_path,
                    goal_id,
                    effect,
                ),
            },
        )
        _write_decision_payload(
            source_turn_effect_gate_path(registry_path, goal_id),
            decision,
            "gate",
        )
        _write_decision_payload(admission_path, decision, "admission")
        persist_journal(source_admission())


def release_source_turn_effect(
    *,
    registry_path: Path,
    goal_id: str,
    effect: SourceTurnEffect,
    source_admission: SourceAdmissionFactory,
    persist_journal: JournalPersist,
) -> None:
    target = guard_path(registry_path, goal_id)
    with exclusive_cross_runtime_file_lock(
        target,
        operation="source_turn_effect_release",
    ):
        registry, active_goal_ref = _source_state(registry_path, goal_id)
        _require_canonical_journal_path(
            registry,
            goal_id=goal_id,
            effect=effect,
        )
        admission_path = _admission_path(registry_path, goal_id, effect)
        _decision(
            "goal.source_session.turn_effect.release",
            {
                "profile_id": SOURCE_SESSION_PROFILE_ID,
                "current_goal_ref": active_goal_ref,
                "gate": _read_gate(registry_path, goal_id),
                "admission": effect.payload(),
                "existing_admission": _read_admission(
                    registry_path,
                    goal_id,
                    effect,
                ),
            },
        )
        persist_journal(source_admission())
        _remove_admission(admission_path)


def source_turn_effect_allows_absent_reexecute(
    *,
    registry_path: Path,
    goal_id: str,
    effect: SourceTurnEffect,
) -> bool:
    target = guard_path(registry_path, goal_id)
    with exclusive_cross_runtime_file_lock(
        target,
        operation="source_turn_effect_resolve_absent",
    ):
        registry, active_goal_ref = _source_state(registry_path, goal_id)
        _require_canonical_journal_path(
            registry,
            goal_id=goal_id,
            effect=effect,
        )
        decision = effect_runtime_result(
            "goal.source_session.turn_effect.resolve_absent",
            {
                "profile_id": SOURCE_SESSION_PROFILE_ID,
                "current_goal_ref": active_goal_ref,
                "gate": _read_gate(registry_path, goal_id),
                "admission": effect.payload(),
                "existing_admission": _read_admission(
                    registry_path,
                    goal_id,
                    effect,
                ),
            },
        )
        if not isinstance(decision, dict):
            raise RuntimeError("source Turn effect decision must be an object")
        if decision.get("kind") == "execute":
            return True
        if decision.get("kind") == "abort":
            return False
        code = str(decision.get("code") or "invalid")
        raise SourceTurnEffectRejected(code)


def decide_source_turn_effect_close_locked(
    *,
    registry_path: Path,
    goal_id: str,
    requested_goal_ref: Mapping[str, str],
    current_goal_ref: Mapping[str, str],
    reserved_goal_ref: Mapping[str, str],
    operation_id: str,
    request_digest: str,
) -> tuple[dict[str, Any], bool]:
    decision = _decision(
        "goal.source_session.turn_effect.gate",
        {
            "profile_id": SOURCE_SESSION_PROFILE_ID,
            "operation": "close",
            "operation_id": operation_id,
            "request_digest": request_digest,
            "requested_goal_ref": dict(requested_goal_ref),
            "current_goal_ref": dict(current_goal_ref),
            "reserved_goal_ref": dict(reserved_goal_ref),
            "gate": _read_gate(registry_path, goal_id),
        },
    )
    gate = decision.get("gate")
    if not isinstance(gate, dict):
        raise RuntimeError("source Turn effect decision omitted gate")
    return gate, decision["kind"] == "commit"


def decide_source_turn_effect_publish_locked(
    *,
    registry_path: Path,
    goal_id: str,
    requested_goal_ref: Mapping[str, str],
    current_goal_ref: Mapping[str, str],
    reserved_goal_ref: Mapping[str, str],
    operation_id: str,
    request_digest: str,
) -> dict[str, Any]:
    decision = _decision(
        "goal.source_session.turn_effect.gate",
        {
            "profile_id": SOURCE_SESSION_PROFILE_ID,
            "operation": "publish",
            "operation_id": operation_id,
            "request_digest": request_digest,
            "requested_goal_ref": dict(requested_goal_ref),
            "current_goal_ref": dict(current_goal_ref),
            "reserved_goal_ref": dict(reserved_goal_ref),
            "gate": _read_gate(registry_path, goal_id),
            "admission_count": len(_admission_paths(registry_path, goal_id)),
        },
    )
    gate = decision.get("gate")
    if not isinstance(gate, dict):
        raise RuntimeError("source Turn effect decision omitted gate")
    return gate


def decide_source_turn_effect_repair_locked(
    *,
    registry_path: Path,
    goal_id: str,
    requested_goal_ref: Mapping[str, str],
    current_goal_ref: Mapping[str, str],
    reserved_goal_ref: Mapping[str, str],
    operation_id: str,
    request_digest: str,
) -> dict[str, Any] | None:
    decision = effect_runtime_result(
        "goal.source_session.turn_effect.gate",
        {
            "profile_id": SOURCE_SESSION_PROFILE_ID,
            "operation": "repair",
            "operation_id": operation_id,
            "request_digest": request_digest,
            "requested_goal_ref": dict(requested_goal_ref),
            "current_goal_ref": dict(current_goal_ref),
            "reserved_goal_ref": dict(reserved_goal_ref),
            "gate": _read_gate(registry_path, goal_id),
            "admission_count": len(_admission_paths(registry_path, goal_id)),
        },
    )
    if not isinstance(decision, dict):
        raise RuntimeError("source Turn effect decision must be an object")
    if decision.get("kind") == "reject":
        raise SourceTurnEffectRejected(str(decision.get("code") or "invalid"))
    if decision.get("kind") == "preserve":
        return None
    if decision.get("kind") not in {"commit", "replay"}:
        raise RuntimeError("source Turn effect decision kind is unsupported")
    gate = decision.get("gate")
    if not isinstance(gate, dict):
        raise RuntimeError("source Turn effect decision omitted gate")
    return gate


def write_source_turn_effect_gate_locked(
    *,
    registry_path: Path,
    goal_id: str,
    gate: dict[str, Any],
) -> None:
    write_journal(source_turn_effect_gate_path(registry_path, goal_id), gate)


def _admission_paths(registry_path: Path, goal_id: str) -> list[Path]:
    directory = _admission_directory(registry_path, goal_id)
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise ValueError("source Turn effect admissions path is not a directory")
    return sorted(directory.glob("*.json"))


def _pending_projection(
    admission: Mapping[str, Any],
    *,
    reason: str,
) -> dict[str, str]:
    return {
        "turn_key": str(admission.get("turn_key") or ""),
        "step_kind": str(admission.get("step_kind") or ""),
        "reason": reason,
        "recovery_action": _PENDING_RECOVERY_ACTIONS[reason],
    }


def drain_releasable_source_turn_effects(
    *,
    registry_path: Path,
    goal_id: str,
    requested_goal_ref: Mapping[str, str],
) -> SourceTurnEffectDrainResult:
    # turn_driver imports the Host admission owner, so defer this reverse edge.
    from ..turn_driver.journal_store import load_turn_journal, turn_journal_path

    registry = load_project_registry(registry_path)
    runtime_root = Path(str(registry.get("common_runtime_root") or ""))
    if not runtime_root.is_absolute() or runtime_root.resolve() != runtime_root:
        raise ValueError("source-session common_runtime_root must be absolute")
    pending: list[dict[str, str]] = []
    released_count = 0
    for admission_path in _admission_paths(registry_path, goal_id):
        admission = _read_object(
            admission_path,
            label="source Turn effect admission",
        )
        if admission is None:
            continue
        if admission.get("goal_ref") != dict(requested_goal_ref):
            pending.append(_pending_projection(admission, reason="goal_ref_mismatch"))
            continue
        turn_key = str(admission.get("turn_key") or "")
        step_kind = str(admission.get("step_kind") or "")
        effect_ref = str(admission.get("effect_ref") or "")
        try:
            journal_path = turn_journal_path(
                runtime_root,
                goal_id=goal_id,
                turn_key=turn_key,
            )
        except ValueError:
            pending.append(
                _pending_projection(admission, reason="journal_identity_invalid")
            )
            continue
        try:
            with exclusive_file_lock(
                journal_path,
                policy=LockAcquisitionPolicy.SINGLE_FLIGHT,
                operation="source_turn_effect_retirement_drain",
            ):
                try:
                    journal = load_turn_journal(journal_path)
                except (OSError, TypeError, ValueError):
                    pending.append(
                        _pending_projection(admission, reason="journal_unreadable")
                    )
                    continue
                hold = (
                    journal.get("source_effect_hold")
                    if isinstance(journal, Mapping)
                    else None
                )
                if hold is not None:
                    expected_hold = {
                        "schema_version": SOURCE_TURN_EFFECT_HOLD_SCHEMA_VERSION,
                        "status": "held",
                        "step_kind": step_kind,
                        "effect_ref": effect_ref,
                    }
                    pending.append(
                        _pending_projection(
                            admission,
                            reason=(
                                "turn_tail_recovery_required"
                                if isinstance(hold, Mapping)
                                and dict(hold) == expected_hold
                                else "turn_tail_conflict"
                            ),
                        )
                    )
                    continue
                attempts = (
                    journal.get("effect_attempts")
                    if isinstance(journal, Mapping)
                    else None
                )
                attempt = (
                    attempts.get(step_kind) if isinstance(attempts, Mapping) else None
                )
                if isinstance(attempt, Mapping):
                    if attempt.get("effect_ref") == effect_ref:
                        pending.append(
                            _pending_projection(
                                admission,
                                reason="provider_readback_required",
                            )
                        )
                    else:
                        pending.append(
                            _pending_projection(
                                admission,
                                reason="journal_effect_conflict",
                            )
                        )
                    continue
                with exclusive_cross_runtime_file_lock(
                    guard_path(registry_path, goal_id),
                    operation="source_turn_effect_retirement_release",
                ):
                    _registry, active_goal_ref = _source_state(
                        registry_path,
                        goal_id,
                    )
                    existing = _read_object(
                        admission_path,
                        label="source Turn effect admission",
                    )
                    release_decision = _decision(
                        "goal.source_session.turn_effect.release",
                        {
                            "profile_id": SOURCE_SESSION_PROFILE_ID,
                            "current_goal_ref": active_goal_ref,
                            "gate": _read_gate(registry_path, goal_id),
                            "admission": admission,
                            "existing_admission": existing,
                        },
                    )
                    _remove_admission(admission_path)
                    if release_decision["kind"] == "commit":
                        released_count += 1
        except LockAcquireTimeoutError:
            pending.append(_pending_projection(admission, reason="executor_active"))
    return SourceTurnEffectDrainResult(
        pending_effects=pending,
        released_count=released_count,
    )
