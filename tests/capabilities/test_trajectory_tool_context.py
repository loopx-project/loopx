import hashlib
import json

import pytest

from loopx.capabilities.benchmark_toolkit.trajectory_tool_context import (
    _argv, _literal_exec_argv, project_tool_context,
)


def test_literal_shell_and_exec_wrapper_are_parsed_without_execution(tmp_path):
    assert _argv('/venv/bin/loopx --format json quota context-read') == [
        '/venv/bin/loopx', '--format', 'json', 'quota', 'context-read']
    assert _argv("/bin/bash -lc 'loopx status'") == ['loopx', 'status']
    assert _argv('loopx todo --title "keep a; b"') == ['loopx', 'todo', '--title', 'keep a; b']
    wrapper = 'const r = await tools.exec_command({cmd: "loopx status", max_output_tokens: 100}); text(r.output);'
    assert _literal_exec_argv(wrapper) == ['loopx', 'status']
    for script in [
        wrapper + 'doSomething();', wrapper.replace('"loopx status"', 'getCommand()'),
        wrapper.replace('max_output_tokens: 100', '...options'),
        wrapper.replace('cmd:', '["cmd"]:'), wrapper.replace('cmd:', 'cmd: "pytest", cmd:'),
        'const r = await tools.exec_command({cmd: "loopx status"}); text("fake");',
    ]:
        assert _literal_exec_argv(script) is None
    marker = tmp_path / 'must-not-exist'
    for command in [f'loopx status; touch {marker}', 'loopx status && pytest',
                    'loopx status | cat', 'loopx status > out', 'loopx status\npytest',
                    '$(echo loopx) status', '`echo loopx` status', 'loopx "unterminated']:
        assert _argv(command) is None
    assert not marker.exists()


@pytest.mark.parametrize('script', [
    'text(await tools.exec_command({cmd: "loopx --format json quota context-read"}))',
    'text((await tools.exec_command({\n cmd: "loopx --format json quota context-read", tty: false})).output);',
])
def test_direct_result_wrappers_preserve_one_literal_invocation(script):
    assert _literal_exec_argv(script) == ['loopx', '--format', 'json', 'quota', 'context-read']


@pytest.mark.parametrize('script', [
    'text(await tools.exec_command({cmd: "loopx status"})); text(await tools.exec_command({cmd: "pytest"}));',
    'text((await tools.exec_command({cmd: "loopx status"})).output + extra)',
    'text(await tools.exec_command({cmd: "loopx status && pytest"}))',
    'text((await tools.exec_command({...opts, cmd: "loopx status"})).output)',
    'text(await tools.exec_command({cmd: `loopx ${command}`}))',
])
def test_direct_wrapper_rejects_mixed_or_dynamic_work(script):
    assert _literal_exec_argv(script) is None


def test_managed_heartbeat_guard_binding_is_observed_as_one_literal_invocation():
    turn = "2026-01-02T03:04:05.006Z"
    assignment = f"LOOPX_TURN={turn}"
    guard = ('/venv/bin/loopx --registry /synthetic/registry.json --format json quota should-run '
             '--goal-id fixture-goal --agent-id fixture-agent --codex-app --turn-instance-id "${LOOPX_TURN:?}"')
    command = f"{assignment}\n{guard}"
    expected = [
        "/venv/bin/loopx", "--registry", "/synthetic/registry.json", "--format", "json", "quota", "should-run",
        "--goal-id", "fixture-goal", "--agent-id", "fixture-agent", "--codex-app", "--turn-instance-id", turn,
    ]
    calls = [
        {"type": "function_call", "name": "exec_command", "call_id": "shell",
         "arguments": json.dumps({"cmd": command})},
        {"type": "custom_tool_call", "name": "exec", "call_id": "literal-wrapper",
         "input": f"text(await tools.exec_command({{cmd: {json.dumps(command)}}}));"},
    ]
    assert [row["argv"] for row in project_tool_context([
        {"type": "response_item", "payload": call} for call in calls
    ])] == [expected, expected]
    for invalid in (
        f"LOOPX_TURN=$(date -u)\n{guard}", f"{assignment}\n{guard} && pytest",
        f"{assignment}\n{guard}\npytest", f"{assignment}\n{guard} --agent-id $OTHER",
        f"{assignment}\n{guard} --turn-instance-id {turn}", f"{assignment}\n{guard} foo#bar; pytest",
        command.replace('"${LOOPX_TURN:?}"', "'${LOOPX_TURN:?}'"), command.replace("LOOPX_TURN:?", "OTHER:?"),
    ):
        assert _argv(invalid) is None


