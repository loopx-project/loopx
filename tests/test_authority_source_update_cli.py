"""Metadata maintenance preserves a registered material's identity and provenance."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.cli_commands.registry_authority_metadata import update_authority_source


def cli(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, '-m', 'loopx.cli', '--registry', str(path),
                           '--format', 'json', *args], capture_output=True, text=True, timeout=45,
                          env={**os.environ, 'LOOPX_USAGE_PING': '0'})


@pytest.fixture
def material(tmp_path: Path) -> Path:
    path = tmp_path / 'project.json'
    path.write_text(json.dumps({'schema_version': '0.1', 'goals': [
        {'id': 'demo', 'repo': str(tmp_path), 'authority_registry': {}}]}))
    result = cli(path, 'register-authority-source', '--goal-id', 'demo', '--source-id', 'guide',
                 '--source-kind', 'doc', '--role', 'guide', '--freshness', 'current',
                 '--source-ref', str(tmp_path / 'never-created.md'), '--revision', 'edition-2',
                 '--topic', 'setup', '--conflict-rule', 'owner decides', '--no-global-sync')
    assert result.returncode == 0, result.stdout + result.stderr
    return path


def update(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return cli(path, 'update-authority-source', '--goal-id', 'demo', '--source-id', 'guide', *args)


@pytest.mark.parametrize('preview', [False, True])
def test_source_session_update_rejected_without_writes(material: Path, preview: bool):
    from loopx.control_plane.projects.registry_codec import SOURCE_SESSION_PROFILE_ID

    registry = json.loads(material.read_text())
    registry['profile_id'] = SOURCE_SESSION_PROFILE_ID
    material.write_text(json.dumps(registry))
    before = material.read_bytes()
    runtime = material.parent / 'denied-runtime'
    result = cli(material, '--runtime-root', str(runtime), 'update-authority-source',
                 '--goal-id', 'demo', '--source-id', 'guide', '--freshness', 'stale',
                 *(['--dry-run'] if preview else []))
    assert result.returncode == 1, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload['ok'] is False and payload['written'] is False
    assert SOURCE_SESSION_PROFILE_ID in payload['error']
    assert material.read_bytes() == before
    assert not runtime.exists()


def test_preview_apply_readback_preserves_reference_topics_and_omitted_fields(material: Path):
    before = material.read_bytes()
    original = json.loads(before)['goals'][0]['authority_registry']['project_materials']['guide']
    preview = update(material, '--freshness', 'stale', '--gate-status', 'needs_access', '--dry-run')
    assert preview.returncode == 0, preview.stdout + preview.stderr
    predicted = json.loads(preview.stdout)
    assert predicted['written'] is False
    assert predicted['global_sync']['wrote'] is False
    assert material.read_bytes() == before
    applied = cli(material, '--runtime-root', str(material.parent / 'runtime'),
                  'update-authority-source', '--goal-id', 'demo', '--source-id', 'guide',
                  '--freshness', 'stale', '--gate-status', 'needs_access')
    assert applied.returncode == 0, applied.stdout + applied.stderr
    entry = json.loads(applied.stdout)['entry']
    assert entry == {**original, 'freshness': 'stale', 'gate_status': 'needs_access'}
    assert entry == predicted['entry']
    read = cli(material, 'authority-sources', '--goal-id', 'demo', '--topic', 'setup',
               '--query', 'stale needs_access edition-2')
    assert read.returncode == 0, read.stdout
    assert json.loads(read.stdout)['sources'][0]['source_ref_sha256'] == original['source_ref_sha256']
    goal = json.loads(material.read_text())['goals'][0]
    assert goal['authority_sources'][0] == entry
    shared = json.loads((material.parent / 'runtime' / 'registry.global.json').read_text())['goals'][0]
    assert shared['authority_registry']['project_material_count'] == 1
    assert shared['authority_registry'] == json.loads(applied.stdout)['authority_registry_summary']
    # Metadata recording neither dereferences the unavailable source nor grants access.
    assert not (material.parent / 'never-created.md').exists()


@pytest.mark.parametrize('args', [(), ('--freshness', ''),
                                  ('--source-id', 'absent', '--freshness', 'stale'),
                                  ('--source-id', '../bad', '--freshness', 'stale'),
                                  ('--goal-id', 'absent', '--freshness', 'stale')])
@pytest.mark.parametrize('preview', [False, True])
def test_invalid_updates_never_create_or_replace(material: Path, args: tuple[str, ...], preview: bool):
    before = material.read_bytes()
    result = update(material, *args, '--no-global-sync', *(['--dry-run'] if preview else []))
    assert result.returncode != 0
    assert json.loads(result.stdout)['written'] is False
    assert material.read_bytes() == before


def test_list_materials_and_import_metadata_preserved(material: Path):
    registry = json.loads(material.read_text())
    authority = registry['goals'][0]['authority_registry']
    original = authority['project_materials']['guide']
    original.update(schema_version='doc_registry_authority_import_v0', default_entry_count=3)
    authority['project_materials'] = [original]
    authority['topic_authority'] = {'setup': [{'source_id': 'guide'}]}
    material.write_text(json.dumps(registry))
    result = update(material, '--owner-status', 'reviewed', '--revision', 'edition-3', '--no-global-sync')
    assert result.returncode == 0, result.stdout
    entry = json.loads(result.stdout)['entry']
    assert entry == {**original, 'owner_status': 'reviewed', 'revision': 'edition-3'}
    assert json.loads(material.read_text())['goals'][0]['authority_registry']['topic_authority'] == authority['topic_authority']


def test_transaction_failure_keeps_original_bytes(material: Path, monkeypatch: pytest.MonkeyPatch):
    from loopx.cli_commands import registry_authority_metadata as authority
    before = material.read_bytes()
    apply = authority._apply_authority_entry

    def fail_after_reduction(*args, **kwargs):
        apply(*args, **kwargs)
        raise ValueError('injected reduction failure')

    monkeypatch.setattr(authority, '_apply_authority_entry', fail_after_reduction)
    with pytest.raises(ValueError, match='injected reduction failure'):
        update_authority_source(registry_path=material, goal_id='demo', source_id='guide', freshness='stale')
    assert material.read_bytes() == before


def test_markdown_preview_and_summary_recovery(material: Path):
    before = material.read_bytes()
    result = cli(material, '--format', 'markdown', 'update-authority-source', '--goal-id', 'demo',
                 '--source-id', 'guide', '--freshness', 'stale', '--dry-run')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Metadata Update' in result.stdout
    assert 'would update recorded metadata' in result.stdout
    assert material.read_bytes() == before
    applied = cli(material, '--format', 'markdown', 'update-authority-source', '--goal-id', 'demo',
                  '--source-id', 'guide', '--freshness', 'stale', '--no-global-sync')
    assert applied.returncode == 0, applied.stdout
    assert 'Metadata Update' in applied.stdout
    assert 'updated recorded metadata' in applied.stdout
    readback = cli(material, '--format', 'markdown', 'authority-sources', '--goal-id', 'demo', '--topic', 'setup')
    assert readback.returncode == 0, readback.stdout
    assert all(value in readback.stdout for value in ('stale', 'edition-2', 'setup'))
    runtime = material.parent / 'runtime'
    result = cli(material, '--runtime-root', str(runtime), 'update-authority-source', '--goal-id', 'demo',
                 '--source-id', 'guide', '--freshness', 'stale')
    assert result.returncode == 0, result.stdout
    shared = runtime / 'registry.global.json'
    snapshot = shared.read_bytes()
    material.rename(material.with_suffix('.saved'))
    result = update(shared, '--freshness', 'current', '--no-global-sync')
    assert result.returncode != 0
    assert 'project registry' in result.stdout
    assert shared.read_bytes() == snapshot
    assert not material.exists()


def test_sync_failure_reports_local_write_and_retry_recovers(material: Path):
    runtime = material.parent / 'blocked-runtime'
    runtime.write_text('not a directory')
    args = ('--runtime-root', str(runtime), 'update-authority-source', '--goal-id', 'demo',
            '--source-id', 'guide', '--freshness', 'stale')
    result = cli(material, *args)
    assert result.returncode != 0
    assert json.loads(result.stdout)['written'] is True
    assert json.loads(material.read_text())['goals'][0]['authority_registry']['project_materials']['guide']['freshness'] == 'stale'
    runtime.unlink()
    result = cli(material, '--runtime-root', str(runtime), 'sync-global', '--goal-id', 'demo')
    assert result.returncode == 0, result.stdout
    assert (runtime / 'registry.global.json').exists()


def test_concurrent_disjoint_metadata_updates_preserve_each_other(material: Path):
    from concurrent.futures import ThreadPoolExecutor
    def apply(changes: dict):
        return update_authority_source(registry_path=material, goal_id='demo', source_id='guide', **changes)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, [{'freshness': 'stale'}, {'owner_status': 'reviewed'}]))
    assert all(result['written'] for result in results)
    entry = json.loads(material.read_text())['goals'][0]['authority_registry']['project_materials']['guide']
    assert entry['freshness'] == 'stale'
    assert entry['owner_status'] == 'reviewed'
    assert entry['revision'] == 'edition-2'
    assert entry['source_ref_sha256']


@pytest.mark.skipif(os.name != 'posix' or getattr(os, 'geteuid', lambda: 0)() == 0,
                    reason='requires POSIX directory permissions enforced for a non-root user')
@pytest.mark.parametrize('output_format', ['json', 'markdown'])
def test_structured_sync_rejection_and_sync_only_recovery(material: Path, output_format: str):
    runtime = material.parent / 'readonly-runtime'
    route = ('--runtime-root', str(runtime))
    initial = cli(material, *route, 'sync-global', '--goal-id', 'demo')
    assert initial.returncode == 0, initial.stdout
    shared = runtime / 'registry.global.json'
    before = shared.read_bytes()
    original = json.loads(material.read_text())['goals'][0]['authority_registry']['project_materials']['guide']
    runtime.chmod(0o500)
    try:
        result = cli(material, *route, '--format', output_format, 'update-authority-source',
                     '--goal-id', 'demo', '--source-id', 'guide', '--freshness', 'stale')
        assert result.returncode == 1, result.stdout
        if output_format == 'json':
            payload = json.loads(result.stdout)
            assert payload['ok'] is False and payload['written'] is True
            assert payload['global_sync']['ok'] is False
            assert payload['global_sync']['write_denied'] is True
            assert payload['global_sync']['error'] in payload['error']
        else:
            assert '- written: `True`' in result.stdout
            assert 'Permission denied' in result.stdout
        assert shared.read_bytes() == before
        committed = json.loads(material.read_text())['goals'][0]['authority_registry']['project_materials']['guide']
        assert committed == {**original, 'freshness': 'stale'}
        # Another observation arrives before recovery. Recovery must not replay stale metadata.
        later = cli(material, *route, 'update-authority-source', '--goal-id', 'demo',
                    '--source-id', 'guide', '--freshness', 'owner_review_pending', '--no-global-sync')
        assert later.returncode == 0, later.stdout
        newest = material.read_bytes()
    finally:
        runtime.chmod(0o700)
    recovered = cli(material, *route, 'sync-global', '--goal-id', 'demo')
    assert recovered.returncode == 0, recovered.stdout
    assert material.read_bytes() == newest
    entry = json.loads(newest)['goals'][0]['authority_registry']['project_materials']['guide']
    assert entry == {**original, 'freshness': 'owner_review_pending'}
    before_summary = json.loads(before)['goals'][0]['authority_registry']
    summary = json.loads(shared.read_text())['goals'][0]['authority_registry']
    assert before_summary['project_material_owner_review_required_count'] == 0
    assert summary['project_material_owner_review_required_count'] == 1
    assert summary['project_material_stale_count'] == 0
    assert summary['project_material_count'] == 1
