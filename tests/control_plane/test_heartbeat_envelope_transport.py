"""Qualify the real worker -> renderer -> guard/selection/settlement boundary."""
import base64
import hashlib
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import zlib
from pathlib import Path

import pytest

from benchmark.runtime.codex import Execution
from benchmark.runtime.worker import heartbeat_body
from loopx.heartbeat_prompt import build_heartbeat_prompt
from loopx.control_plane.quota.turn_envelope import (
    assert_complete_turn_decision, assert_turn_envelope_consumer_version,
    quota_action_signature_document, turn_envelope_action_signature_document,
)
from tests.control_plane.cli_output_fixture import stable_budget_fixture_root
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, ALTERNATIVE_TODO_ID, REPO_ROOT,
    _write_fixture, _configure_selectable_alternative, _run_cli,
    _initialize_fixture_authority,
    _run_generated_cli, _heartbeat_receipt_count, _spend_run_count,
)


def assert_explicit_off_keeps_legacy_prompt(original, disabled):
    # Omitted low-level API values keep their old prompt. Explicit OFF must
    # also fence the new CLI default when the generated guard actually runs.
    assert "--context-delivery off" in disabled["quota_guard_command"]
    assert {key: value.replace(" --context-delivery off", "") if isinstance(value, str) else value
            for key, value in disabled.items() if key != "interface_budget"} == {
                key: value for key, value in original.items() if key != "interface_budget"}
    extra = len(" --context-delivery off")
    assert disabled["interface_budget"]["char_count"] == original["interface_budget"]["char_count"] + extra
    assert disabled["interface_budget"]["budget_char_count"] == original["interface_budget"]["budget_char_count"] + extra
    assert disabled["interface_budget"]["max_chars"] == original["interface_budget"]["max_chars"]


def assert_original_read_and_recovery_duties(body):
    text = ' '.join(body.split())
    for obligation in (
        'Read work_context and required_reads before acting',
        'applicable goals/criteria/constraints/stops',
        'For selection/replan or missing details, follow returned read/recovery instructions',
        'with original capture/identity',
        'never re-admit',
        'Honor explicit full reads',
        'keep capture/handoff flags on selection/same-Turn repair',
        'No spend for waiting alone',
        'RRULE/projected-fallback_hint/ack/fail',
    ):
        assert obligation in text, obligation
    assert any(pointer in text for pointer in (
        'Settle current settlement_plan.ordered_steps in order, else next_cli_actions',
        'Settle current interaction_contract.cli_channel.settlement_plan.ordered_steps in order, else next_cli_actions',
    ))


def assert_original_contract_view(view, source):
    """Both supported displays retain the one captured decision and controls."""
    assert_complete_turn_decision(source)
    agent = source['interaction_contract']['agent_channel']
    # The producer owns these obligations; the bootstrap routes here instead
    # of carrying a second schema/section-selection policy. Verify delivery,
    # not just that the renderer contains a shorter referral.
    instruction = agent['work_context']['instruction']
    for duty in (
        'Read current sources and pending required_reads before work',
        'summaries cannot replace full requirements',
        'Recheck freshness before later actions',
        'Context grants no authority',
    ):
        assert duty in instruction
    if view.get('schema_version') == 'loopx_turn_envelope_v0':
        assert_turn_envelope_consumer_version(view)
        assert view['action_signature']['matches'] is True
        assert view['action_signature']['source_decision_hash'] == source['contract_binding']['content_hash']
        assert turn_envelope_action_signature_document(view) == quota_action_signature_document(source)
        assert [read['command'] for read in view['required_reads']] == [
            read['command'] for read in agent['required_reads']]
        if 'work_context' in view:
            assert view['work_context']['instruction'] == instruction
        recovery = view['detail_ref']['captured_decision']
        assert 'Check ok, Goal/Agent/Turn and source hash before use' in recovery['instruction']
        assert 'Missing or invalid capture requires recovery, not blind guard replay' in recovery['instruction']
    else:
        assert_complete_turn_decision(view)
        assert view['contract_binding'] == source['contract_binding']


def read_original_capture(path, *, registry, runtime):
    # Complete inline output has no hidden second envelope. The caller already
    # owns this publication path; exercise its existing digest-checked reader.
    original = path.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    rc, page = _run_cli(registry, runtime, 'quota', 'context-read',
        '--capture-file', str(path), '--capture-sha256', digest,
        '--page-bytes', '64000', '--max-page-output-bytes', '64000')
    data = b''
    while True:
        assert rc == 0 and page['ok'], page
        assert page['sha256'] == digest and page['offset'] == len(data)
        data += page['content'].encode()
        if page['eof']:
            break
        rc, page = _run_generated_cli(page['next_command'], registry_path=registry)
    assert data == original and hashlib.sha256(data).hexdigest() == digest
    return data


