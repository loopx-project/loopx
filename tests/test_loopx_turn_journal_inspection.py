from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from loopx.cli import main as cli_main
from loopx.cli_commands import turn as turn_command
from loopx.cli_commands import turn_decision
from loopx.cli_commands import turn_rendering, turn_todo_writeback
from loopx.control_plane.turn_driver import executor
from loopx.control_plane.turn_driver import turn_journal_runtime


TURN_KEY = "sha256:" + "a" * 64
COMPLETED_PHASES = [
    "host_execute",
    "typed_result",
    "validation",
    "durable_writeback",
    "quota_spend",
    "scheduler_apply",
    "scheduler_ack",
]


def _journal(*, status: str = "committed") -> dict[str, Any]:
    return {
        "schema_version": "loopx_turn_journal_v0",
        "goal_id": "fixture-goal",
        "turn_key": TURN_KEY,
        "status": status,
        "completed_phases": list(COMPLETED_PHASES),
        "plan": {
            "turn_envelope": {
                "goal_id": "fixture-goal",
                "agent_id": "fixture-agent",
                "action": {
                    "selected_todo": {"todo_id": "todo_fixture0001"},
                },
            },
            "transaction": {
                "turn_key": TURN_KEY,
                "settlement_plan": {
                    "schema_version": "quota_settlement_plan_v1",
                    "identity": {
                        "schema_version": "quota_settlement_identity_v0",
                        "effect_id": (
                            f"fixture-goal:fixture-agent:todo_fixture0001:{TURN_KEY}"
                        ),
                        "goal_id": "fixture-goal",
                        "agent_id": "fixture-agent",
                        "todo_id": "todo_fixture0001",
                        "turn_instance_id": TURN_KEY,
                    }
                },
            },
        },
        "host_result": {"turn_key": TURN_KEY, "private_detail": "do-not-expose"},
        "receipt": {"turn_key": TURN_KEY, "body": "do-not-expose"},
    }


def _write_journal(runtime_root: Path, journal: dict[str, Any]) -> Path:
    path = runtime_root / "goals" / "fixture-goal" / "turns" / f"{'a' * 64}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(journal, indent=2) + "\n", encoding="utf-8")
    return path


def _run_inspection_cli(
    tmp_path: Path,
    *,
    output_format: str,
    goal_id: str = "fixture-goal",
    agent_id: str = "fixture-agent",
    turn_key: str = TURN_KEY,
) -> tuple[int, str]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(tmp_path / "registry.json"),
                "--runtime-root",
                str(tmp_path),
                "turn",
                "inspect-journal",
                "--goal-id",
                goal_id,
                "--agent-id",
                agent_id,
                "--turn-key",
                turn_key,
                "--format",
                output_format,
            ]
        )
    return exit_code, output.getvalue()


def test_existing_run_once_markdown_renderer_remains_intact() -> None:
    rendered = turn_command._render_loopx_turn_execution_markdown(
        {
            "status": "committed",
            "result_kind": "validated_progress",
            "validation": {"status": "passed", "recovery_kind": None},
            "receipt": {"next_phase": None},
            "reason": "dsh_output_budget_exhausted_no_final",
            "host_failure": {
                "kind": "output_budget_exhausted",
                "retryable": False,
            },
            "managed_executor": {
                "execution_profile": "deepseek-v4-flash@high",
                "output_token_budget": {
                    "max_tokens": 16_384,
                    "scope": "per_model_request",
                },
            },
            "effects": {
                "host_invoked": True,
                "state_written": True,
                "quota_spent": True,
            },
        }
    )

    assert rendered.startswith("# LoopX Turn Run Once\n")
    assert "- validation: passed" in rendered
    assert "- quota_spent: True" in rendered
    assert "- execution_profile: deepseek-v4-flash@high" in rendered
    assert "- output_token_limit: 16384" in rendered
    assert "- output_token_limit_scope: per_model_request" in rendered
    assert "- host_failure_kind: output_budget_exhausted" in rendered
    assert "- host_failure_retryable: False" in rendered
    assert "- failure_reason: dsh_output_budget_exhausted_no_final" in rendered