def test_projection_uses_native_response_items_not_mirrors_or_model_prose():
    def item(payload):
        return {'type': 'response_item', 'payload': payload}
    output = '状态已保存'
    rows = [item({'type': 'function_call', 'name': 'exec_command', 'call_id': 'call',
                  'arguments': json.dumps({'cmd': 'loopx status'})}),
            item({'type': 'function_call_output', 'call_id': 'call', 'output': output}),
            {'type': 'event_msg', 'payload': {'type': 'item_completed', 'output': output}},
            item({'type': 'message', 'role': 'user', 'content': [
                {'type': 'input_text', 'text': '任务'}, {'type': 'input_image'}]}),
            item({'type': 'message', 'role': 'assistant', 'content': [{'text': 'loopx status'}]}),
            item({'type': 'custom_tool_call', 'call_id': 'js', 'name': 'exec',
                  'input': 'text(await tools.exec_command({cmd: "loopx status"}));'}),
            item({'type': 'custom_tool_call_output', 'call_id': 'js', 'output': 'saved'})]
    result = project_tool_context(rows)
    assert result[0]['argv'] == ['loopx', 'status']
    assert result[1]['bytes'] == 15
    assert result[1]['sha256'] == hashlib.sha256(output.encode()).hexdigest()
    assert result[2] == {'line': 4, 'kind': 'message', 'bytes': 6, 'unmeasured_parts': 1}
    assert result[3]['argv'] == ['loopx', 'status']
    assert output not in json.dumps(result, ensure_ascii=False)
    assert len(result) == 5


def test_custom_tool_output_blocks_preserve_text_and_nontext_coverage():
    rows = [{'type': 'response_item', 'payload': {'type': 'custom_tool_call_output',
             'call_id': 'call', 'output': [{'type': 'input_text', 'text': '状态'},
                 {'type': 'input_text', 'text': '已保存'}, {'type': 'input_image'}]}}]
    value = project_tool_context(rows)[0]
    assert value['bytes'] == 15
    assert value['unmeasured_parts'] == 1
    assert value['sha256'] == hashlib.sha256('状态已保存'.encode()).hexdigest()


@pytest.mark.parametrize('arguments', ['not json', '[]', '{}', '{"cmd": 42}'])
def test_unsupported_tool_arguments_remain_unassigned(arguments):
    rows = [{'type': 'response_item', 'payload': {'type': 'function_call',
             'name': 'exec_command', 'call_id': 'call', 'arguments': arguments}}]
    assert project_tool_context(rows)[0]['argv'] is None


@pytest.mark.parametrize("command", [
    '*/loopx --format json quota should-run --turn-instance-id "${LOOPX_TURN:?}"',
    'loopx --registry ~/registry.json quota should-run --turn-instance-id "${LOOPX_TURN:?}"',
    'loopx --registry /data/{first,second}.json quota should-run --turn-instance-id "${LOOPX_TURN:?}"',
    'loopx --registry /data/?.json quota should-run --turn-instance-id "${LOOPX_TURN:?}"',
    'loopx --registry /data/[ab].json quota should-run --turn-instance-id "${LOOPX_TURN:?}"',
])
def test_bound_turn_guard_never_guesses_expanded_shell_arguments(command):
    assert _argv('LOOPX_TURN=2026-01-01T00:00:00Z\n' + command) is None


def test_bound_turn_guard_preserves_quoted_literal_glob_names():
    command = ('LOOPX_TURN=2026-01-01T00:00:00Z\n'
        '"/data/*/loopx" --registry "/data/{first,second}.json" quota should-run '
        '--turn-instance-id "${LOOPX_TURN:?}"')
    assert _argv(command) == ['/data/*/loopx', '--registry', '/data/{first,second}.json',
        'quota', 'should-run', '--turn-instance-id', '2026-01-01T00:00:00Z']


@pytest.mark.parametrize("operation", ["todo list", "status", "quota spend-slot"])
def test_turn_assignment_exception_is_only_the_published_guard(operation):
    assert _argv('LOOPX_TURN=2026-01-01T00:00:00Z\nloopx ' + operation
        + ' --turn-instance-id "${LOOPX_TURN:?}"') is None


def test_source_checkout_guard_keeps_its_literal_python_entrypoint():
    command = ('LOOPX_TURN=2026-01-01T00:00:00Z\n'
        '/venv/bin/python3 -m loopx.entrypoint --format json quota should-run '
        '--turn-instance-id "${LOOPX_TURN:?}"')
    assert _argv(command) == ['/venv/bin/python3', '-m', 'loopx.entrypoint', '--format', 'json',
        'quota', 'should-run', '--turn-instance-id', '2026-01-01T00:00:00Z']
