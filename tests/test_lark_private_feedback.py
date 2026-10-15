"""Native state drives presentation; provider receipts never own admission."""
import json
import threading
import time

import pytest
from test_chat_ordinary_project import ordinary  # noqa: F401
from test_lark_private_conversations import connect, connect_group
from test_native_steward_private import steward  # noqa: F401

from loopx.extensions.lark.private_conversations import LarkPrivateConversations


def active(store, row):
    deadline = time.monotonic() + 10
    while store.load_session(row['session_id']).get('active_turn_id') != row['turn_id']:
        assert time.monotonic() < deadline
        time.sleep(.01)


@pytest.mark.parametrize('group', [False, True])
def test_durable_admission_feedback_precedes_blocked_execution_recovery(ordinary, monkeypatch, group):  # noqa: F811
    store, runtime, provider, transport = (connect_group if group else connect)(ordinary)
    entered, received, release = threading.Event(), threading.Event(), threading.Event()
    failures = []
    recover = transport.core.recover
    feedback = transport._feedback

    def acknowledged(*args, **kwargs):
        feedback(*args, **kwargs)
        received.set()

    def blocked_recovery(**kwargs):
        entered.set()
        assert release.wait(10), 'test did not release execution recovery'
        recover(**kwargs)

    def reconcile():
        try:
            transport.reconcile_request(path)
        except BaseException as exc:
            failures.append(exc)

    worker = None
    try:
        event = (provider.topic('receipt_before_recovery', 'ordinary work') if group else
                 provider.event('notes-app', 'receipt_before_recovery', 'ordinary work'))
        assert transport.admit('notes-app', event)['status'] == 'durably_accepted'
        row = transport.core.pending()[0]
        runtime.wait_for_turn(session_id=row['session_id'], turn_id=row['turn_id'], timeout_sec=10)
        path = transport.root / f"{row['request_ref']}.json"
        monkeypatch.setattr(transport.core, 'recover', blocked_recovery)
        monkeypatch.setattr(transport, '_feedback', acknowledged)
        worker = threading.Thread(target=reconcile)
        worker.start()
        assert entered.wait(10), 'reconciliation did not reach execution recovery'
        assert received.wait(10), 'execution recovery blocked admission feedback'
        # The native request and Turn already exist; recovery may block on a
        # host/fence, but receipt presentation must not wait for it.
        assert provider.reaction_creates == [('notes-app', event['message_id'], 'Get')]
        assert json.loads(path.read_text())['feedback']['received']['ok'] is True
        assert provider.writes == []
    finally:
        release.set()
        if worker:
            worker.join(10)
        runtime.close()
    assert not worker.is_alive() and not failures
    assert transport.core.read_request(row['request_ref'])['delivery_verified'] is True
    assert provider.writes == [('notes-app', 'Runtime response.')]
    if group:
        assert provider.topic_writes == [(event['chat_id'], event['message_id'], 'Runtime response.')]
    before = list(provider.reaction_creates), list(provider.writes)
    transport.reconcile_request(path)
    assert (provider.reaction_creates, provider.writes) == before
    assert len(store.list_sessions()) == 1


