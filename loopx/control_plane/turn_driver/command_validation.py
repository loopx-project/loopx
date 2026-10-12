"""Bounded host commands and Reward Memory validator attestations."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .transaction import LoopXTurnResultKind


HOST_ARG_MAX_COUNT = 32
HOST_ARG_MAX_CHARS = 1_024

TaskValidator = Callable[
    [Mapping[str, Any], Mapping[str, Any]],
    Mapping[str, Any],
]


def _normalize_argv(value: Sequence[str], *, label: str) -> list[str]:
    argv = [str(item) for item in value]
    if not argv:
        raise ValueError(f"{label} command must contain at least one argv item")
    if len(argv) > HOST_ARG_MAX_COUNT:
        raise ValueError(f"{label} command exceeds {HOST_ARG_MAX_COUNT} argv items")
    for item in argv:
        if not item or "\x00" in item or len(item) > HOST_ARG_MAX_CHARS:
            raise ValueError(
                f"{label} command contains an empty, NUL, or oversized argv item"
            )
    return argv


def normalize_host_argv(value: Sequence[str]) -> list[str]:
    return _normalize_argv(value, label="host")


def reward_memory_reflection_digest(value: object) -> str:
    """Bind a validator attestation to the exact typed host reflection."""

    text = str(value or "").strip()
    if not text:
        raise ValueError("reward memory reflection is required")
    try:
        reflection = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("reward memory reflection must be JSON") from exc
    if not isinstance(reflection, Mapping):
        raise ValueError("reward memory reflection must decode to an object")
    canonical = json.dumps(
        reflection,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_reward_memory_reflection_validation(
    value: object,
    *,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("reward memory reflection validation must be an object")
    allowed = {"schema_version", "status", "reflection_digest", "evidence_refs"}
    if set(value) - allowed:
        raise ValueError("reward memory reflection validation has unsupported fields")
    if value.get("schema_version") != "reward_memory_reflection_validation_v0":
        raise ValueError("reward memory reflection validation schema is unsupported")
    if value.get("status") != "validated":
        raise ValueError("reward memory reflection validation status must be validated")
    reflection_json = result.get("reward_memory_reflection_json")
    expected_digest = reward_memory_reflection_digest(reflection_json)
    if value.get("reflection_digest") != expected_digest:
        raise ValueError("reward memory reflection validation digest does not match")
    reflection = json.loads(str(reflection_json))
    expected_refs = reflection.get("evidence_refs")
    refs = value.get("evidence_refs")
    if (
        not isinstance(refs, list)
        or any(not isinstance(ref, str) for ref in refs)
        or refs != expected_refs
    ):
        raise ValueError("reward memory reflection evidence refs were not validated")
    return {
        "schema_version": "reward_memory_reflection_validation_v0",
        "status": "validated",
        "reflection_digest": expected_digest,
        "evidence_refs": list(refs),
    }


def build_loopx_turn_command_validator(
    argv: Sequence[str],
    *,
    project: Path,
    timeout_seconds: float,
    failure_recovery_kind: str = LoopXTurnResultKind.REPAIR_REQUIRED.value,
) -> TaskValidator:
    """Build a trusted argv-only postcondition validator for one Turn workspace."""

    normalized = _normalize_argv(argv, label="task validator")
    if failure_recovery_kind not in {
        LoopXTurnResultKind.REPAIR_REQUIRED.value,
        LoopXTurnResultKind.REPLAN_REQUIRED.value,
    }:
        raise ValueError(
            "task validator failure recovery must be repair_required or replan_required"
        )

    def validate(
        _plan: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        try:
            completed = subprocess.run(
                normalized,
                cwd=project,
                input=json.dumps({**result, **({"delivery_result_context": _plan["delivery_result_context"]}
                    if _plan.get("delivery_result_context") is not None else {})},
                    ensure_ascii=False, separators=(",", ":")),
                text=True, encoding="utf-8", errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=max(1.0, timeout_seconds),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {
                "status": "inconclusive",
                "validator_kind": "command",
                "summary": "independent task validation command could not complete",
                "recovery_kind": LoopXTurnResultKind.REPAIR_REQUIRED.value,
            }
        if completed.returncode != 0:
            return {
                "status": "failed",
                "validator_kind": "command",
                "summary": "independent task validation command returned non-zero",
                "recovery_kind": failure_recovery_kind,
                "exit_code": completed.returncode,
            }
        receipt: dict[str, Any] = {
            "status": "passed",
            "validator_kind": "command",
            "summary": "independent task validation command passed",
            "exit_code": 0,
        }
        stdout = completed.stdout.strip()
        if stdout and len(stdout.encode("utf-8")) <= 4_096:
            try:
                reflection_validation = json.loads(stdout)
            except json.JSONDecodeError:
                reflection_validation = None
            if (
                isinstance(reflection_validation, Mapping)
                and reflection_validation.get("schema_version")
                == "reward_memory_reflection_validation_v0"
            ):
                receipt["reward_memory_reflection_validation"] = dict(
                    reflection_validation
                )
        return receipt

    return validate
