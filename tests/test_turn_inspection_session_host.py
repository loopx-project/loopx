"""Journal inspection reads the persisted host; it never launches that host."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from loopx.cli import main
from loopx.control_plane import turn_driver
from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from loopx.control_plane.turn_driver.codex_sessions import (
    _session_path,
    _write_codex_cli_session,
)
from loopx.control_plane.turn_driver.executor import BuiltInHostError, turn_journal_path
from tests.test_turn_envelope import _full_decision


def _complete_source(tmp_path: Path) -> dict:
    from tests.test_loopx_turn_driver import _write_live_fixture

    _, runtime, registry = _write_live_fixture(tmp_path / "source")
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main([
            "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "turn", "plan", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
            "--host", "generic-cli", "--execution-mode", "isolated-headless",
            "--include-transaction-detail",
        ])
    plan = json.loads(output.getvalue())
    assert code == 0 and plan["ok"], plan
    return plan["work_contract"]


def _failed_turn(
    tmp_path: Path, *, host: str, scope: str, legacy: bool = True,
) -> tuple[dict, Path, Path]:
    decision = _full_decision() if legacy else _complete_source(tmp_path)
    goal_id = decision["goal_id"]
    plan = turn_driver.build_loopx_turn_plan(
        build_turn_envelope(decision) if legacy else decision,
        host=host, execution_mode="isolated-headless", session_scope=scope,
    )

    def interrupted(_request):
        raise BuiltInHostError("fixture_interrupted", recovery_kind="resume_session")

    def no_effect(*_args, **_kwargs):
        pytest.fail("an interrupted fixture must not reach settlement")

    result = turn_driver.run_loopx_turn_once(
        plan, host_runner=interrupted, project=tmp_path, runtime_root=tmp_path,
        goal_id=goal_id, timeout_seconds=5, execute=True,
        writeback=no_effect, spend=no_effect, scheduler=no_effect,
        require_decision_capture=True, decision_capture_payload=decision,
    )
    assert result["status"] == "failed"
    lineage = {
        "goal_id": goal_id, "agent_id": "codex-fixture",
        "todo_id": "todo_fixture0001",
    }
    _write_codex_cli_session(
        tmp_path, lineage=lineage, session_scope=scope, host=host,
        session_id="fixture-session",
    )
    return (
        plan,
        turn_journal_path(
            tmp_path, goal_id=goal_id, turn_key=plan["transaction"]["turn_key"],
        ),
        _session_path(tmp_path, lineage, scope),
    )


def _inspect(tmp_path: Path, plan: dict, *, watch: bool, agent: str = "codex-fixture"):
    source = plan.get("work_contract") or plan["turn_envelope"]
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main([
            "--registry", str(tmp_path / "registry.json"),
            "--runtime-root", str(tmp_path), "turn", "inspect-journal",
            "--goal-id", source["goal_id"], "--agent-id", agent,
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
                "goal_id": "fixture-goal", "agent_id": "codex-fixture",
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


@pytest.mark.parametrize("watch", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize(("fault", "message"), [
    ("missing", "Original Turn decision capture is missing"),
    ("tampered", "Turn decision capture does not match the original observation"),
    ("symlink", "Turn decision capture must not be a symlink"),
    ("malformed", "Turn decision capture contains malformed JSON"),
    ("directory", "capture must be a regular file"),
])
def test_inspection_refuses_to_retry_with_unusable_capture(
    tmp_path: Path, watch: bool, legacy: bool, fault: str, message: str,
) -> None:
    plan, journal, session = _failed_turn(tmp_path, host="codex-cli", scope="todo", legacy=legacy)
    capture = journal.parent / "decisions" / journal.name
    original = capture.read_bytes()
    if fault == "missing":
        capture.unlink()
    elif fault == "tampered":
        changed = {**json.loads(original), "reason": "altered after the failed Turn"}
        capture.write_text(json.dumps(changed), encoding="utf-8")
    elif fault == "malformed":
        capture.write_text("{", encoding="utf-8")
    elif fault == "directory":
        capture.unlink()
        capture.mkdir()
    else:
        target = capture.rename(capture.with_name("redirected.json"))
        try:
            capture.symlink_to(target)
        except OSError:
            pytest.skip("creating a symlink requires permission on this host")
    before = journal.read_bytes(), session.read_bytes()

    code, (result,) = _inspect(tmp_path, plan, watch=watch)

    assert code == 1
    assert result["ok"] is False and message in result["error"]
    assert result["effects"] == []
    assert (journal.read_bytes(), session.read_bytes()) == before

    if capture.is_dir():
        capture.rmdir()
    else:
        capture.unlink(missing_ok=True)
    capture.write_bytes(original)
    restored_code, restored_output = _inspect(tmp_path, plan, watch=watch)
    assert restored_code == 0
    assert all(row["effects"] == [] for row in restored_output)
    assert (journal.read_bytes(), session.read_bytes()) == before
    assert capture.read_bytes() == original
