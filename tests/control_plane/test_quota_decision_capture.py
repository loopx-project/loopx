"""Capture is a transport readback, never a second guard or fresh admission."""
import argparse
import json
import os
import shlex
import subprocess
from unittest.mock import patch

import pytest

from loopx.cli_commands.quota_capture import capture_decision, prepare_decision_capture
from loopx.control_plane.quota.error_codes import QuotaCommandValidationError
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, _write_fixture,
    _configure_read_only_todo, _run_cli, _heartbeat_receipt_count,
)


def request(path, **overrides):
    return argparse.Namespace(**dict(
        {'decision_output_dir': path, 'quota_command': 'should-run',
         'turn_instance_id': TURN_ID}, **overrides))


def test_disabled_capture_does_not_touch_disk(tmp_path):
    assert prepare_decision_capture(request(None)) is None
    capture_decision(None, {'not_serializable': object()})
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('overrides', [
    {'quota_command': 'spend-slot'}, {'turn_instance_id': None},
])
def test_invalid_request_does_not_create_directory(tmp_path, overrides):
    target = tmp_path / 'capture'
    with pytest.raises(QuotaCommandValidationError):
        prepare_decision_capture(request(target, **overrides))
    assert not target.exists()


def test_unique_private_capture_and_atomic_publication(tmp_path):
    target = prepare_decision_capture(request(tmp_path / 'first'))
    payload = {'ok': True, 'heartbeat_receipt': {'turn_instance_id': TURN_ID},
               'full_detail': 'complete details ' * 4000}
    capture_decision(target, payload)
    saved = target / 'decision.json'
    assert json.loads(saved.read_text()) == payload
    if os.name != 'nt':
        assert target.stat().st_mode & 0o777 == 0o700
        assert saved.stat().st_mode & 0o777 == 0o600
    with pytest.raises(QuotaCommandValidationError):
        prepare_decision_capture(request(target))
    assert json.loads(saved.read_text()) == payload
    # Interrupted publication exposes no partial decision.json.
    second = prepare_decision_capture(request(tmp_path / 'second'))
    with patch('loopx.cli_commands.quota_capture.os.replace', side_effect=OSError('disk failure')):
        with pytest.raises(RuntimeError, match='receipt may already exist'):
            capture_decision(second, payload)
    assert list(second.iterdir()) == []


