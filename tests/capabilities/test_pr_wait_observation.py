"""A closed queue entry must still resume its exact canonical PR dependency."""
from __future__ import annotations

import contextlib
import io
import json
import sys

import pytest
from subprocess import TimeoutExpired

from loopx.heartbeat_prequota import run_heartbeat_pre_quota
from loopx.todos import add_goal_todo, list_goal_todos
from tests.control_plane.canonical_authority_fixture import (
    isolate_sqlite_runtime, promoted_create_fixture,
)


def _observation(result):
    return next((row.get("readback") for row in result.get("turn_start_capability_hook_dispatch", {}).get("results", [])
                 if row.get("hook_id") == "issue_fix.pr_wait_observation"), None)


def _native_wait_turn(tmp_path, monkeypatch, provider, *, claimed_by="agent-a"):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = promoted_create_fixture(
        tmp_path, provider=provider,
        registered_agents=["agent-a", claimed_by] if claimed_by != "agent-a" else None,
    )
    project = tmp_path / "project"
    data = json.loads(registry.read_text())
    data["goals"][0]["quota"] = {"compute": 0}
    registry.write_text(json.dumps(data))
    todo_id = add_goal_todo(
        registry_path=registry, goal_id="goal-a", role="agent",
        text="Continue after the exact dependency merges", claimed_by=claimed_by,
        resume_when="pr_merged:owner/repo#42",
    )["todo_id"]
    argv = [
        "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "turn", "run-once", "--goal-id", "goal-a", "--agent-id", "agent-a",
        "--host", "generic-cli", "--project", str(project), "--scan-root", str(project),
        "--host-adapter-command-json", json.dumps([
            sys.executable, "-c", "from pathlib import Path; Path('host-started').touch(); "
            "raise AssertionError('business Host must remain paused')",
        ]), "--available-capability", "external_evidence_poll", "--no-global-sync", "--execute",
    ]
    return registry, runtime, project, todo_id, argv


def _invoke_turn(argv):
    from loopx.cli import main

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(argv)
    return code, json.loads(output.getvalue())


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_executing_turn_observes_dependency_before_business_quota(
    tmp_path, monkeypatch, provider,
):
    from loopx.capabilities.issue_fix import pr_lifecycle
    from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path

    registry, runtime, project, todo_id, argv = _native_wait_turn(tmp_path, monkeypatch, provider)
    before = list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]
    calls = []

    def fetch(reference, **kwargs):
        calls.append(reference)
        return {"state": "MERGED", "mergedAt": "2026-10-01T00:00:00Z",
                "url": "https://github.com/owner/repo/pull/42"}

    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", fetch)
    code, result = _invoke_turn([*argv, "--turn-instance-id", "observe-before-business-1"])
    assert code == 1, result
    assert result["error"] == "LoopX Turn route is not host executable", result
    after = list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]
    assert after["resume_ready"] is True, result
    assert _observation(result)["merged_count"] == 1
    assert _observation(result)["state_written"] is True
    assert not (project / "host-started").exists()
    assert len(calls) == 1
    for key in ("text", "status", "claimed_by", "resume_when", "required_write_scopes"):
        assert after.get(key) == before.get(key)
    events = load_rollout_events(rollout_event_log_path(runtime, "goal-a"))
    assert sum(event["event_kind"] == "pr_merge" for event in events) == 1
    assert not any(event["event_kind"] in {"quota_spend", "todo_complete"} for event in events)

    code, replay = _invoke_turn([*argv, "--turn-instance-id", "observe-before-business-2"])
    assert code == 1, replay
    assert replay["error"] == "LoopX Turn route is not host executable", replay
    assert len(calls) == 1
    assert _observation(replay)["external_read_count"] == 0
    assert _observation(replay)["state_written"] is False
    assert not (project / "host-started").exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_turn_observes_dependencies_synchronized_during_turn_start(tmp_path, monkeypatch, provider):
    from loopx.capabilities.issue_fix import pr_lifecycle
    from loopx.cli_commands import lark_inbox

    registry, _, project, _, argv = _native_wait_turn(tmp_path, monkeypatch, provider)
    synchronized = []

    def sync_inbox(**kwargs):
        synchronized.append(add_goal_todo(
            registry_path=registry, goal_id="goal-a", role="agent", claimed_by="agent-a",
            text="Continue the synchronized dependency", resume_when="pr_merged:owner/repo#43",
        )["todo_id"])
        return {}

    calls = []

    def fetch(reference, **kwargs):
        calls.append(reference["number"])
        return {"state": "MERGED", "mergedAt": "2026-10-01T00:00:00Z",
                "url": f"https://github.com/owner/repo/pull/{reference['number']}"}

    monkeypatch.setattr(lark_inbox, "dispatch_goal_lark_turn_start_hooks", sync_inbox)
    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", fetch)
    _, result = _invoke_turn(argv)
    assert set(calls) == {42, 43}, result
    assert list_goal_todos(
        registry_path=registry, goal_id="goal-a", todo_id=synchronized[0],
    )["todo"]["resume_ready"] is True
    assert not (project / "host-started").exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mode", ["absent-capability", "dry-run", "plan"])
