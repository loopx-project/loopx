from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import stat

import pytest

from loopx.control_plane.turn_driver.executor import validate_loopx_turn_host_result
from loopx_memt_turn.config import AdapterError
from loopx_memt_turn.host import run

from memt_fixtures import turn, with_tools


def execute(config, request=None):
    return asyncio.run(run(request or turn(), config))


def test_completed_turn_restores_snapshot_after_server_restart_and_replays_without_calls(config, provider):
    first = execute(config)
    assert first["result_kind"] == "validated_progress"
    plan = {"transaction": {"turn_key": turn()["turn_key"]}, "turn_envelope": turn()["turn_envelope"]}
    assert validate_loopx_turn_host_result(plan, first)["ok"] is True
    count = len(provider.requests)
    assert execute(config) == first
    assert len(provider.requests) == count
    provider.epoch = "restarted-fixture-epoch"
    second = execute(config, turn(2))
    assert second["turn_key"] == turn(2)["turn_key"]
    imports = [body for _, body in provider.requests if body.get("action") == "import"]
    assert len(imports) == 1 and imports[0]["file"] == "snapshot-0.safetensors"
    assert imports[0]["epoch"] == "restarted-fixture-epoch"
    commit_count = sum("|commit:" in body.get("rid", "") for _, body in provider.requests)
    assert commit_count == 2
    for receipt in Path(config.state_dir).rglob("*.json"):
        if not receipt.name.endswith("holder.json"):
            assert stat.S_IMODE(receipt.stat().st_mode) == 0o600


@pytest.mark.parametrize("identity", [
    {"goal": "other-goal"}, {"agent": "other-agent"}, {"instance": "ginst_" + "2" * 32},
])
def test_separate_owner_does_not_import_another_memory(config, provider, identity):
    execute(config)
    execute(config, turn(2, **identity))
    assert not any(body.get("action") == "import" for _, body in provider.requests)


def test_old_instance_pending_turn_does_not_block_new_lifetime(config, provider):
    provider.fail_action = "export"
    with pytest.raises(RuntimeError):
        execute(config)
    provider.fail_action = None
    assert execute(config, turn(2, instance="ginst_" + "2" * 32))["result_kind"] == "validated_progress"
    assert not any(body.get("action") == "import" for _, body in provider.requests)


@pytest.mark.parametrize("reference", [None, {}, {"goal_id": "other-goal", "goal_instance_id": "ginst_" + "1" * 32},
                                        {"goal_id": "public-goal", "goal_instance_id": "legacy"}])
def test_missing_or_mismatched_goal_instance_never_opens_memory(config, provider, reference):
    request = turn()
    request["goal_ref"] = reference
    with pytest.raises(AdapterError, match="exact_goal_instance"):
        execute(config, request)
    assert provider.requests == []
    assert not Path(config.state_dir).exists()


@pytest.mark.parametrize("action", ["export", "publish", "close"])
def test_uncertain_completion_blocks_new_turns_and_never_repeats_work(config, provider, action):
    provider.fail_action = action
    with pytest.raises(RuntimeError):
        execute(config)
    calls = len(provider.requests)
    for request in (turn(), turn(2)):
        with pytest.raises(AdapterError, match="reconciliation"):
            execute(config, request)
    assert len(provider.requests) == calls


def test_disabled_host_and_invalid_authority_have_no_provider_effect(config, provider):
    with pytest.raises(AdapterError, match="disabled"):
        execute(replace(config, enabled=False))
    request = turn()
    request["turn_envelope"]["action"]["primary_action"] = "Tampered authority"
    with pytest.raises(ValueError):
        execute(config, request)
    assert provider.requests == []
    assert not Path(config.state_dir).exists()


def test_config_or_request_change_cannot_reuse_a_completed_turn(config, provider):
    execute(config)
    calls = len(provider.requests)
    request = turn()
    request["session"]["extra"] = "conflict"
    with pytest.raises(AdapterError, match="request_conflict"):
        execute(config, request)
    with pytest.raises(AdapterError, match="configuration_mismatch"):
        execute(replace(config, max_new_tokens=99), turn(2))
    assert len(provider.requests) == calls


