"""Real managed CLI and provider; the deterministic Host tests orchestration,
not model benefit. The separate Agent study must use an actual model."""
from __future__ import annotations

import contextlib
import io
import json
import sys

import pytest

from loopx.cli import main
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from tests.test_loopx_turn_driver import (
    _write_live_fixture, _completion_host_and_validation_scripts, _turn_run_once_completion_argv, _turn_journal,
)


def _cli(args):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(args)
    return code, json.loads(output.getvalue())


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("interruption", [None, "result_response_lost", "direction_call_failed", "stale_direction", "deferred_terminal", "continuous_direction_change"])
def test_managed_first_delivery_judges_after_result_and_replays_without_host(tmp_path, monkeypatch, provider, interruption):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path)
    state = project / ".codex/goals/loopx-turn-fixture/ACTIVE_GOAL_STATE.md"
    todo = {"schema_version": "todo_item_v0", "todo_id": "todo_fixture0001", "index": 1,
        "done": False, "text": "Advance one public fixture.", "role": "agent", "status": "open",
        "archive_state": "active", "source_section": "Agent Todo", "task_class": "advancement_task",
        "action_kind": "fixture", "claimed_by": "codex-fixture", "priority": "P0"}
    if interruption == "deferred_terminal":
        todo["no_followup"] = True
    initialize_canonical_authority(runtime, "loopx-turn-fixture",
        build_todo_runtime_shadow_projection(goal_id="loopx-turn-fixture", handoff_mode="soft_claim", todos=[todo]),
        state_path=state, provider=provider)
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    code, baseline = _cli([*prefix, "refresh-state", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
        "--vision-summary", "Validate the fixture and review remaining work.", "--vision-acceptance", "Fixture validation passes.",
        "--no-global-sync", "--suppress-external-sinks"])
    assert code == 0, baseline
    host_project = project / "host-workspace"
    host_project.mkdir()
    host, validation = _completion_host_and_validation_scripts()
    host = host.replace("request = json.load(sys.stdin)", '''request = json.load(sys.stdin)
with pathlib.Path("host-calls.txt").open("a") as log:
    log.write("direction\\n" if "direction_review" in request else "implementation\\n")
if "direction_review" in request:
    context = request["direction_review"]["context"]
    assert context["basis"]["todo"]["status"] == "done"
    json.dump({"read_context_id": context["read_context_id"], "decision": "continue",
        "vision_unchanged_reason": "The fixture passed; select remaining work from the current frontier."}, sys.stdout)
    raise SystemExit(0)
assert "delivery_result_context" in request
''')
    if interruption == "deferred_terminal":
        host = host.replace('== "done"', '== "open"').replace('"decision": "continue"', '"decision": "terminal_ready"')
    argv = _turn_run_once_completion_argv(host_project, runtime, registry, host, validation)
    host_file = host_project / "host.py"
    host_file.write_text(host, encoding="utf-8")
    argv[argv.index(json.dumps([sys.executable, "-c", host]))] = json.dumps([sys.executable, str(host_file)])
    argv.insert(-1, "--first-delivery")
    if interruption == "result_response_lost":
        from loopx.cli_commands import turn_run_once
        original = turn_run_once.write_turn_validated_completion

        def lose_once(**kwargs):
            original(**kwargs)
            monkeypatch.setattr(turn_run_once, "write_turn_validated_completion", original)
            raise OSError("injected response loss after result CAS")

        monkeypatch.setattr(turn_run_once, "write_turn_validated_completion", lose_once)
    elif interruption in {"stale_direction", "continuous_direction_change"}:
        from loopx.cli_commands import turn_run_once
        original = turn_run_once.refresh_state_run

        def change_basis_once(**kwargs):
            if kwargs.get("first_delivery"):
                state.write_text(state.read_text(encoding="utf-8") + "\n## Direction update\nReview the current remaining frontier.\n", encoding="utf-8")
                if interruption == "stale_direction":
                    monkeypatch.setattr(turn_run_once, "refresh_state_run", original)
            return original(**kwargs)

        monkeypatch.setattr(turn_run_once, "refresh_state_run", change_basis_once)
    elif interruption == "direction_call_failed":
        from loopx.control_plane.turn_driver import direction_review as first_delivery
        original = first_delivery.review_first_delivery

        def fail_once(**kwargs):
            monkeypatch.setattr(first_delivery, "review_first_delivery", original)
            raise OSError("injected interruption before direction Host")

        monkeypatch.setattr(first_delivery, "review_first_delivery", fail_once)
    code, result = _cli(argv)
    if interruption not in {None, "deferred_terminal"}:
        assert result["status"] == "failed", result
        assert result["first_delivery_progress"]["stage"] in {"direction_pending", "operation_unknown"}
        code, result = _cli([*argv[:-1], "--resume-turn-key", result["resume_turn_key"], "--retry-failed-turn", "--execute"])
    if interruption == "continuous_direction_change":
        assert result["status"] == "failed", result
        code, exhausted = _cli([*argv[:-1], "--resume-turn-key", result["resume_turn_key"], "--retry-failed-turn", "--execute"])
        assert exhausted["status"] == "failed", exhausted
        assert "budget exhausted" in json.dumps(exhausted)
        assert (host_project / "host-calls.txt").read_text().splitlines() == ["implementation", "direction", "direction"]
        rows = [json.loads(line) for line in (runtime / "goals/loopx-turn-fixture/runs/index.jsonl").read_text().splitlines()]
        assert not any(row["classification"] == "quota_slot_spent" for row in rows)
        assert not any(row.get("vision_checkpoint", {}).get("read_context") for row in rows)
        return
    assert code == 0 and result["status"] == "committed", json.dumps(result)
    assert result["first_delivery_progress"]["stage"] == "settled"
    calls = ["implementation", "direction"] + (["direction"] if interruption == "stale_direction" else [])
    assert (host_project / "host-calls.txt").read_text().splitlines() == calls
    journal = _turn_journal(runtime)
    assert len(journal["direction_reviews"]) == len(calls) - 1
    if interruption != "deferred_terminal":
        assert journal["delivery_completion"]["ok"]
    assert journal["direction_reviews"][-1]["response"]["read_context_id"] == journal["direction_reviews"][-1]["read_context_id"]
    code, replay = _cli([*argv[:-1], "--resume-turn-key", result["resume_turn_key"], "--execute"])
    assert code == 0 and replay["replayed"], replay
    assert (host_project / "host-calls.txt").read_text().splitlines() == calls
    rows = [json.loads(line) for line in (runtime / "goals/loopx-turn-fixture/runs/index.jsonl").read_text().splitlines()]
    assert sum(row["classification"] == "quota_slot_spent" for row in rows) == 1
    assert sum(bool(row.get("vision_checkpoint", {}).get("read_context")) for row in rows) == 1
