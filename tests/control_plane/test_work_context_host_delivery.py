"""Authored requirements must reach the Host, not just agree after projection."""
import copy
import json
import shlex
from urllib.parse import quote

import pytest

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.effect_runtime import EffectRuntimeRejected
from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from loopx.control_plane.turn_driver.codex_cli import _prompt
from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan
from loopx.control_plane.turn_driver.executor import build_loopx_turn_host_request
from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority, render_prompt
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_authority, isolate_sqlite_runtime, read_fixture_todos,
)
from tests.control_plane.host_process_fixture import assert_host_task_context
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, _configure_read_only_todo, _configure_selectable_alternative,
    _heartbeat_receipt_count, _run_cli, _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
def test_policy_observer_reads_original_signed_capture_after_todo_references(tmp_path, monkeypatch, provider):
    from loopx.control_plane.quota.turn_envelope import assert_complete_turn_decision, compose_turn_decision
    from loopx.control_plane.turn_driver.decision_capture import capture_progressive_context, write_decision_capture
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_source_fixture
    from tests.control_plane.codex_checkpoint_fixture import read_cli_policy_payload

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    initialize_canonical_source_fixture(runtime, GOAL_ID,
        state_path=project / f'.codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md', provider=provider)
    capture = tmp_path / 'signed-capture'
    code, view = _run_cli(registry, runtime, 'quota', 'should-run', '--goal-id', GOAL_ID,
        '--agent-id', AGENT_ID, '--begin-turn',
        '--codex-app', '--context-delivery', 'auto', '--delta-delivery', 'off',
        '--decision-output-dir', str(capture))
    assert code == 0, view
    saved = capture / 'decision.json'
    original_bytes = saved.read_bytes()
    original = json.loads(original_bytes)
    # Exercise the same typed projection used by C1's inline CLI presentation.
    (tmp_path / 'view').mkdir(mode=0o700)
    view = capture_progressive_context(original, directory=tmp_path / 'view', surface='complete_turn')[0]
    assert 'context_reference_instruction' in view and view != original
    source = tmp_path / 'view' / 'projection.json'
    observed = read_cli_policy_payload(json.dumps(view), returncode=code)
    assert observed == original
    assert_complete_turn_decision(observed)
    assert _spend_run_count(runtime) == 0 and saved.read_bytes() == original_bytes
    replacement = compose_turn_decision({
        **{key: value for key, value in original.items() if key not in {'schema_version', 'contract_binding'}},
        'reason': 'a separate valid captured observation',
    })
    assert_complete_turn_decision(replacement)
    replacement_path = source.with_name('replacement.json')
    replacement_digest = write_decision_capture(replacement_path, replacement)
    replaced_view = copy.deepcopy(view)
    argv = shlex.split(replaced_view['context_details']['command_template'])
    argv[argv.index('--capture-file') + 1] = str(replacement_path)
    argv[argv.index('--capture-sha256') + 1] = replacement_digest
    replaced_view['context_details']['command_template'] = shlex.join(argv)
    with pytest.raises(ValueError, match='original capture binding mismatch'):
        read_cli_policy_payload(json.dumps(replaced_view), returncode=code)
    # A copied presentation must not mask corruption of its declared source.
    source.write_text(json.dumps({**original, 'reason': 'changed after capture'}))
    with pytest.raises(ValueError, match='capture digest mismatch'):
        read_cli_policy_payload(json.dumps(view), returncode=code)
    source.unlink()
    with pytest.raises(FileNotFoundError):
        read_cli_policy_payload(json.dumps(view), returncode=code)


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize('repetitions', [1, 40])
def test_authored_requirements_survive_capture_projection_and_host(tmp_path, monkeypatch, provider, repetitions):
    from loopx.control_plane.quota.turn_envelope import bind_turn_decision_capture
    from loopx.control_plane.todos.completed_archive import DEFAULT_MAX_ACTIVE_DONE_TODOS_BEFORE_ARCHIVE

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
    completed = DEFAULT_MAX_ACTIVE_DONE_TODOS_BEFORE_ARCHIVE + 1
    completed_inputs = ''.join(
        f'- [x] [P2] Verify historical artifact {index}.\n'
        f'  <!-- loopx:todo todo_id=todo_historical_{index:02d} status=done task_class=advancement_task -->\n'
        for index in range(completed)
    )
    state.write_text(authored.replace(' -->', f' note={quote(note, safe="")} -->') + completed_inputs,
                     encoding='utf-8')
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
    code, emitted = _run_cli(registry, runtime, *args,
                             '--decision-output-dir', str(capture))
    assert code == 0, emitted
    saved = capture / 'decision.json'
    original_bytes = saved.read_bytes()
    full = json.loads(original_bytes)
    assert emitted == full and full['schema_version'] == 'loopx_turn_envelope_v1'
    promotion = full['promotion_readiness_warning']
    assert promotion['freshness_status'] == 'missing' and promotion['requires_readiness_run'] is True
    archive = full['completed_todo_archive_warning']
    assert archive['active_done_count'] == completed and archive['requires_archive'] is True
    source_context = full['interaction_contract']['agent_channel']['work_context']
    assert source_context['complete'] is True
    assert source_context['instruction']  # Non-vacuous instruction transport.

    plan = build_loopx_turn_plan(full, host='codex-cli', execution_mode='isolated-headless')
    request = build_loopx_turn_host_request(plan)
    model = json.loads(_prompt(request).split('Turn request:\n', 1)[1])
    model_context = assert_host_task_context(model, full)
    model_channel = model_context['interaction_contract']['agent_channel']
    model_task = next(source['content']['todo'] for source in model_channel['work_context']['sources']
                      if source['source'] == 'selected_todo')
    assert model_task.get('text', model_context['selected_todo']['text']) == source_task['text']
    assert model_task['note'] == note
    model_goal = next(source['content'] for source in model_channel['work_context']['sources']
                      if source['source'] == 'goal_state')
    assert model_goal['full_source'] is True and goal in model_goal['text']
    assert all(item['source'] != 'goal_state' for item in model_channel['required_reads'])

    # Keep the historical reader exercised with an explicit legacy projection.
    envelope = build_turn_envelope(full, captured_decision_path=str(saved))
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
    # The current capture delivers the complete Goal inline. Keep the separate
    # acceptance text and avoid making the Worker read the same file twice.
    assert source_by_kind['goal_state']['full_source'] is True
    assert goal in source_by_kind['goal_state']['text']
    assert all(item['source'] != 'goal_state' for item in authority['required_reads'])
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
    plan = build_loopx_turn_plan(full, host='generic-cli', execution_mode='isolated-headless',
                                turn_instance_id=TURN_ID)
    request = build_loopx_turn_host_request(plan)
    assert request['work_contract'] == full
    request.update(bind_turn_decision_capture(
        full, captured_decision_path=str(saved), turn_instance_id=TURN_ID, turn_key=request['turn_key'],
        complete_host_request={key: request[key] for key in ('schema_version', 'turn_key', 'source_decision_hash')},
    ))
    structured = render_prompt(extract_turn_authority(request))
    codex_prompt = _prompt(request)
    for prompt in (structured, codex_prompt):
        assert promotion['message'] not in prompt
        assert archive['archive_command_template'] not in prompt
        assert 'one_step means one work segment' not in prompt
    model_input, _ = json.JSONDecoder().raw_decode(structured.split('Turn request:\n', 1)[1])
    codex_input = json.loads(codex_prompt.split('Turn request:\n', 1)[1])
    assert model_input == codex_input
    worker = assert_host_task_context(model_input, full)
    assert worker['heartbeat_recommendation']['spend_policy'] == full['heartbeat_recommendation']['spend_policy']
    assert worker["reward_memory_recall"] == {"status": "disabled", "goal_id": GOAL_ID, "agent_id": AGENT_ID}
    full_dispatch = full["turn_start_capability_hook_dispatch"]
    assert full_dispatch["results"] and full_dispatch["registered_count"] >= 1
    assert worker["turn_start_capability_hook_dispatch"] == {"results": [
        {key: row[key] for key in ("hook_id", "capability_id", "status")} for row in full_dispatch["results"]
    ]}
    worker_context = worker['interaction_contract']['agent_channel']['work_context']
    worker_sources = {source['source']: source['content'] for source in worker_context['sources']}
    primary = worker_sources['selected_todo']['todo']
    assert primary['note'] == note
    assert primary.get('text', worker['selected_todo']['text']) == source_task['text']
    assert goal in worker_sources['goal_state']['text']
    if repetitions == 1:
        _assert_one_step_spend_policy_parity(full)
    if provider == "sqlite" and repetitions == 1:
        _assert_optional_readbacks_survive_when_relevant(full)
    receipts = _heartbeat_receipt_count(runtime, TURN_ID)
    code, ordinary = _run_cli(registry, runtime, *args)
    assert code == 0, ordinary
    assert ordinary['promotion_readiness_warning'] == promotion
    assert ordinary['completed_todo_archive_warning'] == archive
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


