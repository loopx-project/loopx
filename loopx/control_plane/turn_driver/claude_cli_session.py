"""Private Claude CLI handles; public lineage uses the existing Turn owner.

The document format is local to this provider, not a new shared lifecycle.
Goal-instance admission, session decisions and failed-Turn retry fencing remain
in the existing control-plane owners.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...file_lock import exclusive_file_lock
from ...runtime import validate_goal_id_path_segment
from ..goals.first_party_host_admission import FirstPartyHostGoalAdmission
from .driver import selected_turn_todo


def lineage(envelope: Mapping[str, Any]) -> dict[str, str]:
    result = {
        "goal_id": str(envelope.get("goal_id") or ""),
        "agent_id": str(envelope.get("agent_id") or ""),
        "todo_id": str(selected_turn_todo(envelope).get("todo_id") or ""),
    }
    if not all(result.values()):
        raise ValueError("Claude CLI requires complete Turn lineage")
    result["goal_id"] = validate_goal_id_path_segment(result["goal_id"])
    return result


def session_path(runtime_root: Path, identity: Mapping[str, str]) -> Path:
    digest = hashlib.sha256(
        json.dumps(dict(identity), sort_keys=True).encode()
    ).hexdigest()
    return (
        runtime_root
        / "goals"
        / identity["goal_id"]
        / "claude-turn-sessions"
        / f"{digest}.json"
    )


def valid_session_id(value: object) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        return False


def read_session(
    runtime_root: Path,
    identity: Mapping[str, str],
    goal_admission: FirstPartyHostGoalAdmission | None = None,
) -> dict[str, Any] | None:
    path = session_path(runtime_root, identity)

    def read() -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError):
            raise ValueError("Claude CLI session binding is unreadable") from None
        if (
            not isinstance(value, dict)
            or value.get("host") != "claude-code"
            or any(value.get(key) != item for key, item in identity.items())
            or not valid_session_id(value.get("session_id"))
        ):
            raise ValueError("Claude CLI session binding is invalid")
        return value

    if goal_admission is None:
        return read()
    return goal_admission.select_state(
        read_state=read,
        goal_ref_of=lambda value: (
            value.get("goal_ref") or {"goal_id": value.get("goal_id")}
        ),
    )


def claude_cli_session_binding(
    runtime_root: Path,
    turn_envelope: Mapping[str, Any],
    *,
    goal_admission: FirstPartyHostGoalAdmission | None = None,
) -> dict[str, str] | None:
    identity = lineage(turn_envelope)
    value = read_session(runtime_root, identity, goal_admission)
    return (
        {"schema_version": "loopx_turn_session_binding_v0", **identity}
        if value
        else None
    )


def store_session(
    runtime_root: Path,
    identity: Mapping[str, str],
    *,
    session_id: str,
    goal_ref: Mapping[str, Any] | None,
    goal_admission: FirstPartyHostGoalAdmission | None,
) -> None:
    if not valid_session_id(session_id):
        raise ValueError("Claude CLI returned an invalid session id")
    path = session_path(runtime_root, identity)

    def commit() -> None:
        with exclusive_file_lock(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".binding-", dir=path.parent)
            temporary = Path(name)
            try:
                with os.fdopen(fd, "w") as handle:
                    payload = {
                        "host": "claude-code",
                        **identity,
                        "session_id": session_id,
                    }
                    if goal_ref is not None:
                        payload["goal_ref"] = dict(goal_ref)
                    json.dump(payload, handle, sort_keys=True)
                    handle.write("\n")
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)

    if goal_admission is None:
        commit()
    else:
        goal_admission.accept_result(commit)