@pytest.mark.parametrize('drift', [
    'disabled', 'already_received', 'prepared', 'agent_target', 'native_source',
    'native_session', 'native_turn', 'message', 'turn_client', 'turn_session',
    'missing_turn', 'closed_session', 'session_goal', 'saved_context', 'profile', 'app', 'revoke',
])
def test_early_receipt_requires_exact_durable_admission_and_current_grant(ordinary, monkeypatch, drift):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    try:
        event = provider.event('notes-app', 'receipt_guard', 'ordinary work')
        assert transport.admit('notes-app', event)['status'] == 'durably_accepted'
        row = transport.core.pending()[0]
        runtime.wait_for_turn(session_id=row['session_id'], turn_id=row['turn_id'], timeout_sec=10)
        path = transport.root / f"{row['request_ref']}.json"
        record = json.loads(path.read_text())
        native_path = transport.core.root / path.name
        native = json.loads(native_path.read_text())
        if drift == 'disabled':
            transport.reaction_feedback = False
        elif drift == 'already_received':
            record['feedback'] = {'received': {'ok': True}}
        elif drift == 'prepared':
            native['status'] = 'prepared'
        elif drift == 'agent_target':
            native['agent_target'] = {'session_id': 'selected-original-host'}
        elif drift == 'native_source':
            native['source'] = {**native['source'], 'sender_ref': 'other'}
        elif drift in {'native_session', 'native_turn'}:
            native['session_id' if drift == 'native_session' else 'turn_id'] = 'other'
        elif drift == 'message':
            record['event']['message_id'] = 'om_other_message_same_chat'
        elif drift in {'turn_client', 'turn_session', 'missing_turn'}:
            turn = store.load_turn(row['session_id'], row['turn_id'])
            if drift != 'missing_turn':
                turn['client_turn_id' if drift == 'turn_client' else 'session_id'] = 'other'
            monkeypatch.setattr(store, 'load_turn', lambda *args: None if drift == 'missing_turn' else turn)
        elif drift in {'closed_session', 'session_goal', 'saved_context'}:
            session = store.load_session(row['session_id'])
            if drift == 'closed_session':
                session['status'] = 'closed'
            elif drift == 'session_goal':
                session['goal_id'] = 'another-goal'
            else:
                session['project_context']['workspace_path'] += '-changed'
            monkeypatch.setattr(store, 'load_session', lambda *args: session)
        elif drift == 'profile':
            record['profile'] = 'steward-app'
        elif drift == 'app':
            provider.profile_apps['notes-app'] = 'cli_other'
        elif drift == 'revoke':
            transport.bindings.disconnect(record['binding_id'], expected_revision=transport.bindings.read()['revision'])
        path.write_text(json.dumps(record))
        native_path.write_text(json.dumps(native))

        def recovery_boundary(**kwargs):
            assert kwargs == {'request_ref': row['request_ref']}
            assert provider.reaction_creates == []
            # Stop at the existing recovery boundary: this test establishes
            # that early feedback cannot acknowledge an unverified admission.
            raise RuntimeError('existing recovery still reached')

        monkeypatch.setattr(transport.core, 'recover', recovery_boundary)
        with pytest.raises(RuntimeError, match='existing recovery still reached'):
            transport.reconcile_request(path)
        # The bounded feedback job has joined even when recovery raised.
        assert provider.reaction_creates == []
        assert not provider.writes
    finally:
        runtime.close()


def test_failed_early_receipt_does_not_suppress_recovery_or_duplicate_uncertain_write(ordinary, monkeypatch):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    original = transport.runner
    recover = transport.core.recover
    recovery_calls = []

    def uncertain_receipt(args, cwd=None, timeout=None):
        result = original(args, cwd, timeout)
        if 'reactions' in args and 'create' in args:
            # The provider committed, but its response was lost. Recovery must
            # use the existing Inbox reaction journal instead of sending twice.
            return {'returncode': 1, 'stdout': '', 'stderr': 'response lost'}
        return result

    def recovered(**kwargs):
        recovery_calls.append(kwargs)
        recover(**kwargs)

    try:
        event = provider.event('notes-app', 'uncertain_receipt', 'ordinary work')
        transport.admit('notes-app', event)
        row = transport.core.pending()[0]
        runtime.wait_for_turn(session_id=row['session_id'], turn_id=row['turn_id'], timeout_sec=10)
        transport.runner = uncertain_receipt
        monkeypatch.setattr(transport.core, 'recover', recovered)
        transport.reconcile()
        transport.reconcile()
        assert recovery_calls
        assert provider.reaction_creates == [('notes-app', event['message_id'], 'Get')]
        assert provider.writes.count(('notes-app', 'Runtime response.')) == 1
        assert transport.core.read_request(row['request_ref'])['delivery_verified'] is True
    finally:
        runtime.close()


def test_steward_receipt_uses_its_saved_audience_before_existing_recovery(steward, monkeypatch):  # noqa: F811
    store, runtime, provider, transport, _, _, _ = steward
    event = provider.event('steward-app', 'steward_receipt', 'ordinary conversation')
    assert transport.admit('steward-app', event)['status'] == 'durably_accepted'
    row = transport.core.pending()[0]
    runtime.wait_for_turn(session_id=row['session_id'], turn_id=row['turn_id'], timeout_sec=10)
    assert store.load_session(row['session_id'])['goal_id'] == 'loopx-manager'
    received = threading.Event()
    feedback = transport._feedback

    def acknowledged(*args, **kwargs):
        feedback(*args, **kwargs)
        received.set()

    def recovery_boundary(**kwargs):
        assert received.wait(10), 'execution recovery blocked steward receipt'
        assert provider.reaction_creates == [('steward-app', event['message_id'], 'Get')]
        raise RuntimeError('existing steward recovery reached')

    monkeypatch.setattr(transport.core, 'recover', recovery_boundary)
    monkeypatch.setattr(transport, '_feedback', acknowledged)
    with pytest.raises(RuntimeError, match='existing steward recovery reached'):
        transport.reconcile_request(transport.root / f"{row['request_ref']}.json")


