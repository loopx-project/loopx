"""Filesystem boundary for canonical LoopX Turn journals."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...file_lock import exclusive_file_lock
from ..effect_runtime import EffectRuntimeConflict, effect_runtime_result
from .turn_journal_runtime import (
    write_turn_journal,
)


LOOPX_TURN_JOURNAL_SCHEMA_VERSION = "loopx_turn_journal_v0"
TURN_KEY_RE = re.compile(r"^sha256:(?P<digest>[0-9a-f]{64})$")


def turn_journal_path(runtime_root: Path, *, goal_id: str, turn_key: str) -> Path:
    match = TURN_KEY_RE.fullmatch(turn_key)
    if not match:
        raise ValueError("turn_key must be a sha256 digest")
    return runtime_root / "goals" / goal_id / "turns" / f"{match.group('digest')}.json"


def load_turn_journal(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != LOOPX_TURN_JOURNAL_SCHEMA_VERSION
    ):
        raise ValueError("LoopX Turn journal has an unsupported schema")
    return value


def journal_committed_effect_id(journal: Mapping[str, Any]) -> str | None:
    """Return the typed settlement identity when this is not a legacy journal."""

    stored_plan = journal.get("plan")
    if not isinstance(stored_plan, Mapping):
        return None
    transaction = stored_plan.get("transaction")
    if not isinstance(transaction, Mapping):
        return None
    settlement_plan = transaction.get("settlement_plan")
    if not isinstance(settlement_plan, Mapping):
        return None
    identity = settlement_plan.get("identity")
    if not isinstance(identity, Mapping):
        return None
    effect_id = str(identity.get("effect_id") or "").strip()
    return effect_id or None


def write_turn_journal_checkpoint(
    path: Path,
    journal: Mapping[str, Any],
    *,
    source_admission: Mapping[str, Any] | None = None,
) -> None:
    write_turn_journal(
        str(path),
        journal,
        expected_effect_id=journal_committed_effect_id(journal),
        source_admission=source_admission,
    )


def load_loopx_turn_plan_from_journal(
    runtime_root: Path,
    *,
    goal_id: str,
    turn_key: str,
) -> dict[str, Any]:
    path = turn_journal_path(runtime_root, goal_id=goal_id, turn_key=turn_key)
    with exclusive_file_lock(path):
        journal = load_turn_journal(path)
    if journal is None:
        raise ValueError("LoopX Turn resume journal does not exist")
    plan = journal.get("plan")
    if not isinstance(plan, dict):
        raise TypeError("LoopX Turn resume journal does not contain a plan")
    transaction = (
        plan.get("transaction") if isinstance(plan.get("transaction"), dict) else {}
    )
    if transaction.get("turn_key") != turn_key or journal.get("turn_key") != turn_key:
        raise ValueError("LoopX Turn resume journal has mismatched turn lineage")
    envelope = (
        plan.get("turn_envelope") if isinstance(plan.get("turn_envelope"), dict) else {}
    )
    if envelope.get("goal_id") != goal_id or journal.get("goal_id") != goal_id:
        raise ValueError("LoopX Turn resume journal belongs to another goal")
    return dict(plan)


def find_loopx_turn_key_by_settlement_identity(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    todo_id: str,
    turn_instance_id: str,
) -> str | None:
    """Find one validated history; unreadable or conflicting history blocks retry."""

    try:
        payload = effect_runtime_result("turn_journal.find_settlement", {
            "runtime_root": str(runtime_root.resolve()),
            "goal_id": goal_id,
            "agent_id": agent_id,
            "todo_id": todo_id,
            "turn_instance_id": turn_instance_id,
        })
    except EffectRuntimeConflict as exc:
        # Preserve the public ambiguity exception; the native owner also
        # rejects incomplete history rather than authorizing a fresh Turn.
        raise ValueError(str(exc)) from exc
    if not isinstance(payload, dict) or set(payload) != {"turn_key"}:
        raise RuntimeError("TypeScript Turn journal lookup shape mismatch")
    key = payload["turn_key"]
    if key is not None and (not isinstance(key, str) or not TURN_KEY_RE.fullmatch(key)):
        raise RuntimeError("TypeScript Turn journal lookup key mismatch")
    return key


def turn_journal_observed_capabilities(
    runtime_root: Path,
    *,
    settlement_identity: Mapping[str, Any],
) -> list[str] | None:
    """Read exact terminal Turn evidence through the native journal owner.

    Missing, unreadable, contradictory, or ambiguous history lends no
    capabilities. Historical evidence never grants current execution authority.
    """

    try:
        payload = effect_runtime_result("turn_journal.observed_capabilities", {
            "runtime_root": str(runtime_root.resolve()),
            "settlement_identity": dict(settlement_identity),
        })
    except (RuntimeError, ValueError):
        return None
    if not isinstance(payload, dict) or set(payload) != {"observed_capabilities"}:
        return None
    observed = payload["observed_capabilities"]
    if not isinstance(observed, list) or not all(isinstance(item, str) for item in observed):
        return None
    return observed
