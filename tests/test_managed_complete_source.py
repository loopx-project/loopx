"""Normal managed observations consume the same full source as a fresh Turn."""
from __future__ import annotations

import contextlib
import copy
import io
import json

import pytest

from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.test_loopx_turn_driver import _write_live_fixture
from tests.test_loopx_turn_managed_step import _failed_journal


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("source_fault", [None, "unknown_action", "missing_read_command", "unknown_read_order"])
@pytest.mark.parametrize("entrypoint", ["quota", "managed"])
def test_real_current_entrypoints_check_and_preserve_the_complete_source(
    tmp_path, monkeypatch, provider, source_fault, entrypoint,
):
    from loopx.cli import main as cli_main
    from loopx.cli_commands import quota as quota_command
    from loopx.cli_commands.turn_decision import FreshTurnDecisionOwner
    from loopx.control_plane.turn_driver import loop_controller, turn_journal_path

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    _project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    lineage = {"goal_id": "loopx-turn-fixture", "agent_id": "codex-fixture", "todo_id": "todo_fixture0001"}
    journal = _failed_journal(lineage=lineage)
    path = turn_journal_path(runtime, goal_id=lineage["goal_id"], turn_key=journal["turn_key"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(journal))
    original_journal = path.read_bytes()

    # This is source data, not a prequalified route or a second permission.
    evidence = {"original_tail": "Keep all original requirements and STOP at the budget.",
        "large_integer": 10**45 + 17, "original_float": 1.0}
    resolve = FreshTurnDecisionOwner.resolve

    def source_data(current):
        current["future_supporting_evidence"] = copy.deepcopy(evidence)
        if source_fault == "unknown_action":
            current["effective_action"] = "unregistered_current_action"
        # These declare an executable pre-work obligation, not optional evidence.
        # A current contract cannot silently drop its command or defer its order.
        read = {"source": "fixture_material", "command": "cat -- 'required material.md'",
                "ordering": "before_work", "reason": "Read the complete original material."}
        if source_fault == "missing_read_command":
            read.pop("command")
        elif source_fault == "unknown_read_order":
            read["ordering"] = "after_spend"
        current["interaction_contract"]["agent_channel"].setdefault("required_reads", []).append(read)
        return current

    monkeypatch.setattr(FreshTurnDecisionOwner, "resolve", lambda self: source_data(resolve(self)))
    exported = quota_command._export

    def with_quota_source(name):
        function = exported(name)
        if name == "build_live_quota_should_run_decision":
            return lambda *args, **kwargs: source_data(function(*args, **kwargs))
        return function

    monkeypatch.setattr(quota_command, "_export", with_quota_source)
    observed = []
    request = loop_controller.effect_runtime_projection_result

    def inspect(method, params, **options):
        if method == "turn.controller" and params.get("operation") == "managed":
            assert "historical_route_text" not in params
            observed.append(copy.deepcopy(params["decision"]))
        return request(method, params, **options)

    monkeypatch.setattr(loop_controller, "effect_runtime_projection_result", inspect)
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    argv = [*prefix, "turn", "managed-step", "--goal-id", lineage["goal_id"], "--agent-id", lineage["agent_id"],
        "--turn-key", journal["turn_key"], "--host", "dsh", "--execution-mode", "isolated-headless",
        "--observed-attempt", "1", "--observed-max-attempts", "3"]
    turn_id = "complete-source-qualification"
    if entrypoint == "quota":
        argv = [*prefix, "quota", "should-run", "--goal-id", lineage["goal_id"],
            "--agent-id", lineage["agent_id"], "--todo-id", lineage["todo_id"],
            "--turn-instance-id", turn_id, "--context-delivery", "off", "--complete-turn"]
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = cli_main(argv)
    result = json.loads(output.getvalue())
    if source_fault:
        assert code == 1 and result["ok"] is False, result
        expected = {"unknown_action": "Turn effective_action",
                    "missing_read_command": "required read command",
                    "unknown_read_order": "required read ordering"}[source_fault]
        assert expected in result["error"], result
        assert observed == []
        if entrypoint == "quota":
            assert result["should_run"] is False
            assert result["error_code"] == "quota_decision_transport_failed"
            assert result["diagnostic_code"] == "invalid_request"
            assert result["reason"] == result["error"]
            assert "receipt" in result["recommended_action"]
            assert "saved" not in result["recommended_action"]
            assert "captured_decision" not in result and "source_diagnostic" not in result
            assert "interaction_contract" not in result and "execution_preparation" not in result
            assert result["heartbeat_receipt"]["turn_instance_id"] == turn_id
            assert result["heartbeat_receipt"]["event_id"]
            if source_fault == "missing_read_command":
                markdown_argv = list(argv)
                markdown_argv[markdown_argv.index("--format") + 1] = "markdown"
                markdown_argv[markdown_argv.index("--complete-turn")] = "--turn-envelope"
                markdown = io.StringIO()
                with contextlib.redirect_stdout(markdown):
                    assert cli_main(markdown_argv) == 1
                assert expected in markdown.getvalue()
                assert turn_id in markdown.getvalue()
                assert "saved" not in markdown.getvalue()
            receipt_log = runtime / "goals" / lineage["goal_id"] / "rollout-event-log.jsonl"
            events = (json.loads(line) for line in receipt_log.read_text().splitlines())
            guard_ids = [event["event_id"] for event in events
                if event["event_kind"] == "quota_should_run" and event["run_id"] == turn_id
                and event["agent_id"] == lineage["agent_id"]]
            assert guard_ids == [result["heartbeat_receipt"]["event_id"]]
    else:
        if entrypoint == "managed":
            assert code == 0 and result["disposition"] == "wait", result
            assert len(observed) == 1
            source = observed[0]
        else:
            assert code == 0 and result["should_run"] is True, result
            assert observed == []
            source = result
        assert source["schema_version"] == "loopx_turn_envelope_v1"
        assert "action_signature" not in source
        assert source["future_supporting_evidence"] == evidence
        assert type(source["future_supporting_evidence"]["original_float"]) is float
        assert source["selected_todo"]["todo_id"] == lineage["todo_id"]
        read = next(item for item in source["interaction_contract"]["agent_channel"]["required_reads"]
                    if item["source"] == "fixture_material")
        assert read["command"] == "cat -- 'required material.md'"
        assert read["ordering"] == "before_work"
    assert path.read_bytes() == original_journal
    index = runtime / "goals" / lineage["goal_id"] / "runs/index.jsonl"
    rows = [json.loads(line) for line in index.read_text().splitlines()] if index.exists() else []
    assert not any(row.get("classification") == "quota_slot_spent" for row in rows)
