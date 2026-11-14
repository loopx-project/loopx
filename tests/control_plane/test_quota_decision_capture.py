"""Capture is a transport readback, never a second guard or fresh admission."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shlex
import subprocess
from unittest.mock import patch

import pytest

from loopx.cli_commands.quota_capture import capture_decision, prepare_decision_capture
from loopx.control_plane.quota.error_codes import QuotaCommandValidationError
from tests.control_plane.host_process_fixture import assert_host_task_context, write_python_host_fixture
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_source_fixture, read_fixture_todos,
)
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, _write_fixture,
    _configure_read_only_todo, _run_cli, _heartbeat_receipt_count, _spend_run_count,
)


def request(path, **overrides):
    return argparse.Namespace(**dict(
        {'decision_output_dir': path, 'quota_command': 'should-run',
         'turn_instance_id': TURN_ID}, **overrides))


@pytest.mark.parametrize("overrides", [
    {"turn_instance_id": None}, {"quota_command": "plan"}, {"format": "markdown"},
    {"turn_envelope": True}, {"decision_handoff_socket": "/unused/socket"},
    {"context_delivery": "on", "codex_app": True},
])
def test_prepared_reference_rejects_incompatible_delivery_before_guard(tmp_path, overrides):
    from loopx.cli_commands.quota_capture import prepare_quota_capture

    values = {"format": "json", "context_delivery": "off", **overrides}
    args = request(tmp_path / "capture", prepared_capture_reference=True, **values)
    with pytest.raises(QuotaCommandValidationError, match="prepared capture reference"):
        prepare_quota_capture(args, None)
    assert list(tmp_path.iterdir()) == []


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
def test_native_refusal_is_bounded_without_pending_context_or_admission(tmp_path, monkeypatch, provider):
    import hashlib
    from loopx.control_plane.turn_driver.codex_context import codex_context_hook
    from tests.control_plane.test_quota_settlement_cli import (
        _configure_selectable_alternative, _initialize_fixture_authority, _spend_run_count,
    )

    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    _initialize_fixture_authority(project, runtime, provider=provider)
    capture, native = tmp_path / 'refused-capture', tmp_path / 'native-session'
    native.mkdir(mode=0o700)
    monkeypatch.setenv('CODEX_THREAD_ID', 'refusal-session')
    monkeypatch.delenv('CODEX_ROLLOUT_TRACE_ROOT', raising=False)
    codex_context_hook(str(native), dict(session_id='refusal-session', cwd=str(project),
        hook_event_name='SessionStart', source='startup'))
    journal = (native / 'session.json').read_bytes()
    guard = ['quota', 'should-run', '--goal-id', GOAL_ID, '--agent-id', AGENT_ID,
             '--todo-id', 'todo_missing', '--turn-instance-id', TURN_ID, '--codex-app']
    rc, refused = _run_cli(registry, runtime, *guard, '--decision-output-dir', str(capture),
        '--context-delivery', 'on', '--codex-context-dir', str(native), '--max-page-output-bytes', '4096')
    raw = (capture / 'decision.json').read_bytes()
    original = json.loads(raw)
    assert rc == 1 and refused['ok'] is False
    assert len(raw) > 4096
    assert len(json.dumps(refused, ensure_ascii=False, separators=(',', ':')).encode()) + 1 <= 4096
    assert refused['error_code'] == original['error_code'] == 'quota_action_selection_rejected'
    assert refused['reason'] == original['reason']
    assert refused['heartbeat_receipt'] == original['heartbeat_receipt']
    assert refused['next_cli_actions'] == original['interaction_contract']['cli_channel']['next_cli_actions']
    assert refused['captured_decision']['sha256'] == hashlib.sha256(raw).hexdigest()
    assert (native / 'session.json').read_bytes() == journal
    assert _heartbeat_receipt_count(runtime, TURN_ID) == _spend_run_count(runtime) == 0
    # Explicitly disabled delivery preserves the ordinary failure view.
    rc, disabled = _run_cli(registry, runtime, *guard, '--context-delivery', 'off')
    assert rc == 1 and disabled['error_code'] == refused['error_code']
    assert 'action_selection_qualification' in disabled and 'captured_decision' not in disabled


def test_large_refusal_diagnostics_remain_reachable_by_selected_read(tmp_path):
    from loopx.cli_commands.quota_failure_report import captured_quota_failure_view
    from loopx.cli_commands.quota_capture import read_context_capture

    payload = {'ok': False, 'reason': '必要的诊断证据' * 10000,
               'heartbeat_receipt': {'status': 'not_committed'},
               'unrelated_history': 'optional history ' * 10000}
    capture_decision(tmp_path, payload)
    result = captured_quota_failure_view(payload, capture_directory=tmp_path, max_page_output_bytes=4096)
    assert result['ok'] is False and len(json.dumps(result).encode()) < 4096
    from loopx.cli import build_parser

    command = result['command']
    content = []
    while command:
        tokens = shlex.split(command)
        args = build_parser().parse_args(tokens[tokens.index('--format'):])
        page = read_context_capture(args)
        assert page['ok'], page
        content.append(page['content'])
        command = page.get('next_command')
    selected = {item['pointer']: item['value'] for item in json.loads(''.join(content))['selections']}
    assert selected['/reason'] == payload['reason']
    assert '/unrelated_history' not in selected


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
            '--todo-id', TODO_ID, '--turn-instance-id', TURN_ID, '--runtime-profile', 'generic_cli',
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
    assert 'schema_version' not in ordinary and 'contract_binding' not in full
    assert 'blocked_priority_fallback' not in ordinary['heartbeat_recommendation']
    assert ordinary['heartbeat_recommendation']['blocked_priority_fallback_ref'] == '$.blocked_priority_fallback'
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
        empty_todo_display, initialize_canonical_authority, isolate_sqlite_runtime,
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
    # Keep work in its canonical provider and the full non-Todo Goal readable
    # through the ordinary source reader before strict Host dispatch.
    empty_todo_display(state)
    initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state, provider=provider)
    args = ['quota', 'should-run', '--goal-id', GOAL_ID, '--agent-id', AGENT_ID,
            '--todo-id', TODO_ID, '--turn-instance-id', TURN_ID, '--codex-app',
            '--available-capability', 'shell', '--available-capability', 'filesystem_read']
    capture = tmp_path / 'boundary-decision'
    code, envelope = _run_cli(registry, runtime, *args, '--complete-turn', '--decision-output-dir', str(capture))
    assert code == 0, envelope
    saved = capture / 'decision.json'
    original_bytes = saved.read_bytes()
    full = json.loads(original_bytes)
    assert full['goal_boundary']['write_scope'] == scopes
    assert full['goal_boundary']['guards'] == guards
    assert envelope == full
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope
    envelope = build_turn_envelope(full)
    for field in ('write_scope', 'guards', 'requires_parent_approval'):
        assert envelope['boundary'][field] == full['goal_boundary'][field], field
    authority = extract_turn_authority({'work_contract': full, 'turn_envelope': envelope})
    assert authority['goal_boundary'] == full['goal_boundary']
    # Historical requests still decode scope aliases. The current model view
    # preserves the same authority facts from the checked original source.
    historical = extract_turn_authority({'turn_envelope': envelope})
    assert historical['write_scope'] == scopes
    assert historical['boundary']['guards'] == guards
    assert historical['boundary']['requires_parent_approval'] == ['publish', 'production-action']
    assert 'write_scope' not in historical['boundary']
    prompt = render_prompt(authority)
    transported, _ = json.JSONDecoder().raw_decode(prompt.split('Turn request:\n', 1)[1])
    context = assert_host_task_context(transported, full)
    for field in ('write_scope', 'guards', 'requires_parent_approval'):
        assert context['goal_boundary'][field] == full['goal_boundary'][field], field
    altered = json.loads(json.dumps(envelope))
    altered['boundary']['guards'][-1] = 'Continue without the final restriction.'
    with pytest.raises(ValueError, match='complete Turn contract rejected'):
        extract_turn_authority({'work_contract': full, 'turn_envelope': altered})
    with pytest.raises(ValueError, match='action signature'):
        extract_turn_authority({'turn_envelope': altered})
    before = _heartbeat_receipt_count(runtime, TURN_ID)
    code, replay = _run_cli(registry, runtime, *args)
    assert code == 0, replay
    assert _heartbeat_receipt_count(runtime, TURN_ID) == before
    assert _spend_run_count(runtime) == 0
    assert saved.read_bytes() == original_bytes
    assert envelope['action']['delivery_allowed'] == full['interaction_contract']['agent_channel']['delivery_allowed']


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("projection_fails", [False, True])
def test_explicit_off_turn_envelope_preserves_bounded_view_and_complete_capture(
    tmp_path, monkeypatch, provider, projection_fails,
):
    from loopx.cli import main
    from loopx.control_plane.quota import turn_envelope
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane.test_quota_settlement_cli import _initialize_fixture_authority, _spend_run_count

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_read_only_todo(project)
    _initialize_fixture_authority(project, runtime, provider=provider)
    monkeypatch.chdir(project)
    bindings = ["--registry", str(registry), "--runtime-root", str(runtime)]
    guard = ["quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
             "--turn-instance-id", TURN_ID, "--codex-app", "--context-delivery", "off", "--turn-envelope"]

    from contextlib import redirect_stdout
    from io import StringIO

    if projection_fails:
        compose = turn_envelope.compose_turn_decision_with_raw_envelope

        def reject_view(payload, **options):
            # The typed compatibility projector rejects this target; the
            # original complete decision is still valid and must be saved.
            return compose(payload, **{**options, "captured_decision_path": ""})

        monkeypatch.setattr(turn_envelope, "compose_turn_decision_with_raw_envelope", reject_view)

    def deliver(output_format, *extra, expected=0):
        output = StringIO()
        with redirect_stdout(output):
            assert main([*bindings, "--format", output_format, *guard, *extra]) == expected
        return output.getvalue()

    directory = tmp_path / "complete-capture"
    output = deliver("json", "--decision-output-dir", str(directory), expected=int(projection_fails))
    raw = (directory / "decision.json").read_bytes()
    source = json.loads(raw)
    turn_envelope.assert_complete_turn_decision(source)
    shown = json.loads(output)
    if projection_fails:
        from hashlib import sha256

        assert shown["error_code"] == "quota_decision_transport_failed"
        assert shown["heartbeat_receipt"] == source["heartbeat_receipt"]
        reference = shown["captured_decision"]
        assert reference["path"] == str(directory / "decision.json")
        assert reference["sha256"] == sha256(raw).hexdigest()
        _assert_captured_receipts(reference["command"], source, cwd=project, env=os.environ, output_limit=64_000)
        assert _heartbeat_receipt_count(runtime, TURN_ID) == 1
        assert _spend_run_count(runtime) == 0
        return
    assert shown["schema_version"] == "loopx_turn_envelope_v0"
    assert shown["contract_capsule"] and shown["action_signature"]["matches"] is True
    assert shown["action_signature"]["source_decision_hash"] == source["contract_binding"]["content_hash"]
    identity = source["interaction_contract"]["cli_channel"]["settlement_plan"]["identity"]
    assert identity["turn_instance_id"] == TURN_ID and identity["todo_id"] == TODO_ID
    assert shown["action"]["selected_todo"]["todo_id"] == TODO_ID
    assert shlex.split(shown["detail_ref"]["full_decision"]) == ["cat", "--", str(directory / "decision.json")]
    before = _heartbeat_receipt_count(runtime, TURN_ID)
    markdown = deliver("markdown")
    assert markdown.startswith("# LoopX Turn Envelope")
    assert "effective_action: `normal_run`" in markdown
    assert _heartbeat_receipt_count(runtime, TURN_ID) == before == 1
    assert _spend_run_count(runtime) == 0
    assert (directory / "decision.json").read_bytes() == raw


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
    assert full['schema_version'] == 'loopx_turn_envelope_v1'
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

    assert fresh['action_signature']['source_decision_hash'] == json.loads(
        (second / 'decision.json').read_text())['contract_binding']['content_hash']
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


def test_full_display_preserves_the_actual_capture_source(tmp_path):
    from loopx.cli_commands.quota import _project_quota_cli_payload
    from loopx.cli_commands.quota_capture import capture_decision
    from tests.test_turn_envelope import _full_decision
    args = argparse.Namespace(turn_envelope=False, quota_command='should-run')
    from loopx.control_plane.quota.turn_envelope import compose_turn_decision
    source = compose_turn_decision(_full_decision())
    capture_decision(tmp_path, source)
    raw = (tmp_path / 'decision.json').read_bytes()
    captured = json.loads(raw)
    assert _project_quota_cli_payload(captured, args, frozenset()) == source
    assert (tmp_path / 'decision.json').read_bytes() == raw


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
@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize(('release', 'native_context', 'output_limit', 'capture_path_bytes'), [
    pytest.param(release, native, 1700 if native and release != 'exact' else None, None,
                 id=f"{'native' if native else 'ordinary'}-{release}")
    for native in (False, True)
    for release in ('exact', 'wrong_digest', 'disconnect', 'changed_capture')
] + [
    pytest.param('changed_capture', True, 720, 426, id='bounded-long-capture'),
    pytest.param('changed_capture', True, 671, 423, id='tight-long-capture'),
    pytest.param('changed_capture', True, 720, 482, id='tight-deep-capture'),
    pytest.param('changed_capture', True, 600, 426, id='insufficient-inline-metadata-budget'),
])
def test_real_cli_handoff_preserves_original_receipt(
    tmp_path, provider, release, native_context, output_limit, capture_path_bytes,
):
    import hashlib
    import socket
    import sys
    import tempfile
    from pathlib import Path
    from loopx.control_plane.quota.turn_envelope import assert_complete_turn_decision

    project, runtime, registry = _write_fixture(tmp_path)
    state = _configure_read_only_todo(project)
    initialize_canonical_source_fixture(runtime, GOAL_ID, state_path=state, provider=provider)
    capture = tmp_path / 'handoff-capture'
    if capture_path_bytes:
        # Keep each path component below filesystem limits. Non-ASCII text
        # also catches counting characters instead of serialized UTF-8 bytes.
        remainder = capture_path_bytes - len(os.fsencode(tmp_path / 'decision.json')) - 2
        parent = tmp_path / ('测' * (remainder // 6))
        parent.mkdir()
        capture = parent / ('c' * (remainder - len(os.fsencode(parent.name))))
        assert len(os.fsencode(capture / 'decision.json')) == capture_path_bytes
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
            if output_limit is not None:
                command += ['--max-page-output-bytes', str(output_limit)]
            env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2])}
            if native_context:
                env.update(LOOPX_CONTEXT_DELIVERY='on', LOOPX_EXECUTION_MODE='heartbeat')
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, env=env) as process:
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
                        assert receipts == 1 and process.poll() is None
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
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()

    original_source = json.loads(original)
    assert original_source['heartbeat_receipt']['turn_instance_id'] == TURN_ID
    assert_complete_turn_decision(original_source)
    if release == 'exact':
        assert process.returncode == 0 and json.loads(stdout)['ok'] is True
        assert not stderr
    else:
        assert process.returncode != 0
        if native_context:
            assert not list(context.iterdir())
        if output_limit is None:
            assert 'Do not blindly rerun' in stderr + stdout
        else:
            assert len(stdout.encode()) <= output_limit
            failure = json.loads(stdout)
            assert failure['error_code'] == 'quota_decision_transport_failed'
            if release == 'changed_capture':
                if output_limit == 600:
                    # If a long locator cannot fit, the command reads private
                    # diagnostics with the original receipt, not changed bytes.
                    assert 'captured_decision' not in failure
                    failure = _read_bounded_handoff_reply(failure['command'], cwd=project, env=env,
                                                          output_limit=output_limit)
                    assert failure['heartbeat_receipt'] == original_source['heartbeat_receipt']
                reference = failure['captured_decision']
                assert reference == {'path': str(capture / 'decision.json'), 'sha256': digest}
                assert 'Do not rerun admission.' in failure['instruction']
                assert 'Restore' in failure['instruction'] and 'receipt' in failure['instruction']
                assert 'command' not in failure and 'command' not in reference
                assert (capture / 'decision.json').read_bytes() == original + b'\n'
                if capture_path_bytes:
                    # The direct reader still refuses the changed bytes. Restoring
                    # the exact original file recovers its receipt without a guard.
                    read_original = [sys.executable, '-m', 'loopx.cli', '--format', 'json', 'quota', 'context-read', '--capture-file', reference['path'],
                                     '--capture-sha256', reference['sha256'], '--select', '/heartbeat_receipt',
                                     '--max-page-output-bytes', '4096']
                    rejected = subprocess.run(read_original, cwd=project, env=env, capture_output=True, timeout=30)
                    assert rejected.returncode == 1 and 'digest mismatch' in json.loads(rejected.stdout)['reason']
                    (capture / 'decision.json').write_bytes(original)
                    recovered = _read_bounded_handoff_reply(shlex.join(read_original), cwd=project, env=env,
                                                            output_limit=4096)
                    assert recovered['heartbeat_receipt'] == original_source['heartbeat_receipt']
            else:
                reference = failure['captured_decision']
                assert reference['sha256'] == digest
                recovery = failure.get('command') or reference['command']
                assert (capture / 'decision.json').read_bytes() == original
                if release == 'wrong_digest':
                    _assert_captured_receipts(
                        recovery, original_source, cwd=project, env=env,
                        output_limit=failure.get('recovery_page_output_bytes', output_limit),
                    )
    assert _heartbeat_receipt_count(runtime, TURN_ID) == receipts
    assert _spend_run_count(runtime) == 0


def _assert_captured_receipts(command, source, *, cwd, env, output_limit):
    recovered = _read_bounded_handoff_reply(command, cwd=cwd, env=env, output_limit=output_limit)
    assert recovered['heartbeat_receipt'] == source['heartbeat_receipt']
    assert recovered['rollout_event'] == source['rollout_event']


def _read_bounded_handoff_reply(command, *, cwd, env, output_limit):
    pages = bytearray()
    while command:
        run = subprocess.run(shlex.split(command), cwd=cwd, env=env, capture_output=True,
                             check=True, timeout=30)
        assert len(run.stdout) <= output_limit
        page = json.loads(run.stdout)
        assert page['ok'] and page['offset'] == len(pages)
        pages.extend(page['content'].encode())
        if page['eof']:
            assert len(pages) == page['total_bytes']
            break
        assert page['content'] and page['next_offset'] == len(pages)
        command = page['next_command']
    else:
        pytest.fail('recovery reader did not reach EOF')
    recovered = json.loads(pages)
    if 'selections' in recovered:
        recovered = {row['pointer'].lstrip('/'): row['value'] for row in recovered['selections']}
    return recovered


@pytest.mark.skipif(os.name != "posix", reason="Exercises the POSIX release launcher and Host socket")
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_released_guard_failure_reads_through_its_own_launcher(tmp_path, monkeypatch, provider):
    import socket
    import sys
    import tempfile
    from pathlib import Path

    from tests.control_plane.test_quota_settlement_cli import _initialize_fixture_authority, _spend_run_count

    project, runtime, registry = _write_fixture(tmp_path)
    _configure_read_only_todo(project)
    _initialize_fixture_authority(project, runtime, provider=provider)
    release = Path(__file__).resolve().parents[2] / "scripts" / "loopx"
    wrong_bin = tmp_path / "old runtime"
    wrong_bin.mkdir()
    wrong_cli = wrong_bin / "loopx"
    wrong_cli.write_text("#!/bin/sh\nexit 97\n")
    wrong_cli.chmod(0o755)
    monkeypatch.setenv("PATH", str(wrong_bin) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("LOOPX_PYTHON", sys.executable)
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")

    capture = tmp_path / "original"
    with tempfile.TemporaryDirectory(prefix="lx-host-", dir="/tmp") as directory:
        endpoint = Path(directory).resolve() / "host.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(endpoint))
            listener.listen(1)
            listener.settimeout(20)
            args = [str(release), "--format", "json", "--registry", str(registry),
                    "--runtime-root", str(runtime), "quota", "should-run", "--goal-id", GOAL_ID,
                    "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID,
                    "--context-delivery", "off", "--decision-output-dir", str(capture),
                    "--decision-handoff-socket", str(endpoint)]
            with subprocess.Popen(args, cwd=project, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) as process:
                try:
                    channel, _ = listener.accept()
                    with channel, channel.makefile("rb") as stream:
                        request = json.loads(stream.readline())
                        assert request["sha256"] == sha256((capture / "decision.json").read_bytes()).hexdigest()
                        channel.sendall(b'{"released":false}\n')
                    stdout, stderr = process.communicate(timeout=20)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
    assert process.returncode == 1, (stdout, stderr)
    failure = json.loads(stdout)
    original = (capture / "decision.json").read_bytes()
    reader = shlex.split(failure["captured_decision"]["command"])
    page = subprocess.run([*reader, "--select", "/heartbeat_receipt"], cwd=project,
                          capture_output=True, text=True, timeout=30)
    assert page.returncode == 0, (reader, page.stdout, page.stderr)
    assert reader[0] == str(release)
    assert json.loads(json.loads(page.stdout)["content"])["selections"][0]["value"] == json.loads(original)["heartbeat_receipt"]
    assert (capture / "decision.json").read_bytes() == original
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 1 and _spend_run_count(runtime) == 0


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
            bind_capture_selection_transport(payload, request(None, decision_handoff_socket=str(endpoint)),
                                             capture_directory=tmp_path / 'first-decision')
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


def _replan_selection_hop(tmp_path, host, *, short_view):
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
              '--turn-instance-id', TURN_ID, '--context-delivery', 'off', '--available-capability', 'shell',
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
                '--todo-id', SELECTED_REPLAN_TODO_ID,
                '--turn-envelope' if short_view else '--complete-turn',
                '--decision-output-root', str(capture_root),
                '--decision-handoff-socket', str(endpoint), cwd=project)
            thread.join(10)
    return code, selected, observed, sorted(capture_root.glob('*/decision.json')), runtime


@pytest.mark.skipif(os.name != 'posix', reason='Unix local Host transport')
@pytest.mark.parametrize('short_view', [False, True], ids=['complete', 'short'])
def test_selection_hop_on_committed_replan_turn_returns_committed_result(tmp_path, short_view):
    from loopx.hosts.research_checkpoint import select_checkpoint_replan

    def typed_host(observed):
        # The Host consults the existing typed selector before releasing.
        observed['intent'] = select_checkpoint_replan(observed['decision'],
            goal_id=GOAL_ID, agent_id=AGENT_ID, turn_instance_id=TURN_ID)
        return True

    code, selected, observed, saved, _ = _replan_selection_hop(tmp_path, typed_host, short_view=short_view)
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
    if short_view:
        assert selected['schema_version'] == 'loopx_turn_envelope_v0'
        assert selected['detail_ref']['captured_decision']['path'] == str(saved[0])
        assert selected['action_signature']['matches'] is True
        assert selected['action_signature']['source_decision_hash'] == decision['contract_binding']['content_hash']
    else:
        assert selected['schema_version'] == 'loopx_turn_envelope_v1'
        assert selected == observed['decision']
    assert len(saved) == 1 and json.loads(saved[0].read_text()) == observed['decision']


@pytest.mark.skipif(os.name != 'posix', reason='Unix local Host transport')
@pytest.mark.parametrize('short_view', [False, True], ids=['complete', 'short'])
def test_refused_selection_handoff_is_typed_and_keeps_the_committed_capture(tmp_path, short_view):
    from hashlib import sha256

    code, selected, observed, saved, runtime = _replan_selection_hop(
        tmp_path, lambda _: False, short_view=short_view)
    assert code == 1 and selected['ok'] is False
    # Never a generic collection failure that replaces the original capture.
    assert selected['error_code'] == 'quota_decision_transport_failed', selected
    assert selected['status'] == 'decision_transport_failed'
    assert 'Do not blindly rerun the guard' in selected['reason']
    assert selected['heartbeat_receipt']['turn_instance_id'] == TURN_ID
    assert len(saved) == 1
    assert selected['captured_decision']['path'] == str(saved[0])
    assert selected['captured_decision']['sha256'] == sha256(saved[0].read_bytes()).hexdigest()
    assert json.loads(saved[0].read_text()) == observed['decision']
    assert observed['decision']['ok'] is True
    assert observed['decision']['schema_version'] == 'loopx_turn_envelope_v1'
    assert selected['heartbeat_receipt'] == observed['decision']['heartbeat_receipt']
    assert _heartbeat_receipt_count(runtime, TURN_ID) >= 1


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize('turn_envelope', [False, True])
@pytest.mark.parametrize('output_limit', [None, 1700])
def test_native_recovery_failure_keeps_original_receipt_and_capture(
    tmp_path, monkeypatch, capsys, provider, turn_envelope, output_limit,
):
    import hashlib

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
        assert payload['schema_version'] == 'loopx_turn_envelope_v1'
        assert payload == json.loads(attempts[-1])
        assert payload['heartbeat_receipt']['turn_instance_id'] == TURN_ID
        raise QuotaDecisionTransportError(
            'Current context recovery could not be published or safely invalidated. '
            'Read the saved original decision/receipt; do not rerun admission.'
        )

    monkeypatch.setattr(codex_context, 'prepare_codex_context', refuse_recovery)
    args = ['--registry', str(registry), '--runtime-root', str(runtime), '--format', 'json',
        'quota', 'should-run', '--goal-id', GOAL_ID, '--agent-id', AGENT_ID,
        '--todo-id', TODO_ID, '--turn-instance-id', TURN_ID, '--codex-app',
        '--available-capability', 'shell', '--available-capability', 'filesystem_read',
        '--context-delivery', 'on', '--codex-context-dir', str(context),
        '--decision-output-root', str(captures)]
    args.append('--turn-envelope' if turn_envelope else '--complete-turn')
    if output_limit is not None:
        args += ['--max-page-output-bytes', str(output_limit)]
        parent = tmp_path / ('package-' + 'x' * 180)
        parent.mkdir()
        launcher = write_python_host_fixture(parent / 'loopx', '#!/usr/bin/env python3\n'
            'from loopx.cli import main\nraise SystemExit(main())\n')
        args[:0] = ['--cli-bin', str(launcher)]
    code = main(args)
    output = capsys.readouterr().out
    failure = json.loads(output)
    assert code == 1 and failure['ok'] is False
    assert failure['error_code'] == 'quota_decision_transport_failed'
    saved = list(captures.glob('*/decision.json'))
    assert len(saved) == len(attempts) == 1
    assert saved[0].read_bytes() == attempts[0]
    original = json.loads(attempts[0])
    assert original['ok'] is True
    if output_limit is None:
        assert failure['status'] == 'decision_transport_failed'
        assert failure['heartbeat_receipt'] == original['heartbeat_receipt']
        assert failure['rollout_event'] == original['rollout_event']
        assert failure['captured_decision']['path'] == str(saved[0])
        assert failure['captured_decision']['sha256'] == hashlib.sha256(attempts[0]).hexdigest()
    else:
        assert len(output.encode()) <= output_limit
        reference = failure['captured_decision']
        assert reference['sha256'] == hashlib.sha256(attempts[0]).hexdigest()
        command = failure.get('command') or reference['command']
        assert shlex.split(command)[:3] == [str(launcher), '--cli-bin', str(launcher)]
        _assert_captured_receipts(command, original, cwd=project,
            env={**os.environ, 'LOOPX_USAGE_PING': '0', 'PYTHONPATH': str(Path(__file__).resolve().parents[2])},
            output_limit=failure.get('recovery_page_output_bytes', output_limit))
        assert saved[0].read_bytes() == attempts[0]
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
