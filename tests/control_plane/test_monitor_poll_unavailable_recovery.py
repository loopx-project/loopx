"""An interrupted observation retains its owner before competing closeout."""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.testing.canary_harness import read_run_index
from loopx.todos import list_goal_todos
from test_monitor_poll_transaction_recovery import _fixture, _cli, _poll_events, _quota_receipt, GOAL, AGENT, REPO

_ACTOR_POLL_WRAPPER = """
import os, runpy, sys
import loopx.control_plane.quota.monitor_poll as monitor
original = monitor._native_result
def actor_request(request):
    request = {**request, 'observation': {**request['observation'],
        'actor_agent_id': os.environ['SYNTHETIC_MONITOR_ACTOR']}}
    if os.environ['SYNTHETIC_QUOTA_FAULT'] == '1' and request.get('phase') == 'commit' and request.get('provider_receipt'):
        os._exit(73)
    return original(request)
monitor._native_result = actor_request
sys.argv = ['loopx.cli', *sys.argv[1:]]
runpy.run_module('loopx.cli', run_name='__main__')
"""


def _poll_cli(registry: Path, runtime: Path, poll: tuple[str, ...], actor: str | None,
              *, fault: bool = False) -> tuple[int, dict | None]:
    if actor is None:
        return _cli(registry, runtime, *poll, fault='quota_commit' if fault else None)
    result = subprocess.run([sys.executable, '-c', _ACTOR_POLL_WRAPPER, '--registry', str(registry),
        '--format', 'json', '--runtime-root', str(runtime), *poll], cwd=REPO,
        env={**os.environ, 'LOOPX_USAGE_PING': '0', 'SYNTHETIC_MONITOR_ACTOR': actor,
             'SYNTHETIC_QUOTA_FAULT': '1' if fault else '0'},
        capture_output=True, text=True, check=False, timeout=60)
    return result.returncode, json.loads(result.stdout) if result.stdout.strip() else None

@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize('material', [False, True])
@pytest.mark.parametrize('actor', [None, 'other-synthetic-observer'])
def test_pending_poll_cannot_be_closed_as_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, material: bool, actor: str | None,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state, monitor, poll = _fixture(tmp_path)
    todos = list_goal_todos(registry_path=registry, goal_id=GOAL)['todos']
    if actor is not None:
        data = json.loads(registry.read_text())
        data['goals'][0]['coordination']['registered_agents'].append(actor)
        registry.write_text(json.dumps(data))
        for todo in todos:
            if todo['todo_id'] == monitor:
                todo['claimed_by'] = None
    initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(
        goal_id=GOAL, todos=todos, handoff_mode='soft_claim'), state_path=state, provider=provider)
    if not material:
        args = list(poll)
        args.remove('--material-change')
        for flag in ('--next-agent-todo', '--next-action-kind', '--next-user-todo', '--next-user-task-class'):
            index = args.index(flag)
            del args[index:index + 2]
        poll = tuple(args)
    rc, result = _poll_cli(registry, runtime, poll, actor, fault=True)
    assert (rc, result) == (73, None)
    committed = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)
    pending_path = _quota_receipt(runtime)
    pending_bytes = pending_path.read_bytes()
    pending = json.loads(pending_bytes)
    assert pending['status'] == 'provider_pending'
    assert pending['admitted_decision']['agent_id'] == AGENT
    assert pending['provider_plan']['agent_id'] == (actor or AGENT)
    assert next(todo for todo in committed['todos'] if todo['todo_id'] == monitor)['last_actor_agent_id'] == (actor or AGENT)
    refresh_rc, refresh = _cli(registry, runtime, 'refresh-state', '--goal-id', GOAL,
        '--agent-id', AGENT, '--todo-id', monitor, '--turn-instance-id', 'synthetic-monitor-turn-1',
        '--classification', 'synthetic_unavailable', '--delivery-batch-scale', 'single_surface',
        '--delivery-outcome', 'outcome_gap', '--progress-result-class', 'blocked',
        '--progress-blocker-id', 'blocker:synthetic-source', '--progress-evidence-id', 'evidence:synthetic-source',
        '--vision-unchanged-reason', 'Source unavailable; retain the Monitor.',
        '--no-global-sync', '--suppress-external-sinks')
    assert refresh_rc != 0 and refresh['appended'] is False, refresh
    assert pending_path.read_bytes() == pending_bytes
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == committed
    assert len(_poll_events(runtime)) == 0
    assert not any(row['classification'] == 'synthetic_unavailable' for row in read_run_index(runtime, GOAL))
    retry_rc, retry = _poll_cli(registry, runtime, poll, actor)
    assert retry_rc == 0, retry
    assert len(_poll_events(runtime)) == 1
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == committed
    index_after = read_run_index(runtime, GOAL)
    replay_rc, replay = _poll_cli(registry, runtime, poll, actor)
    assert replay_rc == 0 and replay['replayed'] is True, replay
    assert read_run_index(runtime, GOAL) == index_after
    assert not any(row['classification'] == 'quota_slot_spent' for row in index_after)
