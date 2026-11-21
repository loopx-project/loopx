from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...turn_identity import normalize_turn_instance_id
from ..content_digest import ENVELOPED_SHA256_PATTERN
from ..effect_runtime import effect_runtime_result, source_number_tokens
from ..todos.contract import normalize_todo_id
from .turn_contract_generated import LoopXTurnResultKind


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


def _validate_cached_validation_state(
    payload: Mapping[str, Any],
    journal: Mapping[str, Any],
    *,
    goal_id: str,
    agent_id: str,
    turn_key: str,
) -> bool:
    """Check the compact wire carrier; TS retains continuation eligibility."""

    cached = payload.get("cached_validation_state")
    if not isinstance(cached, dict) or set(cached) != {
        "goal_id", "agent_id", "todo_id", "turn_instance_id", "turn_key",
        "result_kind", "task_validation",
    }:
        return False
    receipt = cached.get("task_validation")
    if not isinstance(receipt, dict) or set(receipt) != {
        "schema_version", "ok", "status",
    }:
        return False
    if (
        receipt.get("schema_version") != "loopx_turn_task_validation_v0"
        or receipt.get("ok") is not True
        or receipt.get("status") != "passed"
        or any(not isinstance(cached.get(key), str) for key in (
            "goal_id", "agent_id", "turn_instance_id", "turn_key", "result_kind",
        ))
        or any(not cached[key].strip() for key in ("goal_id", "agent_id"))
        or not isinstance(payload.get("turn_key"), str)
        or ENVELOPED_SHA256_PATTERN.fullmatch(payload["turn_key"]) is None
        or (cached["todo_id"] is not None and (
            not isinstance(cached["todo_id"], str)
            or normalize_todo_id(cached["todo_id"]) != cached["todo_id"]
        ))
    ):
        return False
    try:
        if normalize_turn_instance_id(cached["turn_instance_id"]) != cached["turn_instance_id"]:
            return False
        LoopXTurnResultKind(cached["result_kind"])
    except ValueError:
        return False

    plan = journal.get("plan")
    transaction = plan.get("transaction") if isinstance(plan, Mapping) else None
    settlement = transaction.get("settlement_plan") if isinstance(transaction, Mapping) else None
    identity = settlement.get("identity") if isinstance(settlement, Mapping) else None
    result = journal.get("host_result")
    if (
        not isinstance(transaction, Mapping)
        or not isinstance(identity, Mapping)
        or not isinstance(result, Mapping)
    ):
        return False
    return bool(
        cached["goal_id"] == goal_id == journal.get("goal_id") == identity.get("goal_id")
        and cached["agent_id"] == agent_id == identity.get("agent_id")
        and cached["todo_id"] == identity.get("todo_id")
        and cached["turn_instance_id"] == identity.get("turn_instance_id")
        and payload["turn_key"] == cached["turn_key"] == turn_key
        == journal.get("turn_key") == transaction.get("turn_key") == result.get("turn_key")
        and cached["result_kind"] == journal.get("result_kind") == result.get("result_kind")
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
    tokens = source_number_tokens(journal)
    if tokens:
        request["source_number_tokens"] = tokens
    payload = effect_runtime_result("turn_journal.inspect", request)
    if not isinstance(payload, dict) or set(payload) not in (
        _PROJECTION_KEYS,
        _PROJECTION_KEYS | {"turn_key", "cached_validation_state"},
    ):
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
    if "cached_validation_state" in payload and not _validate_cached_validation_state(
        payload, journal, goal_id=goal_id, agent_id=agent_id, turn_key=turn_key,
    ):
        raise RuntimeError(
            "TypeScript Turn-journal inspection cached validation state mismatch"
        )
    if payload.get("effects") != []:
        raise RuntimeError("Turn-journal inspection must remain effect-free")
    return payload


def write_turn_journal(
    path: str,
    journal: Mapping[str, Any],
    *,
    expected_effect_id: str | None = None,
    source_admission: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Commit a Turn-journal transition through the TS semantic owner."""

    request: dict[str, Any] = {
        "path": path,
        # TS owns validation, transitions and publication over the native object.
        "journal": dict(journal),
        "expected_effect_id": expected_effect_id,
    }
    # Reuse context-delivery's sparse IO metadata. Keep the ordinary object
    # carrier so quoted task/evidence text is not escaped a second time.
    tokens = source_number_tokens(journal)
    if tokens:
        request["source_number_tokens"] = tokens
    if source_admission is not None:
        request["source_admission"] = dict(source_admission)
    payload = effect_runtime_result(
        "turn_journal.write",
        request,
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