def test_read_only_and_unconfigured_turns_do_not_observe(tmp_path, monkeypatch, provider, mode):
    from loopx.capabilities.issue_fix import pr_lifecycle
    from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path

    registry, runtime, project, todo_id, argv = _native_wait_turn(tmp_path, monkeypatch, provider)
    calls = []

    def fetch(*args, **kwargs):
        calls.append(args)
        raise AssertionError("this invocation must not perform an external observation")

    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", fetch)
    if mode == "absent-capability":
        index = argv.index("--available-capability")
        del argv[index:index + 2]
    elif mode == "dry-run":
        argv.remove("--execute")
    else:
        argv = [*argv[:argv.index("turn")], "turn", "plan", "--goal-id", "goal-a",
                "--agent-id", "agent-a", "--host", "generic-cli", "--scan-root", str(project),
                "--available-capability", "external_evidence_poll"]
    _, result = _invoke_turn(argv)
    assert _observation(result) is None
    assert calls == []
    assert list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]["resume_ready"] is False
    assert not (project / "host-started").exists()
    events = load_rollout_events(rollout_event_log_path(runtime, "goal-a"))
    assert not any(event["event_kind"] in {"pr_merge", "quota_spend", "todo_complete"}
                   or event.get("classification") == "pr_wait_observation" for event in events)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("outcome", ["open", "closed", "timeout", "unauthenticated", "wrong-repo", "missing-timestamp"])
