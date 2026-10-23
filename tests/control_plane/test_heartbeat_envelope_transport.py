"""Qualify the real worker -> renderer -> guard/selection/settlement boundary."""
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from benchmark.runtime.codex import Execution
from benchmark.runtime.worker import heartbeat_body
from loopx.heartbeat_prompt import build_heartbeat_prompt
from tests.control_plane.cli_output_fixture import stable_budget_fixture_root
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, ALTERNATIVE_TODO_ID, REPO_ROOT,
    _write_fixture, _configure_selectable_alternative, _run_cli,
    _initialize_fixture_authority,
    _run_generated_cli, _heartbeat_receipt_count, _spend_run_count,
)


def test_capture_preserves_full_dispatcher_and_envelope_needs_explicit_opt_in(tmp_path):
    args = dict(goal_id=GOAL_ID, agent_id=AGENT_ID, runtime_profile="generic_cli",
                turn_instance_id=TURN_ID, thin=True)
    assert build_heartbeat_prompt(**args) == build_heartbeat_prompt(**args, decision_output_root=None)
    ordinary = build_heartbeat_prompt(**args)
    captured = build_heartbeat_prompt(**args, decision_output_root=tmp_path)
    capture_args = ' --decision-output-root ' + shlex.quote(str(tmp_path))
    assert captured['quota_guard_command'] == ordinary['quota_guard_command'] + capture_args
    assert captured['task_body'].replace(capture_args, '') == ordinary['task_body']
    assert '--turn-envelope' not in captured['quota_guard_command']
    with pytest.raises(ValueError, match='capture root'):
        build_heartbeat_prompt(**args, turn_envelope=True)
    payload = build_heartbeat_prompt(**args, decision_output_root=tmp_path, turn_envelope=True)
    assert payload['interface_budget']['within_budget'], payload['interface_budget']
    assert '--turn-envelope --decision-output-root' in payload['quota_guard_command']
    assert 'writeback.settlement_plan.ordered_steps' in payload['task_body']
    assert 'When present, read inline `agent_context` before planning.' in payload['task_body']
    assert 'an `agent_context.detail_ref`' in payload['task_body']
    assert 'using inline ACK argv' in payload['task_body']
    assert 'needed missing/truncated/referenced command' in payload['task_body']
    assert 'failure argv only after host update failure' in payload['task_body']
    assert '--decision-output-root' in payload['thin_prompt_command']
    assert '--turn-envelope' in payload['thin_prompt_command']
    # The captured allowance must neither leak into ordinary thin nor disable
    # overflow diagnostics, including when Reward Memory is absent.
    no_memory = dict(args, reward_memory_enabled=False)
    ordinary = build_heartbeat_prompt(**no_memory)
    captured_only = build_heartbeat_prompt(**no_memory, decision_output_root=tmp_path)
    assert ordinary['interface_budget']['max_chars'] == 4_200
    assert captured_only['interface_budget']['max_chars'] == 4_200
    oversized = build_heartbeat_prompt(
        **no_memory, decision_output_root=tmp_path, turn_envelope=True,
        agent_scopes=[letter * 320 for letter in 'stu'],
    )
    assert oversized['interface_budget']['max_chars'] == 4_600
    assert oversized['interface_budget']['within_budget'] is False
    for overrides in (dict(turn_instance_id=None), dict(thin=False, full=True),
                      dict(runtime_profile='codex_app_ssh_goal')):
        with pytest.raises(ValueError):
            build_heartbeat_prompt(**(args | overrides), decision_output_root=tmp_path)
    with pytest.raises(ValueError, match='existing directory'):
        build_heartbeat_prompt(**args, decision_output_root=tmp_path / 'missing')


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
def test_captured_scheduler_ack_is_inline_and_failure_detail_stays_on_original_source(
    tmp_path, monkeypatch, provider,
):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    capture = tmp_path / 'capture'
    code, envelope = _run_cli(registry, runtime, 'quota', 'should-run',
        '--goal-id', GOAL_ID, '--agent-id', AGENT_ID, '--todo-id', TODO_ID,
        '--turn-instance-id', TURN_ID, '--codex-app', '--turn-envelope',
        '--decision-output-dir', str(capture), cwd=project)
    assert code == 0, envelope
    original = (capture / 'decision.json').read_bytes()
    source = json.loads(original)['scheduler_hint']['app_automation']
    projected = envelope['scheduler']['app_automation']
    assert projected['ack_cli_args'] == source['ack_hint']['cli_args']
    assert 'ack_cli_args_detail_ref' not in projected
    assert projected['failure_cli_args_detail_ref']['detail_ref'] == (
        'full_decision.scheduler_hint.app_automation.failure_hint.cli_args'
    )
    before = _heartbeat_receipt_count(runtime, TURN_ID)
    read = subprocess.run(shlex.split(envelope['detail_ref']['full_decision']),
        capture_output=True, check=True).stdout
    assert read == original
    assert _heartbeat_receipt_count(runtime, TURN_ID) == before


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize('scope_chars', [0, 320])
def test_bound_captured_prompt_fits_budget_without_reward_memory(
    tmp_path, monkeypatch, provider, scope_chars,
):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    # Match the public budget workload independently of pytest/runner path length.
    # Both runtime and capture routes remain present and count toward the body.
    with stable_budget_fixture_root(tmp_path) as base:
        root = (base / ('p' * (128 - len(str(base.resolve())) - 1))).resolve()
        assert len(str(root)) == 128
        project, runtime, registry = _write_fixture(root)
        _initialize_fixture_authority(project, runtime, provider=provider)
        capture = root / 'capture'
        capture.mkdir()
        argv = [
            sys.executable, '-m', 'loopx.entrypoint', '--format', 'json',
            '--registry', str(registry), '--runtime-root', str(runtime),
            'heartbeat-prompt', '--thin', '--turn-envelope',
            '--decision-output-root', str(capture), '--runtime-profile', 'generic_cli',
            '--goal-id', GOAL_ID, '--agent-id', AGENT_ID, '--turn-instance-id', TURN_ID,
        ]
        if scope_chars:
            argv.extend(['--agent-scope', 's' * scope_chars])
        result = subprocess.run(
            argv, cwd=project, capture_output=True, text=True, check=True,
            env={**os.environ, 'PYTHONPATH': str(REPO_ROOT)},
        )
        payload = json.loads(result.stdout)
        body = payload['task_body']
        assert '--reward-memory-reflection-json' not in body
        assert str(runtime) in body and str(capture) in body
        assert payload['turn_instance_id'] == TURN_ID
        assert payload['interface_budget']['max_chars'] == 4_600
        assert payload['interface_budget']['within_budget'], payload['interface_budget']
        assert len(result.stdout) <= 5_400  # Shipped Agent JSON, not generator metadata.
        assert 'using inline ACK argv' in body
        assert 'needed missing/truncated/referenced command' in body
        assert 'failure argv only after host update failure' in body
        assert 'same-invocation observation, not fresh admission' in body
        assert 'goal_acceptance' in body  # Retain the complete outcome guidance too.


