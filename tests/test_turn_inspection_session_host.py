"""Journal inspection reads the persisted host; it never launches that host."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from loopx.cli import main
from loopx.control_plane import turn_driver
from loopx.control_plane.turn_driver.codex_sessions import (
    _session_path,
    _write_codex_cli_session,
)
from loopx.control_plane.turn_driver.executor import BuiltInHostError, turn_journal_path


def _failed_turn(tmp_path: Path, *, host: str, scope: str) -> tuple[dict, Path, Path]:
    envelope = {
        "ok": True,
        "schema_version": "loopx_turn_envelope_v0",
        "goal_id": "fixture-goal",
        "agent_id": "fixture-agent",
        "should_run": True,
        "effective_action": "normal_run",
        "action": {
            "must_attempt": True,
            "delivery_allowed": True,
            "quiet_noop_allowed": False,
            "selected_todo": {
                "todo_id": "todo_fixture0001", "text": "Advance one public fixture",
            },
        },
        "user": {"action_required": False, "open_count": 0, "notify": "DONT_NOTIFY"},
        "writeback": {"spend_after_validation": True},
        "scheduler": {"action": "run_now"},
        "action_signature": {
            "matches": True, "source_hash": "sha256:fixture",
            "envelope_hash": "sha256:fixture",
        },
        "compaction": {"within_budget": True},
    }
    plan = turn_driver.build_loopx_turn_plan(
        envelope, host=host, execution_mode="isolated-headless", session_scope=scope,
    )

    def interrupted(_request):
        raise BuiltInHostError("fixture_interrupted", recovery_kind="resume_session")

    def no_effect(*_args, **_kwargs):
        pytest.fail("an interrupted fixture must not reach settlement")

    result = turn_driver.run_loopx_turn_once(
        plan, host_runner=interrupted, project=tmp_path, runtime_root=tmp_path,
        goal_id="fixture-goal", timeout_seconds=5, execute=True,
        writeback=no_effect, spend=no_effect, scheduler=no_effect,
    )
    assert result["status"] == "failed"
    lineage = {
        "goal_id": "fixture-goal", "agent_id": "fixture-agent",
        "todo_id": "todo_fixture0001",
    }
    _write_codex_cli_session(
        tmp_path, lineage=lineage, session_scope=scope, host=host,
        session_id="fixture-session",
    )
    return (
        plan,
        turn_journal_path(
            tmp_path, goal_id="fixture-goal", turn_key=plan["transaction"]["turn_key"],
        ),
        _session_path(tmp_path, lineage, scope),
    )


def _inspect(tmp_path: Path, plan: dict, *, watch: bool, agent: str = "fixture-agent"):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main([
            "--registry", str(tmp_path / "registry.json"),
            "--runtime-root", str(tmp_path), "turn", "inspect-journal",
            "--goal-id", "fixture-goal", "--agent-id", agent,
            "--turn-key", plan["transaction"]["turn_key"], "--retry-failed-turn",
            *(["--watch", "--watch-interval", "0.01"] if watch else []),
            "--format", "json",
        ])
    return code, [json.loads(line) for line in output.getvalue().splitlines()]


@pytest.mark.parametrize(("host", "scope"), [
    ("dsh", "todo"), ("codex-cli", "todo"), ("codex-cli", "agent"),
])
@pytest.mark.parametrize("watch", [False, True])
def test_inspection_matches_the_journal_host_without_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: str, scope: str, watch: bool,
) -> None:
    plan, journal, session = _failed_turn(tmp_path, host=host, scope=scope)
    before = (journal.read_bytes(), session.read_bytes())
    observed = []
    original = turn_driver.inspect_loopx_turn_journal

    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        observed.append(result)
        return result

    monkeypatch.setattr(turn_driver, "inspect_loopx_turn_journal", capture)
    code, output = _inspect(tmp_path, plan, watch=watch)

    assert code == 0
    assert observed[0]["recovery_decision"]["can_continue"] is True
    assert {"kind": "host_session_binding", "outcome": "passed",
            "reason": "session_binding_matched"} in observed[0]["recovery_decision"]["checks"]
    assert all(row["effects"] == [] for row in output)
    assert before == (journal.read_bytes(), session.read_bytes())
    assert "fixture-session" not in json.dumps(output)


@pytest.mark.parametrize("watch", [False, True])
@pytest.mark.parametrize("fault", ["missing", "wrong_host", "wrong_scope", "wrong_owner"])
def test_inspection_does_not_fallback_to_an_unrelated_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, watch: bool, fault: str,
) -> None:
    plan, journal, session = _failed_turn(tmp_path, host="dsh", scope="todo")
    if fault == "missing":
        session.unlink()
    elif fault == "wrong_host":
        # A valid Codex binding at the same scope must not stand in for DSH.
        _write_codex_cli_session(
            tmp_path, lineage={
                "goal_id": "fixture-goal", "agent_id": "fixture-agent",
                "todo_id": "todo_fixture0001",
            }, host="codex-cli", session_id="other-host-session",
        )
    else:
        value = json.loads(session.read_text())
        if fault == "wrong_scope":
            value["session_scope"] = "agent"
        else:
            value["agent_id"] = "another-agent"
        session.write_text(json.dumps(value))
    before = journal.read_bytes()
    observed = []
    original = turn_driver.inspect_loopx_turn_journal

    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        observed.append(result)
        return result

    monkeypatch.setattr(turn_driver, "inspect_loopx_turn_journal", capture)
    _inspect(tmp_path, plan, watch=watch)
    decision = observed[0]["recovery_decision"]
    assert decision["can_continue"] is False
    assert decision["reinvoke_host"] is False
    assert observed[0]["effects"] == []
    assert journal.read_bytes() == before