def test_turn_observation_failure_and_retry_are_readable_without_business_effects(
    tmp_path, monkeypatch, provider, outcome,
):
    from loopx.capabilities.issue_fix import pr_lifecycle
    from loopx.cli_commands.turn_rendering import render_loopx_turn_execution_markdown
    from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path

    registry, runtime, project, todo_id, argv = _native_wait_turn(tmp_path, monkeypatch, provider)
    calls = []

    def fetch(*args, **kwargs):
        calls.append(args)
        if outcome == "timeout":
            raise TimeoutExpired("gh", 10)
        if outcome == "unauthenticated":
            raise RuntimeError("metadata provider unavailable")
        return {"url": "https://github.com/other/repo/pull/42" if outcome == "wrong-repo"
                else "https://github.com/owner/repo/pull/42",
                "state": "OPEN" if outcome == "open" else "CLOSED" if outcome == "closed" else "MERGED",
                "mergedAt": "2026-10-01T00:00:00Z" if outcome == "wrong-repo" else None}

    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", fetch)
    failed = outcome not in {"open", "closed"}
    for number in range(2):
        _, result = _invoke_turn([*argv, "--turn-instance-id", f"pending-observation-{number}"])
        observation = _observation(result)
        assert observation["degraded"] is failed
        assert observation["external_read_count"] == (1 if number == 0 else 0)
        assert observation["unavailable_dependency_count"] == (1 if failed else 0)
        assert observation["state_written"] is (number == 0)
        assert f"PR dependency observation degraded: {failed}" in render_loopx_turn_execution_markdown(result)
        assert len(calls) == 1
    after = list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]
    assert after["resume_ready"] is False
    assert after["resume_when"] == "pr_merged:owner/repo#42"
    assert after["status"] == "open" and after["claimed_by"] == "agent-a"
    assert not (project / "host-started").exists()
    events = load_rollout_events(rollout_event_log_path(runtime, "goal-a"))
    assert not any(event["event_kind"] in {"pr_merge", "quota_spend", "todo_complete"} for event in events)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_partial_observation_write_remains_unknown_and_does_not_run_business(
    tmp_path, monkeypatch, provider,
):
    from loopx.capabilities.issue_fix import pr_lifecycle, pr_wait_reconcile

    registry, _, project, todo_id, argv = _native_wait_turn(tmp_path, monkeypatch, provider)
    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", lambda *args, **kwargs: {
        "url": "https://github.com/owner/repo/pull/42", "state": "MERGED", "mergedAt": "2026-10-01T00:00:00Z",
    })

    def lose_observation_receipt(*args, **kwargs):
        raise OSError("local observation receipt unavailable")

    monkeypatch.setattr(pr_wait_reconcile, "append_rollout_event", lose_observation_receipt)
    _, result = _invoke_turn(argv)
    observation = _observation(result)
    assert observation["ok"] is False and observation["degraded"] is True
    assert observation["external_read_count"] is None
    assert observation["state_written"] is None
    assert observation["error_category"] == "OSError"
    assert list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]["resume_ready"] is True
    assert not (project / "host-started").exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_fresh_turns_reconcile_a_later_merge_on_the_retained_cadence(tmp_path, monkeypatch, provider):
    from loopx.capabilities.issue_fix import pr_lifecycle, pr_wait_reconcile
    from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path

    registry, runtime, project, todo_id, argv = _native_wait_turn(tmp_path, monkeypatch, provider)
    clock = ["2026-10-01T01:00:00Z"]
    monkeypatch.setattr(pr_wait_reconcile, "now_utc_iso", lambda: clock[0])
    calls = []

    def fetch(*args, **kwargs):
        calls.append(args)
        return {"url": "https://github.com/owner/repo/pull/42",
                "state": "OPEN" if len(calls) == 1 else "MERGED",
                "mergedAt": "2026-10-01T01:10:00Z" if len(calls) > 1 else None}

    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", fetch)
    for number, (at, count, ready) in enumerate([
        ("2026-10-01T01:00:00Z", 1, False), ("2026-10-01T01:29:59Z", 1, False),
        ("2026-10-01T01:30:00Z", 2, True), ("2026-10-01T02:30:00Z", 2, True),
    ]):
        clock[0] = at
        _invoke_turn([*argv, "--turn-instance-id", f"native-cadence-{number}"])
        after = list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]
        assert after["resume_ready"] is ready
        assert len(calls) == count
    events = load_rollout_events(rollout_event_log_path(runtime, "goal-a"))
    merges = [event for event in events if event["event_kind"] == "pr_merge"]
    assert len(merges) == 1 and merges[0]["recorded_at"] == "2026-10-01T01:10:00Z"
    assert not any(event["event_kind"] in {"quota_spend", "todo_complete"} for event in events)
    assert not (project / "host-started").exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_turn_observer_does_not_take_another_agents_dependency(tmp_path, monkeypatch, provider):
    from loopx.capabilities.issue_fix import pr_lifecycle

    registry, _, project, todo_id, argv = _native_wait_turn(
        tmp_path, monkeypatch, provider, claimed_by="agent-b",
    )
    calls = []
    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", lambda *args, **kwargs: calls.append(args))
    _, result = _invoke_turn(argv)
    assert _observation(result)["external_read_count"] == 0
    after = list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]
    assert after["claimed_by"] == "agent-b" and after["resume_ready"] is False
    assert calls == [] and not (project / "host-started").exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_observation_does_not_resume_a_stopped_goals_business(tmp_path, monkeypatch, provider):
    from loopx.capabilities.issue_fix import pr_lifecycle
    from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path

    registry, runtime, project, todo_id, argv = _native_wait_turn(tmp_path, monkeypatch, provider)
    data = json.loads(registry.read_text())
    data["goals"][0]["quota"] = {"compute": 1}
    data["goals"][0]["activation"] = {
        "schema_version": "loopx_goal_activation_v1", "state": "stopped",
        "updated_at": "2026-10-01T00:00:00Z", "reason": "Stopped by owner", "actor_kind": "owner",
    }
    registry.write_text(json.dumps(data))
    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", lambda *args, **kwargs: {
        "url": "https://github.com/owner/repo/pull/42", "state": "MERGED", "mergedAt": "2026-10-01T00:00:00Z",
    })
    _, result = _invoke_turn(argv)
    assert _observation(result)["merged_count"] == 1
    assert list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]["resume_ready"] is True
    assert json.loads(registry.read_text())["goals"][0]["activation"]["state"] == "stopped"
    assert not (project / "host-started").exists()
    events = load_rollout_events(rollout_event_log_path(runtime, "goal-a"))
    assert not any(event["event_kind"] in {"quota_spend", "todo_complete"} for event in events)