def test_existing_symlink_is_not_followed(tmp_path):
    target = tmp_path / 'link'
    target.symlink_to(tmp_path / 'absent', target_is_directory=True)
    with pytest.raises(QuotaCommandValidationError):
        prepare_decision_capture(request(target))
    assert not (tmp_path / 'absent').exists()


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
def test_real_guard_saved_before_envelope_and_read_without_reexecution(tmp_path, monkeypatch, provider):
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from tests.control_plane.canonical_authority_fixture import (
        initialize_canonical_authority, isolate_sqlite_runtime,
    )
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    # A valid long route exceeds the executable inline argv limit. Its original
    # signed scheduler command must remain resolvable without another guard.
    root = tmp_path.joinpath(*(['long-route-' + 'x' * 170] * 3))
    project, runtime, registry = _write_fixture(root)
    # Keep SQLite's database filename within its provider limit; only the
    # registry transport route needs to exercise the inline argv boundary.
    runtime = tmp_path / 'runtime'
    configured = json.loads(registry.read_text())
    configured['common_runtime_root'] = str(runtime)
    registry.write_text(json.dumps(configured))
    _configure_read_only_todo(project)
    state = project / f'.codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md'
    code, listed = _run_cli(registry, runtime, 'todo', 'list', '--goal-id', GOAL_ID)
    assert code == 0, listed
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID, handoff_mode='soft_claim', todos=listed['todos'],
    )
    initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state, provider=provider)
    capture = tmp_path / "guard ' $(touch INJECTED) `touch INJECTED`"
    args = ['quota', 'should-run', '--goal-id', GOAL_ID, '--agent-id', AGENT_ID,
            '--todo-id', TODO_ID, '--turn-instance-id', TURN_ID, '--codex-app',
            '--available-capability', 'shell', '--available-capability', 'filesystem_read']
    code, envelope = _run_cli(registry, runtime, *args, '--turn-envelope',
                              '--decision-output-dir', str(capture))
    assert code == 0, envelope
    assert envelope['schema_version'] == 'loopx_turn_envelope_v0'
    saved = capture / 'decision.json'
    full = json.loads(saved.read_text())
    app = envelope['scheduler']['app_automation']
    assert 'ack_cli_args' not in app
    ref = app['ack_cli_args_detail_ref']
    assert ref['detail_ref'] == 'full_decision.scheduler_hint.app_automation.ack_hint.cli_args'
    assert 'request' not in ref
    assert shlex.split(envelope['detail_ref']['full_decision']) == ['cat', '--', str(saved)]
    assert full['ok'] is True
    identity = full['interaction_contract']['cli_channel']['settlement_plan']['identity']
    assert identity['turn_instance_id'] == TURN_ID
    assert identity['todo_id'] == TODO_ID
    assert 'interaction_contract' in full
    observed = envelope['detail_ref']['captured_decision']
    assert (observed['goal_id'], observed['agent_id'], observed['turn_instance_id']) == (GOAL_ID, AGENT_ID, TURN_ID)
    assert observed['path'] == str(saved)
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope
    assert envelope['action_signature'] == build_turn_envelope(full)['action_signature']
    before = _heartbeat_receipt_count(runtime, TURN_ID)
    assert before >= 1
    # Deliberately unusable displayed JSON does not require a new CLI evaluation.
    with pytest.raises(json.JSONDecodeError):
        json.loads(json.dumps(envelope)[:30])
    before_files = {p: p.read_bytes() for p in runtime.rglob('*') if p.is_file()}
    # Execute the actual advertised POSIX read command, including hostile path characters.
    if os.name != 'nt':
        readback = subprocess.run(envelope['detail_ref']['full_decision'], shell=True,
                                  cwd=tmp_path, capture_output=True, text=True, check=True)
        assert json.loads(readback.stdout) == full
        value = json.loads(readback.stdout)
        for key in ref['detail_ref'].split('.')[1:]:
            value = value[key]
        assert value == full['scheduler_hint']['app_automation']['ack_hint']['cli_args']
        assert value[value.index('--registry') + 1] == str(registry)
        assert value[value.index('--turn-instance-id') + 1] == TURN_ID
    else:
        assert json.loads(saved.read_text()) == full
    assert not (tmp_path / 'INJECTED').exists()
    assert {p: p.read_bytes() for p in runtime.rglob('*') if p.is_file()} == before_files
    assert _heartbeat_receipt_count(runtime, TURN_ID) == before
    # Reject accidental reuse before any new guard evaluation.
    code, rejected = _run_cli(registry, runtime, *args, '--decision-output-dir', str(capture))
    assert code == 1 and rejected['ok'] is False
    assert _heartbeat_receipt_count(runtime, TURN_ID) == before
    assert json.loads(saved.read_text()) == full
    # A deliberate fresh invocation within the Turn has its own immutable capture.
    second = tmp_path / 'guard-2'
    code, fresh = _run_cli(registry, runtime, *args, '--turn-envelope', '--decision-output-dir', str(second))
    assert code == 0, fresh
    assert json.loads((second / 'decision.json').read_text())['heartbeat_receipt']['status'] == 'replayed'
    assert json.loads(saved.read_text()) == full

    assert shlex.split(fresh['detail_ref']['full_decision']) == ['cat', '--', str(second / 'decision.json')]
    # Missing old evidence fails locally; it never substitutes a new guard observation.
    saved.unlink()
    before = _heartbeat_receipt_count(runtime, TURN_ID)
    if os.name != 'nt':
        failed = subprocess.run(envelope['detail_ref']['full_decision'], shell=True,
                                cwd=tmp_path, capture_output=True, text=True)
        assert failed.returncode != 0
    assert not saved.exists()
    assert _heartbeat_receipt_count(runtime, TURN_ID) == before


def test_full_display_is_unchanged_by_capture_location():
    from loopx.cli_commands.quota import _project_quota_cli_payload
    from tests.test_turn_envelope import _full_decision
    args = argparse.Namespace(turn_envelope=False, quota_command='should-run')
    source = _full_decision()
    baseline = _project_quota_cli_payload(source, args, frozenset(), None)
    captured = _project_quota_cli_payload(source, args, frozenset(), None,
                                         captured_decision_path='/tmp/observation/decision.json')
    assert captured == baseline


