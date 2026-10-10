"""Qualify the real worker -> renderer -> guard/selection/settlement boundary."""
import json
import os
import shlex
import sys
from pathlib import Path

import pytest

from benchmark.runtime.codex import Execution
from benchmark.runtime.worker import heartbeat_body
from loopx.heartbeat_prompt import build_heartbeat_prompt
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TURN_ID, ALTERNATIVE_TODO_ID, REPO_ROOT,
    _write_fixture, _configure_selectable_alternative, _run_cli,
    _run_generated_cli, _heartbeat_receipt_count, _spend_run_count,
)


def test_renderer_disabled_parity_and_opt_in_scope(tmp_path):
    args = dict(goal_id=GOAL_ID, agent_id=AGENT_ID, runtime_profile="generic_cli",
                turn_instance_id=TURN_ID, thin=True)
    assert build_heartbeat_prompt(**args) == build_heartbeat_prompt(**args, decision_output_root=None)
    payload = build_heartbeat_prompt(**args, decision_output_root=tmp_path)
    assert payload['interface_budget']['within_budget'], payload['interface_budget']
    assert '--turn-envelope --decision-output-root' in payload['quota_guard_command']
    assert 'writeback.settlement_plan.ordered_steps' in payload['task_body']
    assert '--decision-output-root' in payload['thin_prompt_command']
    for overrides in (dict(turn_instance_id=None), dict(thin=False, full=True),
                      dict(runtime_profile='codex_app_ssh_goal')):
        with pytest.raises(ValueError):
            build_heartbeat_prompt(**(args | overrides), decision_output_root=tmp_path)
    with pytest.raises(ValueError, match='existing directory'):
        build_heartbeat_prompt(**args, decision_output_root=tmp_path / 'missing')


@pytest.mark.parametrize('mode', ['plain', 'native-goal', 'loopx-goal', 'turn'])
def test_incompatible_worker_rejected(mode):
    with pytest.raises(ValueError, match='turn_envelope'):
        Execution(mode=mode, turn_envelope=True)


@pytest.mark.parametrize('enabled', [False, True])
def test_real_worker_generated_guard_selection_reentry_and_settle_once(tmp_path, enabled):
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    logs = tmp_path / 'wake logs'
    (logs / TURN_ID).mkdir(parents=True)
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT),
               LOOPX_CLI=str(Path(sys.executable).parent / 'loopx'),
               LOOPX_REGISTRY=str(registry), LOOPX_RUNTIME_ROOT=str(runtime),
               LOOPX_PROJECT=str(project), LOOPX_GOAL_ID=GOAL_ID, LOOPX_AGENT_ID=AGENT_ID,
               LOOPX_WAKE_LOG_DIR=str(logs), LOOPX_TURN_ENVELOPE='1' if enabled else '0')
    body = heartbeat_body(env, TURN_ID, native_goal=False)
    # Execute the renderer's actual guard, preserving its route and flags.
    block = body.split('```sh\n', 1)[1].split('```', 1)[0]
    guard = next(line for line in block.splitlines() if 'quota should-run' in line)
    guard = guard.replace('${LOOPX_TURN:?}', TURN_ID)
    argv = shlex.split(guard)
    import subprocess
    result = subprocess.run(argv, env=env, cwd=project, capture_output=True, text=True)
    first = json.loads(result.stdout)
    assert result.returncode == 0, first
    root = logs / TURN_ID / 'decisions'
    if enabled:
        captures = list(root.glob('*/decision.json'))
        assert len(captures) == 1
        full = json.loads(captures[0].read_text())
        assert first['action_signature']['matches']
        detail = shlex.split(first['detail_ref']['full_decision'])
        assert detail[:2] == ['cat', '--']  # Depends on the saved-detail reader contract.
        receipts = _heartbeat_receipt_count(runtime, TURN_ID)
        readback = subprocess.run(detail, env=env, cwd=project, capture_output=True,
                                  text=True, check=True)
        assert json.loads(readback.stdout) == full
        assert _heartbeat_receipt_count(runtime, TURN_ID) == receipts
    else:
        assert not root.exists()
        full = first
    selection = full['interaction_contract']['cli_channel']['selection_command']
    command = selection['route_prefix'] + ' ' + selection['command_args_template'].replace(
        '{todo_id}', ALTERNATIVE_TODO_ID)
    rc, selected = _run_generated_cli(command, registry_path=registry)
    assert rc == 0, selected
    if enabled:
        paths = list(root.glob('*/decision.json'))
        assert len(paths) == 2
        assert json.loads(captures[0].read_text()) == full
        selected_full = json.loads(next(p for p in paths if p != captures[0]).read_text())
        assert selected['action_signature']['matches']
        assert selected['writeback']['settlement_plan'] == selected_full['interaction_contract']['cli_channel']['settlement_plan']
    else:
        selected_full = selected
    identity = selected_full['heartbeat_receipt']['settlement_identity']
    assert identity['todo_id'] == ALTERNATIVE_TODO_ID
    assert identity['turn_instance_id'] == TURN_ID
    before = _heartbeat_receipt_count(runtime, TURN_ID)
    rc, replay = _run_generated_cli(command, registry_path=registry)
    assert rc == 0, replay
    assert _heartbeat_receipt_count(runtime, TURN_ID) == before
    if enabled:
        assert len(list(root.glob('*/decision.json'))) == 3
    rc, refresh = _run_cli(
        registry, runtime, 'refresh-state', '--goal-id', GOAL_ID, '--agent-id', AGENT_ID,
        '--todo-id', ALTERNATIVE_TODO_ID, '--turn-instance-id', TURN_ID,
        '--classification', 'validated_progress', '--delivery-batch-scale', 'implementation',
        '--delivery-outcome', 'outcome_progress', '--delivery-boundary', 'in_flight_continuation',
        '--no-global-sync', '--suppress-external-sinks')
    assert rc == 0, refresh
    spend = refresh['settlement_owed']['command']
    for _ in range(2):
        rc, settled = _run_cli(registry, runtime, *shlex.split(spend)[1:])
        assert rc == 0, settled
    assert _spend_run_count(runtime) == 1


def test_native_worker_and_stable_bootstrap_cannot_use_capture(tmp_path):
    project, runtime, registry = _write_fixture(tmp_path)
    env = dict(os.environ, LOOPX_CLI='unused', LOOPX_REGISTRY=str(registry),
               LOOPX_RUNTIME_ROOT=str(runtime), LOOPX_PROJECT=str(project),
               LOOPX_GOAL_ID=GOAL_ID, LOOPX_AGENT_ID=AGENT_ID,
               LOOPX_WAKE_LOG_DIR=str(tmp_path / 'absent'), LOOPX_TURN_ENVELOPE='1')
    with pytest.raises(ValueError, match='host-owned'):
        heartbeat_body(env, TURN_ID, native_goal=True)
    assert not (tmp_path / 'absent').exists()
    rc, rejected = _run_cli(
        registry, runtime, 'heartbeat-prompt', '--bootstrap', '--thin',
        '--runtime-profile', 'generic_cli', '--goal-id', GOAL_ID, '--agent-id', AGENT_ID,
        '--turn-instance-id', TURN_ID, '--decision-output-root', str(tmp_path))
    assert rc != 0 and rejected['ok'] is False
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 0
    assert not list(tmp_path.glob('decision-*'))
