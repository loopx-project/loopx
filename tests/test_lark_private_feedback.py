"""Native state drives presentation; provider receipts never own admission."""
import json
import time

import pytest
from test_chat_ordinary_project import ordinary  # noqa: F401
from test_lark_private_conversations import connect

from loopx.extensions.lark.private_conversations import LarkPrivateConversations


def active(store, row):
    deadline = time.monotonic() + 10
    while store.load_session(row['session_id']).get('active_turn_id') != row['turn_id']:
        assert time.monotonic() < deadline
        time.sleep(.01)


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
        assert provider.writes == [('notes-app', '正在处理前一条，这条已排队。')]
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