@pytest.mark.parametrize('kind', ['missing', 'file', 'symlink'])
def test_capture_root_rejects_invalid_destination(tmp_path, kind):
    root = tmp_path / 'root'
    if kind == 'file':
        root.write_text('unchanged')
    elif kind == 'symlink':
        root.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(QuotaCommandValidationError):
        prepare_decision_capture(request(None, decision_output_root=root))
    assert not list(tmp_path.glob('decision-*'))


def test_capture_root_allocates_private_distinct_children(tmp_path):
    args = request(None, decision_output_root=tmp_path)
    first = prepare_decision_capture(args)
    second = prepare_decision_capture(args)
    assert first != second
    assert first.parent == second.parent == tmp_path
    if os.name != 'nt':
        assert first.stat().st_mode & 0o777 == 0o700
    with pytest.raises(QuotaCommandValidationError):
        prepare_decision_capture(request(None, decision_output_root=tmp_path, turn_instance_id=None))


@pytest.mark.skipif(os.name != 'posix', reason='Unix local Host transport')
@pytest.mark.parametrize('release', ['exact', 'wrong_digest', 'disconnect', 'changed_capture'])
def test_real_cli_handoff_preserves_original_receipt(tmp_path, release):
    import hashlib
    import socket
    import sys
    import tempfile
    from pathlib import Path

    project, runtime, registry = _write_fixture(tmp_path)
    _configure_read_only_todo(project)
    capture = tmp_path / 'handoff-capture'
    with tempfile.TemporaryDirectory(prefix='quota-host-', dir='/tmp') as short:
        endpoint = Path(short).resolve() / 'host.sock'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(endpoint))
            listener.listen(1)
            listener.settimeout(20)
            command = [sys.executable, '-m', 'loopx.cli', '--format', 'json',
                       '--registry', str(registry), '--runtime-root', str(runtime),
                       'quota', 'should-run', '--goal-id', GOAL_ID, '--agent-id', AGENT_ID,
                       '--todo-id', TODO_ID, '--turn-instance-id', TURN_ID,
                       '--runtime-profile', 'generic_cli', '--available-capability', 'shell',
                       '--available-capability', 'filesystem_read',
                       '--decision-output-dir', str(capture), '--decision-handoff-socket', str(endpoint)]
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2])}) as process:
                try:
                    channel, _ = listener.accept()
                    with channel:
                        channel.settimeout(5)
                        with channel.makefile('rb') as stream:
                            message = json.loads(stream.readline())
                        original = (capture / 'decision.json').read_bytes()
                        digest = hashlib.sha256(original).hexdigest()
                        assert message == {'path': str(capture / 'decision.json'), 'sha256': digest}
                        receipts = _heartbeat_receipt_count(runtime, TURN_ID)
                        assert receipts >= 1 and process.poll() is None
                        # The actual CLI has persisted admission but cannot expose
                        # successful output while the Host is taking its snapshot.
                        import select
                        assert not select.select([process.stdout], [], [], 0)[0]
                        if release == 'changed_capture':
                            (capture / 'decision.json').write_bytes(original + b'\n')
                        if release != 'disconnect':
                            channel.sendall(json.dumps({'released': True, 'sha256': (
                                '0' * 64 if release == 'wrong_digest' else digest)}).encode() + b'\n')
                    stdout, stderr = process.communicate(timeout=15)
                    if release == 'exact':
                        assert process.returncode == 0 and json.loads(stdout)['ok'] is True
                        assert not stderr
                    else:
                        assert process.returncode != 0
                        assert 'Do not blindly rerun' in stderr + stdout
                    assert _heartbeat_receipt_count(runtime, TURN_ID) == receipts
                    assert json.loads(original)['heartbeat_receipt']['turn_instance_id'] == TURN_ID
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()


def test_handoff_requires_explicit_capture_before_guard(tmp_path):
    with pytest.raises(QuotaCommandValidationError, match='invalid decision handoff'):
        prepare_decision_capture(request(None, decision_handoff_socket=str(tmp_path / 'missing')))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name != 'posix', reason='Unix local Host transport')
