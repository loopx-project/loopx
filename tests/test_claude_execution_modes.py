"""Native CLI authority must follow the confirmed task, not model selection."""
import io
import json
from pathlib import Path

import pytest

from loopx.chat_agent import CodexChatAgentError
from loopx.chat_providers import ClaudeCodeAdapter


class Process:
    def __init__(self, error=False):
        self.stdout = io.StringIO(json.dumps({"type": "result", "result": "fixture response", "is_error": error}) + "\n")

    def wait(self):
        return 0


@pytest.mark.parametrize('execution,writable', [(False, False), (True, True)])
def test_native_tool_authority_and_explicit_model(monkeypatch, tmp_path, execution, writable):
    commands = []
    monkeypatch.setattr('loopx.chat_providers.subprocess.Popen', lambda command, **kw: commands.append(command) or Process())
    adapter = ClaudeCodeAdapter('claude', Path(tmp_path), 'fixture-session',
        execution_mode=execution, model='provider-fixture-model', reasoning_effort='high')
    adapter.start_turn('Verify the scoped request.', lambda *_: None)
    command = commands[0]
    assert command[command.index('--model')+1] == 'provider-fixture-model'
    assert command[command.index('--effort')+1] == 'high'
    assert command[command.index('--permission-mode')+1] == ('acceptEdits' if writable else 'plan')
    tools = command[command.index('--tools')+1].split(',')
    assert ('Bash' in tools) is writable
    assert ('Edit' in tools) is writable
    assert ('--allowedTools' in command) is writable


def test_error_result_is_not_a_successful_turn(monkeypatch, tmp_path):
    monkeypatch.setattr('loopx.chat_providers.subprocess.Popen', lambda *args, **kw: Process(error=True))
    adapter = ClaudeCodeAdapter('claude', Path(tmp_path), 'fixture-session')
    events = []
    with pytest.raises(CodexChatAgentError):
        adapter.start_turn('fixture request', lambda kind, _: events.append(kind))
    assert 'answer.final' not in events
    assert not adapter.resumed

