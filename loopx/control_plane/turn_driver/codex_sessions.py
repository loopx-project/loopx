"""Codex transport session persistence; context scope never grants Turn authority."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...boundary_authority import normalize_checkpointed_boundary_authority_entries
from ...file_lock import exclusive_file_lock
from ...runtime import validate_goal_id_path_segment
from ..projects.registry_codec import (
    load_project_registry,
    require_runtime_compatible_project_registry,
)
from ..goals.first_party_host_admission import FirstPartyHostGoalAdmission
from .driver import selected_turn_todo, session_identity_fields

CODEX_CLI_SESSION_SCHEMA_VERSION = "loopx_codex_cli_session_v1"
SESSION_ID_MAX_CHARS = 256


def _lineage(request: Mapping[str, Any]) -> dict[str, str]:
    envelope = request.get("turn_envelope") or {}
    todo = selected_turn_todo(envelope)
    lineage = {
        "goal_id": str(envelope.get("goal_id") or "").strip(),
        "agent_id": str(envelope.get("agent_id") or "").strip(),
        "todo_id": str(todo.get("todo_id") or "").strip(),
    }
    if not all(lineage.values()):
        raise ValueError("Codex CLI host request has incomplete turn lineage")
    lineage["goal_id"] = validate_goal_id_path_segment(lineage["goal_id"])
    return lineage


def _session_path(
    runtime_root: Path,
    lineage: Mapping[str, str],
    session_scope: str = "todo",
) -> Path:
    fields = session_identity_fields(session_scope)
    identity = (
        dict(lineage)
        if session_scope == "todo"
        else {
            **{field: lineage[field] for field in fields},
            "session_scope": session_scope,
        }
    )
    digest = hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    goal_id: str = validate_goal_id_path_segment(lineage["goal_id"])
    return runtime_root / "goals" / goal_id / "turn-sessions" / f"{digest}.json"


def _valid_session_id(value: Any) -> str | None:
    session_id = str(value or "").strip()
    if not session_id or len(session_id) > SESSION_ID_MAX_CHARS:
        return None
    if any(character in session_id for character in ("\x00", "\r", "\n")):
        return None
    return session_id


def load_codex_cli_session(
    runtime_root: Path,
    *,
    lineage: Mapping[str, str],
    session_scope: str = "todo",
) -> dict[str, Any] | None:
    path = _session_path(runtime_root, lineage, session_scope)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    if value.get("schema_version") != CODEX_CLI_SESSION_SCHEMA_VERSION:
        return None
    if value.get("session_scope", "todo") != session_scope:
        return None
    if any(
        value.get(field) != lineage[field]
        for field in session_identity_fields(session_scope)
    ):
        return None
    session_id = _valid_session_id(value.get("session_id"))
    if not session_id:
        return None
    return {**value, "session_id": session_id}


def _codex_session_goal_ref(
    value: Mapping[str, Any],
    *,
    lineage: Mapping[str, str],
    session_scope: str = "todo",
) -> object:
    if (
        value.get("schema_version") != CODEX_CLI_SESSION_SCHEMA_VERSION
        or value.get("session_scope", "todo") != session_scope
        or any(
            value.get(field) != lineage[field]
            for field in session_identity_fields(session_scope)
        )
        or _valid_session_id(value.get("session_id")) is None
    ):
        return {"malformed": True}
    goal_ref = value.get("goal_ref")
    if goal_ref is not None:
        return goal_ref
    return {"goal_id": value.get("goal_id")}


def _read_codex_cli_session_document(
    runtime_root: Path,
    *,
    lineage: Mapping[str, str],
    session_scope: str = "todo",
) -> dict[str, Any] | None:
    path = _session_path(runtime_root, lineage, session_scope)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"malformed": True}
    return value if isinstance(value, dict) else {"malformed": True}


def codex_cli_session_binding(
    runtime_root: Path,
    turn_envelope: Mapping[str, Any],
    *,
    goal_admission: FirstPartyHostGoalAdmission | None = None,
    session_scope: str = "todo",
) -> dict[str, str] | None:
    lineage = _lineage({"turn_envelope": dict(turn_envelope)})
    session = select_codex_cli_session(
        runtime_root,
        lineage=lineage,
        session_scope=session_scope,
        goal_admission=goal_admission,
    )
    if session is None:
        return None
    return {
        "schema_version": "loopx_turn_session_binding_v0",
        **lineage,
    }


def _store_codex_cli_session(
    runtime_root: Path,
    *,
    lineage: Mapping[str, str],
    session_scope: str = "todo",
    session_id: str,
    goal_ref: Mapping[str, Any] | None = None,
    session_profile_digest: str | None = None,
    operation_profile_digest: str | None = None,
    operation_model: str | None = None,
    operation_reasoning_effort: str | None = None,
) -> None:
    with exclusive_file_lock(_session_path(runtime_root, lineage, session_scope)):
        _write_codex_cli_session(
            runtime_root,
            lineage=lineage,
            session_scope=session_scope,
            session_profile_digest=session_profile_digest,
            session_id=session_id,
            goal_ref=goal_ref,
            operation_profile_digest=operation_profile_digest,
            operation_model=operation_model,
            operation_reasoning_effort=operation_reasoning_effort,
        )


def _write_codex_cli_session(
    runtime_root: Path,
    *,
    lineage: Mapping[str, str],
    session_scope: str = "todo",
    session_id: str,
    goal_ref: Mapping[str, Any] | None = None,
    session_profile_digest: str | None = None,
    operation_profile_digest: str | None = None,
    operation_model: str | None = None,
    operation_reasoning_effort: str | None = None,
) -> None:
    normalized_session_id = _valid_session_id(session_id)
    if not normalized_session_id:
        raise ValueError("Codex CLI returned an invalid session id")
    path = _session_path(runtime_root, lineage, session_scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with handle:
            payload: dict[str, Any] = {
                "schema_version": CODEX_CLI_SESSION_SCHEMA_VERSION,
                **lineage,
                "host": "codex-cli",
                "session_id": normalized_session_id,
            }
            if session_scope != "todo":
                payload["session_scope"] = session_scope
            if session_profile_digest is not None:
                payload["session_profile_digest"] = session_profile_digest
            if goal_ref is not None:
                payload["goal_ref"] = dict(goal_ref)
            if operation_profile_digest is not None:
                payload["operation_transport"] = "app-server-operation-tools-v0"
                payload["operation_profile_digest"] = operation_profile_digest
                payload["operation_model"] = operation_model
                payload["operation_reasoning_effort"] = operation_reasoning_effort
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _discard_codex_cli_session(
    runtime_root: Path,
    *,
    lineage: Mapping[str, str],
    session_scope: str = "todo",
) -> None:
    path = _session_path(runtime_root, lineage, session_scope)
    with exclusive_file_lock(path):
        path.unlink(missing_ok=True)


def select_codex_cli_session(
    runtime_root: Path,
    *,
    lineage: Mapping[str, str],
    session_scope: str = "todo",
    goal_admission: FirstPartyHostGoalAdmission | None = None,
) -> dict[str, Any] | None:
    """Select through the existing Goal lifetime fence, then validate identity.

    A present but invalid agent-scoped record is an error, never permission to
    silently create a replacement conversation.
    """
    session_identity_fields(session_scope)

    def read() -> dict[str, Any] | None:
        return _read_codex_cli_session_document(
            runtime_root,
            lineage=lineage,
            session_scope=session_scope,
        )

    value = (
        goal_admission.select_state(
            read_state=read,
            goal_ref_of=lambda item: _codex_session_goal_ref(
                item,
                lineage=lineage,
                session_scope=session_scope,
            ),
        )
        if goal_admission is not None
        else read()
    )
    if value is None:
        return None
    if _codex_session_goal_ref(value, lineage=lineage, session_scope=session_scope) == {
        "malformed": True
    }:
        if session_scope == "agent":
            raise ValueError(
                "invalid agent session binding; explicitly select fresh to replace it"
            )
        return None
    return dict(value)


def codex_session_profile_digest(
    *,
    project: Path,
    codex_bin: str,
    home: Path,
    model: str | None = None,
    reasoning_effort: str | None = None,
    sandbox: str | None = None,
    mcp_server: Mapping[str, Any] | None = None,
) -> str:
    """Bind a shared conversation to its workspace, provider home and settings."""
    config = home / "config.toml"
    profile = {
        "workspace": str(project.resolve()),
        "codex_home": str(home.expanduser().resolve()),
        "codex_bin": str(Path(codex_bin).resolve()),
        "model": model,
        "reasoning_effort": reasoning_effort,
        "sandbox": sandbox,
        "mcp_server": mcp_server,
        "config_digest": hashlib.sha256(
            config.read_bytes() if config.exists() else b""
        ).hexdigest(),
    }
    return hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()


def require_codex_session_profile(binding: Mapping[str, Any], digest: str) -> None:
    if binding.get("session_profile_digest") != digest:
        raise ValueError(
            "Codex session profile changed; explicitly select fresh or use a new runtime"
        )


def approved_codex_workspace_write_resume(
    registry_path: Path | None, *, lineage: Mapping[str, str], project: Path,
) -> bool:
    """Read current, scoped operator approval; session memory grants nothing.

    workspace-write exposes the whole working directory, so approval for a
    single file or a sibling directory cannot authorize this transition.
    """
    if registry_path is None:
        return False
    try:
        registry = load_project_registry(registry_path)
        require_runtime_compatible_project_registry(
            registry, operation="Codex workspace-write session resume"
        )
        goals = [goal for goal in registry.get("goals", [])
                 if goal.get("id") == lineage["goal_id"] and goal.get("status") == "active"]
        if len(goals) != 1:
            return False
        goal = goals[0]
        coordination = goal.get("coordination") or {}
        if lineage["agent_id"] not in coordination.get("registered_agents", []):
            return False
        raw_authority = coordination.get("checkpointed_boundary_authority")
        if isinstance(raw_authority, dict):
            raw_authority = raw_authority.get("entries")
        entries = normalize_checkpointed_boundary_authority_entries(raw_authority)
        root = Path(goal.get("repo") or "").expanduser()
        if not root.is_absolute():
            return False
        root = root.resolve()
        workspace = project.resolve()
        if not workspace.is_relative_to(root):
            return False
        scopes = [scope for entry in entries if entry.get("active") is True
                  for scope in entry.get("write_scope", [])]
        for scope in scopes:
            if scope == "**":
                return True
            if not scope.endswith("/**"):
                continue
            directory = scope[:-3]
            if (Path(directory).is_absolute() or ".." in Path(directory).parts
                    or any(char in directory for char in "*?[]")):
                continue
            approved = (root / directory).resolve()
            if approved.is_relative_to(root) and workspace.is_relative_to(approved):
                return True
    except (OSError, TypeError, ValueError, KeyError, AttributeError):
        return False
    return False
