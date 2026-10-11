"""Creation admission preserves scope, wait/monitor obligations and error order."""
import pytest

from loopx.todos import add_goal_todo
from tests.control_plane.test_todo_authoring_scope import (
    GOAL, authored_goal as authored_goal, execution_exclusion_goal as execution_exclusion_goal,
)


@pytest.mark.parametrize('intent, error', [
    ({'status': 'invalid', 'text': ''}, 'todo status must be one of'),
    ({'text': '[P2] work', 'priority': 'P1', 'validation_command_json': '{}'},
     'priority conflicts'),
    ({'validation_command_json': '{}', 'claimed_by': 'unknown'},
     'must be a JSON string array'),
    ({'unblocks_todo_id': 'bad', 'claimed_by': 'unknown'}, 'not registered'),
    ({'unblocks_todo_id': 'bad', 'resume_when': 'bad'}, 'unsupported Todo resume'),
    ({'unblocks_todo_id': 'bad'}, 'unblocks_todo_id must use'),
    ({'resume_when': 'bad', 'monitor_metadata': {'cadence': 'bad'}},
     'unsupported Todo resume'),
])
def test_create_rejection_order_preserves_source(execution_exclusion_goal, intent, error):
    registry, state, cli = execution_exclusion_goal
    before = state.read_bytes()
    code, initial = cli('list')
    assert code == 0, initial
    with pytest.raises(ValueError, match=error):
        add_goal_todo(registry_path=registry, goal_id=GOAL, state_file=state,
            role='agent', task_class='advancement_task', **{'text': 'work', **intent})
    assert state.read_bytes() == before
    code, after = cli('list')
    assert code == 0, after
    assert after['todo_count'] == initial['todo_count'] == 0
    assert after.get('authority_read') == initial.get('authority_read')


def test_create_monitor_keeps_normalized_wait_and_due_time(execution_exclusion_goal):
    _, _, cli = execution_exclusion_goal
    code, created = cli('add',
        '--role', 'agent', '--task-class', 'continuous_monitor', '--text', '[P1] Observe',
        '--claimed-by', '\u001cAGENT\u0085A\u001f', '--resume-when',
        'resume_at:2026-10-11T10:00:00+08:00', '--cadence', '1h', '--watch-only',
        )
    assert code == 0, created
    code, listed = cli('list',
        '--todo-id', created['todo_id'], )
    assert code == 0, listed
    todo = listed['todo']
    assert todo['claimed_by'] == 'agent-a'
    assert todo['resume_when'] == 'resume_at:2026-10-11T02:00:00Z'
    assert todo['cadence'] == '1h'
    assert todo['next_due_at']


def test_public_create_composes_scope_wait_and_monitor_once(execution_exclusion_goal, monkeypatch):
    from loopx.control_plane.todos import authoring_scope, monitor_metadata, resume_condition
    registry, state, _ = execution_exclusion_goal
    actual = authoring_scope.effect_runtime_result
    calls = []

    def traced(method, params, **kwargs):
        calls.append(method)
        return actual(method, params, **kwargs)

    for module in (authoring_scope, monitor_metadata, resume_condition):
        monkeypatch.setattr(module, 'effect_runtime_result', traced)
    result = add_goal_todo(registry_path=registry, goal_id=GOAL, state_file=state,
        role='agent', text='Observe completion', task_class='continuous_monitor',
        claimed_by='agent-a', resume_when='resume_at:2026-10-11T10:00:00+08:00',
        monitor_metadata={'cadence': '1h', 'watch_only': 'true'})
    assert result['added'] is True
    assert calls == ['todo.creation_scope.plan']