def test_non_budget_failure_keeps_the_existing_compact_rendering() -> None:
    from loopx.cli_commands.turn_rendering import render_loopx_turn_execution_markdown

    rendered = render_loopx_turn_execution_markdown({
        "status": "failed", "result_kind": "host_failure",
        "reason": "provider_failed",
        "host_failure": {"kind": "auth_failed", "retryable": False},
    })
    assert rendered == "\n".join([
        "# LoopX Turn Run Once", "- status: failed", "- result_kind: host_failure",
        "- validation: None", "- recovery_kind: None", "- next_phase: None",
        "- host_invoked: None", "- state_written: None", "- quota_spent: None",
    ])


def test_inspection_markdown_distinguishes_current_plan_from_last_result() -> None:
    rendered = turn_rendering.render_loopx_turn_journal_inspection_markdown(
        {
            "ok": True,
            "decision": "replay_legal",
            "journal_status": "committed",
            "replay_legal": True,
            "goal_matches": True,
            "owner_matches": True,
            "turn_key_matches": True,
            "phases_form_ordered_prefix": True,
            "completed_phases": COMPLETED_PHASES,
            "tombstone_retained": True,
            "violations": [],
            "journal_consistent": True,
            "recovery_decision": {
                "action": "return_existing",
                "can_continue": False,
                "resume_from": None,
                "reinvoke_host": False,
                "reason": "terminal_result_retained",
                "checks": [{"kind": "journal_consistency", "outcome": "passed"}],
            },
            "last_recovery": {
                "planned": {"action": "continue", "resume_from": "validation"},
                "actual": {
                    "status": "finished",
                    "journal_status": "committed",
                    "host_invoked": False,
                },
            },
        }
    )

    assert "- recovery_action: return_existing" in rendered
    assert "- last_recovery_plan: continue" in rendered
    assert "- last_recovery_from: validation" in rendered
    assert "- last_recovery_actual: finished" in rendered
    assert "- last_recovery_result_status: committed" in rendered
    assert "- last_recovery_host_invoked: False" in rendered


def test_inspection_returns_versioned_allowlisted_projection_without_mutation(
    tmp_path: Path,
) -> None:
    journal_path = _write_journal(tmp_path, _journal())
    before_bytes = journal_path.read_bytes()
    before_entries = {
        path.relative_to(tmp_path)
        for path in tmp_path.rglob("*")
        if not path.name.endswith((".lock", ".lock.holder.json"))
    }

    result = executor.inspect_loopx_turn_journal(
        tmp_path,
        goal_id="fixture-goal",
        agent_id="fixture-agent",
        turn_key=TURN_KEY,
    )

    after_entries = {
        path.relative_to(tmp_path)
        for path in tmp_path.rglob("*")
        if not path.name.endswith((".lock", ".lock.holder.json"))
    }
    assert result == {
        "ok": True,
        "schema_version": "loopx_turn_journal_inspection_v1",
        "decision": "replay_legal",
        "journal_status": "committed",
        "replay_legal": True,
        "goal_matches": True,
        "owner_matches": True,
        "turn_key_matches": True,
        "phases_form_ordered_prefix": True,
        "completed_phases": COMPLETED_PHASES,
        "tombstone_retained": True,
        "violations": [],
        "journal_consistent": True,
        "recovery_decision": {
            "schema_version": "loopx_turn_recovery_decision_v0",
            "action": "return_existing",
            "can_continue": False,
            "resume_from": None,
            "reinvoke_host": False,
            "reason": "terminal_result_retained",
            "retry_failed": False,
            "checks": [{"kind": "journal_consistency", "outcome": "passed"}],
        },
        "last_recovery": None,
        "effects": [],
    }
    assert journal_path.read_bytes() == before_bytes
    assert after_entries == before_entries
    assert "do-not-expose" not in json.dumps(result)