def test_shared_native_root_keeps_dynamic_turn_binding_and_bootstrap_options(tmp_path):
    from loopx.cli import build_parser
    from loopx.control_plane.heartbeat.bootstrap_prompt import goal_bootstrap, host_bootstrap_binding

    args = dict(goal_id=GOAL_ID, agent_id=AGENT_ID, runtime_profile="codex_app_heartbeat",
        thin=True, decision_output_root=tmp_path, codex_context_root=str(tmp_path), context_delivery="on", turn_envelope=True)
    prompt = build_heartbeat_prompt(**args)
    guard = prompt["quota_guard_command"]
    assert '--turn-instance-id "${LOOPX_TURN:?}"' in guard
    assert "--begin-turn" not in guard
    assert "--codex-context-root" in guard and "--turn-envelope" in guard
    assert "LOOPX_TURN=<current_time_iso>" in prompt["task_body"]
    # The shell refuses an unbound wake before the CLI can create a receipt.
    result = subprocess.run(guard, shell=True, env={"PATH": os.environ["PATH"]},
        capture_output=True, text=True)
    assert result.returncode != 0 and "LOOPX_TURN" in result.stderr
    assert list(tmp_path.iterdir()) == []
    parser = build_parser()
    cli = ["heartbeat-prompt", "--thin", "--codex-app", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--decision-output-root", str(tmp_path), "--context-delivery", "on", "--codex-app", "--codex-context-root", str(tmp_path), "--turn-envelope", "--bootstrap"]
    parsed = parser.parse_args(cli)
    bootstrap = goal_bootstrap(parsed, registry=tmp_path / "registry.json")
    command = shlex.split(bootstrap.split("```sh\n")[1].split("\n```")[0])
    assert "--bootstrap" not in command and "--turn-instance-id" not in command
    assert command[command.index("--codex-context-root") + 1] == str(tmp_path)
    assert "--turn-envelope" in command
    binding = host_bootstrap_binding(bootstrap)
    assert binding is not None and binding["context_delivery"] == "on" and binding["turn_envelope"] is True


def test_capture_preserves_full_dispatcher_and_envelope_needs_explicit_opt_in(tmp_path):
    args = dict(goal_id=GOAL_ID, agent_id=AGENT_ID, runtime_profile="generic_cli",
                turn_instance_id=TURN_ID, thin=True)
    assert build_heartbeat_prompt(**args) == build_heartbeat_prompt(**args, decision_output_root=None)
    ordinary = build_heartbeat_prompt(**args)
    captured = build_heartbeat_prompt(**args, decision_output_root=tmp_path)
    capture_args = ' --decision-output-root ' + shlex.quote(str(tmp_path))
    assert captured['quota_guard_command'] == ordinary['quota_guard_command'] + capture_args
    assert captured['task_body'].replace(capture_args, '') == ordinary['task_body']
    assert '--turn-envelope' not in captured['quota_guard_command']
    with pytest.raises(ValueError, match='capture root'):
        build_heartbeat_prompt(**args, turn_envelope=True)
    payload = build_heartbeat_prompt(**args, decision_output_root=tmp_path, turn_envelope=True)
    assert payload['interface_budget']['within_budget'], payload['interface_budget']
    assert '--turn-envelope --decision-output-root' in payload['quota_guard_command']
    assert_original_read_and_recovery_duties(payload['task_body'])
    assert '--decision-output-root' in payload['thin_prompt_command']
    assert '--turn-envelope' in payload['thin_prompt_command']
    # The captured allowance must neither leak into ordinary thin nor disable
    # overflow diagnostics, including when Reward Memory is absent.
    no_memory = dict(args, reward_memory_enabled=False)
    ordinary = build_heartbeat_prompt(**no_memory)
    captured_only = build_heartbeat_prompt(**no_memory, decision_output_root=tmp_path)
    assert ordinary['interface_budget']['max_chars'] == 5_100
    assert captured_only['interface_budget']['max_chars'] == 5_100
    # Legal, distinct scopes alone exceed the captured allowance, independent
    # of temporary-path length or how compact the fixed dispatcher becomes.
    scopes = [letter * 320 for letter in 'abcdefghijklmnopqr']
    assert sum(map(len, scopes)) > 5_500
    oversized = build_heartbeat_prompt(
        **no_memory, decision_output_root=tmp_path, turn_envelope=True,
        agent_scopes=scopes,
    )
    assert all(scope in oversized['task_body'] for scope in scopes)
    assert len(oversized['task_body']) > 5_500
    assert oversized['interface_budget']['max_chars'] == 5_500
    assert oversized['interface_budget']['within_budget'] is False
    for overrides in (dict(turn_instance_id=None), dict(thin=False, full=True),
                      dict(runtime_profile='codex_app_ssh_goal')):
        with pytest.raises(ValueError):
            build_heartbeat_prompt(**(args | overrides), decision_output_root=tmp_path)
    with pytest.raises(ValueError, match='existing directory'):
        build_heartbeat_prompt(**args, decision_output_root=tmp_path / 'missing')


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
def test_captured_scheduler_ack_is_inline_and_failure_detail_stays_on_original_source(
    tmp_path, monkeypatch, provider,
):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    capture = tmp_path / 'capture'
    code, envelope = _run_cli(registry, runtime, 'quota', 'should-run',
        '--goal-id', GOAL_ID, '--agent-id', AGENT_ID, '--todo-id', TODO_ID,
        '--turn-instance-id', TURN_ID, '--codex-app', '--turn-envelope',
        '--decision-output-dir', str(capture), cwd=project)
    assert code == 0, envelope
    original = (capture / 'decision.json').read_bytes()
    captured = json.loads(original)
    source = captured['scheduler_hint']['app_automation']
    assert_original_contract_view(envelope, captured)
    # This observer helper explicitly selects off. Its supported bounded
    # display keeps executable ACK argv and original-capture failure details.
    assert envelope['schema_version'] == 'loopx_turn_envelope_v0'
    projected = envelope['scheduler']['app_automation']
    assert projected['ack_cli_args'] == source['ack_hint']['cli_args']
    assert projected['failure_cli_args_detail_ref']['detail_ref'] == 'full_decision.scheduler_hint.app_automation.failure_hint.cli_args'
    before = _heartbeat_receipt_count(runtime, TURN_ID)
    read = read_original_capture(capture / 'decision.json', registry=registry, runtime=runtime)
    assert read == original
    assert _heartbeat_receipt_count(runtime, TURN_ID) == before


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize('scope_chars', [0, 320])
def test_bound_captured_prompt_fits_budget_without_reward_memory(
    tmp_path, monkeypatch, provider, scope_chars,
):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    # Match the public budget workload independently of pytest/runner path length.
    # Both runtime and capture routes remain present and count toward the body.
    with stable_budget_fixture_root(tmp_path) as base:
        root = (base / ('p' * (128 - len(str(base.resolve())) - 1))).resolve()
        assert len(str(root)) == 128
        project, runtime, registry = _write_fixture(root)
        _initialize_fixture_authority(project, runtime, provider=provider)
        capture = root / 'capture'
        capture.mkdir()
        argv = [
            sys.executable, '-m', 'loopx.entrypoint', '--format', 'json',
            '--registry', str(registry), '--runtime-root', str(runtime),
            'heartbeat-prompt', '--thin', '--turn-envelope',
            '--decision-output-root', str(capture), '--runtime-profile', 'generic_cli',
            '--goal-id', GOAL_ID, '--agent-id', AGENT_ID, '--turn-instance-id', TURN_ID,
        ]
        if scope_chars:
            argv.extend(['--agent-scope', 's' * scope_chars])
        result = subprocess.run(
            argv, cwd=project, capture_output=True, text=True, check=True,
            env={**os.environ, 'PYTHONPATH': str(REPO_ROOT)},
        )
        payload = json.loads(result.stdout)
        body = payload['task_body']
        assert '--reward-memory-reflection-json' not in body
        assert str(runtime) in body and str(capture) in body
        assert payload['turn_instance_id'] == TURN_ID
        assert payload['interface_budget']['max_chars'] == 5_500
        assert payload['interface_budget']['within_budget'], payload['interface_budget']
        assert len(result.stdout) <= 5_400  # Shipped Agent JSON, not generator metadata.
        assert_original_read_and_recovery_duties(body)
        assert 'goal_acceptance' in body  # Retain the complete outcome guidance too.


@pytest.mark.parametrize('mode', ['plain', 'native-goal', 'loopx-goal', 'turn'])
def test_incompatible_worker_rejected(mode):
    with pytest.raises(ValueError, match='turn_envelope'):
        Execution(mode=mode, turn_envelope=True)


@pytest.fixture
def private_handoff(request):
    if not request.param:
        yield None
        return
    if os.name != 'posix':
        pytest.skip('Requires a private Unix socket')
    with tempfile.TemporaryDirectory(prefix='hb-capture-', dir='/tmp') as directory:
        endpoint = str(Path(directory).resolve() / 'host.sock')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(endpoint)
            listener.listen()
            listener.settimeout(.1)
            stopped = threading.Event()

            def release():
                while not stopped.is_set():
                    try:
                        channel, _ = listener.accept()
                    except TimeoutError:
                        continue
                    with channel, channel.makefile('rb') as stream:
                        message = json.loads(stream.readline())
                        channel.sendall(json.dumps({'released': True, 'sha256': message['sha256']}).encode() + b'\n')

            thread = threading.Thread(target=release, daemon=True)
            thread.start()
            try:
                yield endpoint
            finally:
                stopped.set()
                thread.join(timeout=5)
                assert not thread.is_alive()


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('private_handoff', [False, True], indirect=True)
@pytest.mark.parametrize('context_delivery', [False, True])
def test_real_worker_generated_guard_selection_reentry_and_settle_once(
    tmp_path, monkeypatch, provider, enabled, private_handoff, context_delivery,
):
    from loopx.control_plane.quota.turn_envelope import assert_complete_turn_decision
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    state = project / f'.codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md'
    original_state = state.read_bytes()
    rc, refused = _run_cli(registry, runtime, 'todo', 'list', '--goal-id', GOAL_ID)
    assert rc == 1 and refused['error_code'] == 'canonical_authority_required', refused
    assert refused['legacy_fallback_used'] is False and state.read_bytes() == original_state
    # Explicitly convert the disposable fixture before the worker runs. Its
    # ordinary guard must not recover authority from the Markdown projection.
    _initialize_fixture_authority(project, runtime, provider=provider)
    logs = tmp_path / 'wake logs'
    (logs / TURN_ID).mkdir(parents=True)
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT),
               LOOPX_CLI=str(Path(sys.executable).parent / 'loopx'),
               LOOPX_REGISTRY=str(registry), LOOPX_RUNTIME_ROOT=str(runtime),
               LOOPX_PROJECT=str(project), LOOPX_GOAL_ID=GOAL_ID, LOOPX_AGENT_ID=AGENT_ID,
               LOOPX_WAKE_LOG_DIR=str(logs), LOOPX_TURN_ENVELOPE='1' if enabled else '0')
    if context_delivery:
        # No native proof in this CLI fixture: composed capture must still
        # save a full baseline and preserve reentry and settlement.
        (tmp_path / 'context').mkdir(mode=0o700)
        env.update(LOOPX_CONTEXT_DELIVERY='on', LOOPX_EXECUTION_MODE='heartbeat', LOOPX_CODEX_CONTEXT_DIR=str(tmp_path / 'context'))
        # Generated CLI reentry uses this process's environment; preserve the
        # same worker declarations carried by the native tool environment.
        monkeypatch.setenv('LOOPX_CONTEXT_DELIVERY', 'on')
        monkeypatch.setenv('LOOPX_EXECUTION_MODE', 'heartbeat')
    else:
        env['LOOPX_CONTEXT_DELIVERY'] = 'off'
        monkeypatch.setenv('LOOPX_CONTEXT_DELIVERY', 'off')
    body = heartbeat_body(env, TURN_ID, native_goal=False, decision_handoff_socket=private_handoff)
    # Execute the renderer's actual guard, preserving its route and flags.
    block = body.split('```sh\n', 1)[1].split('```', 1)[0]
    guard = next(line for line in block.splitlines() if 'quota should-run' in line)
    guard = guard.replace('${LOOPX_TURN:?}', TURN_ID)
    argv = shlex.split(guard)
    assert ("--complete-turn" in argv) is not enabled
    result = subprocess.run(argv, env=env, cwd=project, capture_output=True, text=True)
    first = json.loads(result.stdout)
    assert result.returncode == 0, first
    reader_contracts = []
    def complete_page(value):
        navigation = (value if value.get('schema_version') == 'context_capture_navigation_v1'
            else value.get('navigation'))
        if context_delivery and isinstance(navigation, dict):
            for duty in (
                'Choose the sections needed for the current decision',
                'Read applicable goals, acceptance criteria, constraints and stop conditions before dependent work',
                'read the current operation/settlement contract before effects',
                'After compaction recover only missing necessary facts from this capture',
                'never rerun admission or repeat effects just to recover context',
            ):
                assert duty in navigation['instruction']
            reader_contracts.append(navigation)
        if value.get('schema_version') == 'context_capture_navigation_v1':
            # This compatibility test elects to read the full saved view.
            # A manifest only supplies navigation; it is not delivered body.
            assert value['mode'] == 'full'
            read = subprocess.run(shlex.split(value['recovery']['command']), env=env, cwd=project,
                capture_output=True, text=True, check=True)
            value = json.loads(read.stdout)
            assert value['schema_version'] == 'context_capture_page_v1'
        if value.get('schema_version') != 'context_capture_page_v1':
            return value
        digest, body = value['sha256'], b''
        while True:
            assert value['ok'] and value['sha256'] == digest and value['offset'] == len(body)
            body += value['content'].encode()
            if value['eof']:
                break
            read = subprocess.run(shlex.split(value['next_command']), env=env, cwd=project,
                capture_output=True, text=True, check=True)
            value = json.loads(read.stdout)
        assert hashlib.sha256(body).hexdigest() == digest
        return json.loads(body)
    first = complete_page(first)
    if context_delivery:
        assert reader_contracts, 'The worker must actually receive the delegated reader obligations'
    root = logs / TURN_ID / 'decisions'
    captured = enabled or private_handoff is not None or context_delivery
    if captured:
        captures = list(root.glob('*/decision.json'))
        assert len(captures) == 1
        full = json.loads(captures[0].read_text())
        assert_complete_turn_decision(full)
        if enabled:
            assert_original_contract_view(first, full)
            if not context_delivery:
                assert first['schema_version'] == 'loopx_turn_envelope_v0'
                assert_original_read_and_recovery_duties(body)
            else:
                assert first['schema_version'] == 'loopx_turn_envelope_v1'
            receipts = _heartbeat_receipt_count(runtime, TURN_ID)
            readback = read_original_capture(captures[0], registry=registry, runtime=runtime)
            assert json.loads(readback) == full
            assert _heartbeat_receipt_count(runtime, TURN_ID) == receipts
        else:
            assert first['interaction_contract'] == full['interaction_contract']
            assert 'action_signature' not in first and 'quota' in first
            assert 'Use TurnEnvelope:' not in body
            assert '--turn-envelope' not in argv
    else:
        assert not root.exists()
        full = first
        assert_complete_turn_decision(full)
    selection = full['interaction_contract']['cli_channel']['selection_command']
    command = selection['route_prefix'] + ' ' + selection['command_args_template'].replace(
        '{todo_id}', ALTERNATIVE_TODO_ID)
    rc, selected = _run_generated_cli(command, registry_path=registry)
    assert rc == 0, selected
    selected = complete_page(selected)
    if captured:
        paths = list(root.glob('*/decision.json'))
        assert len(paths) == 2
        assert json.loads(captures[0].read_text()) == full
        selected_full = json.loads(next(p for p in paths if p != captures[0]).read_text())
        assert_complete_turn_decision(selected_full)
        if enabled:
            assert_original_contract_view(selected, selected_full)
            assert selected['schema_version'] == first['schema_version']
            cli = (selected['writeback'] if selected['schema_version'] == 'loopx_turn_envelope_v0'
                else selected['interaction_contract']['cli_channel'])
            assert cli['settlement_plan'] == selected_full['interaction_contract']['cli_channel']['settlement_plan']
        else:
            assert selected['interaction_contract'] == selected_full['interaction_contract']
            assert 'action_signature' not in selected
    else:
        selected_full = selected
        assert_complete_turn_decision(selected_full)
    identity = selected_full['heartbeat_receipt']['settlement_identity']
    assert identity['todo_id'] == ALTERNATIVE_TODO_ID
    assert identity['turn_instance_id'] == TURN_ID
    before = _heartbeat_receipt_count(runtime, TURN_ID)
    rc, replay = _run_generated_cli(command, registry_path=registry)
    assert rc == 0, replay
    assert _heartbeat_receipt_count(runtime, TURN_ID) == before
    if captured:
        assert len(list(root.glob('*/decision.json'))) == 3
    rc, refresh = _run_cli(
        registry, runtime, 'refresh-state', '--goal-id', GOAL_ID, '--agent-id', AGENT_ID,
        '--todo-id', ALTERNATIVE_TODO_ID, '--turn-instance-id', TURN_ID,
        '--classification', 'validated_progress', '--delivery-batch-scale', 'implementation',
        '--delivery-outcome', 'outcome_progress', '--delivery-boundary', 'in_flight_continuation',
        '--no-global-sync', '--suppress-external-sinks', cwd=project)
    assert rc == 0, refresh
    spend = refresh['settlement_owed']['command']
    for _ in range(2):
        rc, settled = _run_cli(registry, runtime, *shlex.split(spend)[1:])
        assert rc == 0, settled
    assert _spend_run_count(runtime) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("native", [False, True], ids=["inline", "native"])