def test_original_journal_recovery_does_not_run_fresh_observation(tmp_path, monkeypatch):
    from loopx.capabilities.issue_fix import pr_lifecycle
    from loopx.cli_commands import turn, turn_run_once

    _, _, _, _, argv = _native_wait_turn(tmp_path, monkeypatch, "file")
    calls = []
    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(turn, "load_loopx_turn_plan_from_journal", lambda *args, **kwargs: {
        "turn_envelope": {"agent_id": "agent-a"},
    })
    monkeypatch.setattr(turn_run_once, "execute_turn_run_once", lambda *args, **kwargs: {
        "ok": True, "status": "committed", "effects": {
            "host_invoked": False, "state_written": False, "quota_spent": False,
        },
    })
    # This isolates the existing recovery-entry wiring; real journal replay is
    # covered separately by the native Turn driver suite.
    code, result = _invoke_turn([*argv, "--resume-turn-key", "sha256:" + "a" * 64])
    assert code == 0, result
    assert calls == [] and _observation(result) is None


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_native_turn_adopts_merge_then_replays_original_journal_once(tmp_path, monkeypatch, provider):
    from loopx.capabilities.issue_fix import pr_lifecycle
    from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path
    from tests.test_loopx_turn_driver import _write_live_fixture

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(
        tmp_path, provider=provider, todo_metadata_extra="resume_when=pr_merged:owner/repo#42",
    )
    calls = []

    def fetch(*args, **kwargs):
        calls.append(args)
        return {"url": "https://github.com/owner/repo/pull/42", "state": "MERGED",
                "mergedAt": "2026-10-01T00:00:00Z"}

    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", fetch)
    runner = tmp_path / "protocol_host.py"
    runner.write_text(
        "import json\nfrom pathlib import Path\n"
        "def run_dsh_turn(**kwargs):\n"
        "    root = Path(kwargs['workspace'])\n"
        "    counter = root / 'host-count'\n"
        "    counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))\n"
        "    (root / 'validated-artifact').write_text('continued')\n"
        "    return json.dumps({'result_kind':'validated_progress',"
        "'work_evidence':['validated-artifact'],"
        "'validation_evidence':['Independent command verifies continued artifact'],"
        "'classification':'dependency_followthrough','summary':'Validated the resumed fixture work.',"
        "'delivery_batch_scale':'implementation','delivery_outcome':'outcome_progress',"
        "'delivery_boundary':'in_flight_continuation','recommended_action':'Continue the original task.'})\n"
    )
    argv = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "turn", "run-once", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
            "--host", "dsh", "--dsh-runner", str(runner), "--project", str(project),
            "--scan-root", str(project), "--available-capability", "external_evidence_poll",
            "--validation-command-json", json.dumps([sys.executable, "-c",
                "from pathlib import Path; assert Path('validated-artifact').read_text() == 'continued'"]),
            "--no-global-sync", "--execute"]
    before = list_goal_todos(registry_path=registry, goal_id="loopx-turn-fixture", todo_id="todo_fixture0001")["todo"]
    assert before["resume_ready"] is False
    code, result = _invoke_turn([*argv, "--turn-instance-id", "adopt-observed-merge"])
    assert code == 0 and result["status"] == "committed", result
    assert (project / "host-count").read_text() == "1"
    observation = _observation(result)
    assert observation["merged_count"] == 1 and observation["duration_ms"] >= 0
    after = list_goal_todos(registry_path=registry, goal_id="loopx-turn-fixture", todo_id="todo_fixture0001")["todo"]
    assert after["resume_ready"] is True
    for key in ("text", "status", "claimed_by", "resume_when"):
        assert after[key] == before[key]

    code, replay = _invoke_turn([*argv, "--resume-turn-key", result["resume_turn_key"]])
    assert code == 0 and replay["status"] == "committed", replay
    assert _observation(replay) is None
    assert len(calls) == 1 and (project / "host-count").read_text() == "1"
    events = load_rollout_events(rollout_event_log_path(runtime, "loopx-turn-fixture"))
    assert sum(event["event_kind"] == "pr_merge" for event in events) == 1
    assert sum(event["event_kind"] == "quota_spend" for event in events) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_prequota_observes_merged_dependency_without_open_queue_membership(tmp_path, monkeypatch, provider):
    from loopx.capabilities.issue_fix import pr_lifecycle

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = promoted_create_fixture(tmp_path, provider=provider)
    config = json.loads(registry.read_text())
    config["goals"][0]["available_capabilities"] = ["external_evidence_poll"]
    registry.write_text(json.dumps(config))
    created = add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
        text="Continue after the dependency merges", claimed_by="agent-a",
        resume_when="pr_merged:owner/repo#42")
    todo_id = created["todo_id"]
    calls = []

    def fetch(reference, **kwargs):
        calls.append(reference)
        return {"state": "MERGED", "mergedAt": "2026-10-01T00:00:00Z",
                "url": "https://github.com/owner/repo/pull/42"}

    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", fetch)
    before = list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]
    assert before["resume_ready"] is False
    result = run_heartbeat_pre_quota(registry_path=registry, runtime_root_arg=str(runtime),
        goal_id="goal-a", agent_id="agent-a")
    assert len(calls) == 1
    assert result["checks"]["pr_dependencies"]["merged_count"] == 1
    after = list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]
    assert after["resume_ready"] is True
    for field in ("resume_when", "status", "claimed_by", "text"):
        assert after[field] == before[field]
    run_heartbeat_pre_quota(registry_path=registry, runtime_root_arg=str(runtime),
        goal_id="goal-a", agent_id="agent-a")
    assert len(calls) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("outcome", ["open", "closed", "timeout", "unauthenticated", "wrong_repo", "malformed"])
