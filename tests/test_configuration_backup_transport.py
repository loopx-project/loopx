"""Large native exports must compare through the existing private transport."""
import json

import pytest

from tests.test_configuration_backup import cli, environment as environment


@pytest.mark.parametrize('change_large_value', [False, True])
def test_large_native_snapshots_compare_without_truncation(environment, tmp_path, change_large_value):
    source, runtime, registry, _ = environment
    state = json.loads(source.read_text())
    optional = state['goals'][0]['control_plane']['optional_provider']
    optional.update(text='x' * 4_500_000, enabled=True)
    source.write_text(json.dumps(state))
    arguments = ('--runtime-root', runtime, '--registry', registry, 'configuration-backup')
    before_path, after_path = tmp_path / 'large-before.json', tmp_path / 'large-after.json'
    assert cli(*arguments, 'export', '--output', before_path, '--execute')[0] == 0
    optional['enabled'] = False
    if change_large_value:
        optional['text'] = 'y' * 4_500_000
    source.write_text(json.dumps(state))
    assert cli(*arguments, 'export', '--output', after_path, '--execute')[0] == 0
    for path in (before_path, after_path):
        assert path.stat().st_size > 4_500_000
        assert cli(*arguments, 'verify', '--input', path)[0] == 0
    inputs = {path: path.read_bytes() for path in (source, before_path, after_path)}
    code, compared = cli(*arguments, 'compare', '--before', before_path, '--after', after_path)
    assert code == 0, compared
    assert compared['change_count'] == (2 if change_large_value else 1)
    assert compared['written'] is False and compared['activation_performed'] is False
    changes = {row['path'].rsplit('/', 1)[-1]: row for row in compared['changes']}
    assert changes['enabled']['before'] is True and changes['enabled']['after'] is False
    if change_large_value:
        assert changes['text']['before'] == 'x' * 4_500_000
        assert changes['text']['after'] == 'y' * 4_500_000
    assert {path: path.read_bytes() for path in inputs} == inputs


def test_comparison_private_response_failure_is_not_retried_or_returned_partial(environment, monkeypatch):
    from loopx.capabilities.configuration_backup import capture_configuration_backup, compare_configuration_backups
    from loopx.control_plane import effect_runtime

    source, runtime, registry, _ = environment
    state = json.loads(source.read_text())
    optional = state['goals'][0]['control_plane']['optional_provider']
    optional['text'] = 'x' * 4_500_000
    source.write_text(json.dumps(state))
    before = capture_configuration_backup(registry_path=registry, runtime_root=runtime)
    optional['text'] = 'y' * 4_500_000
    source.write_text(json.dumps(state))
    after = capture_configuration_backup(registry_path=registry, runtime_root=runtime)
    original_source = source.read_bytes()
    original_read = effect_runtime._read_local_snapshot_response
    observed = []

    def corrupt_readback(envelope, sink, **kwargs):
        observed.append(sink)
        assert kwargs['method'] == 'configuration.backup'
        assert envelope['result_ref']['byte_count'] > 8 * 1024 * 1024
        return original_read({**envelope, 'result_ref': {**envelope['result_ref'], 'sha256': '0' * 64}}, sink, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(effect_runtime, '_read_local_snapshot_response', corrupt_readback)
        with pytest.raises(effect_runtime.EffectRuntimeResponseAmbiguous):
            compare_configuration_backups(before, after)
    assert len(observed) == 1  # An ambiguous response must not replay the operation.
    assert all(not path.exists() and not path.parent.exists() for path in observed)
    assert source.read_bytes() == original_source

    # Exercise the unchanged aggregate request ceiling at a reduced fixture
    # boundary; neither parameter truncation nor a partial result is allowed.
    with monkeypatch.context() as patch:
        patch.setattr(effect_runtime, 'MAX_LOCAL_SNAPSHOT_BYTES', 8 * 1024 * 1024)
        with pytest.raises(effect_runtime.EffectRuntimeRejected, match='local snapshot is oversized') as failure:
            compare_configuration_backups(before, after)
        assert failure.value.diagnostic_code == 'request_too_large'
    assert source.read_bytes() == original_source
