from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...history import load_registry
from ...materials import find_registry_goal, goal_state_path

from ...control_plane.coordination.local_authority import (
    read_canonical_todos_if_promoted,
)
from ...control_plane.todos.active_state_editing import find_todo_block
from ...control_plane.todos.completion_validation import (
    resolve_private_completion_validation_declaration,
)
from ...control_plane.todos.completion_validation_projection import (
    project_completion_validation_authority,
)
from .outcome_lifecycle import (
    _goal_repo,
    _identity_token,
    _reflection,
    _reflection_digest,
    _write_sidecar,
    run_configured_turn_outcome_ingest,
)


CODEX_APP_OUTCOME_CANDIDATE_SCHEMA_VERSION = (
    "codex_app_reward_memory_outcome_candidate_v0"
)
CODEX_APP_OUTCOME_SIDECAR_SCHEMA_VERSION = (
    "codex_app_reward_memory_outcome_sidecar_v0"
)
REFLECTION_VALIDATION_REQUEST_SCHEMA_VERSION = (
    "reward_memory_reflection_validation_request_v0"
)
REFLECTION_VALIDATION_SCHEMA_VERSION = "reward_memory_reflection_validation_v0"
_VALIDATION_TIMEOUT_DEFAULT_SECONDS = 20
_VALIDATION_TIMEOUT_MAX_SECONDS = 29