@pytest.mark.parametrize('mode', ['plain', 'native-goal', 'loopx-goal', 'turn'])
def test_incompatible_worker_rejected(mode):
    with pytest.raises(ValueError, match='turn_envelope'):
        Execution(mode=mode, turn_envelope=True)


@pytest.fixture
def private_handoff(request):
    if not request.param:
        yield None
        return
    if os.name != 'posix':
        pytest.skip('Requires a private Unix socket')
    with tempfile.TemporaryDirectory(prefix='hb-capture-', dir='/tmp') as directory:
        endpoint = str(Path(directory).resolve() / 'host.sock')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(endpoint)
            listener.listen()
            listener.settimeout(.1)
            stopped = threading.Event()

            def release():
                while not stopped.is_set():
                    try:
                        channel, _ = listener.accept()
                    except TimeoutError:
                        continue
                    with channel, channel.makefile('rb') as stream:
                        message = json.loads(stream.readline())
                        channel.sendall(json.dumps({'released': True, 'sha256': message['sha256']}).encode() + b'\n')

            thread = threading.Thread(target=release, daemon=True)
            thread.start()
            try:
                yield endpoint
            finally:
                stopped.set()
                thread.join(timeout=5)
                assert not thread.is_alive()


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('private_handoff', [False, True], indirect=True)
def test_real_worker_generated_guard_selection_reentry_and_settle_once(
    tmp_path, monkeypatch, provider, enabled, private_handoff,
):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    state = project / f'.codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md'
    original_state = state.read_bytes()
    rc, refused = _run_cli(registry, runtime, 'todo', 'list', '--goal-id', GOAL_ID)
    assert rc == 1 and refused['error_code'] == 'canonical_authority_required', refused
    assert refused['legacy_fallback_used'] is False and state.read_bytes() == original_state
    # Explicitly convert the disposable fixture before the worker runs. Its
    # ordinary guard must not recover authority from the Markdown projection.
    _initialize_fixture_authority(project, runtime, provider=provider)
    logs = tmp_path / 'wake logs'
    (logs / TURN_ID).mkdir(parents=True)
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT),
               LOOPX_CLI=str(Path(sys.executable).parent / 'loopx'),
               LOOPX_REGISTRY=str(registry), LOOPX_RUNTIME_ROOT=str(runtime),
               LOOPX_PROJECT=str(project), LOOPX_GOAL_ID=GOAL_ID, LOOPX_AGENT_ID=AGENT_ID,
               LOOPX_WAKE_LOG_DIR=str(logs), LOOPX_TURN_ENVELOPE='1' if enabled else '0')
    body = heartbeat_body(env, TURN_ID, native_goal=False, decision_handoff_socket=private_handoff)
    # Execute the renderer's actual guard, preserving its route and flags.
    block = body.split('```sh\n', 1)[1].split('```', 1)[0]
    guard = next(line for line in block.splitlines() if 'quota should-run' in line)
    guard = guard.replace('${LOOPX_TURN:?}', TURN_ID)
    argv = shlex.split(guard)
    result = subprocess.run(argv, env=env, cwd=project, capture_output=True, text=True)
    first = json.loads(result.stdout)
    assert result.returncode == 0, first
    root = logs / TURN_ID / 'decisions'
    captured = enabled or private_handoff is not None
    if captured:
        captures = list(root.glob('*/decision.json'))
        assert len(captures) == 1
        full = json.loads(captures[0].read_text())
        if enabled:
            assert first['action_signature']['matches']
            detail = shlex.split(first['detail_ref']['full_decision'])
            assert detail[:2] == ['cat', '--']  # Depends on the saved-detail reader contract.
            receipts = _heartbeat_receipt_count(runtime, TURN_ID)
            readback = subprocess.run(detail, env=env, cwd=project, capture_output=True,
                                      text=True, check=True)
            assert json.loads(readback.stdout) == full
            assert _heartbeat_receipt_count(runtime, TURN_ID) == receipts
        else:
            assert first['interaction_contract'] == full['interaction_contract']
            assert 'action_signature' not in first and 'quota' in first
            assert 'Use TurnEnvelope:' not in body
            assert '--turn-envelope' not in argv
    else:
        assert not root.exists()
        full = first
    selection = full['interaction_contract']['cli_channel']['selection_command']
    command = selection['route_prefix'] + ' ' + selection['command_args_template'].replace(
        '{todo_id}', ALTERNATIVE_TODO_ID)
    rc, selected = _run_generated_cli(command, registry_path=registry)
    assert rc == 0, selected
    if captured:
        paths = list(root.glob('*/decision.json'))
        assert len(paths) == 2
        assert json.loads(captures[0].read_text()) == full
        selected_full = json.loads(next(p for p in paths if p != captures[0]).read_text())
        if enabled:
            assert selected['action_signature']['matches']
            assert selected['writeback']['settlement_plan'] == selected_full['interaction_contract']['cli_channel']['settlement_plan']
        else:
            assert selected['interaction_contract'] == selected_full['interaction_contract']
            assert 'action_signature' not in selected
    else:
        selected_full = selected
    identity = selected_full['heartbeat_receipt']['settlement_identity']
    assert identity['todo_id'] == ALTERNATIVE_TODO_ID
    assert identity['turn_instance_id'] == TURN_ID
    before = _heartbeat_receipt_count(runtime, TURN_ID)
    rc, replay = _run_generated_cli(command, registry_path=registry)
    assert rc == 0, replay
    assert _heartbeat_receipt_count(runtime, TURN_ID) == before
    if captured:
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