def test_inspection_reports_blocked_journal_as_successful_read(tmp_path: Path) -> None:
    journal = _journal(status="in_progress")
    journal["completed_phases"] = ["typed_result"]
    _write_journal(tmp_path, journal)

    result = executor.inspect_loopx_turn_journal(
        tmp_path,
        goal_id="fixture-goal",
        agent_id="fixture-agent",
        turn_key=TURN_KEY,
    )

    assert result["ok"] is True
    assert result["decision"] == "replay_blocked"
    assert result["replay_legal"] is False
    assert result["journal_consistent"] is False
    assert result["recovery_decision"]["action"] == "blocked"
    assert result["violations"] == [
        "completed_phases_not_ordered_prefix",
        "journal_not_terminal",
    ]
    assert result["effects"] == []


def test_inspection_rejects_goal_path_traversal_before_lock_or_file_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_access(*args: object, **kwargs: object) -> None:
        raise AssertionError("filesystem access must not occur")

    monkeypatch.setattr(executor, "exclusive_file_lock", unexpected_access)
    monkeypatch.setattr(executor, "_load_journal", unexpected_access)

    with pytest.raises(ValueError, match="single path segment"):
        executor.inspect_loopx_turn_journal(
            tmp_path,
            goal_id="../outside",
            agent_id="fixture-agent",
            turn_key=TURN_KEY,
        )


@pytest.mark.parametrize("agent_id", ["", " fixture-agent "])
def test_inspection_rejects_invalid_agent_identity_before_file_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent_id: str,
) -> None:
    def unexpected_access(*args: object, **kwargs: object) -> None:
        raise AssertionError("filesystem access must not occur")

    monkeypatch.setattr(executor, "exclusive_file_lock", unexpected_access)
    monkeypatch.setattr(executor, "_load_journal", unexpected_access)

    with pytest.raises(ValueError, match="non-empty exact identity"):
        executor.inspect_loopx_turn_journal(
            tmp_path,
            goal_id="fixture-goal",
            agent_id=agent_id,
            turn_key=TURN_KEY,
        )


def test_inspect_journal_cli_branches_before_live_or_write_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_journal(tmp_path, _journal())

    def unexpected_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("inspect-journal reached a live or write path")

    for name in (
        "build_live_quota_should_run_decision",
        "build_loopx_turn_plan",
        "build_turn_envelope",
        "run_codex_cli_host",
        "run_loopx_turn_once",
        "spend_quota_slot",
        "refresh_state_run",
    ):
        monkeypatch.setattr(turn_command, name, unexpected_call)
    # The shared decision owner now performs the live reads this command used to
    # resolve itself, so the guard has to patch them where they live. Patching
    # the old ``turn_command`` names would fail loudly here instead of proving
    # anything: `turn` no longer resolves its own status, scheduler context or
    # operator-inbox projector.
    for name in (
        "build_lark_operator_inbox_urgency_projector",
        "collect_status",
        "scheduler_execution_context_for_turn",
    ):
        monkeypatch.setattr(turn_decision, name, unexpected_call)
    for name in ("complete_goal_todo", "update_goal_todo"):
        monkeypatch.setattr(turn_todo_writeback, name, unexpected_call)
    monkeypatch.setattr(executor, "execute_turn_driver_settlement", unexpected_call)
    monkeypatch.setattr(executor, "_write_journal", unexpected_call)

    exit_code, raw_output = _run_inspection_cli(tmp_path, output_format="json")

    payload = json.loads(raw_output)
    assert exit_code == 0
    assert payload["schema_version"] == "loopx_turn_journal_inspection_v1"
    assert payload["decision"] == "replay_legal"
    assert payload["effects"] == []