def _assert_one_step_spend_policy_parity(full):
    """Standard steering_audit_then_one_step moves spend_policy out; custom keeps it."""
    from loopx.control_plane.quota.turn_envelope import compose_turn_decision
    from loopx.control_plane.turn_driver.host_candidate import (
        ordinary_work_segment_instruction,
        render_dsh_host_prompt,
    )

    standard_policy = (
        "append exactly one heartbeat spend only after scope-bounded work "
        "is validated and written back; one_step: mode, not an operation limit"
    )
    custom_policy = "append once; keep the unfamiliar limit after readback"
    for policy, expects_guidance, extra_fields in (
        (standard_policy, True, {}),
        (custom_policy, False, {}),
        (
            custom_policy,
            False,
            {
                "ordinary_work_segment_guidance": True,
                "_ordinary_work_segment_guidance": True,
            },
        ),
    ):
        source = copy.deepcopy({key: value for key, value in full.items() if key not in {"schema_version", "contract_binding"}})
        source["heartbeat_recommendation"] = {
            **source["heartbeat_recommendation"],
            "recommended_mode": "steering_audit_then_one_step",
            "spend_policy": policy,
        }
        for key, value in extra_fields.items():
            source[key] = value
        decision = compose_turn_decision(source)
        plan = build_loopx_turn_plan(decision, host="generic-cli", execution_mode="isolated-headless",
                                    turn_instance_id=TURN_ID)
        request = build_loopx_turn_host_request(plan)
        authority = extract_turn_authority(request)
        assert (authority.get("_ordinary_work_segment_guidance") is True) is expects_guidance
        assert bool(ordinary_work_segment_instruction(authority)) is expects_guidance
        structured = render_prompt(authority)
        dsh_prompt = render_dsh_host_prompt(request)
        codex_prompt = _prompt(request)
        assert dsh_prompt is not None
        for rendered in (structured, dsh_prompt, codex_prompt):
            assert ("one_step means one work segment, not one operation." in rendered) is expects_guidance
        model_input, _ = json.JSONDecoder().raw_decode(structured.split("Turn request:\n", 1)[1])
        dsh_input, _ = json.JSONDecoder().raw_decode(dsh_prompt.split("Turn request:\n", 1)[1])
        codex_input = json.loads(codex_prompt.split("Turn request:\n", 1)[1])
        assert model_input == codex_input == dsh_input
        task_heartbeat = model_input["task_context"]["heartbeat_recommendation"]
        if expects_guidance:
            assert "spend_policy" not in task_heartbeat
        else:
            assert task_heartbeat["spend_policy"] == custom_policy
            for key, value in extra_fields.items():
                assert model_input["task_context"][key] == value


