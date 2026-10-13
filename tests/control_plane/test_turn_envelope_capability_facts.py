"""One captured native decision must preserve its capability refusal facts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
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


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("replan", [False, True], ids=["continuing-work", "missing-outcome"])
def test_captured_vision_contract_preserves_closeout_and_original_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, replan: bool,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace("action_kind=validate ",
        "action_kind=validate continuation_policy=same_agent_non_delivery "))
    code, listed = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID)
    assert code == 0, listed
    initialize_canonical_authority(runtime, GOAL_ID, build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID, handoff_mode="soft_claim", todos=listed["todos"],
    ), state_path=state, provider=provider)

    # A synthetic retained checkpoint drives the real frontier and CLI owners.
    # Missing outcome evidence must remain a replan; a continuing vision is not
    # closed merely because the selected Task is runnable.
    from tests.control_plane.test_final_outcome_diagnostics import material_run

    run = material_run()
    stamp = datetime.now(timezone.utc).isoformat()
    run.update(goal_id=GOAL_ID, agent_id=AGENT_ID, generated_at=stamp)
    run["agent_vision"].update(agent_id=AGENT_ID, generated_at=stamp)
    run["vision_checkpoint"].update(agent_id=AGENT_ID, generated_at=stamp)
    if not replan:
        run["agent_vision"]["vision_patch"].update(
            acceptance_summary="Verify the remaining real outcome.",
            replan_trigger_summary="The final acceptance remains open.",
        )
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(json.dumps(run) + "\n")
    capture = tmp_path / "vision-capture"
    code, envelope = _run_cli(registry, runtime, "quota", "should-run",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        *([] if replan else ["--todo-id", TODO_ID]),
        "--turn-instance-id", "vision-contract-turn", "--codex-app",
        "--available-capability", "filesystem_read", "--turn-envelope",
        "--decision-output-dir", str(capture), cwd=project)
    assert code == 0, envelope
    full = json.loads((capture / "decision.json").read_text())
    audit = full["vision_continuation_audit"]
    assert audit["required"] and audit["required_before_closeout"]
    assert audit["vision_gap_judge"]["done"] is False
    assert full["goal_frontier_projection"]["replan_required"] is replan
    projected = envelope["contract_capsule"]["vision_continuation_audit"]
    for field in ("required_before_closeout", "recommended_action", "vision_gap_judge",
                  "authoritative_evidence_kinds", "not_satisfied_by"):
        assert projected[field] == audit[field]
    assert envelope["action_signature"]["matches"] is True
    assert "reward_memory" not in envelope["boundary"].get("capabilities", {})

    from loopx.control_plane.quota.turn_envelope import (
        build_turn_envelope, turn_envelope_action_signature_document,
    )

    # Consume the saved source without reentering quota or appending a receipt.
    before = {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
    replay = build_turn_envelope(full, captured_decision_path=str(capture / "decision.json"))
    assert replay["contract_capsule"]["vision_continuation_audit"] == projected
    assert {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()} == before
    changed = json.loads(json.dumps(envelope))
    changed["contract_capsule"]["vision_continuation_audit"]["required_before_closeout"].pop()
    assert turn_envelope_action_signature_document(changed) != turn_envelope_action_signature_document(envelope)
    from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority
    from loopx.control_plane.turn_driver.codex_cli import _prompt

    assert extract_turn_authority({"turn_envelope": envelope})["primary_action"]
    assert json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(",", ":")) in _prompt(
        {"turn_envelope": envelope, "turn_key": "vision-contract-turn"}
    )
    with pytest.raises(ValueError, match="action signature"):
        extract_turn_authority({"turn_envelope": changed})
