"""The settlement binding kind is proved by executing its builder.

Enumerating an owner proves that a value is spelled somewhere. It does not
prove a legal input can make the runtime emit it. This vocabulary's evidence
is the shipped TypeScript builder run against the four inputs that make up
its whole binding domain, re-checked through the Python bridge that reaches
the same code.

These tests fail when the witness stops executing the real builder, when the
builder's branches change meaning, or when it can emit a value outside the
registered domain -- and the report must not keep crediting a check that no
longer runs.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from loopx.control_plane.effect_program import SettlementBindingKind, SettlementIdentity
from loopx.semantics.production import INPUT_WITNESSES, probe_settlement_binding_production

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "loopx" / "semantics" / "vocabulary_v0.json"
SITE = "loopx/control_plane/effect_program.ts::settlementIdentity"
PROBE = ROOT / "scripts" / "settlement_binding_witness.mts"

# The builder's whole binding domain, given as input rather than derived from
# the owner, so a swapped branch shows up as a disagreement.
CASES = (
    ({"todo_id": "t1"}, "todo"),
    ({"replan_obligation_id": "r1"}, "autonomous_replan"),
    ({}, "unbound"),
)


def _vocabulary() -> dict:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return registry["vocabularies"]["settlement_binding_kind"]


def _run_probe(probes: list[dict]) -> list[dict]:
    completed = subprocess.run(
        ["node", "--no-warnings", "--experimental-strip-types", str(PROBE)],
        input=json.dumps({"probes": probes}),
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    return json.loads(completed.stdout)


def test_the_registry_anchors_the_builder_as_the_input_producer() -> None:
    assert _vocabulary()["input_producer"] == SITE
    assert SITE in INPUT_WITNESSES, "the anchored site must have an executable witness"


@pytest.mark.parametrize(("probe", "expected"), CASES)
def test_the_real_builder_emits_the_registered_value(probe: dict, expected: str) -> None:
    (result,) = _run_probe([probe])
    assert result["ok"], result
    assert result["binding_kind"] == expected
    assert expected in _vocabulary()["values"]


@pytest.mark.parametrize(("probe", "expected"), CASES)
def test_the_python_bridge_reaches_the_same_builder(probe: dict, expected: str) -> None:
    identity = SettlementIdentity(
        goal_id="g",
        agent_id="a",
        turn_instance_id="t",
        todo_id=probe.get("todo_id"),
        replan_obligation_id=probe.get("replan_obligation_id"),
    )
    assert identity.binding_kind is SettlementBindingKind(expected)


def test_binding_both_at_once_is_refused_by_the_builder() -> None:
    (result,) = _run_probe([{"todo_id": "t1", "replan_obligation_id": "r1"}])
    assert not result["ok"]
    assert "cannot bind both" in result["error"]


def test_binding_both_at_once_is_refused_through_the_bridge() -> None:
    with pytest.raises(ValueError):
        SettlementIdentity(
            goal_id="g", agent_id="a", turn_instance_id="t",
            todo_id="t1", replan_obligation_id="r1",
        )


def test_the_builder_emits_nothing_outside_the_registered_domain() -> None:
    registered = set(_vocabulary()["values"])
    observed = {
        result["binding_kind"]
        for result in _run_probe([probe for probe, _ in CASES])
        if result["ok"]
    }
    assert observed <= registered
    assert observed == registered, "the witness must cover every registered value"


def test_the_witness_refuses_a_vocabulary_it_is_not_anchored_to() -> None:
    """Registry data cannot point the witness at a different builder."""
    with pytest.raises(ValueError, match="must name the anchored builder"):
        probe_settlement_binding_production({"input_producer": "elsewhere::f", "values": []})


def test_the_witness_refuses_a_value_set_it_does_not_cover() -> None:
    """Adding a value without extending the witness must fail, not pass silently."""
    vocabulary = dict(_vocabulary())
    vocabulary["values"] = [*vocabulary["values"], "a_new_binding_kind"]
    with pytest.raises(ValueError, match="does not cover the registered values"):
        probe_settlement_binding_production(vocabulary)


def test_the_wire_payload_shape_is_recorded_rather_than_assumed() -> None:
    """``binding_kind`` is not on every payload; changing that must be visible."""
    todo, replan, unbound = _run_probe([probe for probe, _ in CASES])
    assert todo["payload_schema_version"] == "quota_settlement_identity_v0"
    assert todo["payload_has_binding_kind"] is False
    assert unbound["payload_schema_version"] == "quota_settlement_identity_v0"
    assert unbound["payload_has_binding_kind"] is False
    assert replan["payload_schema_version"] == "quota_settlement_identity_v1"
    assert replan["payload_has_binding_kind"] is True
