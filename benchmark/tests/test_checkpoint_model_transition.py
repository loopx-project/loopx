"""A new receiver can choose a model without rewriting its captured evidence."""
import hashlib

import pytest

from loopx.hosts.codex_checkpoint import checkpoint_destination_config


@pytest.fixture
def settings(tmp_path):
    source, target = tmp_path / "source.toml", tmp_path / "target.toml"
    source.write_text('model="source-model"\nmodel_reasoning_effort="high"\n'
                      'sandbox_mode="read-only"\nmodel_provider="openai"\n'
                      '[features]\ngoals=false\n')
    target.write_text(source.read_text().replace('"source-model"', '"next-model"')
                      .replace('"high"', '"max"'))
    return source, target, hashlib.sha256(source.read_bytes()).hexdigest()


def test_explicit_model_transition_keeps_source_and_all_other_settings(settings):
    source, target, digest = settings
    before = source.read_bytes()
    data, transition = checkpoint_destination_config(source, digest, target)
    assert data == target.read_bytes() and source.read_bytes() == before
    assert transition == {
        "source_config_sha256": digest,
        "destination_config_sha256": hashlib.sha256(data).hexdigest(),
        "source_model": "source-model", "source_effort": "high",
        "destination_model": "next-model", "destination_effort": "max",
    }
    assert checkpoint_destination_config(source, digest) == (before, None)


@pytest.mark.parametrize("old,new", [
    ('"read-only"', '"danger-full-access"'), ('"openai"', '"other"'),
    ('goals=false', 'goals=true'),
])
def test_model_transition_does_not_change_permissions_provider_or_native_goal(settings, old, new):
    source, target, digest = settings
    target.write_text(target.read_text().replace(old, new))
    with pytest.raises(ValueError, match="only model and reasoning effort"):
        checkpoint_destination_config(source, digest, target)


def test_model_transition_preserves_source_digest_and_rejects_redirected_target(settings, tmp_path):
    source, target, digest = settings
    with pytest.raises(ValueError, match="effective configuration differs"):
        checkpoint_destination_config(target, digest, source)
    link = tmp_path / "target-link.toml"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="physical absolute path"):
        checkpoint_destination_config(source, digest, link)


@pytest.mark.parametrize("replacement", ['model=""', 'model=true', 'model=13'])
def test_model_transition_requires_an_explicit_model(settings, replacement):
    source, target, digest = settings
    target.write_text(target.read_text().replace('model="next-model"', replacement))
    with pytest.raises(ValueError, match="explicit model and reasoning effort"):
        checkpoint_destination_config(source, digest, target)


def test_transition_provenance_survives_exact_session_observations_and_qualified_refresh(tmp_path):
    from loopx.control_plane.turn_driver.codex_sessions import (
        _replace_checkpoint_codex_session, _store_codex_cli_session, load_codex_cli_session,
    )
    facts = {"source_config_sha256": "a" * 64, "destination_config_sha256": "b" * 64,
             "source_profile_digest": "c" * 64, "destination_profile_digest": "d" * 64,
             "source_model": "original", "source_effort": "high",
             "destination_model": "next", "destination_effort": "max"}
    runtime, point = tmp_path / "runtime", tmp_path / "point"
    point.mkdir()
    binding = dict(lineage={"goal_id": "goal", "agent_id": "receiver"},
                   session_scope="agent", session_id="fork", session_profile_digest="d" * 64)
    _store_codex_cli_session(runtime, **binding, checkpoint_artifact_sha256="e" * 64,
        checkpoint_artifact_root=str(point), checkpoint_model_transition=facts)
    _store_codex_cli_session(runtime, **binding)
    current = load_codex_cli_session(runtime, lineage=binding["lineage"], session_scope="agent")
    assert current["checkpoint_model_transition"] == facts
    with pytest.raises(ValueError, match="transition cannot change"):
        _store_codex_cli_session(runtime, **binding,
            checkpoint_model_transition={**facts, "destination_model": "another"})
    _replace_checkpoint_codex_session(runtime, lineage=binding["lineage"], expected=current,
        session_id="fresh-context", refresh={"checkpoint_sha256": "e" * 64})
    refreshed = load_codex_cli_session(runtime, lineage=binding["lineage"], session_scope="agent")
    assert refreshed["checkpoint_model_transition"] == facts
    _store_codex_cli_session(runtime, **{**binding, "session_id": "unrelated"})
    assert "checkpoint_model_transition" not in load_codex_cli_session(
        runtime, lineage=binding["lineage"], session_scope="agent")
