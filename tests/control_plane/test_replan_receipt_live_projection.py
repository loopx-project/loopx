"""Live construction sees the existing receipt before the CLI publishes it."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from loopx.control_plane.effect_program import ReceiptBoundReplayPhase
from loopx.control_plane.quota.live_decision import _project_receipt_replan_settlement
from loopx.control_plane.scheduler.execution_context import scheduler_execution_context_for_runtime_profile
from test_replan_receipt_projection import TURN, OBLIGATION, _payload


def _source():
    payload, identity = _payload()
    receipt = payload.pop("heartbeat_receipt")
    del receipt["semantic_replan_obligation_id"]
    payload.update(ok=True, should_run=True, effective_action="autonomous_replan_required",
        normal_delivery_allowed=False, execution_obligation={"kind": "autonomous_replan_required",
            "must_attempt_work": True, "delivery_allowed": False})
    readback = SimpleNamespace(identity=SimpleNamespace(value=identity), heartbeat_receipt=receipt,
        replay_phase=ReceiptBoundReplayPhase.OPEN,
        semantic_replan_guard={"scope": "turn_guard", "selected_obligation_id": OBLIGATION})
    return payload, readback, identity


def _project(payload, readback, tmp_path):
    _project_receipt_replan_settlement(payload, readback, available_capabilities=None,
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile("generic_cli"),
        turn_instance_id=TURN, registry_path=tmp_path / "registry.json", runtime_root=tmp_path,
        goal_ref=None)


def test_open_review_projects_its_existing_receipt_before_cli_publication(tmp_path):
    payload, readback, identity = _source()
    before = deepcopy(readback.heartbeat_receipt)
    _project(payload, readback, tmp_path)
    assert payload["interaction_contract"]["cli_channel"]["settlement_plan"]["identity"] == identity.as_dict()
    assert payload["heartbeat_receipt"]["semantic_replan_obligation_id"] == OBLIGATION
    assert payload["normal_delivery_allowed"] is False
    assert payload["replan_action_packet"]["settlement_only"] is False
    assert readback.heartbeat_receipt == before


@pytest.mark.parametrize("mismatch", ["phase", "guard", "scope", "identity"])
def test_open_projection_requires_exact_canonical_receipt(tmp_path, mismatch):
    payload, readback, _ = _source()
    if mismatch == "phase":
        readback.replay_phase = ReceiptBoundReplayPhase.SETTLED
    elif mismatch == "guard":
        readback.semantic_replan_guard["selected_obligation_id"] = "replan-2222222222222222"
    elif mismatch == "scope":
        readback.semantic_replan_guard["scope"] = "unscoped"
    else:
        readback.identity.value = None
    _project(payload, readback, tmp_path)
    assert "heartbeat_receipt" not in payload
    assert "interaction_contract" not in payload


def test_open_projection_does_not_replace_an_explicit_conflicting_guard(tmp_path):
    payload, readback, _ = _source()
    readback.heartbeat_receipt["semantic_replan_obligation_id"] = ""
    with pytest.raises(ValueError, match="semantic guard"):
        _project(payload, readback, tmp_path)
