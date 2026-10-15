"""Provider binding CAS and unresolved-result recovery, without production state."""
import json
from types import SimpleNamespace

import pytest

from loopx.control_plane.turn_driver.codex_agent_session import CodexAgentSession
from loopx.control_plane.turn_driver.codex_sessions import (
    _session_path, _store_codex_cli_session, load_codex_cli_session,
)


def session(tmp_path):
    subject = CodexAgentSession.__new__(CodexAgentSession)
    subject.root = tmp_path
    subject.lineage = {"goal_id": "destination", "agent_id": "worker"}
    subject.goal_ref = {"goal_id": "destination", "instance_id": "current"}
    subject.digest = "b" * 64
    _store_codex_cli_session(tmp_path, lineage=subject.lineage, session_scope="agent",
        session_id="original", goal_ref=subject.goal_ref, session_profile_digest=subject.digest,
        checkpoint_artifact_sha256="a" * 64, checkpoint_artifact_root=str(tmp_path))
    subject.admission = SimpleNamespace(require_current=lambda: None, accept_result=lambda callback: callback())
    subject.binding = load_codex_cli_session(tmp_path, lineage=subject.lineage, session_scope="agent")
    subject.session_id = "original"
    subject.receipt = {"session": {"session_id": "original"}}
    subject.checkpoint_refresh = None
    return subject


def observed(path, identity):
    path.write_text(json.dumps({"type": "thread.started", "thread_id": identity}) + "\n")


def test_explicit_refresh_retains_lineage_but_ordinary_replacement_cannot(tmp_path):
    subject = session(tmp_path)
    review = {"identity": {"effect_id": "current-review"}, "decision_sha256": "c" * 64}
    output = tmp_path / "stdout.jsonl"
    subject.prepare_checkpoint_refresh(review, tmp_path / "attempt.json", output)
    assert subject.session_id is None
    assert load_codex_cli_session(tmp_path, lineage=subject.lineage, session_scope="agent")["session_id"] == "original"
    observed(output, "fresh")
    subject.observe(output)
    binding = load_codex_cli_session(tmp_path, lineage=subject.lineage, session_scope="agent")
    assert binding["session_id"] == "fresh" and binding["checkpoint_artifact_sha256"] == "a" * 64
    assert binding["checkpoint_context_refresh"]["review_basis"] == review
    _store_codex_cli_session(tmp_path, lineage=subject.lineage, session_scope="agent", session_id="fresh",
        goal_ref=subject.goal_ref, session_profile_digest=subject.digest)
    assert load_codex_cli_session(tmp_path, lineage=subject.lineage, session_scope="agent") == binding
    _store_codex_cli_session(tmp_path, lineage=subject.lineage, session_scope="agent", session_id="unqualified",
        goal_ref=subject.goal_ref, session_profile_digest=subject.digest)
    replacement = load_codex_cli_session(tmp_path, lineage=subject.lineage, session_scope="agent")
    assert "checkpoint_artifact_sha256" not in replacement and "checkpoint_context_refresh" not in replacement


@pytest.mark.parametrize("fault", ["unknown", "foreign_binding", "old_identity", "actor_revoked", "profile_moved", "ambiguous"])
def test_refresh_failure_never_overwrites_a_different_or_unknown_result(tmp_path, fault):
    subject = session(tmp_path)
    output, attempt = tmp_path / "stdout.jsonl", tmp_path / "attempt.json"
    review = {"identity": {"effect_id": "current-review"}, "decision_sha256": "c" * 64}
    subject.prepare_checkpoint_refresh(review, attempt, output)
    if fault == "unknown":
        with pytest.raises(ValueError, match="unknown"):
            subject.prepare_checkpoint_refresh(review, attempt, output)
    else:
        observed(output, "original" if fault == "old_identity" else "fresh")
        if fault == "ambiguous":
            with output.open("a") as stream:
                stream.write(json.dumps({"type": "thread.started", "thread_id": "another"}) + "\n")
        if fault in {"foreign_binding", "profile_moved"}:
            path = _session_path(tmp_path, subject.lineage, "agent")
            data = json.loads(path.read_bytes())
            data["session_id" if fault == "foreign_binding" else "session_profile_digest"] = "moved"
            path.write_text(json.dumps(data))
        if fault == "actor_revoked":
            def deny(callback):
                raise ValueError("current Goal lifetime revoked")
            subject.admission.accept_result = deny
        before = _session_path(tmp_path, subject.lineage, "agent").read_bytes()
        with pytest.raises((ValueError, RuntimeError)):
            subject.observe(output)
        assert _session_path(tmp_path, subject.lineage, "agent").read_bytes() == before


def test_original_output_recovers_a_lost_observation_without_another_launch(tmp_path):
    subject = session(tmp_path)
    review = {"identity": {"effect_id": "current-review"}, "decision_sha256": "c" * 64}
    original, retry = tmp_path / "original.jsonl", tmp_path / "retry.jsonl"
    attempt = tmp_path / "attempt.json"
    subject.prepare_checkpoint_refresh(review, attempt, original)
    observed(original, "fresh")
    subject.prepare_checkpoint_refresh(review, attempt, retry)
    assert subject.session_id == "fresh" and not retry.exists()
    assert subject.receipt["session"]["action"] == "resume"


def test_unpromoted_source_does_not_invent_a_goal_instance_during_refresh(tmp_path):
    subject = session(tmp_path)
    path = _session_path(tmp_path, subject.lineage, "agent")
    binding = json.loads(path.read_bytes())
    del binding["goal_ref"]
    path.write_text(json.dumps(binding))
    subject.binding = load_codex_cli_session(tmp_path, lineage=subject.lineage, session_scope="agent")
    output = tmp_path / "original.jsonl"
    subject.prepare_checkpoint_refresh({"identity": {"effect_id": "current-review"},
        "decision_sha256": "c" * 64}, tmp_path / "attempt.json", output)
    observed(output, "fresh")
    subject.observe(output)
    current = load_codex_cli_session(tmp_path, lineage=subject.lineage, session_scope="agent")
    assert current["session_id"] == "fresh" and "goal_ref" not in current


def test_redirected_original_output_cannot_recover_an_identity(tmp_path):
    subject = session(tmp_path)
    review = {"identity": {"effect_id": "current-review"}, "decision_sha256": "c" * 64}
    original, attempt = tmp_path / "stdout.jsonl", tmp_path / "attempt.json"
    subject.prepare_checkpoint_refresh(review, attempt, original)
    foreign = tmp_path / "foreign.jsonl"
    observed(foreign, "foreign")
    original.symlink_to(foreign)
    with pytest.raises(ValueError, match="physical absolute"):
        subject.prepare_checkpoint_refresh(review, attempt, original)
    assert load_codex_cli_session(tmp_path, lineage=subject.lineage, session_scope="agent")["session_id"] == "original"
