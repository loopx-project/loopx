"""One bounded LoopX Turn host execution with resumable local receipts."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ...authority import validate_public_safe_text
from ...file_lock import LockAcquireTimeoutError, exclusive_file_lock
from ...runtime import validate_goal_id_path_segment
from ..effect_program import (
    SettlementStepKind,
    interpret_turn_result_packet,
    settlement_result_payload,
)
from ..goals.goal_vision import normalize_goal_vision_packet
from ..work_items.delivery_batch_scale import require_delivery_batch_scale
from ..work_items.delivery_outcome import require_delivery_outcome
from . import subagent_execution_topology as subagent
from .command_validation import (
    TaskValidator,
    build_loopx_turn_command_validator,
    normalize_host_argv,
    normalize_reward_memory_reflection_validation,
    reward_memory_reflection_digest,
)
from .driver import selected_turn_todo
from .execution_readback import execution_payload
from .host_binding import (
    managed_executor_unavailable_payload,
)
from .host_failure import BuiltInHostError, record_host_failure
from .journal_store import (
    LOOPX_TURN_JOURNAL_SCHEMA_VERSION,
    TURN_KEY_RE,
    journal_committed_effect_id as _journal_committed_effect_id,
    load_turn_journal as _load_journal,
    turn_journal_path,
    write_turn_journal_checkpoint as _write_journal,
)
from .lane_fence import single_executor_per_turn_lane
from .recovery import (
    assess_existing_turn_recovery,
    build_turn_recovery_audit,
    require_turn_recovery_continuation,
)
from .post_settlement import PostSettlement, run_post_settlement_callback
from .session_recovery import (
    SessionBindingResolver,
    build_host_recovery_record,
)
from .settlement import (
    TurnEffectResolver,
    TurnSettlementJournalAdapter,
    completion_writeback_outcome,
    execute_turn_driver_settlement,
    invoke_result_effect,
    terminal_closeout_requirement,
    turn_settlement_failure_outcome,
    turn_settlement_outcome,
    turn_effect_resolvers,
    verified_terminal_closeout_effect,
)
from .transaction import (
    LOOPX_TURN_RESULT_SCHEMA_VERSION,
    STOP_RESULT_KINDS as STOP_HOST_RESULT_KINDS,
    TRANSACTION_PHASES,
    build_loopx_turn_transaction_plan,
    validate_loopx_turn_receipt,
)
from .turn_contract_generated import LoopXTurnResultKind

__all__ = [
    "build_loopx_turn_command_validator",
    "reward_memory_reflection_digest",
]

LOOPX_TURN_HOST_REQUEST_SCHEMA_VERSION = "loopx_turn_host_request_v0"
LOOPX_TURN_JOURNAL_INSPECTION_SCHEMA_VERSION = "loopx_turn_journal_inspection_v1"
LOOPX_TURN_TASK_VALIDATION_SCHEMA_VERSION = "loopx_turn_task_validation_v0"
HOST_RESULT_MAX_BYTES = 12_000
HOST_AGENT_VISION_JSON_MAX_CHARS = 3_200
HOST_REWARD_MEMORY_REFLECTION_JSON_MAX_CHARS = 2_400
HOST_PATH_DELTA_MODES = {"", "unchanged", "material_replan"}
HOST_RESULT_TEXT_LIMITS = (
    ("classification", 120),
    ("recommended_action", 1_200),
    ("next_action", 1_200),
    ("vision_unchanged_reason", 240),
    ("summary", 400),
)
MATERIAL_HOST_RESULT_KINDS = {
    LoopXTurnResultKind.VALIDATED_PROGRESS,
    LoopXTurnResultKind.VALIDATED_COMPLETION,
    LoopXTurnResultKind.REPAIR_REQUIRED,
    LoopXTurnResultKind.REPLAN_REQUIRED,
}
HOST_RESULT_FIELDS = {
    "schema_version",
    "turn_key",
    "result_kind",
    "completed_phases",
    "classification",
    "recommended_action",
    "next_action",
    "delivery_batch_scale",
    "delivery_outcome",
    "vision_unchanged_reason",
    "path_delta_mode",
    "agent_vision_json",
    "summary",
    "reward_memory_reflection_json",
}


Writeback = Callable[..., dict[str, Any]]
CompletionWriteback = Callable[..., dict[str, Any]]
CompletionIntent = Callable[[dict[str, Any]], dict[str, Any]]
TerminalCloseout = Callable[..., dict[str, Any]]
Spend = Callable[..., dict[str, Any]]
Scheduler = Callable[[dict[str, Any]], dict[str, Any]]
HostRunner = Callable[[Mapping[str, Any]], dict[str, Any]]


def build_loopx_turn_host_request(plan: Mapping[str, Any]) -> dict[str, Any]:
    transaction = (
        plan.get("transaction") if isinstance(plan.get("transaction"), dict) else {}
    )
    turn_key = str(transaction.get("turn_key") or "")
    if not TURN_KEY_RE.fullmatch(turn_key):
        raise ValueError("LoopX Turn plan has no valid transaction turn_key")
    route = plan.get("route") if isinstance(plan.get("route"), dict) else {}
    if route.get("would_invoke_host") is not True:
        raise ValueError("LoopX Turn route is not host executable")
    request = {
        "schema_version": LOOPX_TURN_HOST_REQUEST_SCHEMA_VERSION,
        "turn_key": turn_key,
        "route": route.get("kind"),
        "session": plan.get("session"),
        "turn_envelope": plan.get("turn_envelope"),
        "result_contract": {
            "schema_version": LOOPX_TURN_RESULT_SCHEMA_VERSION,
            "completed_phases": list(TRANSACTION_PHASES[:2]),
            "stdout": "one public-safe JSON object",
        },
    }
    reward_memory_recall = plan.get("reward_memory_recall")
    if isinstance(reward_memory_recall, Mapping):
        request["reward_memory_recall"] = dict(reward_memory_recall)
    request.update(subagent.subagent_host_request_projection(plan))
    return request


def _bounded_public_text(
    result: Mapping[str, Any],
    field: str,
    *,
    limit: int,
    required: bool,
    errors: list[str],
) -> str | None:
    text = str(result.get(field) or "").strip()
    if required and not text:
        errors.append(f"{field} is required")
        return None
    if not text:
        return None
    if len(text) > limit:
        errors.append(f"{field} exceeds {limit} characters")
        return None
    try:
        validate_public_safe_text(f"host_result.{field}", text)
    except ValueError as exc:
        errors.append(str(exc))
        return None
    return text


def _normalize_host_path_delta(
    plan: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    unchanged_reason: str,
    errors: list[str],
) -> tuple[str, dict[str, Any] | None]:
    path_delta_mode = str(result.get("path_delta_mode") or "").strip()
    raw_agent_vision = result.get("agent_vision_json")
    if raw_agent_vision is None:
        agent_vision_json = ""
    elif isinstance(raw_agent_vision, str):
        agent_vision_json = raw_agent_vision.strip()
    else:
        agent_vision_json = ""
        errors.append("agent_vision_json must be a JSON string")

    # Older generic hosts only supplied an unchanged reason. Keep that
    # contract valid while making new hosts classify material path changes.
    if not path_delta_mode:
        path_delta_mode = "material_replan" if agent_vision_json else "unchanged"
    if path_delta_mode not in HOST_PATH_DELTA_MODES - {""}:
        errors.append("path_delta_mode must be unchanged or material_replan")

    agent_vision: dict[str, Any] | None = None
    if agent_vision_json:
        if len(agent_vision_json) > HOST_AGENT_VISION_JSON_MAX_CHARS:
            errors.append(
                "agent_vision_json exceeds "
                f"{HOST_AGENT_VISION_JSON_MAX_CHARS} characters"
            )
        else:
            try:
                packet = json.loads(agent_vision_json)
                if not isinstance(packet, dict):
                    raise TypeError("agent_vision_json must decode to a JSON object")
                envelope = (
                    plan.get("turn_envelope")
                    if isinstance(plan.get("turn_envelope"), dict)
                    else {}
                )
                agent_vision = normalize_goal_vision_packet(
                    packet,
                    goal_id=str(envelope.get("goal_id") or ""),
                    agent_id=str(envelope.get("agent_id") or "") or None,
                )
            except (json.JSONDecodeError, ValueError) as exc:
                errors.append(f"invalid agent_vision_json: {exc}")

    if path_delta_mode == "material_replan":
        if agent_vision is None:
            errors.append(
                "material_replan requires agent_vision_json with goal_path_delta_v0"
            )
        elif not isinstance(agent_vision.get("path_delta"), dict):
            errors.append(
                "material_replan agent_vision_json requires goal_path_delta_v0"
            )
        elif agent_vision["path_delta"].get("outcome") != "replan":
            errors.append("material_replan goal_path_delta_v0 outcome must be replan")
        if unchanged_reason:
            errors.append("material_replan cannot also declare vision_unchanged_reason")
    elif path_delta_mode == "unchanged":
        if agent_vision is not None:
            errors.append("unchanged path_delta_mode cannot include agent_vision_json")
        if not unchanged_reason:
            errors.append("unchanged path_delta_mode requires vision_unchanged_reason")

    return path_delta_mode, agent_vision


def validate_loopx_turn_host_result(
    plan: Mapping[str, Any],
    value: Mapping[str, Any],
    *,
    completion_lifecycle_configured: bool = False,
) -> dict[str, Any]:
    result = dict(value)
    errors: list[str] = []
    allowed_fields = HOST_RESULT_FIELDS | subagent.subagent_host_result_fields(plan)
    unknown = sorted(set(result) - allowed_fields)
    if unknown:
        errors.append("unsupported host result fields: " + ", ".join(unknown))
    if result.get("schema_version") != LOOPX_TURN_RESULT_SCHEMA_VERSION:
        errors.append("unsupported host result schema_version")

    transaction = (
        plan.get("transaction") if isinstance(plan.get("transaction"), dict) else {}
    )
    turn_key = str(transaction.get("turn_key") or "")
    if not turn_key or str(result.get("turn_key") or "") != turn_key:
        errors.append("host result turn_key does not match the transaction plan")

    try:
        kind = LoopXTurnResultKind(str(result.get("result_kind") or ""))
    except ValueError:
        kind = None
        errors.append("unsupported host result kind")
    if (
        kind is LoopXTurnResultKind.VALIDATED_COMPLETION
        and not completion_lifecycle_configured
    ):
        errors.append("validated_completion requires a todo lifecycle adapter")
    if (
        kind not in MATERIAL_HOST_RESULT_KINDS | STOP_HOST_RESULT_KINDS
        and kind is not LoopXTurnResultKind.VALIDATED_COMPLETION
    ):
        errors.append("host result kind is not accepted by run-once")

    phases = result.get("completed_phases")
    if phases != list(TRANSACTION_PHASES[:2]):
        errors.append("host result completed_phases must be host_execute, typed_result")

    material = kind in MATERIAL_HOST_RESULT_KINDS
    normalized = {
        "schema_version": LOOPX_TURN_RESULT_SCHEMA_VERSION,
        "turn_key": turn_key,
        "result_kind": kind.value if kind else None,
        "completed_phases": list(TRANSACTION_PHASES[:2]),
    }
    for field, limit in HOST_RESULT_TEXT_LIMITS:
        text = _bounded_public_text(
            result,
            field,
            limit=limit,
            required=material and field not in {"summary", "vision_unchanged_reason"},
            errors=errors,
        )
        if text:
            normalized[field] = text
    reflection_json = _bounded_public_text(
        result,
        "reward_memory_reflection_json",
        limit=HOST_REWARD_MEMORY_REFLECTION_JSON_MAX_CHARS,
        required=False,
        errors=errors,
    )
    if reflection_json:
        if material:
            normalized["reward_memory_reflection_json"] = reflection_json
        else:
            errors.append(
                "non-material host results cannot declare a reward memory reflection"
            )
    if material:
        try:
            normalized["delivery_batch_scale"] = require_delivery_batch_scale(
                result.get("delivery_batch_scale")
            ).value
        except ValueError as exc:
            errors.append(str(exc))
        try:
            normalized["delivery_outcome"] = require_delivery_outcome(
                result.get("delivery_outcome")
            ).value
        except ValueError as exc:
            errors.append(str(exc))

        unchanged_reason = str(normalized.get("vision_unchanged_reason") or "")
        path_delta_mode, agent_vision = _normalize_host_path_delta(
            plan,
            result,
            unchanged_reason=unchanged_reason,
            errors=errors,
        )
        if (
            path_delta_mode == "material_replan"
            and kind is not LoopXTurnResultKind.REPLAN_REQUIRED
        ):
            errors.append(
                "material_replan path_delta_mode requires result_kind replan_required"
            )

        normalized["path_delta_mode"] = path_delta_mode
        if agent_vision is not None:
            normalized["agent_vision"] = agent_vision
    elif (
        str(result.get("path_delta_mode") or "").strip()
        or str(result.get("agent_vision_json") or "").strip()
    ):
        errors.append(
            "wait and user_action_required results cannot declare a path delta"
        )
    if subagent.subagent_execution_topology(plan) is not None:
        subagent.observe_subagent_host_result(plan, result, normalized, errors)
    return {
        "ok": not errors,
        "result": normalized,
        "errors": errors,
    }


def _task_validation_receipt(
    *,
    status: str,
    validator_kind: str,
    summary: str,
    recovery_kind: str | None = None,
    exit_code: int | None = None,
    reward_memory_reflection_validation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    if status not in {
        "passed",
        "progress",
        "failed",
        "inconclusive",
        "unavailable",
        "not_required",
    }:
        errors.append("unsupported task validation status")
    if not validator_kind or len(validator_kind) > 80:
        errors.append("task validator kind must contain at most 80 characters")
    if not summary or len(summary) > 240:
        errors.append("task validation summary must contain at most 240 characters")
    for field, text in (("validator_kind", validator_kind), ("summary", summary)):
        if text:
            try:
                validate_public_safe_text(f"task_validation.{field}", text)
            except ValueError as exc:
                errors.append(str(exc))
    if recovery_kind is not None and recovery_kind not in {
        LoopXTurnResultKind.REPAIR_REQUIRED.value,
        LoopXTurnResultKind.REPLAN_REQUIRED.value,
    }:
        errors.append(
            "task validation recovery_kind must be repair_required or replan_required"
        )
    if status in {"failed", "inconclusive", "unavailable"} and recovery_kind is None:
        errors.append("failed task validation requires a typed recovery_kind")
    if status in {"passed", "progress", "not_required"} and recovery_kind is not None:
        errors.append("successful task validation cannot declare recovery_kind")
    if exit_code is not None and (not isinstance(exit_code, int) or exit_code < 0):
        errors.append("task validation exit_code must be a non-negative integer")
    if status == "passed" and exit_code not in {None, 0}:
        errors.append("passed task validation cannot declare a non-zero exit_code")
    if status == "progress" and exit_code in {None, 0}:
        errors.append("progress task validation requires a non-zero exit_code")
    effective_status = status if not errors else "inconclusive"
    effective_recovery_kind = recovery_kind
    if errors and effective_recovery_kind is None:
        effective_recovery_kind = LoopXTurnResultKind.REPAIR_REQUIRED.value
    receipt = {
        "ok": not errors and status in {"passed", "progress", "not_required"},
        "schema_version": LOOPX_TURN_TASK_VALIDATION_SCHEMA_VERSION,
        "status": effective_status,
        "validator_kind": validator_kind or "invalid",
        "summary": summary or "task validation receipt is invalid",
        "recovery_kind": effective_recovery_kind,
        "exit_code": exit_code,
        "errors": errors,
    }
    if reward_memory_reflection_validation is not None:
        receipt["reward_memory_reflection_validation"] = dict(
            reward_memory_reflection_validation
        )
    return receipt


def _run_task_validator(
    plan: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    validator: TaskValidator | None,
) -> dict[str, Any]:
    if validator is None:
        return _task_validation_receipt(
            status="unavailable",
            validator_kind="none",
            summary="independent task validator is required for material host results",
            recovery_kind=LoopXTurnResultKind.REPAIR_REQUIRED.value,
        )
    try:
        value = validator(plan, result)
    except Exception:  # noqa: BLE001 - validator plugins fail closed at this boundary
        return _task_validation_receipt(
            status="inconclusive",
            validator_kind="callback",
            summary="independent task validator did not produce a receipt",
            recovery_kind=LoopXTurnResultKind.REPAIR_REQUIRED.value,
        )
    if not isinstance(value, Mapping):
        return _task_validation_receipt(
            status="inconclusive",
            validator_kind="callback",
            summary="independent task validator returned an invalid receipt",
            recovery_kind=LoopXTurnResultKind.REPAIR_REQUIRED.value,
        )
    unknown = sorted(
        set(value)
        - {
            "status",
            "validator_kind",
            "summary",
            "recovery_kind",
            "exit_code",
            "reward_memory_reflection_validation",
        }
    )
    if unknown:
        return _task_validation_receipt(
            status="inconclusive",
            validator_kind="callback",
            summary="independent task validator returned unsupported receipt fields",
            recovery_kind=LoopXTurnResultKind.REPAIR_REQUIRED.value,
        )
    status = str(value.get("status") or "")
    if status == "not_required":
        return _task_validation_receipt(
            status="inconclusive",
            validator_kind="callback",
            summary="material host results cannot skip independent task validation",
            recovery_kind=LoopXTurnResultKind.REPAIR_REQUIRED.value,
        )
    exit_code_value = value.get("exit_code")
    if exit_code_value is not None and (
        not isinstance(exit_code_value, int) or isinstance(exit_code_value, bool)
    ):
        return _task_validation_receipt(
            status="inconclusive",
            validator_kind="callback",
            summary="independent task validator returned an invalid exit code",
            recovery_kind=LoopXTurnResultKind.REPAIR_REQUIRED.value,
        )
    reflection_validation = None
    if value.get("reward_memory_reflection_validation") is not None:
        try:
            reflection_validation = normalize_reward_memory_reflection_validation(
                value["reward_memory_reflection_validation"],
                result=result,
            )
        except ValueError:
            return _task_validation_receipt(
                status="inconclusive",
                validator_kind="callback",
                summary="independent reward memory reflection validation is invalid",
                recovery_kind=LoopXTurnResultKind.REPAIR_REQUIRED.value,
            )
    return _task_validation_receipt(
        status=status,
        validator_kind=str(value.get("validator_kind") or ""),
        summary=str(value.get("summary") or ""),
        recovery_kind=(
            str(value["recovery_kind"])
            if value.get("recovery_kind") is not None
            else None
        ),
        exit_code=exit_code_value,
        reward_memory_reflection_validation=reflection_validation,
    )


def inspect_loopx_turn_journal(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    turn_key: str,
    retry_failed: bool = False,
    session_binding_resolver: SessionBindingResolver | None = None,
) -> dict[str, object]:
    """Inspect one canonical Turn journal without granting execution authority."""

    safe_goal_id = validate_goal_id_path_segment(goal_id)
    if not agent_id or agent_id != agent_id.strip():
        raise ValueError("agent_id must be a non-empty exact identity")
    path = turn_journal_path(
        runtime_root,
        goal_id=safe_goal_id,
        turn_key=turn_key,
    )
    try:
        with exclusive_file_lock(path):
            journal = _load_journal(path)
    except json.JSONDecodeError:
        raise ValueError("LoopX Turn journal contains malformed JSON") from None
    except LockAcquireTimeoutError:
        raise
    except OSError:
        raise ValueError("LoopX Turn journal could not be read") from None
    if journal is None:
        raise ValueError("LoopX Turn journal does not exist")

    request: dict[str, Any] = {}
    session_check = None
    recovery_error = None
    assess_session = True
    if retry_failed and journal.get("status") == "failed":
        plan = journal.get("plan")
        if isinstance(plan, Mapping):
            try:
                request = build_loopx_turn_host_request(plan)
            except (TypeError, ValueError) as exc:
                assess_session = False
                recovery_error = ValueError("failed-Turn Host request is invalid")
                session_check = {
                    "kind": "host_session_binding",
                    "outcome": "failed",
                    "reason": "host_request_invalid",
                }
                recovery_error.__cause__ = exc

    return assess_existing_turn_recovery(
        journal,
        request,
        goal_id=safe_goal_id,
        agent_id=agent_id,
        turn_key=turn_key,
        retry_failed=retry_failed,
        session_binding_resolver=session_binding_resolver,
        assess_session=assess_session,
        session_recovery_check=session_check,
        recovery_error=recovery_error,
    ).inspection


def _receipt(
    plan: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    completed_phases: Sequence[str],
    failure_kind: LoopXTurnResultKind | None = None,
    failed_phase: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": LOOPX_TURN_RESULT_SCHEMA_VERSION,
        "turn_key": result.get("turn_key"),
        "result_kind": (
            failure_kind.value if failure_kind else result.get("result_kind")
        ),
        "completed_phases": list(completed_phases),
        "failed_phase": failed_phase,
    }
    return validate_loopx_turn_receipt(
        plan.get("transaction") if isinstance(plan.get("transaction"), dict) else {},
        payload,
    )


def _host_failure(
    plan: Mapping[str, Any],
    *,
    kind: LoopXTurnResultKind,
    completed_phases: Sequence[str],
    failed_phase: str,
    reason: str,
) -> dict[str, Any]:
    transaction = (
        plan.get("transaction") if isinstance(plan.get("transaction"), dict) else {}
    )
    result = {"turn_key": transaction.get("turn_key"), "result_kind": kind.value}
    return {
        "ok": False,
        "reason": reason,
        "receipt": _receipt(
            plan,
            result,
            completed_phases=completed_phases,
            failure_kind=kind,
            failed_phase=failed_phase,
        ),
    }


def _run_host(
    request: Mapping[str, Any],
    *,
    argv: Sequence[str],
    project: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            list(argv),
            cwd=project,
            input=json.dumps(request, ensure_ascii=False, separators=(",", ":")),
            text=True, encoding="utf-8", errors="replace",
            capture_output=True,
            timeout=max(1.0, timeout_seconds),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "reason": type(exc).__name__, "returncode": None}
    if completed.returncode != 0:
        return {
            "ok": False,
            "reason": "host command returned non-zero",
            "returncode": completed.returncode,
            "stderr_chars": len(completed.stderr),
        }
    encoded = completed.stdout.encode("utf-8")
    if len(encoded) > HOST_RESULT_MAX_BYTES:
        return {
            "ok": False,
            "reason": "host stdout exceeded the result budget",
            "returncode": 0,
        }
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "reason": "host stdout is not one JSON value",
            "returncode": 0,
        }
    if not isinstance(value, dict):
        return {
            "ok": False,
            "reason": "host stdout must be one JSON object",
            "returncode": 0,
        }
    return {"ok": True, "value": value, "returncode": 0}


def _run_host_runner(
    request: Mapping[str, Any],
    *,
    runner: HostRunner,
) -> dict[str, Any]:
    try:
        value = runner(request)
    except BuiltInHostError as exc:
        return {
            "ok": False,
            "reason": exc.reason,
            "returncode": None,
            "failure_kind": exc.failure_kind,
            **(
                {"recovery_kind": exc.recovery_kind}
                if exc.recovery_kind is not None
                else {}
            ),
        }
    except Exception as exc:  # noqa: BLE001 - host adapters fail closed at boundary
        return {"ok": False, "reason": type(exc).__name__, "returncode": None}
    if not isinstance(value, dict):
        return {"ok": False, "reason": "built-in host result must be one JSON object"}
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) > HOST_RESULT_MAX_BYTES:
        return {
            "ok": False,
            "reason": "built-in host result exceeded the result budget",
        }
    return {"ok": True, "value": value, "returncode": 0}


def _compact_callback(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: payload.get(key)
        for key in (
            "ok",
            "appended",
            "classification",
            "generated_at",
            "slots",
            "reason",
        )
        if key in payload
    }


def _host_result_stage(
    plan: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    host_runner: HostRunner | None,
    argv: Sequence[str] | None,
    completion_lifecycle_configured: bool,
    project: Path,
    timeout_seconds: float,
    journal: dict[str, Any],
    journal_path: Path,
    effects: dict[str, bool],
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any] | None]:
    completed_phases = list(journal.get("completed_phases") or [])
    result = (
        journal.get("host_result")
        if isinstance(journal.get("host_result"), dict)
        else None
    )
    if "typed_result" not in completed_phases:
        journal["host_attempt_count"] = int(journal.get("host_attempt_count") or 0) + 1
        _write_journal(journal_path, journal)
        host_observation = (
            _run_host_runner(request, runner=host_runner)
            if host_runner is not None
            else _run_host(
                request,
                argv=argv or [],
                project=project,
                timeout_seconds=timeout_seconds,
            )
        )
        effects["host_invoked"] = True
        if not host_observation.get("ok"):
            failure = _host_failure(
                plan,
                kind=LoopXTurnResultKind.HOST_FAILURE,
                completed_phases=[],
                failed_phase="host_execute",
                reason=str(host_observation.get("reason") or "host execution failed"),
            )
            journal.update(
                status="failed",
                reason=failure["reason"],
                receipt=failure["receipt"],
                completed_phases=[],
                result_kind=LoopXTurnResultKind.HOST_FAILURE.value,
            )
            record_host_failure(
                journal,
                kind=str(host_observation.get("failure_kind") or "unknown"),
            )
            recovery_kind = host_observation.get("recovery_kind")
            if recovery_kind is not None:
                journal["host_recovery"] = build_host_recovery_record(recovery_kind)
            else:
                journal.pop("host_recovery", None)
            _write_journal(journal_path, journal)
            return (
                None,
                [],
                execution_payload(
                    plan,
                    journal,
                    execute=True,
                    replayed=False,
                    effects=effects,
                ),
            )
        result = dict(host_observation["value"])
        completed_phases = list(TRANSACTION_PHASES[:2])

    validation = validate_loopx_turn_host_result(
        plan,
        result or {},
        completion_lifecycle_configured=completion_lifecycle_configured,
    )
    if not validation.get("ok"):
        failure = _host_failure(
            plan,
            kind=LoopXTurnResultKind.VALIDATION_FAILED,
            completed_phases=list(TRANSACTION_PHASES[:2]),
            failed_phase="validation",
            reason="; ".join(
                validation.get("errors") or ["host result validation failed"]
            ),
        )
        journal.update(
            status="failed",
            reason=failure["reason"],
            receipt=failure["receipt"],
            completed_phases=list(TRANSACTION_PHASES[:2]),
            result_kind=LoopXTurnResultKind.VALIDATION_FAILED.value,
            validation_stage="host_result_contract",
        )
        _write_journal(journal_path, journal)
        return (
            None,
            list(TRANSACTION_PHASES[:2]),
            execution_payload(
                plan,
                journal,
                execute=True,
                replayed=False,
                effects=effects,
            ),
        )

    normalized = dict(validation["result"])
    if len(completed_phases) < 2:
        completed_phases = list(TRANSACTION_PHASES[:2])
    journal.update(
        host_result=normalized,
        result_kind=normalized.get("result_kind"),
        completed_phases=completed_phases,
    )
    _write_journal(journal_path, journal)
    return normalized, completed_phases, None


def _task_validation_stage(
    plan: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    task_validator: TaskValidator | None,
    completed_phases: list[str],
    journal: dict[str, Any],
    journal_path: Path,
    effects: dict[str, bool],
) -> tuple[list[str], dict[str, Any] | None]:
    turn = interpret_turn_result_packet(result)
    kind = LoopXTurnResultKind(turn.observation.decision)
    if kind in STOP_HOST_RESULT_KINDS:
        completed_phases = list(TRANSACTION_PHASES[:3])
        journal.update(
            status="stopped",
            completed_phases=completed_phases,
            task_validation=_task_validation_receipt(
                status="not_required",
                validator_kind="stop_result",
                summary="task validation is not required for a typed stop result",
            ),
            receipt=_receipt(plan, result, completed_phases=completed_phases),
            scheduler={"disposition": "not_applicable"},
        )
        _write_journal(journal_path, journal)
        return completed_phases, execution_payload(
            plan,
            journal,
            execute=True,
            replayed=False,
            effects=effects,
        )

    stored_task_validation = (
        journal.get("task_validation")
        if isinstance(journal.get("task_validation"), dict)
        else None
    )
    task_validation = (
        stored_task_validation
        if "validation" in completed_phases
        and stored_task_validation is not None
        and stored_task_validation.get("ok") is True
        else _run_task_validator(
            plan,
            result,
            validator=task_validator,
        )
    )
    journal["task_validation"] = task_validation
    if not task_validation.get("ok"):
        reason = str(
            task_validation.get("summary") or "independent task validation failed"
        )
        failure = _host_failure(
            plan,
            kind=LoopXTurnResultKind.VALIDATION_FAILED,
            completed_phases=list(TRANSACTION_PHASES[:2]),
            failed_phase="validation",
            reason=reason,
        )
        journal.update(
            status="failed",
            reason=reason,
            receipt=failure["receipt"],
            completed_phases=list(TRANSACTION_PHASES[:2]),
            result_kind=LoopXTurnResultKind.VALIDATION_FAILED.value,
            validation_stage="task_postcondition",
        )
        _write_journal(journal_path, journal)
        return list(TRANSACTION_PHASES[:2]), execution_payload(
            plan,
            journal,
            execute=True,
            replayed=False,
            effects=effects,
        )

    if "validation" not in completed_phases:
        completed_phases = list(TRANSACTION_PHASES[:3])
    journal.update(
        result_kind=result.get("result_kind"),
        completed_phases=completed_phases,
        validation_stage="task_postcondition",
    )
    _write_journal(journal_path, journal)
    return completed_phases, None


def _ensure_turn_settlement_plan(
    plan: Mapping[str, Any],
    transaction_plan: dict[str, Any],
) -> None:
    if isinstance(transaction_plan.get("settlement_plan"), Mapping):
        return
    envelope = plan.get("turn_envelope")
    if not isinstance(envelope, Mapping):
        return
    selected_todo = selected_turn_todo(envelope)
    lineage = {
        "goal_id": str(envelope.get("goal_id") or ""),
        "agent_id": str(envelope.get("agent_id") or ""),
        "todo_id": str(selected_todo.get("todo_id") or ""),
    }
    if not all(lineage.values()):
        return
    host = plan.get("host")
    host_fields = host if isinstance(host, Mapping) else {}
    built = build_loopx_turn_transaction_plan(
        planned=True,
        lineage=lineage,
        host=str(host_fields.get("kind") or "generic-cli"),
        execution_mode=str(host_fields.get("execution_mode") or "isolated-headless"),
        session_action=str(host_fields.get("session_action") or "resume"),
        turn_instance_id=(
            transaction_plan.get("turn_instance_id")
            or transaction_plan.get("turn_key")
        ),
    )
    settlement_plan = built.get("settlement_plan")
    if isinstance(settlement_plan, Mapping):
        transaction_plan["settlement_plan"] = settlement_plan


def _typed_settlement_stage(
    plan: Mapping[str, Any],
    result: dict[str, Any],
    *,
    completed_phases: list[str],
    journal: dict[str, Any],
    journal_path: Path,
    effects: dict[str, bool],
    writeback: Writeback,
    completion_writeback: CompletionWriteback | None,
    completion_intent: CompletionIntent | None,
    terminal_closeout: TerminalCloseout | None,
    spend: Spend,
    effect_resolvers: Mapping[SettlementStepKind, TurnEffectResolver],
    scheduler: Scheduler,
    post_settlement: PostSettlement | None,
) -> dict[str, Any]:
    transaction_plan = (
        plan.get("transaction") if isinstance(plan.get("transaction"), Mapping) else {}
    )

    terminal_closeout_required = False
    completion_intent_error: str | None = None
    if result.get("result_kind") == LoopXTurnResultKind.VALIDATED_COMPLETION.value:
        if (
            completion_writeback is None
            or completion_intent is None
            or terminal_closeout is None
        ):
            raise ValueError(
                "validated_completion requires intent, lifecycle writeback, "
                "and terminal closeout adapters"
            )
        terminal_closeout_required, completion_intent_error = (
            terminal_closeout_requirement(
                plan=plan,
                result=result,
                journal=journal,
                completion_intent=completion_intent,
            )
        )

    def writeback_effect(effect_ref: str) -> Mapping[str, Any]:
        if completion_intent_error:
            return {
                "ok": False,
                "appended": False,
                "reason": completion_intent_error,
            }
        if (
            result.get("result_kind") == LoopXTurnResultKind.VALIDATED_COMPLETION.value
            and not terminal_closeout_required
        ):
            if completion_writeback is None:
                raise ValueError(
                    "validated_completion requires a todo lifecycle adapter"
                )
            callback_payload = invoke_result_effect(
                completion_writeback, result, effect_ref
            )
            completion_outcome = completion_writeback_outcome(
                callback_payload,
                plan=plan,
            )
            if completion_outcome is None:
                return {
                    "ok": False,
                    "appended": False,
                    "reason": str(
                        callback_payload.get("reason")
                        or callback_payload.get("error")
                        or (
                            "todo lifecycle adapter returned an invalid "
                            "completion outcome"
                        )
                    ),
                }
            return {
                **callback_payload,
                "completion": completion_outcome,
            }
        return invoke_result_effect(writeback, result, effect_ref)

    journal_adapter = TurnSettlementJournalAdapter(
        journal,
        effects,
        lambda: _write_journal(journal_path, journal),
        _compact_callback,
    )

    terminal_effect = None
    terminal_checkpoint = None
    if terminal_closeout_required:
        assert terminal_closeout is not None
        terminal_effect = verified_terminal_closeout_effect(
            terminal_closeout, result=result, plan=plan
        )
        terminal_checkpoint = journal_adapter.checkpoint_terminal

    settlement_result = execute_turn_driver_settlement(
        transaction_plan,
        transaction_phases=TRANSACTION_PHASES,
        completed_phases=completed_phases,
        writeback_payload=(
            journal.get("writeback")
            if isinstance(journal.get("writeback"), Mapping)
            else None
        ),
        quota_spend_payload=(
            journal.get("quota_spend")
            if isinstance(journal.get("quota_spend"), Mapping)
            else None
        ),
        writeback=writeback_effect,
        spend=spend,
        checkpoint=journal_adapter.checkpoint,
        committed_effect_id=_journal_committed_effect_id(journal),
        terminal_closeout_required=terminal_closeout_required,
        terminal_closeout_payload=(
            journal.get("terminal_closeout")
            if isinstance(journal.get("terminal_closeout"), Mapping)
            else None
        ),
        terminal_closeout=terminal_effect,
        terminal_checkpoint=terminal_checkpoint,
        prepare=journal_adapter.prepare,
        abort=journal_adapter.abort,
        effect_attempts=journal_adapter.effect_attempts,
        effect_resolvers=effect_resolvers,
        turn_result_kind=str(result.get("result_kind") or "") or None,
    )

    journal["settlement_result"] = settlement_result_payload(settlement_result)
    if settlement_result.failure is not None:
        result_kind, completed_phases, failed_phase = turn_settlement_failure_outcome(
            settlement_result
        )
        failure = _host_failure(
            plan,
            kind=result_kind,
            completed_phases=completed_phases,
            failed_phase=failed_phase,
            reason=settlement_result.failure.reason,
        )
        journal.update(
            status="failed",
            result_kind=result_kind.value,
            reason=failure["reason"],
            receipt=failure["receipt"],
        )
        _write_journal(journal_path, journal)
        return execution_payload(
            plan,
            journal,
            execute=True,
            replayed=False,
            effects=effects,
        )

    settlement_state = settlement_result.value
    if settlement_state is None or settlement_state.quota_spend is None:
        raise ValueError(
            "typed Turn settlement completed without a quota spend receipt"
        )
    outcome = turn_settlement_outcome(settlement_result)
    if outcome is None:
        raise RuntimeError("TypeScript Turn settlement omitted its canonical outcome")
    result = {**result, "result_kind": outcome["result_kind"]}
    completed_phases = [str(phase) for phase in outcome["completed_phases"]]
    spend_payload = dict(settlement_state.quota_spend)
    _write_journal(journal_path, journal)

    scheduler_payload = scheduler(spend_payload)
    journal["scheduler"] = scheduler_payload
    run_post_settlement_callback(
        plan=plan,
        result=result,
        post_settlement=post_settlement,
        journal=journal,
        journal_path=journal_path,
    )
    if scheduler_payload.get("completed") is not True:
        journal.update(
            status="scheduler_action_required",
            receipt=_receipt(plan, result, completed_phases=completed_phases),
        )
        _write_journal(journal_path, journal)
        return execution_payload(
            plan,
            journal,
            execute=True,
            replayed=False,
            effects=effects,
        )

    completed_phases = list(TRANSACTION_PHASES)
    effects["scheduler_acknowledged"] = bool(scheduler_payload.get("acknowledged"))
    journal.update(
        status="committed",
        completed_phases=completed_phases,
        receipt=_receipt(plan, result, completed_phases=completed_phases),
    )
    _write_journal(journal_path, journal)
    return execution_payload(
        plan,
        journal,
        execute=True,
        replayed=False,
        effects=effects,
    )


@single_executor_per_turn_lane(execution_payload)
def run_loopx_turn_once(
    plan: Mapping[str, Any],
    *,
    host_argv: Sequence[str] | None = None,
    host_runner: HostRunner | None = None,
    session_binding_resolver: SessionBindingResolver | None = None,
    project: Path,
    runtime_root: Path,
    goal_id: str,
    timeout_seconds: float,
    execute: bool,
    retry_failed: bool = False,
    task_validator: TaskValidator | None = None,
    writeback: Writeback | None = None,
    completion_writeback: CompletionWriteback | None = None,
    completion_intent: CompletionIntent | None = None,
    terminal_closeout: TerminalCloseout | None = None,
    spend: Spend | None = None,
    writeback_resolver: TurnEffectResolver | None = None,
    spend_resolver: TurnEffectResolver | None = None,
    terminal_closeout_resolver: TurnEffectResolver | None = None,
    scheduler: Scheduler | None = None,
    post_settlement: PostSettlement | None = None,
) -> dict[str, Any]:
    if host_runner is not None and host_argv is not None:
        raise ValueError("run-once accepts either host_argv or host_runner, not both")
    if host_runner is None:
        argv = normalize_host_argv(host_argv or [])
        host_projection = {"executable": Path(argv[0]).name, "argv_count": len(argv)}
    else:
        argv = None
        planned_host = plan.get("host") if isinstance(plan.get("host"), dict) else {}
        host_projection = {
            "executable": "built-in",
            "kind": str(planned_host.get("kind") or "codex-cli"),
        }
    transaction_plan = (
        plan.get("transaction") if isinstance(plan.get("transaction"), dict) else {}
    )
    _ensure_turn_settlement_plan(plan, transaction_plan)
    request = build_loopx_turn_host_request(plan)
    empty_effects = {
        "host_invoked": False,
        "state_written": False,
        "quota_spent": False,
        "scheduler_acknowledged": False,
    }
    fail_closed = managed_executor_unavailable_payload(
        plan, execute=execute, host_projection=host_projection
    )
    if fail_closed is not None:
        # Refuse before journal/host/quota when the planned executor cannot launch.
        return execution_payload(
            plan, fail_closed, execute=True, replayed=False, effects=empty_effects
        )
    if not execute:
        preview = {
            "schema_version": LOOPX_TURN_JOURNAL_SCHEMA_VERSION,
            "status": "preview",
            "host": host_projection,
            "result_kind": None,
            "receipt": None,
            "scheduler": {"disposition": "not_evaluated"},
        }
        return execution_payload(
            plan,
            preview,
            execute=False,
            replayed=False,
            effects=empty_effects,
        )
    if writeback is None or spend is None or scheduler is None:
        raise ValueError(
            "executing run-once requires writeback, spend, and scheduler callbacks"
        )

    turn_key = str(request["turn_key"])
    journal_path = turn_journal_path(runtime_root, goal_id=goal_id, turn_key=turn_key)
    with exclusive_file_lock(journal_path):
        journal = _load_journal(journal_path)
        recovery_decision: dict[str, Any] | None = None
        if journal is not None:
            envelope = (
                plan.get("turn_envelope")
                if isinstance(plan.get("turn_envelope"), Mapping)
                else {}
            )
            agent_id = str(envelope.get("agent_id") or "")
            assessment = assess_existing_turn_recovery(
                journal,
                request,
                goal_id=goal_id,
                agent_id=agent_id,
                turn_key=turn_key,
                retry_failed=retry_failed,
                session_binding_resolver=session_binding_resolver,
            )
            recovery_decision = assessment.decision
            action = recovery_decision.get("action")
            if action == "return_existing":
                payload = execution_payload(
                    plan,
                    journal,
                    execute=True,
                    replayed=True,
                    effects=empty_effects,
                )
                payload["recovery"] = build_turn_recovery_audit(
                    recovery_decision,
                    journal,
                    status="finished",
                    host_invoked=False,
                )
                return payload
            request = require_turn_recovery_continuation(assessment)
            journal["recovery_audit"] = build_turn_recovery_audit(
                recovery_decision,
                journal,
                status="started",
                host_invoked=None,
            )
            _write_journal(journal_path, journal)

        if journal and journal.get("status") == "failed":
            receipt = (
                journal.get("receipt")
                if isinstance(journal.get("receipt"), dict)
                else {}
            )
            if receipt.get("failed_phase") == "validation":
                if journal.get("validation_stage") != "task_postcondition":
                    journal.pop("host_result", None)
                journal.pop("result_kind", None)
                journal["completed_phases"] = (
                    list(TRANSACTION_PHASES[:2])
                    if isinstance(journal.get("host_result"), dict)
                    else []
                )
                journal.pop("task_validation", None)
                journal.pop("validation_stage", None)
            journal.pop("reason", None)
            journal.pop("receipt", None)
            journal.pop("host_recovery", None)
            journal.pop("host_failure", None)
            journal["status"] = "in_progress"
            _write_journal(journal_path, journal)
        if journal is None:
            journal = {
                "schema_version": LOOPX_TURN_JOURNAL_SCHEMA_VERSION,
                "turn_key": turn_key,
                "goal_id": goal_id,
                "status": "in_progress",
                "host": host_projection,
                "completed_phases": [],
                "plan": dict(plan),
            }
            _write_journal(journal_path, journal)

        effects = dict(empty_effects)

        def finish_recovery(payload: dict[str, Any]) -> dict[str, Any]:
            if recovery_decision is None:
                return payload
            journal["recovery_audit"] = build_turn_recovery_audit(
                recovery_decision,
                journal,
                status="finished",
                host_invoked=effects.get("host_invoked") is True,
            )
            _write_journal(journal_path, journal)
            payload["recovery"] = dict(journal["recovery_audit"])
            return payload

        result, completed_phases, terminal = _host_result_stage(
            plan,
            request,
            host_runner=host_runner,
            argv=argv,
            completion_lifecycle_configured=all(
                callback is not None
                for callback in (
                    completion_writeback,
                    completion_intent,
                    terminal_closeout,
                )
            ),
            project=project,
            timeout_seconds=timeout_seconds,
            journal=journal,
            journal_path=journal_path,
            effects=effects,
        )
        if terminal is not None:
            return finish_recovery(terminal)
        assert result is not None

        completed_phases, terminal = _task_validation_stage(
            plan,
            result,
            task_validator=task_validator,
            completed_phases=completed_phases,
            journal=journal,
            journal_path=journal_path,
            effects=effects,
        )
        if terminal is not None:
            return finish_recovery(terminal)

        settled = _typed_settlement_stage(
            plan,
            result,
            completed_phases=completed_phases,
            journal=journal,
            journal_path=journal_path,
            effects=effects,
            writeback=writeback,
            completion_writeback=completion_writeback,
            completion_intent=completion_intent,
            terminal_closeout=terminal_closeout,
            spend=spend,
            effect_resolvers=turn_effect_resolvers(
                writeback=writeback_resolver,
                spend=spend_resolver,
                terminal_closeout=terminal_closeout_resolver,
            ),
            scheduler=scheduler,
            post_settlement=post_settlement,
        )
        return finish_recovery(settled)
