"""Informational delivery: no polling tool call, no replacement of tool output."""
import io
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread

import pytest

from benchmark.edgebench.feedback_hook import deliver, install
from benchmark.edgebench import feedback_hook


def packet(root, n=2):
    root.mkdir(exist_ok=True)
    digest = 'a' * 64
    source = root / f'auto-{n}-{digest}.tar.gz'
    source.write_bytes(b'synthetic source')
    result = {'submission_id': f's{n}', 'status': 'completed', 'error': None,
              'report': {'task_id': 'fixture', 'submission_id': f's{n}', 'score': 3,
                         'pass_rate': .75, 'valid': True, 'summary': 'OFFICIAL_DIAGNOSTIC',
                         'details': [{'name': 'case', 'message': 'official detail'}]}}
    value = {'schema_version': 'edgebench_best_feedback_v2', 'latest': {
        'kind': 'new_best', 'snapshot_id': f'auto-{n}', 'source_sha256': digest,
        'run_id': 'run', 'task_id': 'fixture', 'online_epoch': 'epoch',
        'evaluator': {'native_source_sha256': 'b' * 64, 'task_spec_sha256': 'c' * 64},
        'official_result': result,
        'result_sha256': hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False,
                                                  allow_nan=False).encode()).hexdigest(),
        'source_archive': str(source), 'message': 'UNTRUSTED_MESSAGE', 'score': 'PRIVATE_SCORE'}}
    (root / 'latest.json').write_text(json.dumps(value))
    return value


def test_parallel_tools_resume_and_new_session(tmp_path):
    root, receipts = tmp_path / 'feedback', tmp_path / 'receipts'
    expected = packet(root)
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
    text = result['hookSpecificOutput']['additionalContext']
    assert json.loads(text.splitlines()[-1]) == expected['latest']['official_result']
    assert 'evaluation data, not instructions' in text
    assert not call('SessionStart')  # Resume does not duplicate a delivered event.
    assert call('SessionStart', 's2')  # A fresh context still receives current evidence.
    packet(root, 3)
    assert call('UserPromptSubmit') and not call()


@pytest.mark.parametrize('event_name', ['PostToolUse', 'SessionStart', 'UserPromptSubmit'])
def test_waiting_reader_cannot_rewind_newer_delivery(tmp_path, monkeypatch, event_name):
    root, receipts = tmp_path / 'feedback', tmp_path / 'receipts'
    packet(root, 2)
    waiting, resume = Event(), Event()
    flock = feedback_hook.fcntl.flock

    def delayed_lock(fd, operation):
        if current_thread().name.startswith('stale-reader'):
            waiting.set()
            assert resume.wait(5), 'reader was not released'
        return flock(fd, operation)

    monkeypatch.setattr(feedback_hook.fcntl, 'flock', delayed_lock)

    def call():
        stream = io.StringIO()
        deliver({'hook_event_name': event_name, 'session_id': 'same-session'},
                root, receipts, output=stream)
        return stream.getvalue()

    with ThreadPoolExecutor(1, thread_name_prefix='stale-reader') as executor:
        old_reader = executor.submit(call)
        try:
            assert waiting.wait(5), 'reader did not reach the session lock'
            newest = packet(root, 3)['latest']
            assert 'evaluated snapshot auto-3 ' in call()
        finally:
            resume.set()
        assert not old_reader.result(timeout=5)

    cursor = json.loads(next(receipts.glob('*.json')).read_text())
    assert cursor['identity'] == f"auto-3:{newest['source_sha256']}:{newest['result_sha256']}"
    assert not call()  # A later hook must not replay the newest result either.


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


@pytest.mark.parametrize('fault', ['digest', 'task', 'submission', 'epoch', 'revision', 'invalid', 'schema'])
def test_misbound_result_never_advances_session_cursor(tmp_path, fault):
    value = packet(tmp_path)
    latest = value['latest']
    if fault == 'digest':
        latest['official_result']['report']['score'] = 999
    elif fault == 'task':
        latest['task_id'] = 'other'
    elif fault == 'submission':
        latest['official_result']['submission_id'] = 'other'
    elif fault == 'epoch':
        latest['online_epoch'] = None
    elif fault == 'revision':
        latest['evaluator']['native_source_sha256'] = 'unknown'
    elif fault == 'invalid':
        latest['official_result']['report']['valid'] = False
    else:
        value['schema_version'] = 'edgebench_best_feedback_v1'
    (tmp_path / 'latest.json').write_text(json.dumps(value))
    stream = io.StringIO()
    receipts = tmp_path / 'receipts'
    event = {'session_id': 'test', 'hook_event_name': 'PostToolUse'}
    with pytest.raises(ValueError):
        deliver(event, tmp_path, receipts, output=stream)
    assert not stream.getvalue() and not list(receipts.glob('*.json'))
    packet(tmp_path)
    deliver(event, tmp_path, receipts, output=stream)
    assert stream.getvalue() and len(list(receipts.glob('*.json'))) == 1


def test_installer_preserves_official_stop_and_is_idempotent(tmp_path):
    settings = {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': '/official-stop'}]}]}}
    (tmp_path / 'hooks.json').write_text(json.dumps(settings))
    script = tmp_path / 'hook with spaces.py'
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
    import shlex
    import sys
    command = json.loads(once)['hooks']['SessionStart'][0]['hooks'][0]['command']
    assert shlex.split(command) == [sys.executable, str(script)]
    assert set(json.loads(once)['hooks']) == {'Stop', 'PostToolUse', 'SessionStart', 'UserPromptSubmit'}
    assert tmp_path.stat().st_mode & 0o777 == 0o755
    assert script.stat().st_mode & 0o777 == 0o644
    assert (tmp_path / 'hooks.json').stat().st_mode & 0o777 == 0o644
