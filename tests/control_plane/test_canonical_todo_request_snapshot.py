"""Request-local transport reuse must not become a stale decision cache."""
from copy import deepcopy

import pytest

from loopx.control_plane.coordination import local_authority as authority


def test_snapshot_isolates_nested_consumers_and_runtime_goal_keys(tmp_path, monkeypatch):
    calls = []
    source = {'provider_revision': 'rev-a', 'todos': [{'metadata': {'nested': ['original']}}]}

    def read(**kwargs):
        calls.append(kwargs)
        return deepcopy(source)

    monkeypatch.setattr(authority, 'read_canonical_todos_if_promoted', read)
    snapshot = authority.CanonicalTodoSnapshot()
    args = dict(runtime_root=tmp_path, goal_id='goal-a')
    first = snapshot.read(**args)
    first['todos'][0]['metadata']['nested'].append('consumer-edit')
    source['provider_revision'] = 'rev-b'
    assert snapshot.read(**args) == {
        'provider_revision': 'rev-a', 'todos': [{'metadata': {'nested': ['original']}}],
    }
    assert len(calls) == 1
    snapshot.read(runtime_root=tmp_path / '.', goal_id='goal-a')
    assert len(calls) == 1
    snapshot.read(runtime_root=tmp_path, goal_id='goal-b')
    snapshot.read(runtime_root=tmp_path / 'other', goal_id='goal-a')
    assert len(calls) == 3
    assert authority.CanonicalTodoSnapshot().read(**args)['provider_revision'] == 'rev-b'


@pytest.mark.parametrize('initial', [None, {'todos': [], 'provider_revision': 'empty'}])
def test_empty_and_unpromoted_observations_are_not_cache_misses(tmp_path, monkeypatch, initial):
    calls = []
    def read(**kwargs):
        calls.append(kwargs)
        return initial
    monkeypatch.setattr(authority, 'read_canonical_todos_if_promoted', read)
    snapshot = authority.CanonicalTodoSnapshot()
    for _ in range(2):
        assert snapshot.read(runtime_root=tmp_path, goal_id='goal-a') == initial
    assert len(calls) == 1


def test_failed_snapshot_cannot_recover_inside_the_same_request(tmp_path, monkeypatch):
    def fail(**kwargs):
        raise authority.LocalCoordinationAuthorityUnavailable(
            'provider unavailable', code='unavailable', payload={'detail': ['first']},
        )
    monkeypatch.setattr(authority, 'read_canonical_todos_if_promoted', fail)
    snapshot = authority.CanonicalTodoSnapshot()
    args = dict(runtime_root=tmp_path, goal_id='goal-a')
    with pytest.raises(authority.LocalCoordinationAuthorityUnavailable) as first:
        snapshot.read(**args)
    first.value.payload['detail'].append('mutated')
    monkeypatch.setattr(authority, 'read_canonical_todos_if_promoted', lambda **kwargs: {'todos': []})
    with pytest.raises(authority.LocalCoordinationAuthorityUnavailable) as again:
        snapshot.read(**args)
    assert again.value.code == 'unavailable'
    assert again.value.payload == {'detail': ['first']}
    assert authority.CanonicalTodoSnapshot().read(**args) == {'todos': []}
