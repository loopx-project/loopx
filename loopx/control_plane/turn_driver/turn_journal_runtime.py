from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..effect_runtime import effect_runtime_result


TURN_JOURNAL_INTERPRETATION_REQUEST_SCHEMA_VERSION = (
    "loopx_turn_journal_interpretation_request_v0"
)
TURN_JOURNAL_INSPECTION_SCHEMA_VERSION = "loopx_turn_journal_inspection_v1"
_PROJECTION_KEYS = {
    "ok",
    "schema_version",
    "decision",
    "journal_status",
    "replay_legal",
    "goal_matches",
    "owner_matches",
    "turn_key_matches",
    "phases_form_ordered_prefix",
    "completed_phases",
    "tombstone_retained",
    "violations",
    "journal_consistent",
    "recovery_decision",
    "last_recovery",
    "recorded_effects",
    "effects",
}
_BOOLEAN_PROJECTION_KEYS = {
    "replay_legal",
    "goal_matches",
    "owner_matches",
    "turn_key_matches",
    "phases_form_ordered_prefix",
    "tombstone_retained",
    "journal_consistent",
}


def _validate_recovery_check(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    return bool(
        value.get("kind")
        in {
            "journal_consistency",
            "host_retry_policy",
            "host_session_binding",
            "prepared_effect_readback",
        }
        and value.get("outcome") in {"passed", "failed", "required"}
        and ("reason" not in value or isinstance(value.get("reason"), str))
        and ("step_kind" not in value or isinstance(value.get("step_kind"), str))
    )


def _validate_recovery_decision(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    checks = value.get("checks")
    return bool(
        value.get("schema_version") == "loopx_turn_recovery_decision_v0"
        and value.get("action") in {"continue", "return_existing", "blocked"}
        and isinstance(value.get("can_continue"), bool)
        and (
            value.get("resume_from") is None
            or isinstance(value.get("resume_from"), str)
        )
        and isinstance(value.get("reinvoke_host"), bool)
        and isinstance(value.get("reason"), str)
        and isinstance(value.get("retry_failed"), bool)
        and isinstance(checks, list)
        and all(_validate_recovery_check(check) for check in checks)
    )


def _validate_recovery_audit(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, Mapping):
        return False
    actual = value.get("actual")
    if not isinstance(actual, Mapping):
        return False
    phases = actual.get("completed_phases")
    return bool(
        value.get("schema_version") == "loopx_turn_recovery_audit_v0"
        and _validate_recovery_decision(value.get("planned"))
        and actual.get("status") in {"started", "finished"}
        and isinstance(actual.get("journal_status"), str)
        and isinstance(phases, list)
        and all(isinstance(phase, str) for phase in phases)
        and (
            actual.get("host_invoked") is None
            or isinstance(actual.get("host_invoked"), bool)
        )
    )


def interpret_turn_journal_projection(
    journal: Mapping[str, Any],
    *,
    goal_id: str,
    agent_id: str,
    turn_key: str,
    retry_failed: bool = False,
    session_recovery_check: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Run the TS-owned journal rule through the managed Effect runtime."""

    request = {
        "schema_version": TURN_JOURNAL_INTERPRETATION_REQUEST_SCHEMA_VERSION,
        "journal": journal,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "turn_key": turn_key,
        "retry_failed": retry_failed,
        "session_recovery_check": (
            dict(session_recovery_check)
            if isinstance(session_recovery_check, Mapping)
            else None
        ),
    }
    payload = effect_runtime_result("turn_journal.inspect", request)
    if not isinstance(payload, dict) or set(payload) != _PROJECTION_KEYS:
        raise RuntimeError(
            "TypeScript Turn-journal inspection projection shape mismatch"
        )
    if payload.get("schema_version") != TURN_JOURNAL_INSPECTION_SCHEMA_VERSION:
        raise RuntimeError(
            "TypeScript Turn-journal inspection projection schema mismatch"
        )
    if (
        payload.get("ok") is not True
        or payload.get("decision") not in {"replay_legal", "replay_blocked"}
        or not isinstance(payload.get("journal_status"), str)
        or any(
            not isinstance(payload.get(key), bool) for key in _BOOLEAN_PROJECTION_KEYS
        )
        or not isinstance(payload.get("completed_phases"), list)
        or not all(isinstance(phase, str) for phase in payload["completed_phases"])
        or not isinstance(payload.get("violations"), list)
        or not all(isinstance(violation, str) for violation in payload["violations"])
        or not _validate_recovery_decision(payload.get("recovery_decision"))
        or not _validate_recovery_audit(payload.get("last_recovery"))
        or not isinstance(payload.get("recorded_effects"), dict)
        or set(payload["recorded_effects"]) != {
            "host_invoked", "state_written", "quota_spent", "scheduler_acknowledged",
        }
        or any(value is not None and not isinstance(value, bool)
               for value in payload["recorded_effects"].values())
    ):
        raise RuntimeError(
            "TypeScript Turn-journal inspection projection type mismatch"
        )
    if payload.get("effects") != []:
        raise RuntimeError("Turn-journal inspection must remain effect-free")
    return payload


def write_turn_journal(
    path: str,
    journal: Mapping[str, Any],
    *,
    expected_effect_id: str | None = None,
) -> dict[str, object]:
    """Commit a Turn-journal transition through the TS semantic owner."""

    payload = effect_runtime_result(
        "turn_journal.write",
        {
            "path": path,
            "journal": dict(journal),
            "expected_effect_id": expected_effect_id,
        },
        retry_safe=True,
    )
    if (
        not isinstance(payload, dict)
        or payload.get("ok") is not True
        or not isinstance(payload.get("appended"), bool)
        or not isinstance(payload.get("replayed"), bool)
        or not isinstance(payload.get("operation_id"), str)
    ):
        raise RuntimeError("TypeScript Effect runtime did not commit the journal")
    return payload
