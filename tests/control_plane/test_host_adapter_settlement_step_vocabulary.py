"""The host-adapter settlement plan step order has one registered vocabulary.

`loopx/control_plane/turn_driver/host_todo_completion.ts` authors the ordered
provider plan and `loopx/control_plane/host_adapter_settlement.py` validates the
plan it receives against the same sequence. Until now the two sequences were
private, hand-kept copies: the TypeScript one was a module-local `const`, the
Python one a module-local `_STEP_KINDS`. Neither was exported, so the
repository's semantic inventory could not see either carrier, and neither
`loopx/semantics/vocabulary_v0.json` nor the drift smoke could tie them
together. A divergence between them would have surfaced at runtime as
`TypeScript host provider step order mismatch`, after the plan had already been
accepted and partially executed.

Both sequences are now registered owners of `host_adapter_settlement_step_kind`,
so the drift smoke checks each of them against the registry on every run, and
the tests below check the two against each other directly.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from loopx.control_plane.host_adapter_settlement import (
    HOST_ADAPTER_SETTLEMENT_STEP_SEQUENCE,
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


def test_both_runtimes_carry_the_same_step_sequence() -> None:
    """The plan the TypeScript owner writes must satisfy the Python transport."""
    assert _typescript_step_kinds() == tuple(HOST_ADAPTER_SETTLEMENT_STEP_SEQUENCE)


def test_the_registry_entry_names_both_owners_and_the_same_values() -> None:
    """The registered vocabulary is what the drift smoke checks both owners against."""
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    entry = registry["vocabularies"][VOCABULARY]
    assert entry["owners"]["python"] == (
        "loopx/control_plane/host_adapter_settlement.py::HOST_ADAPTER_SETTLEMENT_STEP_SEQUENCE"
    )
    assert entry["owners"]["typescript"] == (
        "loopx/control_plane/turn_driver/host_todo_completion.ts::HOST_ADAPTER_SETTLEMENT_STEP_KINDS"
    )
    assert entry["values"] == list(HOST_ADAPTER_SETTLEMENT_STEP_SEQUENCE)


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


def test_the_transport_accepts_the_registered_order() -> None:
    steps = _decode_provider_steps(_plan(list(HOST_ADAPTER_SETTLEMENT_STEP_SEQUENCE)))
    assert [step.step_kind for step in steps] == list(HOST_ADAPTER_SETTLEMENT_STEP_SEQUENCE)


def test_the_transport_rejects_a_plan_whose_step_order_drifts() -> None:
    """The tripwire the registry now backs: a reordered plan is refused, not run."""
    drifted = list(HOST_ADAPTER_SETTLEMENT_STEP_SEQUENCE)
    drifted[0], drifted[1] = drifted[1], drifted[0]
    with pytest.raises(RuntimeError, match="step order mismatch"):
        _decode_provider_steps(_plan(drifted))
