"""The real CLI output contract can omit the legacy observation safely."""
from __future__ import annotations

from pathlib import Path

from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority
from loopx.quota import render_quota_should_run_markdown
from test_quota_settlement_cli import AGENT_ID, GOAL_ID, _run_cli, _write_fixture


def test_real_cli_and_host_use_structured_contracts_without_packet(tmp_path: Path) -> None:
    project, runtime, registry = _write_fixture(tmp_path)
    guard = (
        "quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--turn-instance-id", "packet-retirement-cli",
    )
    code, payload = _run_cli(registry, runtime, *guard)
    assert code == 0, payload
    assert "protocol_action_packet" not in payload
    assert "protocol_action_packet:" not in render_quota_should_run_markdown(payload)
    assert payload["interaction_contract"]["agent_channel"]["primary_action"]
    envelope = build_turn_envelope(payload)
    authority = extract_turn_authority({"turn_envelope": envelope})
    assert "protocol_action_packet" not in envelope["contract_capsule"]
    assert authority["primary_action"]
    identity = payload["heartbeat_receipt"].get("settlement_identity")

    code, replay = _run_cli(registry, runtime, *guard)
    assert code == 0, replay
    assert "protocol_action_packet" not in replay
    assert replay["heartbeat_receipt"].get("settlement_identity") == identity
    assert replay["heartbeat_receipt"]["status"] == "replayed"
    assert replay["rollout_event"]["appended"] is False
    assert "protocol_action_packet" not in build_turn_envelope(replay)["contract_capsule"]
