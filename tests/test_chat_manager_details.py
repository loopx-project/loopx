import loopx.chat_manager_details as details
from loopx.chat_manager import manager_workspace


def test_current_owner_decision_keeps_task_meaning_and_relation(monkeypatch, tmp_path):
    monkeypatch.setattr(details, 'list_goal_todos', lambda **_: {
        'ok': True, 'source': 'file_authority',
        'authority_read': {'provider_revision': 'revision-current'},
        'todos': [
            {'todo_id': 'todo_closed', 'role': 'user', 'status': 'done', 'text': 'Old decision'},
            {'todo_id': 'todo_work', 'role': 'agent', 'status': 'open', 'text': 'Publish the reviewed launch packet'},
            {'todo_id': 'todo_decide', 'role': 'user', 'task_class': 'user_gate', 'status': 'open',
             'text': 'Approve the launch copy', 'unblocks_todo_id': 'todo_work', 'blocks_agent': 'worker',
             'note': 'Confirm the proposed account and claims.'},
        ],
    })
    result = details.read_manager_goal_details(tmp_path/'r', tmp_path, 'alpha', owner_scope=True)
    assert result['status'] == 'read'
    assert result['coverage'] == {'active': 2, 'included': 2, 'omitted': 0}
    assert result['authority_revision'] == 'revision-current'
    assert result['todos'][0]['title'] == 'Approve the launch copy'
    assert result['todos'][0]['unblocks_todo_id'] == 'todo_work'
    assert result['todos'][1]['title'] == 'Publish the reviewed launch packet'
    assert result['completed_todos'][0]['todo_id'] == 'todo_closed'
    assert all(r['todo_id'] != 'todo_closed' for r in result['todos'])
    external = details.read_manager_goal_details(tmp_path/'r', tmp_path, 'alpha', owner_scope=False, limit=1)
    assert external['coverage']['omitted'] == 1
    assert 'continuation' not in external['todos'][0]


def test_unreadable_todos_do_not_look_empty_or_expose_error(monkeypatch, tmp_path):
    def unavailable(**_):
        raise OSError('private failure body')
    monkeypatch.setattr(details, 'list_goal_todos', unavailable)
    result = details.read_manager_goal_details(tmp_path/'r', tmp_path, 'alpha', owner_scope=True)
    assert result['status'] == 'unavailable'
    assert result['coverage']['active'] is None
    assert 'private failure body' not in str(result)


def test_manager_receives_default_operating_instructions_without_overwriting_custom(tmp_path):
    workspace = manager_workspace(tmp_path)
    instructions = workspace / 'AGENTS.md'
    assert 'current_todos' in instructions.read_text()
    assert 'not an ID-only inventory' in instructions.read_text()
    instructions.write_text('Custom owner instructions')
    manager_workspace(tmp_path)
    assert instructions.read_text() == 'Custom owner instructions'


def test_stale_progress_does_not_strip_fresh_todo_details(monkeypatch, tmp_path):
    import loopx.chat_manager_context as context

    calls = []
    monkeypatch.setattr(context, 'build_goal_portfolio', lambda **_: {
        'goals': [{'goal_id': 'alpha', 'quality': 'stale', 'progress': 'unknown', 'agents': []}],
        'coverage': {'discovered': 1, 'stale': 1},
    })
    def read(**kwargs):
        calls.append(kwargs['goal_id'])
        return {'ok': True, 'source': 'file_authority', 'todos': [
            {'todo_id': 'todo_decide', 'role': 'user', 'status': 'open',
             'task_class': 'user_gate', 'text': 'Approve the corrected launch claims'},
        ]}
    monkeypatch.setattr(details, 'list_goal_todos', read)
    result = context.manager_turn_context(tmp_path/'registry.json', {'channel_id': 'manager'}, tmp_path)
    row = result['goals'][0]
    assert row['quality'] == 'stale' and row['progress'] == 'unknown'
    assert row['current_todos']['status'] == 'read'
    assert row['current_todos']['todos'][0]['title'] == 'Approve the corrected launch claims'
    assert calls == ['alpha']


def test_responsible_actor_claimant_and_declared_cadence_remain_distinct(monkeypatch, tmp_path):
    monkeypatch.setattr(details, "list_goal_todos", lambda **_: {
        "ok": True, "source": "file_authority", "todos": [
            {"todo_id": "monitor", "status": "open", "agent_id": "responsible-agent",
             "claimed_by": "claiming-agent", "cadence": "4h", "text": "Inspect when due"},
            {"todo_id": "unassigned", "status": "open", "text": "Await assignment"},
        ],
    })
    result = details.read_manager_goal_details(tmp_path / "registry", tmp_path, "alpha", owner_scope=False)
    rows = {row["todo_id"]: row for row in result["todos"]}
    assert rows["monitor"]["agent_id"] == "responsible-agent"
    assert rows["monitor"]["claimed_by"] == "claiming-agent"
    assert rows["monitor"]["cadence"] == "4h"
    assert all(key not in rows["unassigned"] for key in ["agent_id", "claimed_by", "cadence"])
