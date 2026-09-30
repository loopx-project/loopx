"""Filesystem boundary for canonical LoopX Turn journals."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...file_lock import exclusive_file_lock
from ..effect_program import SettlementIdentity
from .turn_journal_runtime import (
    interpret_turn_journal_projection,
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
    """Resolve one journal from its durable settlement identity.

    A host process can finish after its caller loses the command reply, so the
    caller-stable ``turn_instance_id`` is the recovery address.  Never select a
    journal by recency or by a partial Goal/Agent match: the transaction's
    typed settlement identity must equal every supplied field, and ambiguity
    fails closed.
    """

    expected = SettlementIdentity(
        goal_id=goal_id,
        agent_id=agent_id,
        todo_id=todo_id,
        turn_instance_id=turn_instance_id,
    )
    turns_dir = runtime_root / "goals" / goal_id / "turns"
    if not turns_dir.is_dir():
        return None
    matches: list[str] = []
    for path in sorted(turns_dir.glob("*.json")):
        turn_key = f"sha256:{path.stem}"
        if not TURN_KEY_RE.fullmatch(turn_key):
            continue
        try:
            plan = load_loopx_turn_plan_from_journal(
                runtime_root,
                goal_id=goal_id,
                turn_key=turn_key,
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if _journal_plan_turn_instance_id(plan) != turn_instance_id:
            continue
        transaction = plan.get("transaction")
        settlement = (
            transaction.get("settlement_plan")
            if isinstance(transaction, Mapping)
            else None
        )
        identity = (
            settlement.get("identity") if isinstance(settlement, Mapping) else None
        )
        if not isinstance(identity, Mapping):
            continue
        try:
            actual = SettlementIdentity.from_runtime_payload(identity)
        except RuntimeError:
            continue
        if _identity_binding_tuple(actual) == _identity_binding_tuple(expected):
            matches.append(turn_key)
    if len(matches) > 1:
        raise ValueError("LoopX Turn settlement identity matched multiple journals")
    return matches[0] if matches else None


def _journal_plan_turn_instance_id(plan: Mapping[str, Any]) -> str | None:
    transaction = plan.get("transaction")
    if isinstance(transaction, Mapping):
        direct = str(transaction.get("turn_instance_id") or "").strip()
        if direct:
            return direct
    settlement = (
        transaction.get("settlement_plan")
        if isinstance(transaction, Mapping)
        else None
    )
    identity = (
        settlement.get("identity") if isinstance(settlement, Mapping) else None
    )
    if isinstance(identity, Mapping):
        nested = str(identity.get("turn_instance_id") or "").strip()
        if nested:
            return nested
    return None


def _envelope_observed_capabilities(envelope: Mapping[str, Any]) -> list[str]:
    """Read the capability set this Turn's scheduler decision already froze.

    Two durable sub-sources compose the validated set, mirroring what the
    scheduler consumed: the journaled boundary declares goal/coordination
    capabilities, and a journaled capability gate whose required capabilities
    were not missing proves the scheduler observed them for this Turn.
    Anything else stays absent so downstream gates fail closed.
    """

    observed: list[str] = []

    def append(values: Any) -> None:
        if not isinstance(values, list):
            return
        for capability in values:
            rendered = str(capability or "").strip()
            if rendered and rendered not in observed:
                observed.append(rendered)

    boundary = (
        envelope.get("boundary")
        if isinstance(envelope.get("boundary"), Mapping)
        else {}
    )
    append(boundary.get("available_capabilities"))
    gate = (
        envelope.get("capability_gate")
        if isinstance(envelope.get("capability_gate"), Mapping)
        else {}
    )
    required = gate.get("required_capabilities")
    missing = gate.get("missing_capabilities")
    if isinstance(required, list) and required and missing in (None, []):
        append(required)
    return observed


def _identity_binding_tuple(identity: SettlementIdentity) -> tuple[str, ...]:
    """Render the typed identity fields that bind one settlement effect."""

    return (
        identity.goal_id,
        identity.agent_id,
        identity.binding_kind.value,
        identity.binding_id,
        identity.turn_instance_id,
        identity.effect_id,
    )


def _journal_settlement_identity_matches(
    plan: Mapping[str, Any], completion: SettlementIdentity
) -> bool:
    """Require the journal's typed settlement identity to be this completion's."""

    transaction = plan.get("transaction")
    if not isinstance(transaction, Mapping):
        return False
    settlement = transaction.get("settlement_plan")
    if not isinstance(settlement, Mapping):
        return False
    identity = settlement.get("identity")
    if not isinstance(identity, Mapping):
        return False
    try:
        journal_identity = SettlementIdentity.from_runtime_payload(identity)
    except RuntimeError:
        return False
    return _identity_binding_tuple(journal_identity) == _identity_binding_tuple(
        completion
    )


def _settlement_matched_journal_capabilities(
    path: Path,
    *,
    completion: SettlementIdentity,
) -> list[str] | None:
    """Read capabilities only after the TS owner validated this journal."""

    turn_key = f"sha256:{path.stem}"
    if not TURN_KEY_RE.fullmatch(turn_key):
        return None
    try:
        journal = load_turn_journal(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if journal is None or journal.get("goal_id") != completion.goal_id:
        return None
    plan = journal.get("plan")
    if not isinstance(plan, Mapping):
        return None
    if _journal_plan_turn_instance_id(plan) != completion.turn_instance_id:
        return None
    try:
        inspection = interpret_turn_journal_projection(
            journal,
            goal_id=completion.goal_id,
            agent_id=completion.agent_id,
            turn_key=turn_key,
        )
    except (RuntimeError, ValueError):
        return None
    if (
        inspection.get("decision") != "replay_legal"
        or inspection.get("violations") != []
    ):
        return None
    if not _journal_settlement_identity_matches(plan, completion):
        return None
    envelope = plan.get("turn_envelope")
    if not isinstance(envelope, Mapping):
        return None
    return _envelope_observed_capabilities(envelope)


def turn_journal_observed_capabilities(
    runtime_root: Path,
    *,
    settlement_identity: Mapping[str, Any],
) -> list[str] | None:
    """Return capabilities only a journal fully bound to this settlement observed.

    The reader stays a consumer of the TypeScript-owned journal authority:
    every candidate journal must pass ``turn_journal.inspect`` replay
    validation for this completion's goal/agent/turn identity, and its typed
    settlement-plan identity must equal the current completion identity
    (goal/agent/binding/turn/effect). A journal that only shares the goal and
    a caller-supplied turn id — another agent's, another Todo's, or one whose
    transaction and settlement disagree — provides no evidence, so unreadable,
    missing, unmatched, or ambiguous journals return None and callers fail
    closed instead of borrowing capabilities from a foreign settlement.
    """

    try:
        completion = SettlementIdentity.from_runtime_payload(
            dict(settlement_identity)
        )
    except RuntimeError:
        return None
    turns_dir = runtime_root / "goals" / completion.goal_id / "turns"
    if not turns_dir.is_dir():
        return None
    matched: list[list[str]] = []
    for path in sorted(turns_dir.glob("*.json")):
        evidence = _settlement_matched_journal_capabilities(
            path, completion=completion
        )
        if evidence is not None:
            matched.append(evidence)
    if len(matched) != 1:
        # Zero fully-bound journals means no evidence; more than one would be
        # an ambiguity about a single settlement effect the reader must not
        # resolve by picking a winner.
        return None
    return matched[0]