def test_default_worker_recovery_preserves_complete_source_and_settlement(tmp_path, monkeypatch, provider, native):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane.codex_checkpoint_fixture import read_cli_policy_payload
    from tests.control_plane.test_quota_settlement_cli import _configure_read_only_todo

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_read_only_todo(project)
    _initialize_fixture_authority(project, runtime, provider=provider)
    logs = tmp_path / "wake logs"
    cli = str(Path(sys.executable).parent / "loopx")
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), LOOPX_CLI=cli,
        LOOPX_REGISTRY=str(registry), LOOPX_RUNTIME_ROOT=str(runtime),
        LOOPX_PROJECT=str(project), LOOPX_GOAL_ID=GOAL_ID, LOOPX_AGENT_ID=AGENT_ID,
        LOOPX_WAKE_LOG_DIR=str(logs), LOOPX_EXECUTION_MODE="heartbeat",
        LOOPX_CONTEXT_DELIVERY="on" if native else "off",
        LOOPX_TURN_ENVELOPE="0", LOOPX_COMPLETE_TURN="0")
    if native:
        context = tmp_path / "native context"
        context.mkdir(mode=0o700)
        env["LOOPX_CODEX_CONTEXT_DIR"] = str(context)

    def run(argv):
        result = subprocess.run(argv, env=env, cwd=project, capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stdout + result.stderr
        return read_cli_policy_payload(result.stdout, returncode=result.returncode)

    def guard(turn):
        (logs / turn).mkdir(parents=True)
        body = heartbeat_body(env, turn, native_goal=False)
        command = next(line for line in body.splitlines() if " quota should-run " in line)
        argv = shlex.split(command.replace('${LOOPX_TURN:?}', turn))
        assert "--complete-turn" in argv and "--turn-envelope" not in argv
        return run([*argv, "--todo-id", TODO_ID])

    original_turn, later_turn = "original-worker-turn", "later-worker-turn"
    original = guard(original_turn)
    assert_complete_turn_decision(original)
    identity = original["heartbeat_receipt"]["settlement_identity"]
    recovery = guard(later_turn)
    assert recovery["unsettled_host_turn_recovery"]["repair"] == "resume_prior_turn"
    actions = recovery["interaction_contract"]["cli_channel"]["next_cli_actions"]
    resume, reenter = map(shlex.split, (actions[1], actions[-1]))
    for argv in (resume, reenter):
        assert "--complete-turn" in argv
        assert not {"--turn-envelope", "--decision-handoff-socket",
                    "--codex-context-dir", "--codex-context-root"} & set(argv)
        if native:
            assert argv[argv.index("--context-delivery") + 1] == "auto"
            assert argv[argv.index("--delta-delivery") + 1] == "off"
            assert argv[argv.index("--decision-output-root") + 1] == str(logs / later_turn / "decisions")
        else:
            assert argv[argv.index("--context-delivery") + 1] == "off"
    resumed = run(resume)
    assert_complete_turn_decision(resumed)
    plan = resumed["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert resumed["heartbeat_receipt"]["settlement_identity"] == plan["identity"] == identity
    assert plan["schema_version"] == "quota_settlement_plan_v2"
    assert next(step for step in plan["ordered_steps"] if step["kind"] == "quota_spend")["owner"] == "runtime"
    assert _heartbeat_receipt_count(runtime, original_turn) == 1 and _spend_run_count(runtime) == 0

    writeback = next(step for step in plan["ordered_steps"] if step["kind"] == "durable_writeback")
    command = writeback["command_template"]
    for token, value in {
        "<validated_progress>": "verified_worker_result", "<scale>": "implementation",
        "<outcome>": "outcome_progress", "<boundary>": "in_flight_continuation",
    }.items():
        command = command.replace(token, value)
    command += " --delivery-boundary in_flight_continuation --no-global-sync --suppress-external-sinks"
    settled = run(shlex.split(command))
    assert settled["settlement_progress"]["state"] == "settled"
    assert settled["settlement_identity"] == identity
    assert run(shlex.split(command))["idempotent_replay"]
    continued = run(reenter)
    assert_complete_turn_decision(continued)
    assert continued["heartbeat_receipt"]["settlement_identity"]["turn_instance_id"] == later_turn
    assert _heartbeat_receipt_count(runtime, original_turn) == 1 and _spend_run_count(runtime) == 1


def test_native_worker_and_stable_bootstrap_cannot_use_capture(tmp_path):
    project, runtime, registry = _write_fixture(tmp_path)
    env = dict(os.environ, LOOPX_CLI='unused', LOOPX_REGISTRY=str(registry),
               LOOPX_RUNTIME_ROOT=str(runtime), LOOPX_PROJECT=str(project),
               LOOPX_GOAL_ID=GOAL_ID, LOOPX_AGENT_ID=AGENT_ID,
               LOOPX_WAKE_LOG_DIR=str(tmp_path / 'absent'), LOOPX_TURN_ENVELOPE='1')
    with pytest.raises(ValueError, match='host-owned'):
        heartbeat_body(env, TURN_ID, native_goal=True)
    assert not (tmp_path / 'absent').exists()
    rc, rejected = _run_cli(
        registry, runtime, 'heartbeat-prompt', '--bootstrap', '--thin',
        '--runtime-profile', 'generic_cli', '--goal-id', GOAL_ID, '--agent-id', AGENT_ID,
        '--turn-instance-id', TURN_ID, '--decision-output-root', str(tmp_path))
    assert rc != 0 and rejected['ok'] is False
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 0
    assert not list(tmp_path.glob('decision-*'))


@pytest.mark.skipif(os.name != 'posix', reason='Requires a private Unix socket')
def test_renderer_retains_owned_handoff_without_enabling_it_by_default(tmp_path):
    import socket
    import tempfile
    args = dict(goal_id=GOAL_ID, agent_id=AGENT_ID, runtime_profile='generic_cli',
                turn_instance_id=TURN_ID, thin=True)
    assert build_heartbeat_prompt(**args) == build_heartbeat_prompt(**args, decision_handoff_socket=None)
    with tempfile.TemporaryDirectory(prefix='hb-handoff-', dir='/tmp') as directory:
        endpoint = str(Path(directory).resolve() / 'host.sock')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(endpoint)
            packet = build_heartbeat_prompt(**args, decision_output_root=tmp_path,
                                            decision_handoff_socket=endpoint)
            for field in ('quota_guard_command', 'thin_prompt_command'):
                argv = shlex.split(packet[field])
                assert argv[argv.index('--decision-handoff-socket') + 1] == endpoint
                assert '--turn-envelope' not in argv
            assert 'Use TurnEnvelope:' not in packet['task_body']
            with pytest.raises(ValueError, match='capture root'):
                build_heartbeat_prompt(**args, decision_handoff_socket=endpoint)
    with pytest.raises(ValueError, match='invalid decision handoff socket'):
        build_heartbeat_prompt(**args, decision_output_root=tmp_path, decision_handoff_socket=endpoint)


@pytest.mark.parametrize("profile", [None, "generic_cli", "claude_code"])
def test_native_context_prompt_rejects_unqualified_on_and_keeps_off_inert(tmp_path, monkeypatch, profile):
    monkeypatch.delenv("LOOPX_EXECUTION_MODE", raising=False)
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    args = dict(goal_id=GOAL_ID, agent_id=AGENT_ID, runtime_profile=profile,
                thin=True)
    assert_explicit_off_keeps_legacy_prompt(build_heartbeat_prompt(**args), build_heartbeat_prompt(**args, context_delivery="off"))
    with pytest.raises(ValueError, match="supported declared run mode"):
        build_heartbeat_prompt(**args, context_delivery="on", codex_context_dir=str(tmp_path / "absent"))
    assert list(tmp_path.iterdir()) == []


def test_native_context_prompt_keeps_capture_on_reentry(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", "on")
    monkeypatch.setenv("LOOPX_EXECUTION_MODE", "heartbeat")
    args = dict(goal_id=GOAL_ID, agent_id=AGENT_ID, runtime_profile="generic_cli",
                turn_instance_id=TURN_ID, thin=True, decision_output_root=tmp_path)
    plain = build_heartbeat_prompt(**args)
    context = str(tmp_path / "private context")
    enabled = build_heartbeat_prompt(**args, context_delivery="on", codex_context_dir=context)
    suffix = " --context-delivery on --codex-context-dir " + shlex.quote(context)
    assert enabled["quota_guard_command"] == plain["quota_guard_command"] + suffix
    assert suffix in enabled["thin_prompt_command"]
    assert enabled["task_body"].replace(suffix, "") == plain["task_body"]
    with pytest.raises(ValueError, match="capture root"):
        build_heartbeat_prompt(**(args | {"decision_output_root": None}), context_delivery="on", codex_context_dir=context)
    with pytest.raises(ValueError, match="invalid decision handoff socket"):
        build_heartbeat_prompt(**args, context_delivery="on", codex_context_dir=context, decision_handoff_socket="unused")
    with tempfile.TemporaryDirectory(prefix="context-handoff-", dir="/tmp") as short:
        endpoint = str(Path(short).resolve() / "host.sock")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(endpoint)
            packet = build_heartbeat_prompt(**args, context_delivery="on", codex_context_dir=context, decision_handoff_socket=endpoint)
            for field in ("quota_guard_command", "thin_prompt_command"):
                argv = shlex.split(packet[field])
                assert argv[argv.index("--decision-output-root") + 1] == str(tmp_path)
                assert argv[argv.index("--decision-handoff-socket") + 1] == endpoint
                assert argv[argv.index("--codex-context-dir") + 1] == context


@pytest.mark.parametrize("turn_envelope", [False, True])
def test_ssh_goal_capture_keeps_native_begin_turn_and_off_boundary(tmp_path, turn_envelope):
    args = dict(goal_id=GOAL_ID, agent_id=AGENT_ID, runtime_profile="codex_app_ssh_goal", thin=True)
    original = build_heartbeat_prompt(**args)
    assert_explicit_off_keeps_legacy_prompt(original, build_heartbeat_prompt(**args, context_delivery="off"))
    assert "--decision-output-root" not in original["quota_guard_command"]
    with pytest.raises(ValueError, match="host-owned"):
        build_heartbeat_prompt(**args, decision_output_root=tmp_path)
    enabled = build_heartbeat_prompt(**args, context_delivery="on", decision_output_root=tmp_path,
        codex_context_root=str(tmp_path), turn_envelope=turn_envelope)
    guard = enabled["quota_guard_command"]
    assert "--runtime-profile codex_app_ssh_goal --begin-turn" in guard
    assert "--heartbeat-turn-receipt" not in guard and "${LOOPX_TURN" not in guard
    assert "--decision-output-root" in guard and "--codex-context-root" in guard
    assert "visible-goal" in enabled["quota_spend_command"]
    assert enabled["interface_budget"]["within_budget"]
    assert shlex.split(guard).count("--complete-turn") == 1
    assert "--turn-envelope" not in shlex.split(guard)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("delivery", ["on", "off", "delta-only", "saved-loader", "saved-loader-off"])
def test_codex_app_thin_guard_uses_complete_source_for_new_captures(tmp_path, monkeypatch, provider, delivery):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane.test_quota_settlement_cli import _configure_read_only_todo

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_read_only_todo(project)
    _initialize_fixture_authority(project, runtime, provider=provider)
    captures = tmp_path / "captures"
    captures.mkdir(mode=0o700)
    inline = delivery in {"off", "saved-loader-off"}
    options = ("--thin", "--codex-app", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--context-delivery", "off" if inline or delivery == "delta-only" else "auto")
    if delivery != "saved-loader-off":
        options += ("--decision-output-root", str(captures))
    if not inline:
        options += ("--codex-context-root", str(captures))
    if delivery == "delta-only":
        options += ("--delta-delivery", "auto")
    if delivery == "off":
        # Without a native directory, an App loader needs its known Turn ID
        # to choose a capture destination before it can render a guard.
        options += ("--turn-instance-id", TURN_ID)
    if delivery in {"saved-loader", "saved-loader-off"}:
        # A stored bootstrap written before C1 has no complete-source flag.
        rc, bootstrap = _run_cli(registry, runtime, "heartbeat-prompt", *options, "--bootstrap", cwd=project)
        assert rc == 0, bootstrap
        command = bootstrap["task_body"].split("```sh\n", 1)[1].split("\n```", 1)[0]
        rc, prompt = _run_generated_cli(command, registry_path=registry, cwd=project)
    else:
        rc, prompt = _run_cli(registry, runtime, "heartbeat-prompt", *options, cwd=project)
    assert rc == 0, json.dumps(prompt)
    guard = shlex.split(next(line for line in prompt["task_body"].splitlines()
        if " quota should-run " in line))
    assert not {"--complete-turn", "--turn-envelope"}.intersection(guard)
    guard[guard.index("--turn-instance-id") + 1] = TURN_ID
    rc, view = _run_cli(registry, runtime, *guard[guard.index("quota"):],
        "--todo-id", TODO_ID, cwd=project, raw_guard=False)
    assert rc == 0, view
    if delivery == "saved-loader-off":
        assert not list(captures.iterdir())
        source = view
    else:
        (saved,) = captures.glob("decision-*/decision.json")
        source = json.loads(read_original_capture(saved, registry=registry, runtime=runtime))
    assert_complete_turn_decision(source)
    if inline:
        assert view == source
        assert not {"context_delivery", "first_read_command", "reference_file"} & view.keys()
    else:
        recovery = shlex.split(view["recovery"]["command"])
        recovery.extend(("--select", "/heartbeat_receipt"))
        read = subprocess.run(recovery, cwd=project, capture_output=True, text=True, timeout=30)
        assert read.returncode == 0, read.stdout + read.stderr
        page = json.loads(read.stdout)
        assert page["ok"] and page["eof"], page
        assert json.loads(page["content"])["selections"] == [
            {"pointer": "/heartbeat_receipt", "value": source["heartbeat_receipt"]},
        ]
        if delivery == "delta-only":
            # Explicitly disabling Progressive still permits an independently
            # qualified Delta route. No required-body navigation is added.
            assert "first_read_command" not in view
    receipt = source["heartbeat_receipt"]
    plan = source["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert receipt["settlement_identity"] == plan["identity"]
    if inline:
        assert plan["schema_version"] == "quota_settlement_plan_v1"
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 1
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_antigravity_sidecar_gate_does_not_admit_work(tmp_path, monkeypatch, provider):
    from loopx.agy_goal_mode import build_antigravity_sidecar_template
    from loopx.control_plane.scheduler.scheduler_hint import SCHEDULER_HOST_FACTS_CHUNK_FLAG
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane.test_quota_settlement_cli import _configure_read_only_todo

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    monkeypatch.setenv("LOOPX_RUNTIME_ROOT", str(tmp_path / "runtime"))
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_read_only_todo(project)
    _initialize_fixture_authority(project, runtime, provider=provider)
    template = build_antigravity_sidecar_template(
        goal_id=GOAL_ID, agent_id=AGENT_ID, runtime_root=str(runtime),
    )["sidecar"]["loopx-heartbeat"]
    gate = shlex.split(template["gate_command"])
    rc, observation = _run_cli(registry, runtime, *gate[gate.index("quota"):], cwd=project, raw_guard=False)
    assert rc == 0 and observation[template["wake_when_json_field_true"]] is True, observation
    assert not {"contract_binding", "heartbeat_receipt", "first_read_command"} & observation.keys()
    assert not (runtime / "context-delivery").exists()
    rc, inline = _run_cli(
        registry, runtime, *gate[gate.index("quota"):-1], "--context-delivery", "off",
        cwd=project, raw_guard=False,
    )
    assert rc == 0 and inline["should_run"] == observation["should_run"], inline

    def followup(argv):
        args, chunks = [], []
        values = iter(argv)
        for token in values:
            if token == SCHEDULER_HOST_FACTS_CHUNK_FLAG:
                chunks.append(next(values))
            else:
                args.append(token)
        assert chunks
        encoded = "".join(chunks)
        packet = json.loads(zlib.decompress(base64.b64decode(encoded + "=" * (-len(encoded) % 4))))
        assert packet["host_facts"].pop("generated_at")
        return args, packet

    for field in template["stateful_backoff"].values():
        actual, expected = observation, inline
        for segment in field.split("."):
            actual, expected = actual[segment], expected[segment]
        if field.endswith(".cli_args"):
            # A new observation timestamps its own transport, even with identical scheduler facts.
            assert followup(actual) == followup(expected), field
        else:
            assert actual == expected, field


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("host", ["antigravity_app", "trae_app"])
@pytest.mark.parametrize("delivery", ["auto", "off"])
def test_stateless_app_wake_defaults_to_complete_source_and_recovers_agent_spend(
    tmp_path, monkeypatch, provider, host, delivery,
):
    from loopx.agy_goal_mode import build_antigravity_sidecar_template
    from loopx.host_loop_activation import build_host_loop_activation_packet
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane.test_quota_settlement_cli import _configure_read_only_todo

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    monkeypatch.setenv("LOOPX_RUNTIME_ROOT", str(tmp_path / "runtime"))
    project, runtime, registry = _write_fixture(tmp_path)
    state = _configure_read_only_todo(project)
    acceptance = "Verify the aggregate before submitting evidence. 保留验收记录。\n" * 168
    state.write_text(
        state.read_text(encoding="utf-8").replace("## Next Action", acceptance + "\n## Next Action", 1),
        encoding="utf-8",
    )
    _initialize_fixture_authority(project, runtime, provider=provider)
    if host == "trae_app":
        activation = build_host_loop_activation_packet(
            agent_type="trae_app", goal_id=GOAL_ID, agent_id=AGENT_ID,
            registered_agents=[AGENT_ID], runtime_root=str(runtime), registry=str(registry),
        )
        command = activation["activation_input_command"]
        if delivery == "off":
            command += " --context-delivery off"
        rc, loader = _run_generated_cli(command, registry_path=registry, cwd=project)
        assert rc == 0 and loader["ok"], loader
        bootstrap = loader["task_body"]
    elif delivery == "auto":
        bootstrap = build_antigravity_sidecar_template(
            goal_id=GOAL_ID, agent_id=AGENT_ID, runtime_root=str(runtime),
        )["sidecar"]["loopx-heartbeat"]["bootstrap_prompt"]
    else:
        rc, loader = _run_cli(
            registry, runtime, "heartbeat-prompt", "--thin", "--antigravity_app", "--context-delivery", "off",
            "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--bootstrap", cwd=project,
        )
        assert rc == 0 and loader["ok"], loader
        bootstrap = loader["task_body"]
    command = bootstrap.split("```sh\n", 1)[1].split("\n```", 1)[0]
    rc, prompt = _run_generated_cli(command, registry_path=registry, cwd=project)
    assert rc == 0 and prompt["ok"], prompt
    command = next(line for line in prompt["task_body"].splitlines() if " quota should-run " in line)
    guard = shlex.split(command)
    assert not {"--complete-turn", "--turn-envelope", "--codex-context-root"} & set(guard)
    assert guard[guard.index("--context-delivery") + 1] == delivery
    guard[guard.index("--turn-instance-id") + 1] = TURN_ID
    guard = guard[guard.index("quota"):]

    def read_pages(command, *, max_output_bytes, selection=False):
        reference, body = None, b""
        schema = "context_capture_selection_page_v1" if selection else "context_capture_page_v1"
        pin = "reference_file" if selection else "sha256"
        while True:
            result = subprocess.run(
                shlex.split(command), cwd=project, capture_output=True, timeout=30,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            assert len(result.stdout) <= max_output_bytes
            page = json.loads(result.stdout)
            assert page["ok"] and page["schema_version"] == schema and page["offset"] == len(body), page
            reference = reference or page[pin]
            assert page[pin] == reference, page
            body += page["content"].encode()
            if page["eof"]:
                assert len(body) == page["total_bytes"]
                if not selection:
                    assert hashlib.sha256(body).hexdigest() == reference
                return body
            command = page["next_command"]

    def read_current(view):
        if delivery == "off":
            assert not {"context_delivery", "first_read_command", "reference_file"} & view.keys()
            return view
        assert view["schema_version"] == "context_capture_navigation_v1" and view["mode"] == "full"
        return json.loads(read_pages(view["recovery"]["command"], max_output_bytes=64000))

    rc, refused = _run_cli(
        registry, runtime, *guard, "--context-delivery", "on", "--delta-delivery", "auto",
        cwd=project, raw_guard=False,
    )
    assert rc != 0 and refused["ok"] is False, refused
    assert _heartbeat_receipt_count(runtime, TURN_ID) == _spend_run_count(runtime) == 0

    rc, view = _run_cli(registry, runtime, *guard, cwd=project, raw_guard=False)
    assert rc == 0, view
    source = read_current(view)
    assert source["should_run"] is True, source
    assert_complete_turn_decision(source)
    assert source["selected_todo"]["todo_id"] == TODO_ID
    identity = source["heartbeat_receipt"]["settlement_identity"]
    plan = source["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert plan["schema_version"] == "quota_settlement_plan_v1"
    assert plan["identity"] == identity and identity["turn_instance_id"] == TURN_ID
    writeback = next(step for step in plan["ordered_steps"] if step["kind"] == "durable_writeback")
    spend = next(step for step in plan["ordered_steps"] if step["kind"] == "quota_spend")
    assert spend["owner"] == "agent" and "--settle-turn" not in writeback["command_template"]

    if delivery == "auto":
        body = read_pages(view["first_read_command"], max_output_bytes=12000, selection=True)
        rows = {row["pointer"]: row["value"] for row in json.loads(body)["selections"]}
        agent_path = "/interaction_contract/agent_channel"
        assert rows["/heartbeat_receipt"]["settlement_identity"] == identity
        assert rows["/selected_todo"]["todo_id"] == TODO_ID
        assert any(acceptance in value for pointer, value in rows.items()
                   if pointer.startswith(agent_path + "/work_context/sources/") and pointer.endswith("/content/text"))
        assert rows[agent_path + "/required_reads"] == source["interaction_contract"]["agent_channel"]["required_reads"]
        assert rows["/interaction_contract/cli_channel/settlement_plan/identity"] == identity
    else:
        assert not (runtime / "context-delivery").exists()

    command = writeback["command_template"]
    for token, value in {
        "<validated_progress>": "stateless_app_fixture_progress", "<scale>": "implementation",
        "<outcome>": "outcome_progress", "<boundary>": "in_flight_continuation",
    }.items():
        command = command.replace(token, value)
    command += " --delivery-boundary in_flight_continuation --no-global-sync --suppress-external-sinks"
    rc, write_result = _run_generated_cli(command, registry_path=registry, cwd=project)
    assert rc == 0 and write_result["settlement_identity"] == identity, write_result
    assert _spend_run_count(runtime) == 0

    # A lost writeback response leaves only the original spend due.
    rc, view = _run_cli(registry, runtime, *guard, "--todo-id", TODO_ID, cwd=project, raw_guard=False)
    assert rc == 0, view
    recovery = read_current(view)
    assert recovery["normal_delivery_allowed"] is False, recovery
    assert_complete_turn_decision(recovery)
    assert recovery["settlement_progress"]["state"] == "spend_required"
    [spend_command] = recovery["interaction_contract"]["cli_channel"]["next_cli_actions"]
    assert "spend-slot" in spend_command
    rc, settled = _run_generated_cli(spend_command, registry_path=registry, cwd=project)
    assert rc == 0 and settled["settlement_progress"]["state"] == "settled", settled
    assert settled["settlement_identity"] == identity
    rc, replay = _run_generated_cli(spend_command, registry_path=registry, cwd=project)
    assert rc == 0 and replay["ok"] and replay["appended"] is False, replay
    rc, view = _run_cli(registry, runtime, *guard, "--todo-id", TODO_ID, cwd=project, raw_guard=False)
    assert rc == 0, view
    assert read_current(view)["should_run"] is False
    assert _heartbeat_receipt_count(runtime, TURN_ID) == _spend_run_count(runtime) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("delivery", ["on", "off", "saved-loader"])
def test_ssh_goal_generated_command_uses_complete_source(tmp_path, monkeypatch, provider, delivery):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane.test_quota_settlement_cli import _configure_read_only_todo

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_read_only_todo(project)
    _initialize_fixture_authority(project, runtime, provider=provider)
    captures = tmp_path / "capture"
    captures.mkdir(mode=0o700)
    options = ("--thin", "--runtime-profile", "codex_app_ssh_goal",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    if delivery == "off":
        options += ("--context-delivery", "off")
    else:
        options += ("--context-delivery", "on", "--decision-output-root", str(captures),
            "--codex-context-root", str(captures))
    if delivery == "saved-loader":
        # The old bootstrap keeps its exact old invocation. Running that
        # generator with the new runtime must produce a current guard.
        rc, bootstrap = _run_cli(registry, runtime, "heartbeat-prompt",
            *options, "--turn-envelope", "--bootstrap", cwd=project)
        assert rc == 0, bootstrap
        command = bootstrap["task_body"].split("```sh\n", 1)[1].split("\n```", 1)[0]
        assert "--turn-envelope" in shlex.split(command)
        rc, prompt = _run_generated_cli(command, registry_path=registry, cwd=project)
    else:
        rc, prompt = _run_cli(registry, runtime, "heartbeat-prompt", *options, cwd=project)
    assert rc == 0, prompt
    guard = prompt["quota_guard_command"]
    assert guard in prompt["task_body"]
    rc, current = _run_generated_cli(guard + f" --todo-id {TODO_ID}", registry_path=registry, cwd=project)
    assert rc == 0, current
    assert_complete_turn_decision(current)
    assert "detail_ref.full_decision" not in prompt["task_body"]
    assert shlex.split(guard).count("--complete-turn") == 1
    assert "--turn-envelope" not in shlex.split(guard)
    identity = current["heartbeat_receipt"]["settlement_identity"]
    plan = current["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert plan["identity"] == identity
    assert identity["todo_id"] == TODO_ID
    assert _heartbeat_receipt_count(runtime, identity["turn_instance_id"]) == 1
    assert _spend_run_count(runtime) == 0
    if delivery == "off":
        assert "--context-delivery off" in guard and not list(captures.iterdir())
    else:
        (saved,) = captures.glob("decision-*/decision.json")
        original = json.loads(read_original_capture(saved, registry=registry, runtime=runtime))
        assert original["contract_binding"] == current["contract_binding"]
        assert _heartbeat_receipt_count(runtime, identity["turn_instance_id"]) == 1
        assert "same-capture read/recovery instructions" in prompt["task_body"]

    assert plan["schema_version"] == "quota_settlement_plan_v2"
    writeback = next(step for step in plan["ordered_steps"] if step["kind"] == "durable_writeback")
    command = writeback["command_template"]
    for key, value in {"<validated_progress>": "verified_source", "<scale>": "implementation",
                       "<outcome>": "outcome_progress", "<boundary>": "in_flight_continuation"}.items():
        command = command.replace(key, value)
    command += " --delivery-boundary in_flight_continuation --no-global-sync --suppress-external-sinks"
    rc, settled = _run_generated_cli(command, registry_path=registry, cwd=project)
    assert rc == 0 and settled["settlement_progress"]["state"] == "settled", settled
    assert settled["settlement_identity"] == identity
    rc, replay = _run_generated_cli(command, registry_path=registry, cwd=project)
    assert rc == 0 and replay["idempotent_replay"], replay
    assert replay["settlement_identity"] == identity and _spend_run_count(runtime) == 1
    assert _heartbeat_receipt_count(runtime, identity["turn_instance_id"]) == 1


@pytest.mark.parametrize("delivery", ["default", "off", "on", "envelope"])
def test_visible_goal_reads_required_material_without_unconditional_capture_traversal(tmp_path, delivery):
    args = dict(goal_id=GOAL_ID, agent_id=AGENT_ID,
                runtime_profile="codex_app_ssh_goal", thin=True)
    if delivery == "off":
        args["context_delivery"] = "off"
    elif delivery in {"on", "envelope"}:
        args.update(context_delivery="on", decision_output_root=tmp_path,
                    codex_context_root=str(tmp_path), turn_envelope=delivery == "envelope")
    prompt = build_heartbeat_prompt(**args)
    body = prompt["task_body"]
    assert "work_context" in body and "required_reads" in body and "before work" in body
    assert "Complete visible Goal only on" in body
    assert "no work/spend/completion claim" in body
    assert "exact identities/effect ids" in body.lower()
    for enlarged_prerequisite in (
        "Read the complete captured contract before selection",
        "Read complete same-invocation detail_ref.full_decision before",
    ):
        assert enlarged_prerequisite not in body
    if delivery in {"on", "envelope"}:
        assert "same-capture read/recovery instructions" in body
        assert "detail_ref.full_decision" not in body
    if delivery == "off":
        assert "--context-delivery off" in prompt["quota_guard_command"]
        assert "--decision-output-root" not in prompt["quota_guard_command"]


@pytest.mark.parametrize("turn_envelope", [False, True])
@pytest.mark.parametrize("path_width,cli_width", [(160, 140), (133, 180)])
def test_ssh_goal_capture_fits_host_with_full_command_paths(tmp_path, turn_envelope, path_width, cli_width):
    # Fixed physical path width qualifies the shipped command workload on every
    # OS; the native cases also retain their actual, unshortened pytest paths.
    with stable_budget_fixture_root(tmp_path) as base:
        root = (base / ("p" * (path_width - len(str(base.resolve())) - 1))).resolve()
        root.mkdir()
        # Fix both dimensions, rather than inheriting this checkout's path.
        # The second profile reproduces the installed executable's width.
        cli_root = (base / ("c" * (cli_width - len(str(base.resolve())) - 7))).resolve()
        cli = str(cli_root / "loopx")
        assert len(cli) == cli_width
        captures, context = root / "captures", root / "context"
        captures.mkdir()
        context.mkdir()
        paths = {"registry_path": root / "project/.loopx/registry.json",
                 "runtime_root": root / "runtime", "cli_bin": cli}
        args = dict(goal_id=GOAL_ID, agent_id=AGENT_ID, runtime_profile="codex_app_ssh_goal",
                    thin=True, **paths)
        ordinary = build_heartbeat_prompt(**args)
        assert_explicit_off_keeps_legacy_prompt(ordinary, build_heartbeat_prompt(**args, context_delivery="off"))
        enabled = build_heartbeat_prompt(**args, context_delivery="on", decision_output_root=captures,
            codex_context_root=str(context), turn_envelope=turn_envelope)
        body, command = enabled["task_body"], shlex.split(enabled["quota_guard_command"])
        assert enabled["interface_budget"]["max_chars"] == 4000 and len(body) <= 4000
        for option, value in (("--registry", paths["registry_path"]),
                              ("--runtime-root", paths["runtime_root"]),
                              ("--decision-output-root", captures), ("--codex-context-root", context)):
            assert command[command.index(option) + 1] == str(value)
        assert command[0] == paths["cli_bin"]
        assert "--begin-turn" in command and "--heartbeat-turn-receipt" not in command
        assert "--context-delivery" in command and "LOOPX_TURN=" not in body
        assert "detail_ref.full_decision" not in body
        with pytest.raises(ValueError, match="4000-character"):
            build_heartbeat_prompt(**args, context_delivery="on", decision_output_root=captures,
                codex_context_root=str(context), turn_envelope=turn_envelope, agent_scopes=[c * 320 for c in "stu"])