def _assert_optional_readbacks_survive_when_relevant(full):
    from loopx.control_plane.quota.turn_envelope import compose_turn_decision
    from loopx.control_plane.turn_driver.codex_cli import _prompt
    from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan
    from loopx.control_plane.turn_driver.executor import build_loopx_turn_host_request

    for case in ("active", "failed", "extended"):
        source = copy.deepcopy({key: value for key, value in full.items() if key not in {"schema_version", "contract_binding"}})
        recall = source["reward_memory_recall"]
        dispatch = source["turn_start_capability_hook_dispatch"]
        if case == "active":
            recall["context"] = {"guidance": ["Use independent verification before reporting."]}
            recall["automatic_recall"] = True
            recall["experiment"].update(enabled=True, automatic_recall=True)
            dispatch["results"][0].update(status="observed", observation_count=1,
                                          readback={"summary_lines": ["A required artifact is available."]})
        elif case == "failed":
            recall.update(status="runtime_unavailable", reason_code="runtime_unavailable")
            dispatch["results"][0].update(status="unavailable", error_code="context_unavailable")
        else:
            recall["experiment"]["host_coverage"][0]["future_context"] = {"must_read": "captured evidence"}
            dispatch["results"][0]["readback"] = {"future_context": {"must_read": "captured evidence"}}
        decision = compose_turn_decision(source)
        plan = build_loopx_turn_plan(decision, host="generic-cli", execution_mode="isolated-headless",
                                    turn_instance_id=TURN_ID)
        request = build_loopx_turn_host_request(plan)
        model = json.loads(_prompt(request).split("Turn request:\n", 1)[1])["task_context"]
        assert model["reward_memory_recall"] == recall, case
        assert model["turn_start_capability_hook_dispatch"] == dispatch, case


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize(('declaration', 'diagnostic'), [
    ('version', 'unsupported complete Turn decision version'),
    ('hash_codec', 'unsupported complete Turn decision hash codec'),
    ('content_hash', 'complete Turn decision content integrity mismatch'),
])
def test_real_turn_refuses_unsupported_consumer_version_before_launch(
    tmp_path, monkeypatch, provider, declaration, diagnostic,
):
    import contextlib
    import io
    import sys
    from loopx.cli import main
    from loopx.cli_commands import turn
    from tests.test_loopx_turn_driver import _write_live_fixture

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    original_builder = turn.build_loopx_turn_plan
    observed = []

    def incompatible_writer(decision, **kwargs):
        packet = copy.deepcopy(decision)
        assert packet['schema_version'] == 'loopx_turn_envelope_v1'
        if declaration == 'version':
            packet['schema_version'] = 'unsupported_work_contract'
        else:
            packet['contract_binding'][declaration] = 'unsupported_required_semantics'
        observed.append(packet)
        return original_builder(packet, **kwargs)

    monkeypatch.setattr(turn, 'build_loopx_turn_plan', incompatible_writer)
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
    assert diagnostic in result['error'], result
    # Generic CLI error effects are unknown; the marker and ledger independently
    # prove this pre-dispatch refusal, without interpreting null as false.
    assert _spend_run_count(runtime) == 0
