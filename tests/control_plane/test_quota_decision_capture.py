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
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_source_fixture, read_fixture_todos,
)
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
    {'context_delivery': 'off'}, {'format': 'markdown'}, {'quota_command': 'plan'},
    {'decision_output_dir': None}, {'decision_handoff_socket': '/unused/socket'},
])
def test_deferred_native_presentation_requires_original_capture_before_admission(tmp_path, overrides):
    from loopx.cli_commands.quota_capture import prepare_quota_capture
    values = dict(defer_context_presentation=True, format='json', context_delivery='on',
                  codex_app=True, decision_output_dir=tmp_path / 'capture')
    values.update(overrides)
    path = values.pop('decision_output_dir')
    with pytest.raises(QuotaCommandValidationError, match='deferred context presentation'):
        prepare_quota_capture(request(path, **values), None)
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
def test_real_cli_fallback_reference_keeps_capture_and_turn_effects(tmp_path, monkeypatch, provider):
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.quota.turn_envelope import quota_action_signature_document
    from tests.control_plane.canonical_authority_fixture import (
        initialize_canonical_authority, isolate_sqlite_runtime,
    )
    from tests.control_plane.test_quota_settlement_cli import _spend_run_count

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = _configure_read_only_todo(project)
    with state.open('a') as stream:
        stream.write('\n- [ ] [P0] Preserve the original dependency and recovery evidence.\n'
                     f'  <!-- loopx:todo todo_id=todo_fixture_blocked status=blocked '
                     f'claimed_by={AGENT_ID} task_class=advancement_task '
                     'action_kind=validate reason=fixture_dependency -->\n')
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID, handoff_mode='soft_claim', todos=read_fixture_todos(state),
    )
    initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state, provider=provider)
    args = ['quota', 'should-run', '--goal-id', GOAL_ID, '--agent-id', AGENT_ID,
            '--todo-id', TODO_ID, '--turn-instance-id', TURN_ID, '--codex-app',
            '--available-capability', 'shell', '--available-capability', 'filesystem_read']
    capture = tmp_path / 'original-decision'
    code, ordinary = _run_cli(registry, runtime, *args, '--decision-output-dir', str(capture))
    assert code == 0, ordinary
    saved = capture / 'decision.json'
    original_bytes = saved.read_bytes()
    full = json.loads(original_bytes)
    fallback = full['blocked_priority_fallback']
    assert full['heartbeat_recommendation']['blocked_priority_fallback'] == fallback
    assert ordinary['blocked_priority_fallback'] == fallback
    assert ordinary['heartbeat_recommendation']['blocked_priority_fallback_ref'] == '$.blocked_priority_fallback'
    assert 'blocked_priority_fallback' not in ordinary['heartbeat_recommendation']
    assert ordinary['heartbeat_receipt'] == full['heartbeat_receipt']
    assert ordinary['interaction_contract']['cli_channel']['settlement_plan'] == full[
        'interaction_contract']['cli_channel']['settlement_plan']
    # Raw source hashes distinguish renderings; every action dimension must match.
    assert quota_action_signature_document(ordinary) == quota_action_signature_document(full)
    before = _heartbeat_receipt_count(runtime, TURN_ID)
    code, replay = _run_cli(registry, runtime, *args)
    assert code == 0, replay
    assert _heartbeat_receipt_count(runtime, TURN_ID) == before
    assert _spend_run_count(runtime) == 0
    assert saved.read_bytes() == original_bytes


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
def test_real_cli_boundary_clauses_reach_shared_host_without_loss(tmp_path, monkeypatch, provider):
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority, render_prompt
    from tests.control_plane.canonical_authority_fixture import (
        initialize_canonical_authority, isolate_sqlite_runtime,
    )
    from tests.control_plane.test_quota_settlement_cli import _spend_run_count

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = _configure_read_only_todo(project)
    scopes = [f'public/surface/{i}/**' for i in range(19)] + [
        'public/' + 'nested/' * 30 + 'two  spaces/**', 'tests/**',
    ]
    guards = [f'Preserve evidence {i}' for i in range(9)] + [
        'Keep the complete original evidence.\n' * 12 + 'Stop before publishing private material.',
    ]
    configured = json.loads(registry.read_text())
    goal = configured['goals'][0]
    goal['coordination'].update(write_scope=scopes, requires_parent_approval=['publish', 'production-action'])
    goal['guards'] = guards
    registry.write_text(json.dumps(configured))
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID, handoff_mode='soft_claim', todos=read_fixture_todos(state),
    )
    initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state, provider=provider)
    args = ['quota', 'should-run', '--goal-id', GOAL_ID, '--agent-id', AGENT_ID,
            '--todo-id', TODO_ID, '--turn-instance-id', TURN_ID, '--codex-app',
            '--available-capability', 'shell', '--available-capability', 'filesystem_read']
    capture = tmp_path / 'boundary-decision'
    code, envelope = _run_cli(registry, runtime, *args, '--turn-envelope', '--decision-output-dir', str(capture))
    assert code == 0, envelope
    saved = capture / 'decision.json'
    original_bytes = saved.read_bytes()
    full = json.loads(original_bytes)
    assert full['goal_boundary']['write_scope'] == scopes
    assert full['goal_boundary']['guards'] == guards
    for field in ('write_scope', 'guards', 'requires_parent_approval'):
        assert envelope['boundary'][field] == full['goal_boundary'][field], field
    authority = extract_turn_authority({'turn_envelope': envelope})
    assert authority['write_scope'] == scopes
    assert authority['boundary']['guards'] == guards
    assert authority['boundary']['requires_parent_approval'] == ['publish', 'production-action']
    assert 'write_scope' not in authority['boundary']  # Existing aliases carry these facts once.
    prompt = render_prompt(authority)
    transported, _ = json.JSONDecoder().raw_decode(prompt.split('Turn authority JSON:\n', 1)[1])
    assert transported == authority
    altered = json.loads(json.dumps(envelope))
    altered['boundary']['guards'][-1] = 'Continue without the final restriction.'
    with pytest.raises(ValueError, match='action signature'):
        extract_turn_authority({'turn_envelope': altered})
    before = _heartbeat_receipt_count(runtime, TURN_ID)
    code, replay = _run_cli(registry, runtime, *args)
    assert code == 0, replay
    assert _heartbeat_receipt_count(runtime, TURN_ID) == before
    assert _spend_run_count(runtime) == 0
    assert saved.read_bytes() == original_bytes
    assert envelope['action']['delivery_allowed'] == full['interaction_contract']['agent_channel']['delivery_allowed']


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
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID, handoff_mode='soft_claim', todos=read_fixture_todos(state),
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
@pytest.mark.parametrize('native_context', [False, True])
def test_real_cli_handoff_preserves_original_receipt(tmp_path, release, native_context):
    import hashlib
    import socket
    import sys
    import tempfile
    from pathlib import Path

    project, runtime, registry = _write_fixture(tmp_path)
    state = _configure_read_only_todo(project)
    initialize_canonical_source_fixture(runtime, GOAL_ID, state_path=state)
    capture = tmp_path / 'handoff-capture'
    context = tmp_path / 'native-context'
    if native_context:
        context.mkdir(mode=0o700)
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
            if native_context:
                command += ['--context-delivery', 'on', '--codex-context-dir', str(context)]
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2]), **({'LOOPX_CONTEXT_DELIVERY': 'on', 'LOOPX_EXECUTION_MODE': 'heartbeat'} if native_context else {})}) as process:
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
                        if native_context:
                            assert list(context.iterdir()) == []  # no projection before release
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
                        if native_context:
                            assert list(context.iterdir()) == []
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