def test_handoff_rejects_shared_or_symlink_endpoint_and_preserves_selection(tmp_path):
    import socket
    import tempfile
    from pathlib import Path
    from loopx.cli_commands.quota_capture import validate_handoff_socket, bind_capture_selection_transport

    with tempfile.TemporaryDirectory(prefix='quota-host-', dir='/tmp') as short:
        private = Path(short).resolve()
        endpoint = private / 'host.sock'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(endpoint))
            assert validate_handoff_socket(str(endpoint)) == endpoint
            alias = private / 'alias.sock'
            alias.symlink_to(endpoint)
            with pytest.raises(QuotaCommandValidationError):
                validate_handoff_socket(str(alias))
            private.chmod(0o755)
            try:
                with pytest.raises(QuotaCommandValidationError):
                    validate_handoff_socket(str(endpoint))
            finally:
                private.chmod(0o700)
            with pytest.raises(QuotaCommandValidationError, match='explicit decision capture'):
                prepare_decision_capture(request(None, decision_handoff_socket=str(endpoint)))
            payload = {'interaction_contract': {'cli_channel': {'selection_command': {
                'command_args_template': 'quota should-run --todo-id TODO'}}}}
            bind_capture_selection_transport(payload, request(None, decision_output_root=tmp_path,
                                                              decision_handoff_socket=str(endpoint)))
            words = shlex.split(payload['interaction_contract']['cli_channel']['selection_command']['command_args_template'])
            assert words[-4:] == ['--decision-output-root', str(tmp_path), '--decision-handoff-socket', str(endpoint)]
            assert '--turn-envelope' not in words  # Independent from display mode.


def test_capture_root_accepts_guided_turn_before_identity_is_minted(tmp_path):
    target = prepare_decision_capture(request(
        None,
        decision_output_root=tmp_path,
        turn_instance_id=None,
        begin_turn=True,
    ))

    assert target is not None
    assert target.parent == tmp_path


def test_guided_turn_captures_the_minted_identity(tmp_path):
    project, runtime, registry = _write_fixture(tmp_path)
    capture_root = tmp_path / "captures"
    capture_root.mkdir()

    code, payload = _run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "codex_app_ssh_goal",
        "--begin-turn",
        "--decision-output-root",
        str(capture_root),
        "--scan-path",
        str(project),
    )

    assert code == 0, payload
    saved = list(capture_root.glob("decision-*/decision.json"))
    assert len(saved) == 1
    full = json.loads(saved[0].read_text(encoding="utf-8"))
    minted = full["heartbeat_receipt"]["turn_instance_id"]
    assert minted.startswith("guided-start:")
    assert payload["heartbeat_receipt"]["turn_instance_id"] == minted


def _replan_selection_hop(tmp_path, host):
    """Commit a replan guard, then select a Todo on that Turn through a Host.

    The real CLI, quota owner and TS checkpoint selector run unchanged; only
    the Host side of the private socket is a synthetic test listener.
    """
    import hashlib
    import socket
    import tempfile
    import threading
    from pathlib import Path
    from tests.control_plane.test_quota_settlement_cli import (
        SELECTED_REPLAN_TODO_ID, _configure_selected_todo_replan_fixture,
    )

    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry)
    common = ('--goal-id', GOAL_ID, '--agent-id', AGENT_ID, '--runtime-profile', 'generic_cli',
              '--turn-instance-id', TURN_ID, '--available-capability', 'shell',
              '--available-capability', 'filesystem_write')
    code, guard = _run_cli(registry, runtime, 'quota', 'should-run', *common, cwd=project)
    assert code == 0 and guard['effective_action'] == 'autonomous_replan_required', guard
    assert guard['heartbeat_receipt']['status'] == 'committed'
    capture_root = tmp_path / 'decisions'
    capture_root.mkdir(mode=0o700)
    observed = {}
    with tempfile.TemporaryDirectory(prefix='quota-host-', dir='/tmp') as short:
        endpoint = Path(short).resolve() / 'host.sock'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(endpoint))
            listener.listen(1)
            listener.settimeout(60)

            def serve():
                channel, _ = listener.accept()
                with channel:
                    message = json.loads(channel.makefile('rb').readline())
                    raw = Path(message['path']).read_bytes()
                    assert hashlib.sha256(raw).hexdigest() == message['sha256']
                    observed['decision'] = json.loads(raw)
                    if host(observed):
                        channel.sendall(json.dumps({'released': True, 'sha256': message['sha256']}).encode() + b'\n')

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            code, selected = _run_cli(
                registry, runtime, 'quota', 'should-run', *common,
                '--todo-id', SELECTED_REPLAN_TODO_ID, '--turn-envelope',
                '--decision-output-root', str(capture_root),
                '--decision-handoff-socket', str(endpoint), cwd=project)
            thread.join(10)
    return code, selected, observed, sorted(capture_root.glob('*/decision.json')), runtime