@pytest.mark.skipif(os.name != 'posix', reason='Requires a private Unix socket')
def test_renderer_retains_owned_handoff_without_enabling_it_by_default(tmp_path):
    import socket
    import tempfile
    args = dict(goal_id=GOAL_ID, agent_id=AGENT_ID, runtime_profile='generic_cli',
                turn_instance_id=TURN_ID, thin=True)
    assert build_heartbeat_prompt(**args) == build_heartbeat_prompt(**args, decision_handoff_socket=None)
    with tempfile.TemporaryDirectory(prefix='hb-handoff-', dir='/tmp') as directory:
        endpoint = str(Path(directory).resolve() / 'host.sock')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(endpoint)
            packet = build_heartbeat_prompt(**args, decision_output_root=tmp_path,
                                            decision_handoff_socket=endpoint)
            for field in ('quota_guard_command', 'thin_prompt_command'):
                argv = shlex.split(packet[field])
                assert argv[argv.index('--decision-handoff-socket') + 1] == endpoint
                assert '--turn-envelope' not in argv
            assert 'Use TurnEnvelope:' not in packet['task_body']
            with pytest.raises(ValueError, match='capture root'):
                build_heartbeat_prompt(**args, decision_handoff_socket=endpoint)
    with pytest.raises(ValueError, match='invalid decision handoff socket'):
        build_heartbeat_prompt(**args, decision_output_root=tmp_path, decision_handoff_socket=endpoint)
