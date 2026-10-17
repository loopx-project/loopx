from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...file_lock import exclusive_cross_runtime_file_lock
from ...history import load_registry
from ...materials import find_registry_goal, goal_state_path
from ...paths import resolve_runtime_root
from ...control_plane.effect_runtime import effect_runtime_result
from ...control_plane.reward_memory import reward_memory_goal_policy

from ...control_plane.coordination.local_authority import (
    read_canonical_todos_if_promoted,
)
from ...control_plane.todos.active_state_editing import find_todo_block
from ...control_plane.todos.completion_validation import (
    resolve_private_completion_validation_declaration,
)
from ...control_plane.todos.completion_validation_projection import (
    completion_validation_declaration_sha256,
    project_completion_validation_authority,
)
from .outcome_lifecycle import (
    _goal_repo,
    _identity_token,
    _reflection,
    _reflection_digest,
    _validated_reflection_evidence,
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


def _candidate_binding(
    *, registry_path: Path, identity: Mapping[str, Any], digest: str,
    declaration: dict[str, Any] | None, previous: Mapping[str, Any] | None,
    revalidating: bool,
) -> dict[str, Any]:
    goal = find_registry_goal(load_registry(registry_path), str(identity["goal_id"]))
    if goal is None:
        raise ValueError("Codex App reward memory Goal is unavailable")
    return dict(effect_runtime_result("reward_memory.app_outcome.bind", {
        "identity": dict(identity), "reflection_digest": digest,
        "policy": reward_memory_goal_policy(goal, agent_id=str(identity["agent_id"])),
        "validation_declaration_sha256": (
            completion_validation_declaration_sha256(declaration) if declaration else None
        ),
        "previous": dict(previous) if previous is not None else None,
        "revalidating": revalidating,
    }))


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
        or not isinstance(expected_refs, list)
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
    identity = {"goal_id": goal_id, "agent_id": agent_id, "todo_id": todo_id,
                "turn_instance_id": turn_instance_id, "effect_id": effect_id}
    key = effect_runtime_result("reward_memory.app_outcome.identity", {"identity": identity})
    candidate_id = str(key["candidate_id"])
    declaration = _validation_declaration(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        todo_id=todo_id,
        state_file=state_file,
    )
    path = codex_app_outcome_candidate_sidecar_path(
        _goal_repo(registry_path, goal_id), goal_id=goal_id,
        agent_id=agent_id, candidate_id=candidate_id,
    )
    with exclusive_cross_runtime_file_lock(path, operation="app_outcome_stage"):
        previous = _load_candidate_sidecar(path)
        if previous is not None and (
            previous.get("schema_version") != CODEX_APP_OUTCOME_SIDECAR_SCHEMA_VERSION
            or previous.get("reflection") != reflection
        ):
            raise ValueError("Codex App reward memory candidate identity mismatch")
        binding = _candidate_binding(
            registry_path=registry_path, identity=identity, digest=digest,
            declaration=declaration, previous=previous, revalidating=True,
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
        sidecar = {
            "schema_version": CODEX_APP_OUTCOME_SIDECAR_SCHEMA_VERSION,
            **binding,
            "reflection": reflection,
            "task_validation": validation,
            "observed_at": previous.get("observed_at", observed_at) if previous else observed_at,
            "raw_content_captured": False,
        }
        # Always keep the current independent result; an older passed invocation
        # cannot mask a failed or changed validator on an explicit refresh retry.
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
    candidate_id: str | None,
    writeback_appended: bool,
    spend_appended: bool,
    runtime_root: Path | None = None,
) -> dict[str, Any] | None:
    """Finalize one staged App reflection after exact writeback+spend readback."""

    identity = {"goal_id": goal_id, "agent_id": agent_id, "todo_id": todo_id,
                "turn_instance_id": turn_instance_id, "effect_id": effect_id}
    key = effect_runtime_result("reward_memory.app_outcome.identity", {"identity": identity})
    path = codex_app_outcome_candidate_sidecar_path(
        _goal_repo(registry_path, goal_id),
        goal_id=goal_id,
        agent_id=agent_id,
        candidate_id=str(key["candidate_id"]),
    )
    if not path.exists() and candidate_id:
        # Legacy metadata is an exact lookup only, never a directory scan. A
        # missing binding stamp fails closed below; it cannot acquire authority.
        path = codex_app_outcome_candidate_sidecar_path(
            _goal_repo(registry_path, goal_id), goal_id=goal_id,
            agent_id=agent_id, candidate_id=candidate_id,
        )
    if not path.exists() and not candidate_id:
        return None
    with exclusive_cross_runtime_file_lock(path, operation="app_outcome_finalize"):
        value = _load_candidate_sidecar(path)
        if value is None or value.get("schema_version") != (
            CODEX_APP_OUTCOME_SIDECAR_SCHEMA_VERSION
        ):
            raise ValueError("Codex App reward memory candidate sidecar is unavailable")
        reflection = value.get("reflection")
        if not isinstance(reflection, Mapping):
            raise ValueError("Codex App reward memory candidate reflection is invalid")
        serialized = json.dumps(reflection, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        declaration = _validation_declaration(
            registry_path=registry_path,
            runtime_root=runtime_root or resolve_runtime_root(load_registry(registry_path), None, registry_path=registry_path),
            goal_id=goal_id, todo_id=todo_id, state_file=Path(),
        )
        _candidate_binding(
            registry_path=registry_path, identity=identity,
            digest=_reflection_digest(serialized), declaration=declaration,
            previous=value, revalidating=False,
        )
        settlement_evidence = {
            "schema_version": "turn_post_settlement_evidence_v0",
            "task_validation": dict(value.get("task_validation") or {}),
            "writeback": {"ok": writeback_appended, "appended": writeback_appended},
            "quota_spend": {"ok": spend_appended, "appended": spend_appended},
        }
        if _validated_reflection_evidence(
            reflection_json=serialized, reflection=reflection,
            settlement_evidence=settlement_evidence,
        ) is None:
            return _candidate_base(
                goal_id=goal_id, agent_id=agent_id, status="evidence_validation_required",
                reason_code="reflection_not_bound_to_independent_validation",
            ) | {"host_wiring": "codex_app_refresh_spend_post_settlement"}
        receipt = run_configured_turn_outcome_ingest(
            registry_path=registry_path,
            goal_id=goal_id,
            agent_id=agent_id,
            turn_key=effect_id,
            host_result={
                "reward_memory_reflection_json": serialized
            },
            settlement_evidence=settlement_evidence,
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
) -> dict[str, Any] | None:
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
