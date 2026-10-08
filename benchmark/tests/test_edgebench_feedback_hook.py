"""Informational delivery: no polling tool call, no replacement of tool output."""
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from benchmark.edgebench.feedback_hook import deliver, install


def packet(root, n=2):
    root.mkdir(exist_ok=True)
    digest = 'a' * 64
    source = root / f'auto-{n}-{digest}.tar.gz'
    source.write_bytes(b'synthetic source')
    value = {'schema_version': 'edgebench_best_feedback_v1', 'latest': {
        'kind': 'new_best', 'snapshot_id': f'auto-{n}', 'source_sha256': digest,
        'source_archive': str(source), 'message': 'UNTRUSTED_MESSAGE', 'score': 'PRIVATE_SCORE'}}
    (root / 'latest.json').write_text(json.dumps(value))
    return value


def test_parallel_tools_resume_and_new_session(tmp_path):
    root, receipts = tmp_path / 'feedback', tmp_path / 'receipts'
    packet(root)
    def call(name='PostToolUse', session='s1'):
        stream = io.StringIO()
        deliver({'hook_event_name': name, 'session_id': session}, root, receipts, output=stream)
        return stream.getvalue()
    with ThreadPoolExecutor(8) as executor:
        outputs = list(executor.map(lambda _: call(), range(8)))
    assert sum(bool(item) for item in outputs) == 1
    result = json.loads(next(item for item in outputs if item))
    assert set(result) == {'hookSpecificOutput'}
    assert 'auto-2' in result['hookSpecificOutput']['additionalContext']
    assert 'PRIVATE_SCORE' not in str(result) and 'UNTRUSTED_MESSAGE' not in str(result)
    assert not call('SessionStart')  # Resume does not duplicate a delivered event.
    assert call('SessionStart', 's2')  # A fresh context still receives current evidence.
    packet(root, 3)
    assert call('UserPromptSubmit') and not call()


def test_empty_and_malformed_do_not_disclose(tmp_path):
    value = packet(tmp_path)
    event = {'session_id': 'test', 'hook_event_name': 'PostToolUse'}
    (tmp_path / 'latest.json').write_text(json.dumps(value | {'latest': None}))
    output = io.StringIO()
    deliver(event, tmp_path, tmp_path / 'receipts', output=output)
    assert not output.getvalue()
    value['latest']['snapshot_id'] = '../private'
    (tmp_path / 'latest.json').write_text(json.dumps(value))
    with pytest.raises(ValueError):
        deliver(event, tmp_path, tmp_path / 'receipts', output=output)
    assert not output.getvalue()


def test_installer_preserves_official_stop_and_is_idempotent(tmp_path):
    settings = {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': '/official-stop'}]}]}}
    (tmp_path / 'hooks.json').write_text(json.dumps(settings))
    script = tmp_path / 'hook.py'
    script.write_text('# synthetic provider hook')
    script.chmod(0o600)
    previous_umask = os.umask(0o077)
    try:
        install(tmp_path, script)
    finally:
        os.umask(previous_umask)
    once = (tmp_path / 'hooks.json').read_text()
    install(tmp_path, script)
    assert (tmp_path / 'hooks.json').read_text() == once
    assert json.loads(once)['hooks']['Stop'] == settings['hooks']['Stop']
    assert set(json.loads(once)['hooks']) == {'Stop', 'PostToolUse', 'SessionStart', 'UserPromptSubmit'}
    assert tmp_path.stat().st_mode & 0o777 == 0o755
    assert script.stat().st_mode & 0o777 == 0o644
    assert (tmp_path / 'hooks.json').stat().st_mode & 0o777 == 0o644
