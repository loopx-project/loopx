"""The operator can understand a current plan and read its original full source."""
from __future__ import annotations

import copy
import json
import subprocess
import sys

import pytest

from loopx.cli_commands.turn_rendering import render_loopx_turn_plan_markdown
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_source_fixture,
    isolate_sqlite_runtime,
)
from tests.control_plane.test_turn_host_decision_capture import _fixture_module


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("ready", [True, False], ids=["ready", "quota-wait"])
def test_cli_plan_explains_work_and_retains_read_only_source(tmp_path, monkeypatch, provider, ready):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    f = _fixture_module()
    project, runtime, _workspace, registry = f._write_fixture(tmp_path, turn_count=2)
    if not ready:
        config = json.loads(registry.read_text())
        config["goals"][0]["quota"]["compute"] = 0
        registry.write_text(json.dumps(config))
    state = project / ".codex/goals" / f.GOAL_ID / "ACTIVE_GOAL_STATE.md"
    initialize_canonical_source_fixture(runtime, f.GOAL_ID, state_path=state, provider=provider)
    prefix = [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
              "--runtime-root", str(runtime)]
    plan_args = ["turn", "plan", "--include-transaction-detail", "--goal-id", f.GOAL_ID, "--agent-id", f.AGENT_ID,
                 "--host", "codex-cli", "--execution-mode", "isolated-headless"]
    json_read = subprocess.run([*prefix, *plan_args, "--format", "json"], capture_output=True, text=True)
    assert json_read.returncode == 0, json_read.stderr + json_read.stdout
    plan = json.loads(json_read.stdout)
    source = plan["work_contract"]
    assert source["should_run"] is ready
    before = copy.deepcopy(plan)
    rendered = render_loopx_turn_plan_markdown(plan, include_transaction_detail=True)
    assert f"- should_run: {ready}" in rendered
    assert source["effective_action"] in rendered
    assert source["reason"] in rendered
    assert "Inspection only" in rendered
    if ready:
        assert source["selected_todo"]["todo_id"] in rendered
        assert source["selected_todo"]["text"] in rendered
        assert "Selected work (summary)" in rendered
    # The renderer consumes the checked CLI projection rather than projecting
    # another contract or changing which source the operator reads.
    assert plan == before
    assert json.loads(rendered.split("```json\n", 1)[1].split("\n```", 1)[0]) == plan
    assert "session" in plan and "transaction" in plan
    assert "settlement_plan" not in plan["transaction"]
    cli_read = subprocess.run([*prefix, *plan_args, "--format", "markdown"], capture_output=True, text=True)
    assert cli_read.returncode == 0, cli_read.stderr + cli_read.stdout
    assert f"- should_run: {ready}" in cli_read.stdout
    assert source["effective_action"] in cli_read.stdout
    assert "Inspection only" in cli_read.stdout
    detail = json.loads(cli_read.stdout.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert detail["work_contract"]["reason"] == source["reason"]
    assert detail["work_contract"]["should_run"] is ready
    assert "session" in detail and "transaction" in detail
    assert "settlement_plan" not in detail["transaction"]
    if ready:
        assert detail["work_contract"]["selected_todo"] == source["selected_todo"]
    # The default human hot path stays byte-for-byte compatible. Detail must
    # remain a deliberate request, just as it already is for JSON consumers.
    default_args = [arg for arg in plan_args if arg != "--include-transaction-detail"]
    short = subprocess.run([*prefix, *default_args, "--format", "markdown"], capture_output=True, text=True)
    assert short.returncode == 0, short.stderr + short.stdout
    assert "## Full plan" not in short.stdout and "Selected work" not in short.stdout
    assert len(short.stdout.splitlines()) == 6
    assert not (project / f.MARKER_NAME).exists()
    assert not list((runtime / "goals" / f.GOAL_ID / "turns").glob("*.json"))
    assert not list(runtime.rglob("turn-sessions/*.json"))
    assert f._quota_spend_count(runtime) == 0


def test_plan_readback_keeps_unknowns_and_does_not_invent_a_task_or_recovery():
    plan = {"ok": True, "schema_version": "loopx_turn_plan_v1", "work_contract": {
        "should_run": None, "effective_action": "replan_required", "reason": "Review current evidence",
    }, "route": {"kind": "replan_required", "would_invoke_host": False}}
    text = render_loopx_turn_plan_markdown(plan, include_transaction_detail=True)
    assert "- should_run: None" in text
    assert "replan_required" in text and "Review current evidence" in text
    assert "Selected work" not in text and "Read full context" not in text
    assert "Inspection only" in text


def test_historical_plan_and_errors_keep_the_existing_format():
    old = {"ok": True, "host": {"kind": "codex-cli", "execution_mode": "isolated-headless"},
           "route": {"kind": "ready_for_host", "would_invoke_host": True},
           "turn_envelope": {"action": {"selected_todo": {"text": "historical"}}}}
    assert render_loopx_turn_plan_markdown(old) == (
        "# LoopX Turn Plan\n- host: codex-cli\n- execution_mode: isolated-headless\n"
        "- route: ready_for_host\n- would_invoke_host: True\n- side_effects: none")
    assert render_loopx_turn_plan_markdown({"ok": False, "error": "Source unavailable"}) == "LoopX Turn plan failed: Source unavailable"
