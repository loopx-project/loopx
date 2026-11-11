"""Builtin completion uses the real Todo validator and canonical lease fence."""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

from loopx.cli import main
from loopx.control_plane.todos.mutation_api import add_goal_todo
from loopx.control_plane.turn_driver.codex_cli import codex_cli_result_schema
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.test_loopx_turn_driver import _write_live_fixture


def _cli(registry: Path, runtime: Path, *args: str) -> tuple[int, dict]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(["--registry", str(registry), "--runtime-root", str(runtime),
                     "--format", "json", *args])
    return code, json.loads(output.getvalue())


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("failure", [None, "independent_validation", "declared_validation", "stale_lease"])
def test_builtin_codex_completes_before_accountable_writeback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, failure: str | None,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider, handoff_mode="hard_lease")
    added = add_goal_todo(
        registry_path=registry, goal_id="loopx-turn-fixture", role="agent",
        text="Deliver the independently checked artifact.", task_class="advancement_task",
        action_kind="fixture", claimed_by="codex-fixture", agent_id="codex-fixture",
        validation_command_json=json.dumps([sys.executable, "-c", "raise SystemExit(1)"
            if failure == "declared_validation" else "raise SystemExit(0)"]),
    )
    todo_id = added["todo_id"]
    claim_code, claim = _cli(registry, runtime, "todo", "claim", "--goal-id", "loopx-turn-fixture",
        "--todo-id", todo_id, "--agent-id", "codex-fixture", "--claimed-by", "codex-fixture")
    assert claim_code == 0, claim
    host_calls = []

    def host(request, **_kwargs):
        host_calls.append(request["turn_key"])
        if failure == "stale_lease":
            # A renewal advances the exact generation even for the same owner.
            proof = json.loads(next((runtime / "goals" / "loopx-turn-fixture" / "turns").glob("*.json")).read_text())["plan"]["completion_lease_proof"]
            code, renewed = _cli(registry, runtime, "task-lease", "renew", "--goal-id", "loopx-turn-fixture",
                "--todo-id", todo_id, "--owner", "codex-fixture", "--idempotency-key", proof["task_lease_idempotency_key"],
                "--expected-version", str(proof["task_lease_expected_version"]))
            assert code == 0, renewed
        return {"schema_version": "loopx_turn_result_v0", "turn_key": request["turn_key"],
            "result_kind": "validated_completion", "completed_phases": ["host_execute", "typed_result"],
            "classification": "fixture_delivery", "recommended_action": "Inspect the next current task.",
            "next_action": "Read the fresh decision.", "delivery_batch_scale": "implementation",
            "delivery_outcome": "outcome_progress", "vision_unchanged_reason": "The remaining Goal is unchanged.",
            "work_evidence": ["artifact:checked-fixture"], "validation_evidence": ["check:independent-fixture"],
            "summary": "The selected artifact is complete."}

    monkeypatch.setattr("loopx.cli_commands.turn_run_once.run_codex_cli_host", host)
    argv = ["turn", "run-once", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
        "--todo-id", todo_id, "--host", "codex-cli", "--project", str(project),
        "--validation-command-json", json.dumps([sys.executable, "-c", "raise SystemExit(1)"
            if failure == "independent_validation" else "raise SystemExit(0)"]),
        "--no-global-sync", "--execute"]
    code, result = _cli(registry, runtime, *argv)
    journals = list((runtime / "goals" / "loopx-turn-fixture" / "turns").glob("*.json"))
    assert len(journals) == 1
    journal = json.loads(journals[0].read_text())
    assert len(host_calls) == 1
    if failure:
        assert result["status"] != "committed", result
        assert "quota_spend" not in journal["completed_phases"]
        assert "durable_writeback" not in journal["completed_phases"]
        return
    assert code == 0 and result["status"] == "committed", result
    assert journal["writeback"]["completion"] == {"todo_id": todo_id, "continuation": "active_goal"}
    events = [json.loads(line) for line in (runtime / "goals" / "loopx-turn-fixture" / "rollout-event-log.jsonl").read_text().splitlines()]
    identity = journal["plan"]["transaction"]
    kinds = [event["event_kind"] for event in events if event.get("run_id") == identity.get("turn_instance_id", journal["turn_key"])]
    assert kinds.index("todo_complete") < kinds.index("refresh_state") < kinds.index("quota_spend")
    replay_args = argv[:-1]
    todo_option = replay_args.index("--todo-id")
    del replay_args[todo_option:todo_option + 2]
    code, replay = _cli(registry, runtime, *replay_args, "--resume-turn-key", journal["turn_key"], "--execute")
    assert code == 0 and replay["replayed"] is True, replay
    assert len(host_calls) == 1
    assert not any(replay["effects"].values())


def test_builtin_codex_advertises_explicit_completion_without_promoting_progress() -> None:
    kinds = codex_cli_result_schema()["properties"]["result_kind"]["enum"]
    assert "validated_completion" in kinds and "validated_progress" in kinds
