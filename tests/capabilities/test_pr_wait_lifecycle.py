"""Dependency facts cross executing entrypoints without borrowing business authority."""
from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from loopx.capabilities.issue_fix import pr_lifecycle, pr_wait_reconcile
from loopx.control_plane.agents.capability_memory import agent_capability_memory
from loopx.capabilities.turn_start import extend_turn_start_dispatch
from loopx.heartbeat_prequota import run_heartbeat_pre_quota
from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path
from loopx.todos import add_goal_todo, list_goal_todos
from tests.capabilities.test_pr_wait_observation import _invoke_turn, _native_wait_turn, _observation
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.test_loopx_turn_driver import _write_live_fixture


def _declare_observation(registry):
    config = json.loads(registry.read_text())
    config["goals"][0]["available_capabilities"] = ["external_evidence_poll"]
    registry.write_text(json.dumps(config))


def _guard_args(argv, identity):
    return [*argv[:argv.index("turn")], "quota", "should-run", "--goal-id", "goal-a",
            "--agent-id", "agent-a", "--codex-app", "--turn-instance-id", identity]


def _without_invocation_capability(argv):
    result = list(argv)
    i = result.index("--available-capability")
    del result[i:i + 2]
    return result


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_shared_cadence_across_quota_native_and_compatibility_prequota(tmp_path, monkeypatch, provider):
    registry, runtime, project, todo_id, argv = _native_wait_turn(tmp_path, monkeypatch, provider)
    _declare_observation(registry)
    original = list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]
    clock = ["2026-10-01T01:00:00Z"]
    monkeypatch.setattr(pr_wait_reconcile, "now_utc_iso", lambda: clock[0])
    calls = []

    def fetch(reference, **kwargs):
        calls.append(reference)
        return {"url": "https://github.com/owner/repo/pull/42",
                "state": "OPEN" if len(calls) == 1 else "MERGED",
                "mergedAt": None if len(calls) == 1 else "2026-10-01T01:12:00Z"}

    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", fetch)
    _, guard = _invoke_turn(_guard_args(argv, "configured-guard"))
    assert _observation(guard)["external_read_count"] == 1
    assert list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]["resume_ready"] is False
    _, native = _invoke_turn([*_without_invocation_capability(argv), "--turn-instance-id", "inherited-native"])
    assert _observation(native)["external_read_count"] == 0
    compat = run_heartbeat_pre_quota(registry_path=registry, runtime_root_arg=str(runtime),
        goal_id="goal-a", agent_id="agent-a")
    assert compat["checks"]["pr_dependencies"]["external_read_count"] == 0
    clock[0] = "2026-10-01T01:29:59Z"
    _invoke_turn(_guard_args(argv, "not-yet-due"))
    assert len(calls) == 1
    clock[0] = "2026-10-01T01:30:00Z"
    _, resumed = _invoke_turn(_guard_args(argv, "due-guard"))
    assert _observation(resumed)["merged_count"] == 1
    current = list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]
    assert current["resume_ready"] is True
    assert current["resume_condition"]["matched_event_at"] == "2026-10-01T01:12:00Z"
    for field in ("text", "status", "claimed_by", "resume_when", "required_write_scopes"):
        assert current.get(field) == original.get(field)
    events = load_rollout_events(rollout_event_log_path(runtime, "goal-a"))
    assert len(calls) == 2 and sum(e["event_kind"] == "pr_merge" for e in events) == 1
    assert not any(e["event_kind"] in {"quota_spend", "todo_complete"} for e in events)
    assert not (project / "host-started").exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_host_observation_inheritance_revocation_and_existing_fact_retention(tmp_path, monkeypatch, provider):
    registry, runtime, _, todo_id, argv = _native_wait_turn(tmp_path, monkeypatch, provider)
    calls = []

    def fetch(reference, **kwargs):
        calls.append(reference)
        return {"url": f"https://github.com/{reference['repo']}/pull/{reference['number']}",
                "state": "MERGED", "mergedAt": "2026-10-01T00:00:00Z"}

    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", fetch)
    _, off = _invoke_turn(_guard_args(argv, "unconfigured"))
    assert _observation(off) is None and calls == []
    agent_capability_memory(registry_path=registry, runtime_root=runtime, goal_id="goal-a",
        agent_id="agent-a", available=["external_evidence_poll"], execute=True)
    _, inherited = _invoke_turn(_guard_args(argv, "host-inheritance"))
    assert _observation(inherited)["merged_count"] == 1
    next_id = add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
        text="Observe the next dependency", claimed_by="agent-a", resume_when="pr_merged:owner/repo#43")["todo_id"]
    agent_capability_memory(registry_path=registry, runtime_root=runtime, goal_id="goal-a",
        agent_id="agent-a", unavailable=["external_evidence_poll"], execute=True)
    _declare_observation(registry)  # A remembered revocation also defeats inheritance.
    _, revoked = _invoke_turn(_guard_args(argv, "revoked"))
    assert _observation(revoked) is None and len(calls) == 1
    assert list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=next_id)["todo"]["resume_ready"] is False
    assert list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]["resume_ready"] is True


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_readonly_consumers_do_not_turn_permission_into_observation(tmp_path, monkeypatch, provider):
    registry, runtime, _, todo_id, argv = _native_wait_turn(tmp_path, monkeypatch, provider)
    _declare_observation(registry)
    calls = []
    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", lambda *args, **kw: calls.append(args))
    prefix = argv[:argv.index("turn")]
    _invoke_turn([*prefix, "todo", "list", "--goal-id", "goal-a", "--todo-id", todo_id])
    dry = list(argv)
    dry.remove("--execute")
    _invoke_turn(dry)
    plan = [*prefix, "turn", "plan", "--goal-id", "goal-a", "--agent-id", "agent-a",
            "--host", "generic-cli", ]
    _invoke_turn(plan)
    for mode in ("--full", "--thin"):
        _invoke_turn([*prefix, "heartbeat-prompt", "--goal-id", "goal-a", "--agent-id", "agent-a",
                      "--codex-app", mode])
    assert calls == []
    assert list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]["resume_ready"] is False
    assert not any(e["event_kind"] == "pr_merge" for e in load_rollout_events(rollout_event_log_path(runtime, "goal-a")))


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_concurrent_registered_callers_share_one_goal_observation(tmp_path, monkeypatch, provider):
    registry, runtime, _, _, _ = _native_wait_turn(tmp_path, monkeypatch, provider)
    _declare_observation(registry)
    entered, finish = threading.Event(), threading.Event()
    calls = []

    def fetch(reference, **kwargs):
        calls.append(reference)
        entered.set()
        assert finish.wait(10)
        return {"url": "https://github.com/owner/repo/pull/42", "state": "OPEN"}

    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", fetch)
    args = dict(registry_path=registry, runtime_root=runtime, goal_id="goal-a", agent_id="agent-a", available=None)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(extend_turn_start_dispatch, {}, **args)
        assert entered.wait(10)
        try:
            second = extend_turn_start_dispatch({}, **args)
            observation = next(r["readback"] for r in second["results"] if r["hook_id"] == "issue_fix.pr_wait_observation")
            assert observation["observation_in_progress"] is True
            assert observation["external_read_count"] == 0 and observation["state_written"] is False
        finally:
            finish.set()
        completed = first.result(timeout=10)
    assert len(calls) == 1
    assert next(r["readback"] for r in completed["results"] if r["hook_id"] == "issue_fix.pr_wait_observation")["external_read_count"] == 1
    third = extend_turn_start_dispatch({}, **args)
    assert next(r["readback"] for r in third["results"] if r["hook_id"] == "issue_fix.pr_wait_observation")["external_read_count"] == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_observer_failure_does_not_gate_independent_validated_work(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    _declare_observation(registry)
    waiting = add_goal_todo(registry_path=registry, goal_id="loopx-turn-fixture", role="agent",
        text="Wait for a separate dependency", claimed_by="codex-fixture", resume_when="pr_merged:owner/repo#42")["todo_id"]
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(args)
        raise TimeoutError("metadata request unavailable")

    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", unavailable)
    runner = tmp_path / "protocol_host.py"
    runner.write_text(
        "import json\nfrom pathlib import Path\n"
        "def run_dsh_turn(**kwargs):\n"
        "    root = Path(kwargs['workspace'])\n"
        "    (root / 'validated-artifact').write_text('independent work accepted')\n"
        "    return json.dumps({'result_kind':'validated_progress','classification':'independent_work',"
        "'work_evidence':['validated-artifact'],'validation_evidence':['Independent command checks exact artifact'],"
        "'summary':'Delivered independent fixture work.','delivery_batch_scale':'implementation',"
        "'delivery_outcome':'outcome_progress','delivery_boundary':'in_flight_continuation',"
        "'recommended_action':'Continue the accepted work.'})\n"
    )
    code, result = _invoke_turn(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "turn", "run-once", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
        "--host", "dsh", "--dsh-runner", str(runner), "--project", str(project),
        "--todo-id", "todo_fixture0001", "--turn-instance-id", "independent-work",
        "--validation-command-json", json.dumps([sys.executable, "-c", "from pathlib import Path; "
            "assert Path('validated-artifact').read_text() == 'independent work accepted'"]),
        "--no-global-sync", "--execute"])
    assert code == 0 and result["status"] == "committed", result
    assert len(calls) == 1 and _observation(result)["degraded"] is True
    assert list_goal_todos(registry_path=registry, goal_id="loopx-turn-fixture", todo_id=waiting)["todo"]["resume_ready"] is False
    events = load_rollout_events(rollout_event_log_path(runtime, "loopx-turn-fixture"))
    assert sum(e["event_kind"] == "quota_spend" for e in events) == 1
    assert not any(e["event_kind"] in {"pr_merge", "todo_complete"} for e in events)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_app_lark_context_backend_reads_shared_facts_without_polling(tmp_path, monkeypatch, provider):
    from loopx.capabilities.manager_context.inspection import ManagerInspection

    registry, runtime, _, todo_id, argv = _native_wait_turn(tmp_path, monkeypatch, provider)
    _declare_observation(registry)
    calls = []

    def merged(*args, **kwargs):
        calls.append(args)
        return {"url": "https://github.com/owner/repo/pull/42", "state": "MERGED",
                "mergedAt": "2026-10-01T01:12:00Z"}

    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", merged)
    inspectors = [ManagerInspection(context={"scope": "owner_goal", "goals": [{"goal_id": "goal-a"}]},
        registry_path=registry, runtime_root=runtime, owner_scope=True, scope_valid=lambda: True,
        record=lambda result: None, channel_id=channel) for channel in (None, "private-lark-fixture")]

    def read(inspector):
        result = inspector.read("loopx_context_read", {"view": "todos", "goal_id": "goal-a", "todo_id": todo_id})
        assert result["ok"] is True and result["included"] == 1, result
        return result["rows"][0]

    assert all(read(inspector)["resume_ready"] is False for inspector in inspectors)
    assert calls == []
    _invoke_turn(_guard_args(argv, "observe-before-context"))
    events = load_rollout_events(rollout_event_log_path(runtime, "goal-a"))
    for inspector in inspectors:
        row = read(inspector)
        assert row["resume_ready"] is True and row["resume_when"] == "pr_merged:owner/repo#42"
    assert len(calls) == 1
    assert load_rollout_events(rollout_event_log_path(runtime, "goal-a")) == events


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_lost_observation_response_refreshes_cached_readiness(tmp_path, monkeypatch, provider):
    from loopx.capabilities.issue_fix import pr_wait_hook

    registry, runtime, _, todo_id, argv = _native_wait_turn(tmp_path, monkeypatch, provider)
    # Capture the old pending projection before permission is configured.
    _invoke_turn([*_guard_args(argv, "capture-pending"), "--write-projection-cache"])
    _declare_observation(registry)
    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", lambda *a, **kw: {
        "url": "https://github.com/owner/repo/pull/42", "state": "MERGED", "mergedAt": "2026-10-01T01:12:00Z"})
    original = pr_wait_hook.reconcile_pr_wait_dependencies

    def response_lost(**kwargs):
        original(**kwargs)
        raise OSError("response lost after local commit")

    monkeypatch.setattr(pr_wait_hook, "reconcile_pr_wait_dependencies", response_lost)
    _, result = _invoke_turn([*_guard_args(argv, "lost-response"), "--use-projection-cache"])
    readback = _observation(result)
    assert readback["external_read_count"] is None and readback["state_written"] is None
    todo = list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]
    assert todo["resume_ready"] is True
    assert todo["resume_condition"]["matched_event_at"] == "2026-10-01T01:12:00Z"
    assert result["turn_start_capability_hook_dispatch"]["results"][0]["external_writes_performed"] is False


