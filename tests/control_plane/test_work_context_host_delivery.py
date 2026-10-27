"""Authored requirements must reach the Host, not just agree after projection."""
import copy
import json
import os
import shlex
import subprocess
from urllib.parse import quote

import pytest

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority, render_prompt
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_authority, isolate_sqlite_runtime, read_fixture_todos,
)
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, _configure_read_only_todo,
    _heartbeat_receipt_count, _run_cli, _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize('repetitions', [1, 40])
def test_authored_requirements_survive_capture_projection_and_host(tmp_path, monkeypatch, provider, repetitions):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = _configure_read_only_todo(project)
    task = ('Compare the retained evidence / 保留原始证据. ' * repetitions
            + 'Stop before publication; return unresolved discrepancies.')
    goal = ('Keep acceptance separate from task status.\n' * 12
            + 'Missing comparison evidence remains unknown, not unchanged.')
    note = 'A complete task body does not replace this separate review requirement.'
    authored = state.read_text().replace(
        '- [ ] [P1] Validate and settle the selected delivery.', f'- [ ] [P1] {task}',
    ).replace('## Next Action', f'## Acceptance\n\n{goal}\n\n## Next Action')
    # Author only disposable source input, before creating canonical authority.
    state.write_text(authored.replace(' -->', f' note={quote(note, safe="")} -->'), encoding='utf-8')
    todos = read_fixture_todos(state)
    source_task = next(item for item in todos if item['todo_id'] == TODO_ID)
    # The fixture helper returns an attention view. Seed the canonical record
    # from the authored full body, never its display truncation.
    source_task.update(text=f'[P1] {task}', note=note)
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID, handoff_mode='soft_claim', todos=todos,
    )
    initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state, provider=provider)
    args = ['quota', 'should-run', '--goal-id', GOAL_ID, '--agent-id', AGENT_ID,
            '--todo-id', TODO_ID, '--turn-instance-id', TURN_ID, '--codex-app',
            '--available-capability', 'shell', '--available-capability', 'filesystem_read']
    capture = tmp_path / 'original-context'
    code, envelope = _run_cli(registry, runtime, *args, '--turn-envelope',
                              '--decision-output-dir', str(capture))
    assert code == 0, envelope
    saved = capture / 'decision.json'
    original_bytes = saved.read_bytes()
    full = json.loads(original_bytes)
    source_context = full['interaction_contract']['agent_channel']['work_context']
    assert source_context['complete'] is True
    assert source_context['instruction']  # Non-vacuous instruction transport.
    authority = extract_turn_authority({'turn_envelope': envelope})
    # The raw heartbeat and the structured Host have different effect owners,
    # but ordinary execution must not lose notification, bypass or scheduler
    # restrictions just because no staged replan reader is present.
    assert 'replan_action_packet' not in authority
    assert authority['agent_id'] == AGENT_ID
    for field in ('must_attempt', 'delivery_allowed', 'quiet_noop_allowed'):
        assert authority['action'][field] == full['interaction_contract']['agent_channel'][field]
    assert authority['should_run'] == full['should_run']
    assert authority['effective_action'] == full['effective_action']
    assert full['safe_bypass_allowed'] is False
    assert authority['execution_policy']['safe_bypass_allowed'] is False
    assert authority['user']['notify'] == full['interaction_contract']['user_channel']['notify']
    assert authority['scheduler'] == envelope['scheduler']
    assert authority['detail_ref']['captured_decision'] == envelope['detail_ref']['captured_decision']
    # These are execution obligations, not display summaries. The ordinary
    # structured Host must receive the same contracts as the raw quota caller.
    for field in ('work_lane_contract', 'execution_obligation', 'automation_liveness'):
        assert full[field]
        assert envelope['contract_capsule'][field] == full[field]
        assert authority['contract_capsule'][field] == full[field]
    context = authority['work_context']
    assert context['instruction'] == source_context['instruction']
    assert authority['selected_todo']['text_source_verified'] is True
    source_by_kind = {source['source']: source['content'] for source in context['sources']}
    task_detail = source_by_kind['selected_todo']['todo']
    assert task_detail['note'] == note
    delivered_task = task_detail.get('text', authority['selected_todo']['text'])
    assert delivered_task == source_task['text']
    # This fixture has an unregistered Goal narrative: pending read must remain
    # explicit even when all available inline sources were loaded successfully.
    goal_read = next(item for item in authority['required_reads'] if item['source'] == 'goal_state')
    assert goal_read['ordering'] == 'before_work'
    if os.name != 'nt':
        readback = subprocess.run(shlex.split(goal_read['command']), capture_output=True,
                                  text=True, check=True)
        assert goal in readback.stdout
    else:
        assert goal in state.read_text(encoding='utf-8')
    # The actual Host prompt must contain the independent source requirements,
    # not merely match an action signature computed by the same projector.
    prompt = render_prompt(authority)
    transported, _ = json.JSONDecoder().raw_decode(prompt.split('Turn authority JSON:\n', 1)[1])
    assert transported['selected_todo'] == authority['selected_todo']
    assert transported['required_reads'] == authority['required_reads']
    assert transported['work_context'] == context
    assert transported['contract_capsule'] == authority['contract_capsule']
    assert transported['action'] == authority['action']
    for field in ('user', 'execution_policy', 'scheduler'):
        assert transported[field] == envelope[field]
    assert 'the outer Turn executor owns those effects' in prompt
    # A compromised carrier cannot silently remove either the instruction or a
    # separate obligation while retaining the signed action's original digest.
    for carrier, changed_field in (('work_context', 'instruction'),
                                   ('work_context', 'sources'),
                                   ('contract_capsule', 'execution_obligation'),
                                   ('execution_policy', 'safe_bypass_allowed')):
        altered = copy.deepcopy(envelope)
        del altered[carrier][changed_field]
        with pytest.raises(ValueError, match='action signature'):
            extract_turn_authority({'turn_envelope': altered})
    # A matching v0 digest does not authorize interpreting a future wire version.
    # Version declarations are outside the historical action digest document.
    from loopx.control_plane.effect_runtime import EffectRuntimeRejected
    from loopx.control_plane.turn_driver.codex_cli import _prompt
    from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan
    for path in (('schema_version',), ('action_signature', 'schema_version'),
                 ('action_signature', 'coverage')):
        for unsupported in ('future_required_semantics', None):
            altered = copy.deepcopy(envelope)
            carrier = altered if len(path) == 1 else altered[path[0]]
            carrier[path[-1]] = unsupported
            for consumer in (extract_turn_authority, _prompt):
                with pytest.raises(EffectRuntimeRejected, match='unsupported TurnEnvelope'):
                    consumer({'turn_envelope': altered})
            plan = build_loopx_turn_plan(altered, host='generic-cli', execution_mode='isolated-headless')
            assert plan['route']['kind'] == 'contract_error'
            assert plan['route']['would_invoke_host'] is False
    receipts = _heartbeat_receipt_count(runtime, TURN_ID)
    code, ordinary = _run_cli(registry, runtime, *args)
    assert code == 0, ordinary
    ordinary_context = ordinary['interaction_contract']['agent_channel']['work_context']
    assert ordinary_context['instruction'] == source_context['instruction']
    ordinary_task = next(source['content']['todo'] for source in ordinary_context['sources']
                         if source['source'] == 'selected_todo')
    assert ordinary_task.get('text', ordinary['selected_todo']['text']) == source_task['text']
    assert ordinary_task['note'] == note
    assert ordinary['interaction_contract']['cli_channel']['settlement_plan']['identity'] == full[
        'interaction_contract']['cli_channel']['settlement_plan']['identity']
    assert _heartbeat_receipt_count(runtime, TURN_ID) == receipts
    assert _spend_run_count(runtime) == 0
    assert saved.read_bytes() == original_bytes


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize('declaration', ['envelope', 'signature', 'coverage'])
def test_real_turn_refuses_unsupported_consumer_version_before_launch(tmp_path, monkeypatch, provider, declaration):
    import contextlib
    import io
    import sys
    from loopx.cli import main
    from loopx.cli_commands import turn
    from tests.test_loopx_turn_driver import _write_live_fixture

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    original_builder = turn.build_turn_envelope
    observed = []

    def incompatible_writer(*args, **kwargs):
        packet = original_builder(*args, **kwargs)
        if declaration == 'envelope':
            packet['schema_version'] = 'unsupported_work_contract'
        else:
            packet['action_signature']['schema_version' if declaration == 'signature' else 'coverage'] = 'unsupported_required_semantics'
        observed.append(copy.deepcopy(packet))
        return packet

    monkeypatch.setattr(turn, 'build_turn_envelope', incompatible_writer)
    marker = project / 'host-started'
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(['--registry', str(registry), '--runtime-root', str(runtime), '--format', 'json',
            'turn', 'run-once', '--host', 'generic-cli', '--goal-id', 'loopx-turn-fixture',
            '--agent-id', 'codex-fixture', '--project', str(project), '--host-command-json',
            json.dumps([sys.executable, '-c', f'from pathlib import Path; Path({str(marker)!r}).touch()']),
            '--no-global-sync', '--execute'])
    result = json.loads(output.getvalue())
    assert observed and code != 0, result
    assert not marker.exists()
    assert 'unsupported TurnEnvelope' in result['error'], result
    assert 'compatible consumer' in result['error'], result
    # Generic CLI error effects are unknown; the marker and ledger independently
    # prove this pre-dispatch refusal, without interpreting null as false.
    assert _spend_run_count(runtime) == 0
