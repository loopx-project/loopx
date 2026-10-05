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


def test_real_guard_saved_before_envelope_and_read_without_reexecution(tmp_path):
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_read_only_todo(project)
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