def test_unsatisfied_and_failed_observations_survive_restart_with_bounded_retry(tmp_path, monkeypatch, provider, outcome):
    from loopx.capabilities.issue_fix import pr_lifecycle, pr_wait_reconcile
    from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = promoted_create_fixture(tmp_path, provider=provider)
    config = json.loads(registry.read_text())
    config["goals"][0]["available_capabilities"] = ["external_evidence_poll"]
    registry.write_text(json.dumps(config))
    todo_id = add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
        text="Pending dependency", claimed_by="agent-a", resume_when="pr_merged:owner/repo#42")["todo_id"]
    clock = ["2026-10-01T01:00:00Z"]
    monkeypatch.setattr(pr_wait_reconcile, "now_utc_iso", lambda: clock[0])
    calls = []

    def fetch(reference, **kwargs):
        calls.append(reference)
        assert kwargs["compact"] is True
        if outcome == "timeout":
            raise TimeoutExpired("gh", 10)
        if outcome == "unauthenticated":
            raise RuntimeError("provider unavailable")
        if outcome == "malformed":
            return {"url": "https://github.com/owner/repo/pull/42"}
        return {"state": "OPEN" if outcome == "open" else "CLOSED" if outcome == "closed" else "MERGED",
                "mergedAt": "2026-10-01T00:00:00Z" if outcome == "wrong_repo" else None,
                "url": "https://github.com/other/repo/pull/42" if outcome == "wrong_repo" else "https://github.com/owner/repo/pull/42"}

    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", fetch)
    args = dict(registry_path=registry, runtime_root_arg=str(runtime), goal_id="goal-a", agent_id="agent-a")
    first = run_heartbeat_pre_quota(**args)["checks"]["pr_dependencies"]
    assert first["external_read_count"] == 1
    assert first["merged_count"] == 0
    assert first["degraded"] is (outcome not in {"open", "closed"})
    assert first["closed_without_merge_count"] == (1 if outcome == "closed" else 0)
    assert list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]["resume_ready"] is False
    events = load_rollout_events(rollout_event_log_path(runtime, "goal-a"))
    assert not any(event["event_kind"] == "pr_merge" for event in events)
    clock[0] = "2026-10-01T01:29:59Z"
    quiet = run_heartbeat_pre_quota(**args)["checks"]["pr_dependencies"]
    assert quiet["external_read_count"] == 0
    assert quiet["degraded"] is (outcome not in {"open", "closed"})
    assert len(calls) == 1
    clock[0] = "2026-10-01T01:30:00Z"
    assert run_heartbeat_pre_quota(**args)["checks"]["pr_dependencies"]["external_read_count"] == 1
    assert len(calls) == 2