def test_inspect_journal_cli_json_and_markdown_share_allowlisted_projection(
    tmp_path: Path,
) -> None:
    _write_journal(tmp_path, _journal())

    json_exit, json_output = _run_inspection_cli(tmp_path, output_format="json")
    markdown_exit, markdown_output = _run_inspection_cli(
        tmp_path,
        output_format="markdown",
    )

    payload = json.loads(json_output)
    assert json_exit == 0
    assert markdown_exit == 0
    assert set(payload) == {
        "ok",
        "schema_version",
        "decision",
        "journal_status",
        "replay_legal",
        "goal_matches",
        "owner_matches",
        "turn_key_matches",
        "phases_form_ordered_prefix",
        "completed_phases",
        "tombstone_retained",
        "violations",
        "journal_consistent",
        "recovery_decision",
        "last_recovery",
        "effects",
    }
    assert markdown_output == (
        "# LoopX Turn Journal Inspection\n"
        "- recovery_action: return_existing\n"
        "- recovery_can_continue: False\n"
        "- recovery_from: None\n"
        "- recovery_reinvoke_host: False\n"
        "- recovery_reason: terminal_result_retained\n"
        "- recovery_checks: journal_consistency:passed\n"
        "- journal_consistent: True\n"
        "- replay_decision: replay_legal\n"
        "- journal_status: committed\n"
        "- replay_legal: True\n"
        "- goal_matches: True\n"
        "- owner_matches: True\n"
        "- turn_key_matches: True\n"
        "- phases_form_ordered_prefix: True\n"
        f"- completed_phases: {', '.join(COMPLETED_PHASES)}\n"
        "- tombstone_retained: True\n"
        "- violations: none\n"
        "- effects: none\n"
    )
    assert "do-not-expose" not in json_output
    assert "do-not-expose" not in markdown_output


def test_inspect_journal_cli_returns_zero_for_identity_mismatch(tmp_path: Path) -> None:
    _write_journal(tmp_path, _journal())

    exit_code, raw_output = _run_inspection_cli(
        tmp_path,
        output_format="json",
        agent_id="other-agent",
    )

    payload = json.loads(raw_output)
    assert exit_code == 0
    assert payload["decision"] == "replay_blocked"
    assert payload["owner_matches"] is False
    assert payload["violations"] == ["owner_mismatch"]


@pytest.mark.parametrize(
    ("journal_text", "error_fragment"),
    (
        (None, "journal does not exist"),
        ("{not-json", "contains malformed JSON"),
        ('{"schema_version": "future_turn_journal_v1"}', "unsupported schema"),
    ),
)
def test_inspect_journal_cli_returns_nonzero_when_inspection_cannot_complete(
    tmp_path: Path,
    journal_text: str | None,
    error_fragment: str,
) -> None:
    if journal_text is not None:
        path = _write_journal(tmp_path, _journal())
        path.write_text(journal_text, encoding="utf-8")

    exit_code, raw_output = _run_inspection_cli(tmp_path, output_format="json")

    payload = json.loads(raw_output)
    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["schema_version"] == "loopx_turn_journal_inspection_v1"
    assert error_fragment in payload["error"]
    assert payload["effects"] == []


def test_inspect_journal_cli_does_not_expose_path_on_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal_path = _write_journal(tmp_path, _journal())

    def denied_read(path: Path) -> None:
        raise OSError(13, "permission denied", str(path))

    monkeypatch.setattr(executor, "_load_journal", denied_read)

    exit_code, raw_output = _run_inspection_cli(tmp_path, output_format="json")

    payload = json.loads(raw_output)
    assert exit_code == 1
    assert payload["error"] == "LoopX Turn journal could not be read"
    assert str(journal_path) not in raw_output