def test_slow_receipt_provider_does_not_delay_existing_execution_recovery(ordinary, monkeypatch):  # noqa: F811
    _, runtime, provider, transport = connect(ordinary)
    receipt_entered, recovered, release = threading.Event(), threading.Event(), threading.Event()
    feedback, recover = transport._feedback, transport.core.recover
    failures = []

    def slow_feedback(*args, **kwargs):
        receipt_entered.set()
        assert release.wait(30), 'test did not release provider feedback'
        feedback(*args, **kwargs)

    def recovery(**kwargs):
        recovered.set()
        recover(**kwargs)

    def reconcile():
        try:
            transport.reconcile_request(path)
        except BaseException as exc:
            failures.append(exc)

    worker = None
    try:
        event = provider.event('notes-app', 'slow_receipt_provider', 'ordinary work')
        transport.admit('notes-app', event)
        row = transport.core.pending()[0]
        runtime.wait_for_turn(session_id=row['session_id'], turn_id=row['turn_id'], timeout_sec=10)
        path = transport.root / f"{row['request_ref']}.json"
        monkeypatch.setattr(transport, '_feedback', slow_feedback)
        monkeypatch.setattr(transport.core, 'recover', recovery)
        worker = threading.Thread(target=reconcile)
        worker.start()
        assert receipt_entered.wait(10)
        assert recovered.wait(10), 'receipt provider blocked native execution recovery'
    finally:
        release.set()
        if worker:
            worker.join(10)
        runtime.close()
    assert not worker.is_alive() and not failures
    assert provider.reaction_creates == [('notes-app', event['message_id'], 'Get')]
    assert transport.core.read_request(row['request_ref'])['delivery_verified'] is True


def test_default_feedback_tracks_queue_execution_stop_and_app_isolation(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    try:
        slow = provider.event('notes-app', 'feedback_slow', 'wait for interrupt')
        transport.admit('notes-app', slow)
        first = transport.core.pending()[0]
        active(store, first)
        queued = provider.event('notes-app', 'feedback_queued', 'follow-up')
        transport.admit('notes-app', queued)
        transport.reconcile()
        assert ('notes-app', slow['message_id'], 'OnIt') in provider.reaction_creates
        assert ('notes-app', queued['message_id'], 'Get') in provider.reaction_creates
        assert ('notes-app', queued['message_id'], 'OnIt') not in provider.reaction_creates
        assert provider.writes == [('notes-app', '正在处理前一条，这条已排队。'),
                                   ('notes-app', '⏳ **已开始处理**')]
        assert any(row[1:] == (slow['message_id'], 'OnIt') for row in provider.reactions.values())
        # A new provider instance reuses durable receipts, not in-memory emoji state.
        replay = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
                                         runner=provider, cli_bin='lark-cli')
        before = list(provider.reaction_creates)
        replay.admit('notes-app', {**slow, 'event_id': 'replayed'})
        replay.reconcile()
        assert provider.reaction_creates == before
        other = provider.event('steward-app', 'feedback_other', '/status')
        replay.admit('steward-app', other)
        replay.reconcile()
        assert ('steward-app', other['message_id'], 'Get') in provider.reaction_creates
        assert not any(profile == 'steward-app' and ref == slow['message_id']
                       for profile, ref, _ in provider.reaction_creates)
        replay.admit('notes-app', provider.event('notes-app', 'feedback_stop', '/stop'))
        follow = next(row for row in replay.core.pending() if row['message'] == 'follow-up')
        runtime.wait_for_turn(session_id=follow['session_id'], turn_id=follow['turn_id'], timeout_sec=10)
        replay.reconcile()
        assert not any(row[1:] == (slow['message_id'], 'OnIt') for row in provider.reactions.values())
        assert ('notes-app', slow['message_id'], 'Get') in provider.reactions.values()
        assert all(row['goal_id'] is None for row in store.list_sessions())
    finally:
        runtime.close()


