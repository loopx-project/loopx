"""Real App HTTP and bounded extension calls in the same isolated distribution.

Only the Lark command runner is synthetic; no remote message or Base write is
performed. App import, actions, providers, backup and restart are real.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_chat_cold_source_import as cold
from test_cold_source_import_cli import workspace
from test_todo_consumer_retirement import (
    consumer_distribution, isolated_todo_distribution, retirement_wheel,  # noqa: F401 -- shared fixtures
)


@pytest.fixture
def cold_api(tmp_path, monkeypatch, consumer_distribution):  # noqa: F811
    package, _command = consumer_distribution
    monkeypatch.setenv("LOOPX_COLD_IMPORT_PACKAGE", str(package))

    def isolated_workspace(*args):
        result = list(workspace(*args))
        # The copied receiver is first, with only the selected dependencies;
        # -S prevents an editable finder restoring the removed modules.
        env = result[-1]
        env["PYTHONPATH"] = os.pathsep.join([env["PYTHONPATH"], os.environ["PYTHONPATH"]])
        return tuple(result)

    def require_retired_receiver(env):
        receiver = Path(env["PYTHONPATH"].split(os.pathsep)[0]) / "loopx"
        # One copied distribution, before every server start/restart and
        # auxiliary CLI: neither earlier fixture alone proves this boundary.
        for name in ("todos.py", "bootstrap.py",
            "control_plane/todos/legacy_mutation.py", "control_plane/todos/line_update.py",
            "control_plane/scheduler/legacy_monitor_poll.py",
            "control_plane/coordination/runtime_shadow.py",
            "control_plane/coordination/runtime_shadow_writer_adapter.py",
            "control_plane/coordination/local_authority_shadow_adapter.py",
            "control_plane/coordination/local_authority_shadow_outbox.py"):
            assert not (receiver / name).exists(), name

    def selected_process(command, **kwargs):
        assert command[0] == sys.executable
        require_retired_receiver(kwargs["env"])
        return subprocess.Popen([command[0], "-S", *command[1:]], **kwargs)

    def selected_run(command, **kwargs):
        assert command[0] == sys.executable
        require_retired_receiver(kwargs["env"])
        return subprocess.run([command[0], "-S", *command[1:]], **kwargs)

    # Reuse the existing independent cold-import HTTP oracle and process
    # lifetime, adapting only distribution/interpreter provenance in its harness.
    monkeypatch.setattr(cold, "workspace", isolated_workspace)
    monkeypatch.setattr(cold, "subprocess", SimpleNamespace(
        Popen=selected_process, run=selected_run, DEVNULL=subprocess.DEVNULL))
    yield from cold.cold_api.__wrapped__(
        tmp_path, monkeypatch, SimpleNamespace(param=False), False)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_app_import_actions_and_restart_without_legacy_consumers(cold_api, provider):
    cold.test_cold_http_preview_reload_confirm_original_recovery(cold_api, provider)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_app_rejects_missing_lease_and_provider_without_legacy_consumers(cold_api, provider):
    cold.test_app_enforces_lease_and_missing_provider_without_legacy_fallback(cold_api, provider)


def test_app_rejects_changed_source_and_backup_without_legacy_consumers(cold_api):
    cold.test_cold_http_changed_source_backup_and_untrusted_inputs(cold_api)


def test_lark_and_explore_source_readers_without_historic_facade(
    tmp_path, monkeypatch, consumer_distribution,  # noqa: F811
):
    monkeypatch.setenv("LOOPX_COLD_IMPORT_PACKAGE", str(consumer_distribution[0]))
    _, state, _, _, _, receiver, env = workspace(tmp_path, monkeypatch)
    env["PYTHONPATH"] = os.pathsep.join([str(receiver), os.environ["PYTHONPATH"]])
    original = state.read_bytes()
    registry = tmp_path / "project/.loopx/registry.json"
    script = """
import json,sys
from pathlib import Path
from loopx.extensions.lark.presentation.kanban import LarkKanbanConfig,sync_loopx_todos_to_lark_kanban
from loopx.capabilities.issue_fix.explore_projection import project_issue_fix_explore_graph
registry = Path(sys.argv[1])
def forbidden_runner(*args, **kwargs):
    raise AssertionError('preview must not execute remote commands')
config = LarkKanbanConfig(base_token='base_public_fixture',table_id='table_public_fixture')
result = sync_loopx_todos_to_lark_kanban(config,registry_path=registry,goal_id='cold',
    execute=False,runner=forbidden_runner)
