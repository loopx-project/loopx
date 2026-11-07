"""Matched optional host-native delegation, with immutable continuing profiles."""
from dataclasses import replace
import tomllib

import pytest

from benchmark.runtime.codex import Execution, codex_settings, native_child_instructions, prepare_codex_home


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "3"])
def test_child_limit_rejects_nonpositive_or_untyped_inputs(value):
    with pytest.raises(ValueError, match="native_child_limit"):
        Execution(native_child_limit=value)


@pytest.mark.parametrize("mode", ["plain", "native-goal", "heartbeat"])
def test_all_hosts_get_identical_explicit_capacity_and_default_stays_absent(tmp_path, mode):
    kwargs = dict(workspace=tmp_path, model="fixture", effort="xhigh", base_url="", wire_api="responses")
    legacy = codex_settings(execution=Execution(mode=mode), **kwargs)
    assert "[agents]" not in legacy
    assert legacy == codex_settings(execution=Execution(mode=mode, native_child_limit=None), **kwargs)
    configured = codex_settings(execution=Execution(mode=mode, native_child_limit=3), **kwargs)
    data = tomllib.loads(configured)
    assert data["agents"] == {"max_concurrent_threads_per_session": 3}
    assert configured.replace("[agents]\nmax_concurrent_threads_per_session = 3\n", "") == legacy


def test_same_session_and_restored_home_cannot_drift_capacity(tmp_path):
    home, skills = tmp_path / "home", tmp_path / "skills"
    skills.mkdir()
    execution = Execution(native_child_limit=3)
    args = dict(home=home, execution=execution, workspace=tmp_path, model="fixture",
                effort="xhigh", base_url="http://127.0.0.1:1/v1", api_key="fixture",
                wire_api="responses", skills=skills)
    prepare_codex_home(**args)
    session = home / "sessions" / "original.jsonl"
    session.parent.mkdir()
    session.write_text('{"original_session":true}\n')
    original = {p.relative_to(home): p.read_bytes() for p in [home / "config.toml", session]}
    prepare_codex_home(**args)
    assert {p: (home / p).read_bytes() for p in original} == original
    # These are also the checks run by the receiving worker after sealed restore.
    for limit in [None, 2, 4]:
        with pytest.raises(ValueError, match="settings changed within a trial"):
            prepare_codex_home(**(args | {"execution": replace(execution, native_child_limit=limit)}))
        assert {p: (home / p).read_bytes() for p in original} == original


@pytest.mark.parametrize("profile", ["official", "native-goal", "heartbeat-explore"])
def test_same_task_authorization_and_no_extra_persistent_agents(tmp_path, monkeypatch, profile):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-credential")
    args = dict(config=SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
                profile=profile, cwd=str(tmp_path), feedback_prompt="Original workspace policy\nOriginal task",
                workspace_instructions="Original workspace policy")
    legacy = SForgeWorker(**args)
    assert legacy.feedback_prompt == "Original workspace policy\nOriginal task"
    assert legacy.workspace_instructions == "Original workspace policy"
    enabled = SForgeWorker(**args, native_child_limit=3)
    shared = native_child_instructions(3)
    assert enabled.feedback_prompt == shared + "Original workspace policy\nOriginal task"
    assert enabled.workspace_instructions == shared + "Original workspace policy"
    assert "Only the parent submits" in shared
    assert "does not authorize registering persistent LoopX Agents" in shared
    assert "optional" in shared and "Stop" in shared
    with pytest.raises(ValueError, match="explicit task prompt"):
        SForgeWorker(**(args | {"feedback_prompt": None}), native_child_limit=3)
    # The existing workspace-policy owner also guards direct API callers;
    # native-only authorization cannot be injected into a restricted wrapper.
    with pytest.raises(ValueError, match="native task wrapper"):
        SForgeWorker(**(args | {"feedback": "blind"}), native_child_limit=3)


def test_worker_environment_propagates_only_explicit_capacity(tmp_path, monkeypatch):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex
    monkeypatch.setenv("LOOPX_NATIVE_CHILD_LIMIT", "99")
    for mode in ["heartbeat", "native-goal"]:
        agent = BenchmarkCodex(logs_dir=tmp_path / mode, model_name="fixture", execution_mode=mode,
                               native_child_limit=3)
        agent._task_path = "/usr/bin:/bin"
        agent._profile_env = lambda: {}
        env = agent._worker_env(cwd=str(tmp_path))
        assert env["LOOPX_NATIVE_CHILD_LIMIT"] == "3"
        agent.execution = replace(agent.execution, native_child_limit=None)
        assert agent._worker_env(cwd=str(tmp_path))["LOOPX_NATIVE_CHILD_LIMIT"] == ""


@pytest.mark.parametrize("limit,feedback", [("0", "native"), ("-1", "native"), ("3", "blind")])
def test_cli_rejects_invalid_collaboration_before_creating_a_trial(tmp_path, limit, feedback):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.edgebench.run import main
    with pytest.raises(SystemExit) as rejected:
        main(["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
              "--run-id", "unadmitted", "--worker", "heartbeat-explore", "--model", "fixture",
              "--effort", "high", "--judge-url", "http://localhost:1", "--feedback", feedback,
              "--native-child-limit", limit])
    assert rejected.value.code == 2
    assert not list(tmp_path.iterdir())
