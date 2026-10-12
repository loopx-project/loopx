"""Real App HTTP and bounded extension calls in the same isolated distribution.

Only the Lark command runner is synthetic; no remote message or Base write is
performed. App import, actions, providers, backup and restart are real.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
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

    def selected_process(command, **kwargs):
        assert command[0] == sys.executable
        return subprocess.Popen([command[0], "-S", *command[1:]], **kwargs)

    def selected_run(command, **kwargs):
        assert command[0] == sys.executable
        return subprocess.run([command[0], "-S", *command[1:]], **kwargs)

    # Reuse the existing independent cold-import HTTP oracle and process
    # lifetime, adapting only distribution/interpreter provenance in its harness.
    monkeypatch.setattr(cold, "workspace", isolated_workspace)
    monkeypatch.setattr(cold, "subprocess", SimpleNamespace(
        Popen=selected_process, run=selected_run, DEVNULL=subprocess.DEVNULL))
    yield from cold.cold_api.__wrapped__(
        tmp_path, monkeypatch, SimpleNamespace(param=False), True)


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
