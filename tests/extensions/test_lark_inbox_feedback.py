"""Intermediate delivery and durable provider feedback recovery contracts."""
import subprocess
from pathlib import Path

from test_lark_inbox_reactions import ReactionRunner, ReplyRunner, _fixture

from loopx.extensions.lark import inbox_reactions as inbox_reactions_module
from loopx.extensions.lark.inbox_reactions import (
    complete_lark_event_inbox_reactions, lark_inbox_reaction_receipts,
    mark_lark_event_inbox_processing, record_lark_inbox_reaction,
)
from loopx.extensions.lark.inbox_reply import reply_lark_event_inbox, verify_lark_inbox_reply


def test_intermediate_reply_and_recovery_leave_processing_until_final(tmp_path: Path) -> None:
    config, inbox, project = _fixture(tmp_path)
    runner = ReplyRunner(readback_text='处理中')
    record_lark_inbox_reaction(inbox=inbox, message_id='om_reaction_fixture', phase='processing',
                              reaction_id='reaction_OnIt', emoji_type='OnIt')
    attempts = []
    intermediate = reply_lark_event_inbox(project=project, config_path=config, message_id='om_reaction_fixture',
        text='处理中', execute=True, runner=runner, finalize_reactions=False, delivery_attempt_recorder=attempts.append)
    assert intermediate['ok'] and intermediate['reply_verified']
    assert intermediate['reaction_cleanup_deferred'] and not intermediate['reaction_cleanup_verified']
    assert 'processing' in lark_inbox_reaction_receipts(inbox=inbox, message_id='om_reaction_fixture')
    verified = verify_lark_inbox_reply(project=project, config_path=config, message_id='om_reaction_fixture',
        text='处理中', attempt=attempts[0], runner=runner, finalize_reactions=False)
    assert verified['ok'] and not verified['reaction_cleanup_verified']
    assert not any('delete' in args for args in runner.calls)
    runner.readback_text = '处理完成'
    final = reply_lark_event_inbox(project=project, config_path=config, message_id='om_reaction_fixture',
        text='处理完成', execute=True, runner=runner)
    assert final['ok'] and final['reaction_cleanup_verified']
    assert lark_inbox_reaction_receipts(inbox=inbox, message_id='om_reaction_fixture') == {}


def test_processing_known_create_recovers_receipt_before_terminal_cleanup(tmp_path: Path, monkeypatch) -> None:
    config, inbox, project = _fixture(tmp_path)
    runner = ReactionRunner()
    real_record = inbox_reactions_module.record_lark_inbox_reaction
    monkeypatch.setattr(inbox_reactions_module, 'record_lark_inbox_reaction',
                        lambda **_: (_ for _ in ()).throw(OSError('durable receipt unavailable')))
    first = mark_lark_event_inbox_processing(project=project, config_path=config,
                                            message_id='om_reaction_fixture', execute=True, runner=runner)
    assert not first['ok']
    assert inbox_reactions_module._load_reaction_creation_operation(inbox=inbox, message_id='om_reaction_fixture',
                                                          reaction_phase='processing')['phase'] == 'created'
    monkeypatch.setattr(inbox_reactions_module, 'record_lark_inbox_reaction', real_record)
    before = len([args for args in runner.calls if 'create' in args])
    completed = complete_lark_event_inbox_reactions(project=project, config_path=config,
                                                   message_id='om_reaction_fixture', execute=True, runner=runner)
    assert completed['ok']
    assert len([args for args in runner.calls if 'create' in args]) == before
    assert lark_inbox_reaction_receipts(inbox=inbox, message_id='om_reaction_fixture') == {}


def test_processing_unknown_create_never_repeats_after_recovery(tmp_path: Path) -> None:
    config, inbox, project = _fixture(tmp_path)
    calls = []
    def uncertain(args):
        calls.append(args)
        raise subprocess.TimeoutExpired(args, 1)
    first = mark_lark_event_inbox_processing(project=project, config_path=config,
                                            message_id='om_reaction_fixture', execute=True, runner=uncertain)
    assert not first['ok']
    for operation in (mark_lark_event_inbox_processing, complete_lark_event_inbox_reactions):
        recovered = operation(project=project, config_path=config, message_id='om_reaction_fixture',
                              execute=True, runner=uncertain)
        assert recovered['status'] == 'provider_outcome_uncertain'
    assert len(calls) == 1
    assert inbox_reactions_module._load_reaction_creation_operation(inbox=inbox, message_id='om_reaction_fixture',
                                                          reaction_phase='processing')['phase'] == 'prepared'


def test_failed_delete_with_incomplete_readback_is_not_verified() -> None:
    def runner(args):
        if 'delete' in args:
            return {'returncode': 1}
        return {'returncode': 0, 'stdout': '{"ok":true,"data":{"items":[],"has_more":true}}'}
    assert not inbox_reactions_module._delete_reaction(runner=runner, profile='synthetic-app',
                                                      message_id='om_synthetic', reaction_id='reaction_own')
