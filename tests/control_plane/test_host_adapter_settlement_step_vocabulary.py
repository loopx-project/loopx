"""Registered Host adapter step kinds and legal phase-specific plan orders."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from loopx.control_plane.host_adapter_settlement import (
    HOST_ADAPTER_SETTLEMENT_STEP_KINDS,
    _decode_provider_steps,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPOSITORY_ROOT / "loopx" / "semantics" / "vocabulary_v0.json"
TYPESCRIPT_OWNER = REPOSITORY_ROOT / "loopx" / "control_plane" / "turn_driver" / "host_todo_completion.ts"
VOCABULARY = "host_adapter_settlement_step_kind"


def _typescript_step_kinds() -> tuple[str, ...]:
    """Read the TypeScript owner the way the semantic inventory reads it."""
    source = TYPESCRIPT_OWNER.read_text(encoding="utf-8")
    match = re.search(
        r"export const HOST_ADAPTER_SETTLEMENT_STEP_KINDS = \[([^\]]*)\] as const;",
        source,
    )
    assert match, "the TypeScript owner must export its step kinds as an as-const array"
    return tuple(re.findall(r'"([^"]+)"', match.group(1)))


def test_both_runtimes_carry_the_same_step_kinds() -> None:
    """The plan the TypeScript owner writes must satisfy the Python transport."""
    assert _typescript_step_kinds() == tuple(HOST_ADAPTER_SETTLEMENT_STEP_KINDS)


def test_the_registry_entry_names_both_owners_and_the_same_values() -> None:
    """The registered vocabulary is what the drift smoke checks both owners against."""
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    entry = registry["vocabularies"][VOCABULARY]
    assert entry["owners"]["python"] == (
        "loopx/control_plane/host_adapter_settlement.py::HOST_ADAPTER_SETTLEMENT_STEP_KINDS"
    )
    assert entry["owners"]["typescript"] == (
        "loopx/control_plane/turn_driver/host_todo_completion.ts::HOST_ADAPTER_SETTLEMENT_STEP_KINDS"
    )
    assert entry["values"] == list(HOST_ADAPTER_SETTLEMENT_STEP_KINDS)


def _plan(step_kinds: list[str]) -> dict:
    """One minimal provider plan: the last step carries no continuation condition."""
    steps = []
    for index, step_kind in enumerate(step_kinds):
        steps.append({
            "step_kind": step_kind,
            "args": ["todo", "complete"],
            "legacy_args": None,
            "continue_when": {"all": []} if index < len(step_kinds) - 1 else None,
        })
    return {"provider_id": "loopx_cli", "kind": "ordered_cli_sequence", "steps": steps}


@pytest.mark.parametrize("sequence", [
    ["guard", "delivery_context"],
    ["guard", "lifecycle_completion", "direction_context"],
    ["guard", "lifecycle_completion", "durable_writeback", "quota_spend"],
    ["guard", "lifecycle_completion", "durable_writeback", "quota_spend", "terminal_closeout"],
])
def test_the_transport_accepts_each_phase_order(sequence) -> None:
    steps = _decode_provider_steps(_plan(sequence))
    assert [step.step_kind for step in steps] == sequence


@pytest.mark.parametrize("sequence", [
    ["delivery_context", "guard"],
    ["guard", "direction_context", "lifecycle_completion"],
    ["lifecycle_completion", "guard", "durable_writeback", "quota_spend"],
    ["guard", "lifecycle_completion", "quota_spend", "durable_writeback", "terminal_closeout"],
])
def test_the_transport_rejects_a_plan_whose_step_order_drifts(sequence) -> None:
    """The tripwire the registry now backs: a reordered plan is refused, not run."""
    with pytest.raises(RuntimeError, match="step order mismatch"):
        _decode_provider_steps(_plan(sequence))