@pytest.mark.parametrize("root_source", ["saved", "explicit", "fallback"])
@pytest.mark.parametrize("output_limit", [None, 32000])
def test_saved_selection_transport_preserves_identity_and_rebinds_once(tmp_path, root_source, output_limit):
    from loopx.cli import build_parser
    from loopx.cli_commands.quota_capture import bind_capture_selection_transport

    saved, explicit, fallback, native = (tmp_path / name for name in
        ("saved captures", "explicit captures", "private cache", "native session"))
    words = ["quota", "should-run", "--goal-id", "goal", "--agent-id", "agent",
        "--todo-id", "{todo_id}", "--turn-instance-id", "original-turn", "--codex-app",
        "--context-delivery", "on", "--codex-context-root", str(tmp_path / "old native"), "--turn-envelope",
        "--max-page-output-bytes", "64000"]
    if root_source != "fallback":
        words += ["--decision-output-root", str(saved)]
    selection = {"command_args_template": shlex.join(words)}
    payload = {"interaction_contract": {"cli_channel": {"selection_command": selection}}}
    args = request(None, decision_output_root=explicit if root_source == "explicit" else None,
        context_delivery="on", codex_context_dir=str(native), turn_envelope=True,
        max_page_output_bytes=output_limit)
    bind_capture_selection_transport(payload, args, capture_root=fallback)
    first = selection["command_args_template"]
    bind_capture_selection_transport(payload, args, capture_root=fallback)
    assert selection["command_args_template"] == first
    argv = shlex.split(first)
    parsed = build_parser().parse_args(argv)
    assert (parsed.goal_id, parsed.agent_id, parsed.todo_id, parsed.turn_instance_id) == (
        "goal", "agent", "{todo_id}", "original-turn")
    assert parsed.decision_output_root == str({"saved": saved, "explicit": explicit, "fallback": fallback}[root_source])
    assert parsed.context_delivery == "on" and parsed.codex_context_dir == str(native)
    assert parsed.codex_context_root is None and parsed.turn_envelope
    assert parsed.max_page_output_bytes == (output_limit or 64000)
    assert all(argv.count(option) == 1 for option in
        ("--decision-output-root", "--context-delivery", "--codex-context-dir", "--turn-envelope",
         "--max-page-output-bytes"))
    assert not list(tmp_path.iterdir())


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
    initialize_canonical_source_fixture(runtime, GOAL_ID,
        state_path=project / f'.codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md')
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
    initialize_canonical_source_fixture(runtime, GOAL_ID,
        state_path=project / f'.codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md')
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
    # Binding a Todo does not discharge the original semantic replan. This
    # fixture has not admitted delivery or produced work/closeout evidence.
    # Preserve that boundary instead of inferring closeout from "upgraded".
    decision = observed['decision']
    receipt = decision['heartbeat_receipt']
    assert receipt.get('closeout_required') is not True
    assert decision['interaction_contract']['agent_channel']['delivery_allowed'] is False
    assert decision['replan_action_packet']['decision'] == 'replan_required'
    assert decision['replan_action_packet'].get('settlement_only') is not True
    assert observed['intent'] == {
        'capture': True,
        'obligation_id': receipt['semantic_replan_obligation_id'],
        'settlement_effect_id': receipt['settlement_identity']['effect_id'],
        'execution_authority_granted': False,
    }
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


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize('turn_envelope', [False, True])
def test_native_recovery_failure_keeps_original_receipt_and_capture(
    tmp_path, monkeypatch, capsys, provider, turn_envelope,
):
    from loopx.cli import main
    from loopx.control_plane.quota.error_codes import QuotaDecisionTransportError
    from loopx.control_plane.turn_driver import codex_context
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane.test_quota_settlement_cli import _spend_run_count

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = _configure_read_only_todo(project)
    initialize_canonical_source_fixture(runtime, GOAL_ID, state_path=state, provider=provider)
    original_state = state.read_bytes()
    context, captures = tmp_path / 'native', tmp_path / 'captures'
    context.mkdir(mode=0o700)
    captures.mkdir(mode=0o700)
    attempts = []

    def refuse_recovery(payload, **kwargs):
        saved = kwargs['capture_directory'] / 'decision.json'
        attempts.append(saved.read_bytes())
        if turn_envelope:
            assert payload['schema_version'] == 'loopx_turn_envelope_v0'
            assert 'heartbeat_receipt' not in payload
        raise QuotaDecisionTransportError(
            'Current context recovery could not be published or safely invalidated. '
            'Read the saved original decision/receipt; do not rerun admission.'
        )

    monkeypatch.setattr(codex_context, 'project_codex_context', refuse_recovery)
    args = ['--registry', str(registry), '--runtime-root', str(runtime), '--format', 'json',
        'quota', 'should-run', '--goal-id', GOAL_ID, '--agent-id', AGENT_ID,
        '--todo-id', TODO_ID, '--turn-instance-id', TURN_ID, '--codex-app',
        '--available-capability', 'shell', '--available-capability', 'filesystem_read',
        '--context-delivery', 'on', '--codex-context-dir', str(context),
        '--decision-output-root', str(captures)]
    if turn_envelope:
        args.append('--turn-envelope')
    code = main(args)
    failure = json.loads(capsys.readouterr().out)
    assert code == 1 and failure['ok'] is False
    assert failure['error_code'] == 'quota_decision_transport_failed'
    assert failure['status'] == 'decision_transport_failed'
    saved = list(captures.glob('*/decision.json'))
    assert len(saved) == len(attempts) == 1
    assert saved[0].read_bytes() == attempts[0]
    original = json.loads(attempts[0])
    assert original['ok'] is True
    assert failure['heartbeat_receipt'] == original['heartbeat_receipt']
    assert failure['rollout_event'] == original['rollout_event']
    assert failure['captured_decision'] == {'path': str(saved[0])}
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 1
    assert _spend_run_count(runtime) == 0
    assert state.read_bytes() == original_state


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