def _load_candidate_sidecar(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Codex App reward memory candidate sidecar is unreadable") from exc
    if not isinstance(value, dict):
        raise ValueError("Codex App reward memory candidate sidecar is invalid")
    return value


def codex_app_outcome_candidate_sidecar_path(
    repo: Path,
    *,
    goal_id: str,
    agent_id: str,
    candidate_id: str,
) -> Path:
    return (
        repo
        / ".local"
        / "loopx"
        / "reward-memory-app-outcomes"
        / _identity_token(goal_id, fallback="goal")
        / _identity_token(agent_id, fallback="agent")
        / f"{_identity_token(candidate_id, fallback='candidate')}.json"
    )


def _candidate_base(
    *,
    goal_id: str,
    agent_id: str,
    status: str,
    reason_code: str | None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": CODEX_APP_OUTCOME_CANDIDATE_SCHEMA_VERSION,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "status": status,
        "reason_code": reason_code,
        "candidate_id": None,
        "reflection_digest": None,
        "validation_bound": False,
        "raw_content_projected": False,
        "provider_sync_count": 0,
        "external_writes_performed": False,
        "host_wiring": "codex_app_refresh_stage",
        "fail_open": True,
    }


def _canonical_todo(
    *,
    runtime_root: Path,
    goal_id: str,
    todo_id: str,
    state_file: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    canonical = read_canonical_todos_if_promoted(
        runtime_root=runtime_root,
        goal_id=goal_id,
    )
    if canonical is not None:
        for item in canonical["todos"]:
            if item.get("todo_id") == todo_id:
                return dict(item), str(item.get("role") or "") or None
        return None, None
    try:
        lines = state_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None, None
    match = find_todo_block(lines, todo_id=todo_id)
    if match is None:
        return None, None
    role, _section, _start, _end, block = match
    source = dict(block)
    source["role"] = role
    return project_completion_validation_authority(source), role


def _validation_declaration(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    todo_id: str,
    state_file: Path,
) -> dict[str, Any] | None:
    # Refresh projections intentionally omit private state paths. Resolve the
    # declaration from this invocation's registered source, never the display
    # projection or a default registry. Keep the explicit path for callers whose
    # isolated registry has no matching Goal.
    goal = find_registry_goal(load_registry(registry_path), goal_id)
    if goal is not None:
        registered_state = goal_state_path(goal)
        if registered_state is not None:
            state_file = registered_state
    canonical_todo, role = _canonical_todo(
        runtime_root=runtime_root,
        goal_id=goal_id,
        todo_id=todo_id,
        state_file=state_file,
    )
    if canonical_todo is None:
        return None
    return resolve_private_completion_validation_declaration(
        canonical_todo=canonical_todo,
        state_file=state_file,
        runtime_root=runtime_root,
        registry_path=registry_path,
        goal_id=goal_id,
        todo_id=todo_id,
        role=role,
        persist_if_resolved=False,
    )


def _validator_argv(declaration: Mapping[str, Any]) -> list[str] | None:
    raw_argv = declaration.get("validation_command_argv")
    if isinstance(raw_argv, list) and raw_argv and all(
        isinstance(item, str) and item for item in raw_argv
    ):
        return list(raw_argv)
    command = declaration.get("validation_command")
    if not isinstance(command, str) or not command.strip():
        return None
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    return argv or None


def _run_reflection_validator(
    *,
    declaration: Mapping[str, Any],
    workspace: Path,
    request: Mapping[str, Any],
    reflection: Mapping[str, Any],
    reflection_digest: str,
) -> dict[str, Any]:
    argv = _validator_argv(declaration)
    if argv is None or not workspace.is_dir():
        return {
            "ok": False,
            "status": "unavailable",
            "validator_kind": "todo_declared_command",
            "summary": "Todo has no runnable reflection-aware validation command",
            "exit_code": None,
        }
    timeout = declaration.get("validation_timeout_seconds")
    timeout_seconds = (
        int(timeout)
        if isinstance(timeout, int)
        and not isinstance(timeout, bool)
        and 1 <= timeout <= _VALIDATION_TIMEOUT_MAX_SECONDS
        else _VALIDATION_TIMEOUT_DEFAULT_SECONDS
    )
    try:
        completed = subprocess.run(
            argv,
            cwd=workspace,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            input=json.dumps(
                request,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {
            "ok": False,
            "status": "inconclusive",
            "validator_kind": "todo_declared_command",
            "summary": "Todo reflection validation command did not complete",
            "exit_code": None,
        }
    if completed.returncode != 0:
        return {
            "ok": False,
            "status": "failed",
            "validator_kind": "todo_declared_command",
            "summary": "Todo reflection validation command returned non-zero",
            "exit_code": completed.returncode,
        }
    stdout = completed.stdout.strip()
    if not stdout or len(stdout.encode("utf-8")) > 4_096:
        validation = None
    else:
        try:
            validation = json.loads(stdout)
        except json.JSONDecodeError:
            validation = None
    allowed = {"schema_version", "status", "reflection_digest", "evidence_refs"}
    expected_refs = reflection.get("evidence_refs")
    if (
        not isinstance(validation, Mapping)
        or set(validation) - allowed
        or validation.get("schema_version") != REFLECTION_VALIDATION_SCHEMA_VERSION
        or validation.get("status") != "validated"
        or validation.get("reflection_digest") != reflection_digest
        or validation.get("evidence_refs") != expected_refs
    ):
        return {
            "ok": False,
            "status": "inconclusive",
            "validator_kind": "todo_declared_command",
            "summary": "Todo validator did not attest the exact reflection and evidence",
            "exit_code": 0,
        }
    return {
        "ok": True,
        "status": "passed",
        "validator_kind": "todo_declared_command",
        "summary": "Todo validator attested the exact reflection and evidence",
        "exit_code": 0,
        "reward_memory_reflection_validation": {
            "schema_version": REFLECTION_VALIDATION_SCHEMA_VERSION,
            "status": "validated",
            "reflection_digest": reflection_digest,
            "evidence_refs": list(expected_refs),
        },
    }


def stage_codex_app_turn_outcome_candidate(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    todo_id: str,
    turn_instance_id: str,
    effect_id: str,
    state_file: Path,
    validation_workspace: Path,
    reflection_json: str,
    observed_at: str,
) -> dict[str, Any]:
    """Stage private App reflection only; provider writes wait for spend readback."""

    if not all(
        str(value or "").strip()
        for value in (goal_id, agent_id, todo_id, turn_instance_id, effect_id)
    ):
        raise ValueError("Codex App reward memory candidate identity is incomplete")
    reflection = _reflection(reflection_json)
    if reflection is None or reflection.get("status") in {
        "no_evidence",
        "legacy_no_write",
    }:
        legacy = (
            reflection is not None and reflection.get("status") == "legacy_no_write"
        )
        return _candidate_base(
            goal_id=goal_id,
            agent_id=agent_id,
            status="no_eligible_evidence",
            reason_code=(
                "legacy_reflection_requires_transferable_experience"
                if legacy
                else "app_refresh_declared_no_reward_evidence"
            ),
        )
    digest = _reflection_digest(reflection_json)
    candidate_id = "app:" + hashlib.sha256(
        (
            f"{goal_id}\n{agent_id}\n{todo_id}\n{turn_instance_id}\n"
            f"{effect_id}\n{digest}"
        ).encode("utf-8")
    ).hexdigest()[:20]
    declaration = _validation_declaration(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        todo_id=todo_id,
        state_file=state_file,
    )
    validation_request = {
        "schema_version": REFLECTION_VALIDATION_REQUEST_SCHEMA_VERSION,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "todo_id": todo_id,
        "turn_instance_id": turn_instance_id,
        "effect_id": effect_id,
        "reflection": reflection,
        "reflection_digest": digest,
    }
    validation = (
        _run_reflection_validator(
            declaration=declaration,
            workspace=validation_workspace,
            request=validation_request,
            reflection=reflection,
            reflection_digest=digest,
        )
        if declaration is not None
        else {
            "ok": False,
            "status": "unavailable",
            "validator_kind": "none",
            "summary": "Todo has no caller-declared validation command",
            "exit_code": None,
        }
    )
    path = codex_app_outcome_candidate_sidecar_path(
        _goal_repo(registry_path, goal_id),
        goal_id=goal_id,
        agent_id=agent_id,
        candidate_id=candidate_id,
    )
    previous = _load_candidate_sidecar(path)
    if previous is not None and previous.get("schema_version") != (
        CODEX_APP_OUTCOME_SIDECAR_SCHEMA_VERSION
    ):
        raise ValueError("Codex App reward memory candidate sidecar is invalid")
    sidecar = {
        "schema_version": CODEX_APP_OUTCOME_SIDECAR_SCHEMA_VERSION,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "todo_id": todo_id,
        "turn_instance_id": turn_instance_id,
        "effect_id": effect_id,
        "candidate_id": candidate_id,
        "reflection": reflection,
        "reflection_digest": digest,
        "task_validation": validation,
        "observed_at": observed_at,
        "raw_content_captured": False,
    }
    if previous is not None:
        for field in (
            "goal_id",
            "agent_id",
            "todo_id",
            "turn_instance_id",
            "effect_id",
            "candidate_id",
            "reflection",
            "reflection_digest",
        ):
            if previous.get(field) != sidecar[field]:
                raise ValueError("Codex App reward memory candidate identity mismatch")
        previous_validation = previous.get("task_validation")
        if (
            isinstance(previous_validation, Mapping)
            and previous_validation.get("ok") is True
        ):
            validation = dict(previous_validation)
        elif validation.get("ok") is True:
            _write_sidecar(path, sidecar)
    else:
        _write_sidecar(path, sidecar)
    validation_bound = validation.get("ok") is True
    return _candidate_base(
        goal_id=goal_id,
        agent_id=agent_id,
        status=("validation_bound" if validation_bound else "awaiting_evidence_validation"),
        reason_code=(
            None if validation_bound else "reflection_not_bound_to_independent_validation"
        ),
    ) | {
        "candidate_id": candidate_id,
        "reflection_digest": digest,
        "validation_bound": validation_bound,
        "sidecar_receipt_reused": previous is not None,
    }


def run_staged_codex_app_turn_outcome_ingest(
    *,
    registry_path: Path,
    goal_id: str,
    agent_id: str,
    todo_id: str,
    turn_instance_id: str,
    effect_id: str,
    candidate_id: str,
    writeback_appended: bool,
    spend_appended: bool,
) -> dict[str, Any]:
    """Finalize one staged App reflection after exact writeback+spend readback."""

    path = codex_app_outcome_candidate_sidecar_path(
        _goal_repo(registry_path, goal_id),
        goal_id=goal_id,
        agent_id=agent_id,
        candidate_id=candidate_id,
    )
    value = _load_candidate_sidecar(path)
    if value is None or value.get("schema_version") != (
        CODEX_APP_OUTCOME_SIDECAR_SCHEMA_VERSION
    ):
        raise ValueError("Codex App reward memory candidate sidecar is unavailable")
    for field, expected in {
        "goal_id": goal_id,
        "agent_id": agent_id,
        "todo_id": todo_id,
        "turn_instance_id": turn_instance_id,
        "effect_id": effect_id,
        "candidate_id": candidate_id,
    }.items():
        if value.get(field) != expected:
            raise ValueError("Codex App reward memory candidate identity mismatch")
    reflection = value.get("reflection")
    if not isinstance(reflection, Mapping):
        raise ValueError("Codex App reward memory candidate reflection is invalid")
    receipt = run_configured_turn_outcome_ingest(
        registry_path=registry_path,
        goal_id=goal_id,
        agent_id=agent_id,
        turn_key=effect_id,
        host_result={
            "reward_memory_reflection_json": json.dumps(
                reflection,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        },
        settlement_evidence={
            "schema_version": "turn_post_settlement_evidence_v0",
            "task_validation": dict(value.get("task_validation") or {}),
            "writeback": {"ok": writeback_appended, "appended": writeback_appended},
            "quota_spend": {"ok": spend_appended, "appended": spend_appended},
        },
        observed_at=str(value.get("observed_at") or "") or None,
    )
    return receipt | {"host_wiring": "codex_app_refresh_spend_post_settlement"}


def stage_codex_app_turn_outcome_candidate_fail_open(
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        return stage_codex_app_turn_outcome_candidate(**kwargs)
    except (OSError, RuntimeError, TypeError, ValueError):
        return _candidate_base(
            goal_id=str(kwargs.get("goal_id") or ""),
            agent_id=str(kwargs.get("agent_id") or ""),
            status="runtime_unavailable",
            reason_code="app_outcome_stage_failed",
        )


def run_staged_codex_app_turn_outcome_ingest_fail_open(
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        return run_staged_codex_app_turn_outcome_ingest(**kwargs)
    except (OSError, RuntimeError, TypeError, ValueError):
        return {
            **_candidate_base(
                goal_id=str(kwargs.get("goal_id") or ""),
                agent_id=str(kwargs.get("agent_id") or ""),
                status="runtime_unavailable",
                reason_code="app_outcome_finalize_failed",
            ),
            "host_wiring": "codex_app_refresh_spend_post_settlement",
        }


__all__ = [
    "CODEX_APP_OUTCOME_CANDIDATE_SCHEMA_VERSION",
    "CODEX_APP_OUTCOME_SIDECAR_SCHEMA_VERSION",
    "REFLECTION_VALIDATION_REQUEST_SCHEMA_VERSION",
    "codex_app_outcome_candidate_sidecar_path",
    "run_staged_codex_app_turn_outcome_ingest",
    "run_staged_codex_app_turn_outcome_ingest_fail_open",
    "stage_codex_app_turn_outcome_candidate",
    "stage_codex_app_turn_outcome_candidate_fail_open",
]
