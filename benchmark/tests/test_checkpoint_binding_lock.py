"""Checkpoint binding keeps the real registry fence without nesting its owner."""

import hashlib
import json
import sys

import pytest


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("change_goal", [False, True])
@pytest.mark.parametrize("model_transition", [False, True])
def test_checkpoint_binding_and_readback_recheck_destination(
    tmp_path, monkeypatch, capsys, replay, change_goal, model_transition,
):
    from loopx.cli import main
    from loopx.control_plane import effect_runtime
    from loopx.control_plane.turn_driver import codex_agent_session
    from loopx.control_plane.turn_driver.codex_sessions import (
        _store_codex_cli_session, select_codex_cli_session,
    )
    from loopx.hosts import codex_checkpoint

    registry, runtime = tmp_path / "registry.json", tmp_path / "runtime"
    workspace, home, unit = (tmp_path / name for name in ("workspace", "home", "point"))
    for path in (workspace, home, unit):
        path.mkdir()
    config = home / "config.toml"
    config.write_text('model="synthetic"\nmodel_reasoning_effort="high"\n[features]\ngoals=false\n')
    source_config = tmp_path / "source.toml"
    source_config.write_bytes(config.read_bytes())
    prefix = ["--format", "json", "--registry", str(registry), "--runtime-root", str(runtime)]

    def cli(*args):
        code = main([*prefix, *args])
        output = capsys.readouterr().out
        assert code == 0, output

    cli("bootstrap", "--project", str(workspace), "--goal-id", "destination",
        "--objective", "Verify a synthetic retained candidate.",
        "--adapter-kind", "read_only_project_map_v0", "--adapter-status", "connected-read-only",
        "--write-scope", "**", "--no-global-sync")
    cli("configure-goal", "--goal-id", "destination", "--registered-agent", "receiver",
        "--agent-work-mode", "receiver=active", "--execute")
    env = {"LOOPX_REGISTRY": str(registry), "LOOPX_RUNTIME_ROOT": str(runtime),
           "LOOPX_PROJECT": str(workspace), "LOOPX_GOAL_ID": "destination",
           "LOOPX_AGENT_ID": "receiver", "CODEX_BIN": sys.executable,
           "CODEX_HOME": str(home), "MODEL_NAME": "synthetic", "REASONING_EFFORT": "high"}
    session_type = codex_agent_session.CodexAgentSession
    prepared = session_type(env, context="resume", sandbox="read-only", receipt={})
    digest = "a" * 64
    native = {"session_id": "source-session", "completed_turn_id": "retained-turn",
              "turn_ids": ["retained-turn"], "visible_history_sha256": "b" * 64,
              "host_version": "fixture", "guest_home": str(home), "guest_workspace": str(workspace),
              "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest()}
    (unit / "checkpoint.json").write_text(json.dumps({"point_metadata": {
        "session": {"session_profile_digest": prepared.digest}, "native_session": native,
        "loopx_state": {"goal_id": "source", "agent_id": "source-agent"}}}))
    destination = None
    if model_transition:
        destination = tmp_path / "destination.toml"
        destination.write_text(source_config.read_text().replace('"synthetic"', '"synthetic-next"')
                               .replace('"high"', '"max"'))
        config.write_bytes(destination.read_bytes())
        env.update(MODEL_NAME="synthetic-next", REASONING_EFFORT="max")
    destination_profile = session_type(env, context="resume", sandbox="read-only", receipt={}).digest
    transition = None
    if model_transition:
        transition = {**codex_checkpoint.checkpoint_destination_config(
            source_config, native["config_sha256"], destination)[1],
            "source_profile_digest": prepared.digest, "destination_profile_digest": destination_profile}
    if replay:
        _store_codex_cli_session(runtime, lineage=prepared.lineage, session_scope="agent",
            session_id="destination-session", goal_ref=prepared.goal_ref,
            session_profile_digest=destination_profile, checkpoint_artifact_sha256=digest,
            checkpoint_artifact_root=str(unit), checkpoint_model_transition=transition)
    before = select_codex_cli_session(runtime, lineage=prepared.lineage, session_scope="agent")

    # Native storage/inference is outside this lock regression. Registry,
    # launch-authority capture and session binding use their production owners.
    real_effect = effect_runtime.effect_runtime_result

    def verify(name, payload, **kwargs):
        if name == "runtime.checkpoint_artifacts":
            assert payload == {"action": "verify", "root": str(unit), "expected_sha256": digest}
            return {"verified": True}
        return real_effect(name, payload, **kwargs)

    forks = []

    def fork(*args, **kwargs):
        forks.append(True)
        assert kwargs.get("destination_config") == destination
        return {"session_id": "destination-session", "model_calls": 0}

    def construct(*args, **kwargs):
        session = session_type(*args, **kwargs)
        if change_goal:
            cli("configure-goal", "--goal-id", "destination",
                "--agent-work-mode", "receiver=monitor_only", "--execute")
        return session

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", verify)
    monkeypatch.setattr(codex_checkpoint, "fork_native_checkpoint", fork)
    monkeypatch.setattr(codex_checkpoint, "native_session_checkpoint", lambda *a, **k: {
        "metadata": {**native, "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest()}})
    monkeypatch.setattr(codex_agent_session, "CodexAgentSession", construct)
    if change_goal:
        with pytest.raises(ValueError, match="Goal configuration changed"):
            codex_checkpoint.admit_native_checkpoint_session(
                env, unit, digest, source_config, sandbox="read-only", timeout=30,
                **({"destination_config": destination} if destination is not None else {}))
        assert select_codex_cli_session(runtime, lineage=prepared.lineage, session_scope="agent") == before
    else:
        result = codex_checkpoint.admit_native_checkpoint_session(
            env, unit, digest, source_config, sandbox="read-only", timeout=30,
            **({"destination_config": destination} if destination is not None else {}))
        assert result["session_binding_admitted"] and result["session_id"] == "destination-session"
        binding = select_codex_cli_session(runtime, lineage=prepared.lineage, session_scope="agent")
        assert binding["checkpoint_artifact_sha256"] == digest
        assert binding["checkpoint_artifact_root"] == str(unit)
        assert binding["session_profile_digest"] == destination_profile
        if replay and model_transition:
            assert result["model_transition"]["source_model"] == "synthetic"
            assert result["model_transition"]["destination_effort"] == "max"
    assert (source_config.read_bytes() != config.read_bytes()) is model_transition
    assert len(forks) == (0 if replay else 1)
