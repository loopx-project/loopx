"""A capture recheck must not mutate its source, or overlook a real change."""
import json
import sys

import pytest


@pytest.fixture
def source(tmp_path, capsys):
    from loopx.cli import main
    from loopx.control_plane.turn_driver.codex_agent_session import CodexAgentSession
    from loopx.control_plane.turn_driver.codex_sessions import _store_codex_cli_session
    from loopx.hosts.research_checkpoint import native_binding_participant

    project, runtime, home = (tmp_path / name for name in ("project", "runtime", "home"))
    for path in (project, runtime, home):
        path.mkdir()
    registry = project / ".loopx/registry.json"
    (home / "config.toml").write_text("[features]\ngoals=false\n")
    prefix = ["--format", "json", "--registry", str(registry), "--runtime-root", str(runtime)]
    for argv in (["bootstrap", "--project", str(project), "--goal-id", "source",
                  "--objective", "Preserve a synthetic candidate.", "--no-global-sync"],
                 ["configure-goal", "--goal-id", "source", "--registered-agent", "agent", "--execute"]):
        code = main([*prefix, *argv])
        assert code == 0, capsys.readouterr().out
    env = {"LOOPX_REGISTRY": str(registry), "LOOPX_RUNTIME_ROOT": str(runtime),
           "LOOPX_PROJECT": str(project), "LOOPX_GOAL_ID": "source", "LOOPX_AGENT_ID": "agent",
           "CODEX_BIN": sys.executable, "CODEX_HOME": str(home), "MODEL_NAME": "synthetic",
           "REASONING_EFFORT": "high", "LOOPX_CODEX_SANDBOX": "read-only"}
    session = CodexAgentSession(env, context="resume", sandbox="read-only", receipt={})
    _store_codex_cli_session(runtime, lineage=session.lineage, session_scope="agent",
        session_id="source-session", goal_ref=session.goal_ref, session_profile_digest=session.digest)
    components, session, raw = native_binding_participant(env,
        [{"id": "project", "source_path": str(project)}], {"session": {"session_id": "source-session"}})
    return env, components, session, raw


@pytest.mark.parametrize("fault", [None, "registry", "session", "profile", "provenance", "config", "missing"])
def test_binding_observation_preserves_witness_and_rejects_changes(source, tmp_path, fault):
    from pathlib import Path
    from loopx.control_plane.effect_runtime import effect_runtime_result
    from loopx.control_plane.turn_driver.codex_sessions import _session_path
    from loopx.hosts.research_checkpoint import verify_native_binding_participant

    env, components, session, raw = source
    before = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe", "components": components})
    binding_path = _session_path(session.root, session.lineage, "agent")
    if fault == "registry":
        registry = Path(env["LOOPX_REGISTRY"])
        registry.write_bytes(registry.read_bytes() + b"\n")
    elif fault == "config":
        (Path(env["CODEX_HOME"]) / "config.toml").write_text("model = 'changed'\n")
    elif fault == "missing":
        binding_path.unlink()
    elif fault:
        value = json.loads(binding_path.read_bytes())
        value[{"session": "session_id", "profile": "session_profile_digest",
               "provenance": "checkpoint_artifact_sha256"}[fault]] = "changed"
        binding_path.write_text(json.dumps(value))
    if fault:
        with pytest.raises(ValueError, match="registry moved|binding or profile moved"):
            verify_native_binding_participant(env, session, raw)
    else:
        verify_native_binding_participant(env, session, raw)
        verify_native_binding_participant(env, session, raw)
        after = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe", "components": components})
        assert before == after
        sealed = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "capture",
            "components": components, "destination": str(tmp_path / "point"), "point_metadata": {},
            "expected_source_sha256": before["source_sha256"]})
        assert sealed["sha256"]
        # Even advisory lock bytes remain part of the artifact fence. This fix
        # must not introduce a wildcard exclusion for lock-named task files.
        lock = Path(env["LOOPX_REGISTRY"] + ".lock")
        lock.write_text("changed after preparation")
        with pytest.raises(Exception, match="changed|moved|witness"):
            effect_runtime_result("runtime.checkpoint_artifacts", {"action": "capture",
                "components": components, "destination": str(tmp_path / "later"), "point_metadata": {},
                "expected_source_sha256": before["source_sha256"]})