def test_terminal_cleanup_recovers_verified_answer_without_resend(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    try:
        event = provider.event('notes-app', 'cleanup_slow', 'wait for interrupt')
        transport.admit('notes-app', event)
        row = transport.core.pending()[0]
        active(store, row)
        transport.reconcile()
        transport.admit('notes-app', provider.event('notes-app', 'cleanup_stop', '/stop'))
        runtime.wait_for_turn(session_id=row['session_id'], turn_id=row['turn_id'], timeout_sec=10)
        provider.fail_reaction_delete = True
        transport.reconcile()
        record_path = transport.root / f"{row['request_ref']}.json"
        record = json.loads(record_path.read_text())
        assert record['status'] != 'delivered'
        assert record['deliveries']['terminal']['verified'] is False
        assert record['deliveries']['terminal']['attempt']
        before = list(provider.writes)
        transport.reconcile()
        assert provider.writes == before
        provider.fail_reaction_delete = False
        transport.reconcile()
        assert provider.writes == before
        assert json.loads(record_path.read_text())['status'] == 'delivered'
    finally:
        runtime.close()


@pytest.mark.parametrize('enabled', [False, True])
def test_opt_out_or_missing_reaction_permission_preserves_real_admission(ordinary, enabled):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    transport.reaction_feedback = enabled
    provider.fail_reaction_create = True
    try:
        rejected = provider.event('notes-app', 'wrong_feedback', '/status')
        assert transport.admit('steward-app', rejected)['status'] == 'audience_rejected'
        event = provider.event('notes-app', 'no_scope_feedback', 'plain request')
        assert transport.admit('notes-app', event)['status'] == 'durably_accepted'
        row = transport.core.pending()[0]
        runtime.wait_for_turn(session_id=row['session_id'], turn_id=row['turn_id'], timeout_sec=10)
        assert transport.reconcile() == 1
        assert provider.reaction_creates == []
        assert bool(any('reactions' in call for call in provider.calls)) == enabled
        assert any(text == 'Runtime response.' for _, text in provider.writes)
        assert any(text == '已收到，正在处理。' for _, text in provider.writes)
        assert len(store.list_sessions()) == 1
    finally:
        runtime.close()


def test_native_private_default_post_preserves_general_result_structure_and_safe_links(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    fake, workspace = ordinary[-2:]
    text = (f"**结果**\n\n- [本地报告]({workspace}/report.md)\n"
            "- [公开来源](https://example.org/source)\n\n> 待确认限制\n\n```python\nprint('ok')\n```")
    fake.write_text(fake.read_text().replace('"message": "Runtime response.",', f'"message": {text!r},'))
    try:
        event = provider.event('notes-app', 'rich_result', 'ordinary work')
        transport.admit('notes-app', event)
        row = transport.core.pending()[0]
        runtime.wait_for_turn(session_id=row['session_id'], turn_id=row['turn_id'], timeout_sec=10)
        assert transport.reconcile() == 1
        final = provider.messages[f'om_out_{len(provider.writes) - 1}']
        assert final['msg_type'] == 'post'
        assert len(provider.writes) == 1  # emoji admission does not duplicate the answer
        visible = json.loads(final['body']['content'])['zh_cn']['content'][0][0]['text']
        assert visible == text.replace(f'[本地报告]({workspace}/report.md)', '本地报告')
        assert '[project]' not in visible and str(workspace) not in visible
        assert transport.core.read_request(row['request_ref'])['delivery_verified'] is True
    finally:
        runtime.close()


def test_native_post_default_recovers_an_existing_plain_text_attempt_without_resend(ordinary, monkeypatch):  # noqa: F811
    import loopx.extensions.lark.private_conversations as native
    store, runtime, provider, transport = connect(ordinary)
    send = native.reply_lark_event_inbox
    def old_text_send(**kwargs):
        return send(**{**kwargs, 'content_format': 'text'})
    try:
        event = provider.event('notes-app', 'old_text_receipt', '/status')
        transport.admit('notes-app', event)
        provider.verify_replies = False
        with monkeypatch.context() as patch:
            patch.setattr(native, 'reply_lark_event_inbox', old_text_send)
            assert transport.reconcile() == 0
        assert len(provider.writes) == 1
        provider.verify_replies = True
        assert transport.reconcile() == 1
        assert len(provider.writes) == 1
        assert provider.messages['om_out_0']['msg_type'] == 'text'
    finally:
        runtime.close()