@pytest.mark.skipif(os.name != 'posix', reason='Unix local Host transport')
def test_selection_hop_on_committed_replan_turn_returns_committed_result(tmp_path):
    from loopx.control_plane.effect_runtime import effect_runtime_result

    def typed_host(observed):
        # The Host consults the existing typed selector before releasing.
        observed['intent'] = effect_runtime_result('runtime.checkpoint_artifacts', {
            'action': 'select_replan', 'quota': observed['decision'], 'goal_id': GOAL_ID,
            'agent_id': AGENT_ID, 'turn_instance_id': TURN_ID})
        return True

    code, selected, observed, saved, _ = _replan_selection_hop(tmp_path, typed_host)
    assert observed['decision']['ok'] is True
    assert observed['decision']['heartbeat_receipt']['status'] == 'upgraded'
    # The binding hop is the same Turn's pending closeout, not a new point.
    assert observed['intent'] == {'capture': False, 'reason': 'turn_closeout_pending',
                                  'execution_authority_granted': False}
    assert code == 0 and selected['ok'] is True, selected
    assert selected['schema_version'] == 'loopx_turn_envelope_v0'
    assert len(saved) == 1 and json.loads(saved[0].read_text()) == observed['decision']


@pytest.mark.skipif(os.name != 'posix', reason='Unix local Host transport')
def test_refused_selection_handoff_is_typed_and_keeps_the_committed_capture(tmp_path):
    code, selected, observed, saved, runtime = _replan_selection_hop(tmp_path, lambda _: False)
    assert code == 1 and selected['ok'] is False
    # Never a generic collection failure that replaces the original capture.
    assert selected['error_code'] == 'quota_decision_transport_failed', selected
    assert selected['status'] == 'decision_transport_failed'
    assert 'Do not blindly rerun the guard' in selected['reason']
    assert selected['heartbeat_receipt']['turn_instance_id'] == TURN_ID
    assert len(saved) == 1 and selected['captured_decision'] == {'path': str(saved[0])}
    assert json.loads(saved[0].read_text()) == observed['decision']
    assert observed['decision']['ok'] is True
    assert _heartbeat_receipt_count(runtime, TURN_ID) >= 1


def test_ended_wake_handoff_endpoint_is_rejected_with_current_wake_remedy(tmp_path):
    from loopx.control_plane.turn_driver.decision_capture import validate_handoff_socket

    with pytest.raises(QuotaCommandValidationError) as raised:
        validate_handoff_socket(str(tmp_path / 'ended-wake' / 'host.sock'))
    message = str(raised.value)
    assert message.startswith('invalid decision handoff socket')
    assert 'No guard was run' in message and 'current' in message


def test_unexpected_collection_failure_names_typed_runtime_cause_only():
    from pathlib import Path
    from loopx.cli_commands.quota_failure_report import quota_failure_payload
    from loopx.control_plane.effect_runtime import EffectRuntimeInternalError

    args = argparse.Namespace(quota_command='should-run', goal_id=GOAL_ID, verbose=False)
    typed = quota_failure_payload(args, registry_path=Path('registry.json'), runtime_root_arg=None,
        error=EffectRuntimeInternalError('Effect runtime handler failed unexpectedly',
                                         diagnostic_code='unexpected_handler_error'))
    assert typed['error_code'] == 'quota_unexpected_collection_error'
    assert typed['reason'] == 'Effect runtime handler failed unexpectedly'
    assert (typed['error_class'], typed['diagnostic_code']) == (
        'EffectRuntimeInternalError', 'unexpected_handler_error')
    private = quota_failure_payload(args, registry_path=Path('registry.json'), runtime_root_arg=None,
        error=ValueError('/private/runtime/secret.json'))
    assert private['reason'] == 'quota collection failed' and private['error_class'] == 'ValueError'
    assert 'secret' not in json.dumps(private) and 'diagnostic_code' not in private