assert result['ok'] and result['todo_count'] == 1, result
assert not result['external_write_performed'] and result['successful_write_count'] == 0
assert result['records'][0]['todo_id'] == 'todo_current'
first = project_issue_fix_explore_graph(registry_path=registry,goal_id='cold',execute=True)
second = project_issue_fix_explore_graph(registry_path=registry,goal_id='cold',execute=True)
assert second['appended_event_count'] == 0
assert first['semantic_digest'] == second['semantic_digest']
print(json.dumps({'todo_count': result['todo_count'], 'replayed': True}))
"""
    result = subprocess.run([sys.executable, "-S", "-c", script, str(registry)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"todo_count": 1, "replayed": True}
    assert state.read_bytes() == original


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_fresh_research_execution_and_historical_replay_use_retained_owners(
    tmp_path, provider, consumer_distribution,  # noqa: F811
):
    from canonical_authority_fixture import initialize_canonical_authority
    from tests.capabilities import test_explore_research_evidence as research
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.todos.list_readback import list_goal_todos

    # Seed through the existing source fixture, then use the selected copied
    # distribution for every CLI decision. No editable finder can restore it.
    registry, runtime, log, observation = research.execution_fixture(tmp_path)
    state = tmp_path / "project/ACTIVE_GOAL_STATE.md"
    todos = list_goal_todos(registry_path=registry, goal_id=research.GOAL, role="agent")["todos"]
    initialize_canonical_authority(runtime, research.GOAL,
        build_todo_runtime_shadow_projection(goal_id=research.GOAL, todos=todos, handoff_mode="soft_claim"),
        state_path=state, provider=provider)
    package, command = consumer_distribution
    packet = tmp_path / "observation.json"
    packet.write_text(json.dumps(observation))
    registry_bytes = registry.read_bytes()

    def cli(*args):
        return subprocess.run([*command, "--registry", str(registry), "--runtime-root", str(runtime),
                               "--format", "json", *args], cwd=tmp_path,
                              capture_output=True, text=True, timeout=60)

    def observe(*actor, expected_error=None):
        before = log.read_bytes()
        source_bytes = state.read_bytes()
        result = cli("explore", "observe", "--goal-id", research.GOAL,
                     "--observation-json", str(packet), *actor)
        assert state.read_bytes() == source_bytes and registry.read_bytes() == registry_bytes
        if expected_error:
            assert result.returncode != 0 and expected_error in result.stdout + result.stderr
            assert log.read_bytes() == before
            return None
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    todo_id = observation["execution_lineage"]["successor_todo_id"]
    observe(expected_error="actor differs")
    observe("--agent-id", "other-agent", expected_error="actor differs")
    deferred = cli("todo", "update", "--goal-id", research.GOAL, "--todo-id", todo_id,
                   "--agent-id", "fixture-agent", "--status", "deferred",
                   "--reason", "await-synthetic-capacity", "--resume-when", "capacity_available:fixture")
    assert deferred.returncode == 0, deferred.stdout + deferred.stderr
    observe("--agent-id", "fixture-agent", expected_error="runnable joint-probe")
    reopened = cli("todo", "update", "--goal-id", research.GOAL, "--todo-id", todo_id,
                   "--agent-id", "fixture-agent", "--status", "open", "--clear-resume-when")
    assert reopened.returncode == 0, reopened.stdout + reopened.stderr
    missing = json.loads(json.dumps(observation))
    missing["execution_lineage"]["successor_todo_id"] = "todo_missing"
    missing["progress"]["work_item_id"] = "todo_missing"
    packet.write_text(json.dumps(missing))
    observe("--agent-id", "fixture-agent", expected_error="runnable joint-probe")
    packet.write_text(json.dumps(observation))
    written = observe("--agent-id", "fixture-agent")
    assert written["written"] and written["observation"]["execution_lineage"] == observation["execution_lineage"]
    summary = cli("explore", "summary", "--goal-id", research.GOAL)
    assert summary.returncode == 0, summary.stdout + summary.stderr
    joint = next(node for node in json.loads(summary.stdout)["nodes"] if node["node_id"] == "joint")
    assert joint["research_observation"]["execution_lineage"] == observation["execution_lineage"]
    research.node(log, "a", status="open")
    changed = json.loads(json.dumps(observation))
    changed["progress"]["coverage_scope_id"] = "fresh-after-input-change"
    packet.write_text(json.dumps(changed))
    observe("--agent-id", "fixture-agent", expected_error="input")
    changed_todo = cli("todo", "update", "--goal-id", research.GOAL, "--todo-id", todo_id,
                       "--agent-id", "fixture-agent", "--action-kind", "inspect")
    assert changed_todo.returncode == 0, changed_todo.stdout + changed_todo.stderr
    packet.write_text(json.dumps(observation))
    before = log.read_bytes()
    replay = observe()
    assert replay["replayed"] and not replay["written"] and log.read_bytes() == before
    assert registry.read_bytes() == registry_bytes
    assert (package / "control_plane/todos/legacy_mutation.py").exists() is False