def test_unavailable_permission_source_never_contacts_provider(tmp_path, monkeypatch):
    from loopx.capabilities.issue_fix import pr_wait_hook

    registry, runtime, _, _, _ = _native_wait_turn(tmp_path, monkeypatch, "file")
    _declare_observation(registry)
    monkeypatch.setattr(pr_wait_hook, "_availability", lambda *a: (_ for _ in ()).throw(OSError("source unavailable")))
    calls = []
    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", lambda *a, **kw: calls.append(a))
    dispatch = extend_turn_start_dispatch({}, registry_path=registry, runtime_root=runtime,
        goal_id="goal-a", agent_id="agent-a", available=None)
    assert calls == [] and dispatch.get("failures", []) == []
    row = next(r for r in dispatch["results"] if r["hook_id"] == "issue_fix.pr_wait_observation")
    assert row["status"] == "unavailable" and row["local_private_state_mutated"] is False
    assert row["readback"]["external_read_count"] == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_full_source_selection_is_independent_of_bounded_hook_diagnostics(tmp_path, monkeypatch, provider):
    from loopx.capabilities.issue_fix import pr_wait_hook

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    lines = tuple(line for number in range(1, 501) for line in (
        f"- [ ] [P2] Continue dependency {number}.",
        f"  <!-- loopx:todo todo_id=todo_dependency{number:04} status=open task_class=advancement_task "
        f"action_kind=fixture claimed_by=codex-fixture priority=P2 resume_when=pr_merged:owner/repo#{number} -->"))
    _, runtime, registry = _write_live_fixture(tmp_path, provider=provider, extra_agent_todo_lines=lines)
    _declare_observation(registry)
    calls, full_results = [], []
    original = pr_wait_hook.reconcile_pr_wait_dependencies

    def capture(**kwargs):
        result = original(**kwargs)
        full_results.append(result)
        return result

    def fetch(reference, **kwargs):
        calls.append(reference)
        return {"url": f"https://github.com/{reference['repo']}/pull/{reference['number']}", "state": "OPEN"}

    monkeypatch.setattr(pr_wait_hook, "reconcile_pr_wait_dependencies", capture)
    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", fetch)
    dispatch = extend_turn_start_dispatch({}, registry_path=registry, runtime_root=runtime,
        goal_id="loopx-turn-fixture", agent_id="codex-fixture", available=None)
    assert dispatch.get("failures", []) == []
    result = next(r for r in dispatch["results"] if r["hook_id"] == "issue_fix.pr_wait_observation")
    assert len(calls) == 4 and full_results[0]["plan"]["due_count"] == 500
    assert len(json.dumps(full_results[0]).encode()) > 64 * 1024
    assert len(json.dumps(result).encode()) < 64 * 1024
    assert result["readback"]["plan"]["diagnostic_coverage"]["waiting_targets"] == {
        "total": 500, "included": 16, "omitted": 484}
    # The next batch comes from complete canonical state, never the diagnostic prefix.
    extend_turn_start_dispatch({}, registry_path=registry, runtime_root=runtime,
        goal_id="loopx-turn-fixture", agent_id="codex-fixture", available=None)
    assert len(calls) == 8 and len({r["number"] for r in calls}) == 8
