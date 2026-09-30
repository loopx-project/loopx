"""Generated bindings must preserve the owner domain and fail closed on drift."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.control_plane.quota.effective_action import EffectiveAction
from loopx.control_plane.agents.agent_scope_frontier import AgentScopeFrontierAction
from loopx.semantics.inventory import SourceFile
from scripts import generate_semantic_bindings as generator


def test_checked_in_semantic_artifacts_are_fresh() -> None:
    result = subprocess.run(
        [sys.executable, str(Path(generator.__file__)), "--check"],
        cwd=generator.ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("path, enum, symbol, array", [
    (generator.BINDING, EffectiveAction, "EffectiveAction", "EFFECTIVE_ACTIONS"),
    (generator.FRONTIER_BINDING, AgentScopeFrontierAction, "AgentScopeFrontierAction", "AGENT_SCOPE_FRONTIER_ACTIONS"),
])
def test_typescript_binding_preserves_python_member_names_and_values(path, enum, symbol, array) -> None:
    result = subprocess.run(
        ["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e",
         f"import {{{symbol}, {array}}} from {json.dumps(path.as_uri())};"
         f"console.log(JSON.stringify({{members: {symbol}, values: {array}}}));"],
        cwd=generator.ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    binding = json.loads(result.stdout)
    assert binding["members"] == {name: member.value for name, member in enum.__members__.items()}
    assert binding["values"] == [member.value for member in enum]


@pytest.mark.parametrize("initial", [None, "stale artifact\n"])
def test_check_does_not_repair_stale_or_missing_artifacts(tmp_path, monkeypatch, capsys, initial):
    artifact = tmp_path / "binding.ts"
    if initial is not None:
        artifact.write_text(initial, encoding="utf-8")
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    monkeypatch.setattr(generator, "build_artifacts", lambda: {artifact: "current artifact\n"})
    monkeypatch.setattr(sys, "argv", ["generate_semantic_bindings.py", "--check"])
    assert generator.main() == 1
    assert "uv run python scripts/generate_semantic_bindings.py" in capsys.readouterr().err
    assert (artifact.read_text(encoding="utf-8") if artifact.exists() else None) == initial

    monkeypatch.setattr(sys, "argv", ["generate_semantic_bindings.py"])
    assert generator.main() == 0
    written_at = artifact.stat().st_mtime_ns
    assert artifact.read_text(encoding="utf-8") == "current artifact\n"
    assert generator.main() == 0
    assert artifact.stat().st_mtime_ns == written_at
    monkeypatch.setattr(sys, "argv", ["generate_semantic_bindings.py", "--check"])
    assert generator.main() == 0


@pytest.mark.parametrize("declarations", [
    '    NORMAL = "normal"\n    UNKNOWN = "unregistered"\n',
    '    NORMAL = "normal"\n    ALIAS = "normal"\n',
])
def test_generator_rejects_owner_mismatch_and_aliases(tmp_path, monkeypatch, declarations):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"vocabularies": {"effective_action": {
        "owners": {"python": "loopx/owner.py::Action"}, "values": ["normal"],
    }}}), encoding="utf-8")
    monkeypatch.setattr(generator, "REGISTRY", registry)
    source = SourceFile("loopx/owner.py", ".py", 'from enum import Enum\nclass Action(str, Enum):\n' + declarations)
    monkeypatch.setattr(generator, "load_sources", lambda root: [source])
    with pytest.raises(ValueError, match="owner and registry differ"):
        generator.build_artifacts()


@pytest.mark.parametrize('declaration', [
    '    NEW = "review_" + "only"\n',
    '    ALIAS = NORMAL_RUN\n',
    '    NORMAL_RUN = "normal_run"\n',
])
def test_generator_rejects_unsupported_or_duplicate_owner_members(monkeypatch, declaration):
    sources = generator.load_sources(generator.ROOT)
    owner = 'loopx/control_plane/quota/effective_action.py'
    changed = [SourceFile(s.path, s.suffix, s.text + declaration) if s.path == owner else s for s in sources]
    monkeypatch.setattr(generator, 'load_sources', lambda root: changed)
    monkeypatch.setattr(sys, 'argv', ['generate_semantic_bindings.py', '--check'])
    with pytest.raises(ValueError, match=r'effective_action.py:\d+:.*member'):
        generator.main()


def test_generator_does_not_silently_drop_annotated_members(monkeypatch):
    sources = generator.load_sources(generator.ROOT)
    owner = 'loopx/control_plane/quota/effective_action.py'
    changed = [SourceFile(s.path, s.suffix, s.text + '    NEW: str = "review_only"\n') if s.path == owner else s for s in sources]
    monkeypatch.setattr(generator, 'load_sources', lambda root: changed)
    with pytest.raises(ValueError, match='owner and registry differ'):
        generator.build_artifacts()


@pytest.mark.parametrize('declarations,member', [
    ('    NEW = factory()\n', 'NEW'),
    ('    NEW = 1\n', 'NEW'),
    ('    NEW: str\n', 'NEW'),
    ('    NEW: str = "computed" + "value"\n', 'NEW'),
    ('    A = B = "new"\n', 'A,B'),
    ('    A, B = "new", "value"\n', 'A,B'),
    ('    NORMAL = "run"\n    ALIAS = "run"\n', 'ALIAS'),
    ('    NORMAL = "run"\n    NORMAL: str = "wait"\n', 'NORMAL'),
    ('    _ignore_ = "NORMAL"\n    NORMAL = "run"\n', '_ignore_'),
])
def test_strict_owner_errors_name_the_path_line_and_member(declarations, member):
    source = SourceFile('loopx/owner.py', '.py', 'class Action(str, Enum):\n' + declarations)
    with pytest.raises(ValueError) as error:
        generator.enum_members(source, 'Action', strict=True)
    assert str(error.value).startswith('loopx/owner.py:')
    assert f'member {member}' in str(error.value)


def test_annotated_literal_binding_includes_every_declared_member(monkeypatch):
    sources = generator.load_sources(generator.ROOT)
    owner = 'loopx/control_plane/quota/effective_action.py'
    changed = [SourceFile(s.path, s.suffix, s.text.replace('NORMAL_RUN =', 'NORMAL_RUN: str ='))
               if s.path == owner else s for s in sources]
    monkeypatch.setattr(generator, 'load_sources', lambda root: changed)
    assert generator.build_artifacts()[generator.BINDING] == generator.BINDING.read_text()


def test_failed_owner_validation_never_writes_any_artifact(tmp_path, monkeypatch):
    sources = generator.load_sources(generator.ROOT)
    owner = 'loopx/control_plane/agents/agent_scope_frontier.py'
    changed = [SourceFile(s.path, s.suffix, s.text.replace(
        'AGENT_SCOPE_WAIT = "agent_scope_wait"', 'AGENT_SCOPE_WAIT = invoke_source()'))
        if s.path == owner else s for s in sources]
    monkeypatch.setattr(generator, 'load_sources', lambda root: changed)
    artifact = tmp_path / 'binding.ts'
    artifact.write_text('preserve me')
    monkeypatch.setattr(generator, 'BINDING', artifact)
    monkeypatch.setattr(sys, 'argv', ['generate_semantic_bindings.py'])
    with pytest.raises(ValueError, match='member AGENT_SCOPE_WAIT'):
        generator.main()
    assert artifact.read_text() == 'preserve me'


def test_strict_extraction_does_not_execute_inspected_source():
    source = SourceFile('loopx/owner.py', '.py',
                        'raise RuntimeError("source must not execute")\nclass Action(str, Enum):\n RUN: str = "run"\n')
    assert generator.enum_members(source, 'Action', strict=True) == {'RUN': 'run'}


def test_digest_binding_is_derived_from_the_typescript_owner():
    import re

    namespace = {}
    exec(compile(generator.DIGEST_BINDING.read_text(), '<generated digest binding>', 'exec'), namespace)
    assert namespace['BARE_SHA256_PATTERN'].fullmatch('a' * 64)
    assert namespace['ENVELOPED_SHA256_PATTERN'].fullmatch('sha256:' + 'a' * 64)
    for value in ['A' * 64, 'a' * 63, 'a' * 65, 'g' * 64, 'a' * 64 + '\n']:
        assert namespace['BARE_SHA256_PATTERN'].fullmatch(value) is None
    assert namespace['ENVELOPED_SHA256_PATTERN'].fullmatch('a' * 64) is None
    assert namespace['BARE_SHA256_PATTERN'].fullmatch('sha256:' + 'a' * 64) is None
    assert isinstance(namespace['BARE_SHA256_PATTERN'], re.Pattern)
    assert generator.DIGEST_BINDING.relative_to(generator.ROOT).as_posix() in generator.verified_generated_paths()


@pytest.mark.parametrize('suffix', ['i', 'g', 'm'])
def test_digest_generator_rejects_regex_flags(suffix):
    source = generator.DIGEST_OWNER.read_text().replace('/;', f'/{suffix};', 1)
    with pytest.raises(ValueError, match='flagless'):
        generator.render_digest_binding(source)


@pytest.mark.parametrize('mutation', ['extra', 'missing', 'dynamic', 'unsupported'])
def test_digest_generator_rejects_untranslated_source(mutation):
    source = generator.DIGEST_OWNER.read_text()
    if mutation == 'extra':
        source += '\nthrow new Error("must not execute");\n'
    elif mutation == 'missing':
        source = source[:source.index('export const BARE_SHA256_PATTERN')]
    elif mutation == 'dynamic':
        source = source.replace('/^[0-9a-f]{64}$/', 'buildPattern()')
    else:
        source = source.replace('[0-9a-f]', r'\p{ASCII}')
    with pytest.raises(ValueError, match='digest owner'):
        generator.render_digest_binding(source)


def test_changed_digest_owner_invalidates_old_binding(tmp_path, monkeypatch):
    source = tmp_path / 'content_digest.ts'
    source.write_text(generator.DIGEST_OWNER.read_text().replace('{64}', '{63}'))
    monkeypatch.setattr(generator, 'DIGEST_OWNER', source)
    # Detect source drift without rewriting any checked-in binding.
    with pytest.raises(ValueError, match='stale generated semantic artifact'):
        generator.verified_generated_paths()


def test_generated_filename_or_header_does_not_exempt_hand_edits(tmp_path, monkeypatch):
    artifact = tmp_path / 'content_digest.py'
    expected = generator.render_digest_binding(generator.DIGEST_OWNER.read_text())
    artifact.write_text(expected.replace('{64}', '{63}'))
    monkeypatch.setattr(generator, 'ROOT', tmp_path)
    monkeypatch.setattr(generator, 'build_artifacts', lambda: {artifact: expected})
    with pytest.raises(ValueError, match='stale generated semantic artifact'):
        generator.verified_generated_paths()
    assert '{63}' in artifact.read_text()