def test_replaying_old_result_cannot_roll_back_a_newer_memory_cursor(config, provider):
    execute(config)
    execute(config, turn(2))
    execute(config)
    execute(config, turn(3))
    imports = [body["file"] for _, body in provider.requests if body.get("action") == "import"]
    assert imports == ["snapshot-0.safetensors", "snapshot-1.safetensors"]


def test_exported_candidate_receipt_recovers_cursor_write_without_reexecuting(config, provider):
    first = execute(config)
    head_path = next(Path(config.state_dir).rglob("head.json"))
    head = json.loads(head_path.read_text())
    head["pending_turn"] = turn()["turn_key"]
    head["snapshot"] = None
    head_path.write_text(json.dumps(head))
    calls = len(provider.requests)
    assert execute(config) == first
    assert len(provider.requests) == calls
    execute(config, turn(2))
    imports = [body["file"] for _, body in provider.requests if body.get("action") == "import"]
    assert imports == ["snapshot-0.safetensors"]


def test_missing_cursor_does_not_silently_start_empty_memory(config, provider):
    execute(config)
    next(Path(config.state_dir).rglob("head.json")).unlink()
    calls = len(provider.requests)
    with pytest.raises(AdapterError, match="cursor_missing"):
        execute(config, turn(2))
    assert len(provider.requests) == calls


def test_real_mcp_tool_round_records_once_and_does_not_receive_provider_key(config, provider, monkeypatch):
    monkeypatch.setenv("MEMT_API_KEY", "synthetic-secret-not-for-tools")
    config = with_tools(config)
    provider.replies = ["<tool_call><function=record_observation><parameter=observation>first</parameter></function></tool_call>"]
    first = execute(config)
    assert (Path(config.workspace) / "observations.txt").read_text() == "first\n"
    assert execute(config) == first
    assert (Path(config.workspace) / "observations.txt").read_text() == "first\n"


def test_exhausted_tool_budget_does_not_commit_partial_history(config, provider):
    config = replace(with_tools(config), max_tool_calls=1)
    provider.replies = ["<tool_call><function=record_observation><parameter=observation>one</parameter></function></tool_call>",
                        "<tool_call><function=record_observation><parameter=observation>two</parameter></function></tool_call>"]
    with pytest.raises(ExceptionGroup) as failure:
        execute(config)
    assert failure.value.subgroup(AdapterError) is not None
    assert (Path(config.workspace) / "observations.txt").read_text() == "one\n"
    assert not provider.snapshots


@pytest.mark.parametrize("truncate_continuation", [False, True])
def test_truncated_tool_round_never_dispatches_its_calls_or_publishes(config, provider, truncate_continuation):
    config = with_tools(config)
    calls = ["<tool_call><function=record_observation><parameter=observation>one</parameter></function></tool_call>",
             "<tool_call><function=record_observation><parameter=observation>two</parameter></function></tool_call>"]
    provider.replies = calls if truncate_continuation else calls[:1]
    provider.finishes = ["stop", "length"] if truncate_continuation else ["length"]
    with pytest.raises(ExceptionGroup) as failure:
        execute(config)
    assert failure.value.subgroup(AdapterError) is not None
    output = Path(config.workspace) / "observations.txt"
    assert output.read_text() == "one\n" if truncate_continuation else not output.exists()
    assert not provider.snapshots


@pytest.mark.parametrize("answer,finish", [("not JSON", "stop"), ('{"result_kind":"wait"}', "length")])
def test_invalid_or_truncated_output_never_becomes_a_snapshot(config, provider, answer, finish):
    provider.candidate, provider.finish = answer, finish
    with pytest.raises(AdapterError):
        execute(config)
    assert provider.snapshots == {}


@pytest.mark.parametrize("summary", ["x" * 401, {"invalid": "type"}])
def test_structurally_invalid_candidate_is_rejected_before_memory_export(config, provider, summary):
    provider.candidate = json.dumps({"result_kind": "validated_progress", "classification": "recorded",
                                    "next_action": "Validate the artifact.", "summary": summary})
    with pytest.raises(AdapterError, match="invalid_typed_candidate"):
        execute(config)
    assert provider.snapshots == {}


def test_receipt_is_not_a_transcript_or_credential_log(config, provider):
    execute(config)
    receipts = [json.loads(path.read_text()) for path in Path(config.state_dir).rglob("*.json")]
    assert not any("input_ids" in receipt or "responses" in receipt for receipt in receipts)
