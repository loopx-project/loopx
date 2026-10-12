"""Durable record that a Kiro session has been under the LoopX gate.

Before `/loopx` binds a session, the gate stays out of the way so start-goal
can run. After that, losing the binding must not reopen the session: a
registry that goes missing, a binding record that is deleted or corrupted, or
an agent that is removed would otherwise read exactly like "never bound" and
wave every tool through. The first time the gate resolves a session to a bound
Goal it writes a record here; from then on the gate treats any failure to
resolve that session as a fault and denies state-changing calls.

The record lives under the Kiro home, not the project's ``.loopx/``, so it
survives the project registry disappearing. One file per session, named by a
digest of the session id so the id never becomes a path. A record that cannot
be parsed still counts as armed: its existence is the fact that matters.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ARMING_SCHEMA_VERSION = "loopx_kiro_cli_gate_arming_v0"
ARMING_SUBPATH = Path("loopx-gate") / "armed-sessions"


def arming_root(kiro_root: Path) -> Path:
    return kiro_root / ARMING_SUBPATH


def _record_path(root: Path, session_id: str) -> Path:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]
    return root / f"{digest}.json"


def is_armed(root: Path, session_id: str | None) -> bool:
    if not str(session_id or "").strip():
        return False
    return _record_path(root, str(session_id)).exists()


def armed_record(root: Path, session_id: str) -> dict[str, Any]:
    try:
        payload = json.loads(_record_path(root, session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def arm(root: Path, session_id: str, context: Mapping[str, Any]) -> None:
    """Record that ``session_id`` is gated for this Goal and agent.

    Idempotent; a changed binding (the session re-bound to another lane)
    refreshes the record. Raises OSError when the record cannot be written,
    so the caller can fail closed rather than run without the guarantee.
    """
    record = {
        "schema_version": ARMING_SCHEMA_VERSION,
        "goal_id": context.get("goal_id"),
        "agent_id": context.get("agent_id"),
    }
    path = _record_path(root, session_id)
    if armed_record(root, session_id) == record:
        return
    root.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(record) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def clear_all(root: Path) -> None:
    """Drop every arming record; used when the gated agent is retired."""
    shutil.rmtree(root, ignore_errors=True)
