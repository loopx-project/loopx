"""One captured native decision must preserve its capability refusal facts."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_authority, isolate_sqlite_runtime,
)
from tests.control_plane.reward_memory_host_fixture import enable_live_memory
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _run_cli, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mode", ["off", "recall_only", "ingest_only", "stale"])
@pytest.mark.parametrize("network_available", [False, True])
def test_real_captured_guard_preserves_required_and_missing_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
    mode: str, network_available: bool,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path, required_capability="network")
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace("action_kind=validate ",
        "action_kind=validate continuation_policy=same_agent_non_delivery "))
    code, listed = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID)
    assert code == 0, listed
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID, handoff_mode="soft_claim", todos=listed["todos"],
    )
    initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state, provider=provider)
    if mode != "off":
        config = enable_live_memory(registry, recall=mode == "recall_only", ingest=mode == "ingest_only")
        if mode == "stale":
            config.write_text(config.read_text() + "\n")
    capabilities = ["--available-capability", "filesystem_read"]
    if network_available:
        capabilities.extend(["--available-capability", "network"])
    capture = tmp_path / "capture"
    code, envelope = _run_cli(registry, runtime, "quota", "should-run",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
        "--turn-instance-id", "turn-capability-facts", "--codex-app", *capabilities,
        "--turn-envelope", "--decision-output-dir", str(capture), cwd=project)
    assert code == (0 if network_available else 1), envelope
    full = json.loads((capture / "decision.json").read_text())
    gate = full["capability_gate"]
    assert "network" in gate["required"]
    assert ("network" in gate["missing"]) is (not network_available)
    assert full["interaction_contract"]["agent_channel"]["delivery_allowed"] is network_available
    compact = envelope["boundary"]["capability_gate"]
    assert compact["required"] == gate["required"]
    assert compact["missing"] == gate["missing"]
    assert compact["action"] == gate["action"]
    assert envelope["action"]["delivery_allowed"] is network_available
    assert envelope["action_signature"]["matches"] is True
    # The source remains one full observation, not another admission evaluation.
    from loopx.control_plane.quota.turn_envelope import (
        build_turn_envelope, turn_envelope_action_signature_document,
    )
    before = {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
    assert build_turn_envelope(full)["boundary"]["capability_gate"] == compact
    assert {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()} == before
    changed = json.loads(json.dumps(envelope))
    changed["boundary"]["capability_gate"]["missing"] = ["different-capability"]
    assert turn_envelope_action_signature_document(changed) != turn_envelope_action_signature_document(envelope)
    if mode in {"off", "stale"}:
        assert "reward_memory" not in envelope["boundary"].get("capabilities", {})
