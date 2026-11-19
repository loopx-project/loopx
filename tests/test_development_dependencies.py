"""Manual inspection reports uncertainty without mutating environments."""
import json
import subprocess

import pytest

from scripts import development_dependencies as deps


@pytest.fixture
def node(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    (root / 'package.json').write_text('{"dependencies":{"example":"1.0.0"}}')
    lock = {'lockfileVersion': 3, 'packages': {'node_modules/example': {
        'version': '1.0.0', 'integrity': 'test', 'resolved': 'https://example.invalid/pkg'}}}
    (root / 'package-lock.json').write_text(json.dumps(lock))
    target = root / 'node_modules'
    (target / 'example').mkdir(parents=True)
    (target / '.package-lock.json').write_text(json.dumps(lock))
    (target / 'example/package.json').write_text('{"name":"example","version":"1.0.0"}')
    calls = []
    monkeypatch.setattr(deps.shutil, 'which', lambda name: name)
    def run(command, **kwargs):
        calls.append(command)
        assert command[1:] == ['ls', '--all', '--include=dev', '--json']
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(deps.subprocess, 'run', run)
    return root, calls


def test_consistent_install_is_read_only(node):
    root, calls = node
    before = {p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns)
              for p in root.rglob('*') if p.is_file()}
    assert deps.inspect_node_dependencies(root, 'node')['status'] == 'check_passed'
    after = {p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns)
             for p in root.rglob('*') if p.is_file()}
    assert before == after
    assert len(calls) == 1


def test_missing_install_is_only_reported(tmp_path):
    (tmp_path / 'package.json').write_text('{}')
    (tmp_path / 'package-lock.json').write_text('{}')
    assert deps.inspect_node_dependencies(tmp_path, 'node')['status'] == 'missing'
    assert not (tmp_path / 'node_modules').exists()


@pytest.mark.parametrize('change', ['native', 'lifecycle', 'metadata', 'lock', 'bin'])
def test_uncertain_or_broken_install_requires_manual_check(node, change):
    root, _ = node
    package = root / 'node_modules/example'
    if change == 'native':
        (package / 'addon.node').touch()
    elif change == 'lifecycle':
        (root / 'package.json').write_text('{"scripts":{"prepare":"echo build"}}')
    elif change == 'metadata':
        (package / 'package.json').unlink()
    elif change == 'lock':
        (root / 'package-lock.json').write_text('{"lockfileVersion":3,"packages":{}}')
    else:
        (package / 'package.json').write_text(
            '{"name":"example","version":"1.0.0","bin":{"example":"missing.js"}}')
    assert deps.inspect_node_dependencies(root, 'node')['status'] == 'needs_manual_check'
    assert package.is_dir()


def test_invalid_dependency_graph_requires_manual_check(node, monkeypatch):
    root, _ = node
    monkeypatch.setattr(deps.subprocess, 'run', lambda *a, **kw:
                        subprocess.CompletedProcess(a[0], 1))
    assert deps.inspect_node_dependencies(root, 'node')['status'] == 'needs_manual_check'


def test_unavailable_npm_requires_manual_check(node, monkeypatch):
    root, _ = node
    monkeypatch.setattr(deps.shutil, 'which', lambda _: None)
    assert deps.inspect_node_dependencies(root, 'node')['reason'] == 'npm_unavailable'


def test_symlink_environment_is_not_followed(node):
    root, _ = node
    (root / 'node_modules').rename(root / 'other')
    (root / 'node_modules').symlink_to(root / 'other', target_is_directory=True)
    assert deps.inspect_node_dependencies(root, 'node')['reason'] == 'linked_environment'
    assert (root / 'other/example/package.json').exists()


@pytest.mark.parametrize('obsolete', ['setup', 'retire', '--apply'])
def test_mutating_cli_options_are_rejected(monkeypatch, obsolete):
    monkeypatch.setattr('sys.argv', ['development_dependencies.py', '--scope', 'node', obsolete])
    with pytest.raises(SystemExit) as result:
        deps.main()
    assert result.value.code == 2