def test_inspection_has_no_python_fallback_when_typescript_runtime_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_journal(tmp_path, _journal())

    def missing_node(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("Turn-journal inspection requires Node.js 22.18.0 or newer")

    monkeypatch.setattr(turn_journal_runtime, "effect_runtime_result", missing_node)

    exit_code, raw_output = _run_inspection_cli(tmp_path, output_format="json")

    payload = json.loads(raw_output)
    assert exit_code == 1
    assert payload == {
        "ok": False,
        "schema_version": "loopx_turn_journal_inspection_v1",
        "error": "Turn-journal inspection requires Node.js 22.18.0 or newer",
        "effects": [],
    }


def test_typescript_runtime_uses_one_typed_rpc_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def rpc(method: str, params: dict[str, object]) -> dict[str, object]:
        calls.append((method, params))
        return {
            "ok": True,
            "schema_version": "loopx_turn_journal_inspection_v1",
            "decision": "replay_legal",
            "journal_status": "committed",
            "replay_legal": True,
            "goal_matches": True,
            "owner_matches": True,
            "turn_key_matches": True,
            "phases_form_ordered_prefix": True,
            "completed_phases": COMPLETED_PHASES,
            "tombstone_retained": True,
            "violations": [],
            "journal_consistent": True,
            "recovery_decision": {
                "schema_version": "loopx_turn_recovery_decision_v0",
                "action": "return_existing",
                "can_continue": False,
                "resume_from": None,
                "reinvoke_host": False,
                "reason": "terminal_result_retained",
                "retry_failed": False,
                "checks": [
                    {"kind": "journal_consistency", "outcome": "passed"}
                ],
            },
            "last_recovery": None,
            "effects": [],
        }

    monkeypatch.setattr(turn_journal_runtime, "effect_runtime_result", rpc)

    result = turn_journal_runtime.interpret_turn_journal_projection(
        _journal(),
        goal_id="fixture-goal",
        agent_id="fixture-agent",
        turn_key=TURN_KEY,
    )

    assert result["decision"] == "replay_legal"
    assert len(calls) == 1
    assert calls[0][0] == "turn_journal.inspect"
    assert calls[0][1]["turn_key"] == TURN_KEY


def test_typescript_runtime_fails_closed_when_process_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def cannot_start(*args: object, **kwargs: object) -> object:
        raise RuntimeError("TypeScript Effect runtime request failed")

    monkeypatch.setattr(turn_journal_runtime, "effect_runtime_result", cannot_start)

    with pytest.raises(RuntimeError, match="runtime request failed"):
        turn_journal_runtime.interpret_turn_journal_projection(
            _journal(),
            goal_id="fixture-goal",
            agent_id="fixture-agent",
            turn_key=TURN_KEY,
        )


def test_typescript_runtime_rejects_malformed_projection_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "ok": True,
        "schema_version": "loopx_turn_journal_inspection_v1",
        "decision": "replay_legal",
        "journal_status": "committed",
        "replay_legal": "true",
        "goal_matches": True,
        "owner_matches": True,
        "turn_key_matches": True,
        "phases_form_ordered_prefix": True,
        "completed_phases": COMPLETED_PHASES,
        "tombstone_retained": True,
        "violations": [],
        "journal_consistent": True,
        "recovery_decision": {
            "schema_version": "loopx_turn_recovery_decision_v0",
            "action": "return_existing",
            "can_continue": False,
            "resume_from": None,
            "reinvoke_host": False,
            "reason": "terminal_result_retained",
            "retry_failed": False,
            "checks": [{"kind": "journal_consistency", "outcome": "passed"}],
        },
        "last_recovery": None,
        "effects": [],
    }

    monkeypatch.setattr(
        turn_journal_runtime,
        "effect_runtime_result",
        lambda *_args, **_kwargs: payload,
    )

    with pytest.raises(RuntimeError, match="projection type mismatch"):
        turn_journal_runtime.interpret_turn_journal_projection(
            _journal(),
            goal_id="fixture-goal",
            agent_id="fixture-agent",
            turn_key=TURN_KEY,
        )
