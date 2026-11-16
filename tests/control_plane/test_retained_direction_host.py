"""The existing Host direction IO carries the owner's retained-result proof."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from loopx.control_plane.quota.settlement import SettlementIdentity
from loopx.control_plane.turn_driver.checkpoint_continuation import CurrentAgentCheckpoint
from tests.control_plane.test_retained_result_binding import direction
from tests.control_plane.test_quota_settlement_cli import GOAL_ID, _projected_cli_args, _spend_run_count


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("existing_read", [True, False])
@pytest.mark.parametrize("replace_packet", [False, True])
def test_real_direction_transport_preserves_result_proof_and_reuses_saved_return(
    provider, existing_read, replace_packet, tmp_path, monkeypatch,
):
    runtime, call, prior, turn, context, supplement = direction(provider, tmp_path, monkeypatch)
    identity = SettlementIdentity.from_runtime_payload(prior["settlement_identity"])
    contexts = runtime / "goals" / GOAL_ID / "checkpoint-contexts"
    path = contexts / (hashlib.sha256(identity.effect_id.encode()).hexdigest() + ".json")
    work = json.loads(path.with_name(hashlib.sha256((identity.effect_id + ":delivery_result").encode()).hexdigest() + ".json").read_text(encoding="utf-8"))
    state = Path(work["work_basis"]["source"]["state_file"])
    project = runtime.parent / "project"
    registry = project / ".loopx/registry.json"
    packet = json.loads((project / "current-direction.json").read_text(encoding="utf-8"))
    if not existing_read:
        path.unlink()  # Disposable direction read only; original R/work proof is untouched.
    calls = []

    def decide(current):
        assert current["retained_result_binding"] == context["retained_result_binding"]
        calls.append(current["read_context_id"])
        return {"read_context_id": current["read_context_id"], "agent_vision": packet}

    driver = CurrentAgentCheckpoint(registry, runtime, state, identity, decide)
    decided = driver.continue_direction({}, purpose="supplement_checkpoint")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["current_agent_binding"]["retained_result_binding_sha256"]
    assert saved["retained_result_binding"] == context["retained_result_binding"]
    assert driver.continue_direction({}, purpose="supplement_checkpoint") == decided
    assert calls == [decided["checkpoint_read_context_id"]]
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    before = index.read_bytes()
    if replace_packet:
        packet["vision_patch"]["acceptance_summary"] = "Substitute a different judgment after the Host return."
        (project / "current-direction.json").write_text(json.dumps(packet), encoding="utf-8")
    supplement = supplement.replace(context["read_context_id"], decided["checkpoint_read_context_id"])
    rc, qualified = call(*_projected_cli_args(supplement, turn_instance_id=turn))
    if replace_packet:
        assert rc == 1 and "checkpoint_commit_unknown" in json.dumps(qualified), qualified
        assert index.read_bytes() == before
    else:
        assert rc == 0 and qualified["vision_checkpoint"]["satisfied"] is True, qualified
    assert _spend_run_count(runtime) == 0
