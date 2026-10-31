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
            item({'type': 'custom_tool_call_output', 'call_id': 'js', 'output': 'unassigned'})]
    result = project_tool_context(rows)
    assert result[0]['argv'] == ['loopx', 'status']
    assert result[1]['bytes'] == 15
    assert result[1]['sha256'] == hashlib.sha256(output.encode()).hexdigest()
    assert result[2] == {'line': 4, 'kind': 'message', 'bytes': 6, 'unmeasured_parts': 1}
    assert result[3]['argv'] is None
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
