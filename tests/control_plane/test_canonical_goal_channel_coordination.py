"""Canonical ownership is authoritative even when the display/local files disagree."""
import json

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.goals.goal_channel_projection import build_goal_channel_projection
from test_goal_channel_hard_lease_visibility import GOAL_ID, _status_item, _write_active_lease


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
def test_empty_canonical_ownership_never_revives_display_claim_or_local_lease(tmp_path, monkeypatch, provider):
    if provider == 'sqlite':
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    state = tmp_path / 'state.md'
    state.write_text('---\nhandoff_mode: legacy\n---\n\n## Agent Todo\n')
    _write_active_lease(tmp_path, owner='stale-agent')
    projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID, handoff_mode='soft_claim', todos=[])
    initialize_canonical_authority(tmp_path, GOAL_ID, projection, state_path=state, provider=provider)
    before = state.read_bytes()
    observed = build_goal_channel_projection(goal_id=GOAL_ID, status_item=_status_item(claimed_by='stale-agent'), runtime_root=tmp_path)
    assert observed['active_leases'] == []
    assert observed['coordination_observation']['source_authority'] == provider + '_v0'
    assert observed['coordination_authority'] == {
        'state': 'promoted',
    }
    assert observed['mode'] == 'read_only'
    assert observed['truth_contract']['projection_is_writable'] is False
    assert observed['truth_contract']['write_authority'] == 'none'
    assert state.read_bytes() == before
    state.unlink()
    assert build_goal_channel_projection(goal_id=GOAL_ID, runtime_root=tmp_path)['active_leases'] == []


def test_explicit_empty_observation_is_not_unspecified():
    observed = build_goal_channel_projection(goal_id=GOAL_ID, status_item=_status_item(claimed_by='stale-agent'), active_leases=[])
    assert observed['active_leases'] == []
    assert observed['coordination_authority']['state'] == 'promotion_required'
    assert observed['mode'] == 'read_only'
    assert observed['truth_contract']['write_authority'] == 'none'


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
def test_canonical_failure_is_visible_and_never_discloses_or_falls_back(tmp_path, monkeypatch, provider):
    from loopx.control_plane.goals import coordination_observation as adapter
    from loopx.presentation.renderers.goal_channel_html import render_goal_channel_projection_html
    if provider == 'sqlite':
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    state = tmp_path / 'state.md'
    state.write_text('## Agent Todo\n')
    projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID, handoff_mode='legacy', todos=[])
    initialize_canonical_authority(tmp_path, GOAL_ID, projection, state_path=state, provider=provider)
    _write_active_lease(tmp_path, owner='stale-agent')
    def unavailable(*_args):
        raise RuntimeError('PRIVATE_BACKEND_PATH_AND_CREDENTIAL')
    monkeypatch.setattr(adapter, 'effect_runtime_result', unavailable)
    observed = build_goal_channel_projection(goal_id=GOAL_ID, status_item=_status_item(claimed_by='stale-agent'), runtime_root=tmp_path)
    assert observed['active_leases'] == []
    assert observed['coordination_observation']['status'] == 'unavailable'
    assert observed['coordination_authority']['state'] == 'unavailable'
    assert observed['source_warnings'][-1]['kind'] == 'coordination_unavailable'
    html = render_goal_channel_projection_html(observed)
    assert 'PRIVATE_' not in html and 'PRIVATE_' not in json.dumps(observed)
    assert 'Coordination Authority' in html
    assert 'repair_canonical_authority' in html
    assert 'Task ownership could not be read' in html
    assert 'Task ownership is unavailable; see Source Warnings.' in html
    assert 'No active claim or lease projected.' not in html


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
def test_real_cli_export_uses_canonical_ownership(tmp_path, monkeypatch, provider):
    import subprocess
    import sys
    if provider == 'sqlite':
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    from test_goal_channel_hard_lease_visibility import _write_status_workspace
    registry, repo, runtime = _write_status_workspace(tmp_path)
    _write_active_lease(runtime, owner='stale-agent')
    state = repo / 'ACTIVE_GOAL_STATE.md'
    projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID, handoff_mode='legacy', todos=[])
    initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state, provider=provider)
    before = state.read_bytes()
    result = subprocess.run([sys.executable, '-m', 'loopx.cli', '--registry', str(registry), '--format', 'json',
        'status', '--goal-id', GOAL_ID], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    def channels(value):
        if isinstance(value, dict):
            if 'goal_channel_projection' in value:
                yield value['goal_channel_projection']
            for child in value.values():
                yield from channels(child)
        elif isinstance(value, list):
            for child in value:
                yield from channels(child)
    found = list(channels(payload))
    assert found, payload.keys()
    assert all(item['active_leases'] == [] for item in found)
    assert all(item['coordination_observation']['source_authority'] == provider + '_v0' for item in found)
    assert state.read_bytes() == before
