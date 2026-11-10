"""Backend registration preparation applies to native controls as well."""
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("sforge")
pytest.importorskip("harbor")


@pytest.fixture
def native_cli(tmp_path, monkeypatch):
    from benchmark.edgebench import run

    monkeypatch.setenv("LOOPX_SRC_DIR", str(tmp_path))
    monkeypatch.setenv("LOOPX_EXPECTED_COMMIT", "fixture")
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-login")
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    monkeypatch.setattr(run, "source_pins", lambda *args: ("fixture", "fixture"))
    monkeypatch.setattr(run, "load_benchmark", lambda *args: None)
    monkeypatch.setattr(run, "make_task_spec", lambda *args: SimpleNamespace(
        cwd="/task", work_image_key="work", judge_image_key="judge", internet=False,
        game_mode=False, submit_paths=["solver.py"], work=SimpleNamespace(agent_query="fixture task"),
        judge=SimpleNamespace(selection="score_first", score_direction="maximize")))
    (tmp_path / "fixture.json").write_text("{}")
    # Reaching any of these is outside this CLI registration boundary.
    monkeypatch.setattr(run, "archive_source", lambda *args, **kwargs:
                        pytest.fail("registration preparation must precede source staging"))
    args = ["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
            "--run-id", "run", "--model", "fixture", "--effort", "xhigh",
            "--feedback", "native", "--judge-url", "http://192.0.2.1:8080"]
    return run, args, tmp_path / "runs/run/fixture"


@pytest.mark.parametrize("profile", ["official", "single", "native-goal", "heartbeat-resume", "heartbeat-explore"])
@pytest.mark.parametrize("custom_registration", [False, True])
def test_native_cli_prepares_registration_for_every_profile(native_cli, monkeypatch,
                                                           profile, custom_registration):
    from benchmark.runtime.sforge_backend import RecordingDockerBackend

    run, args, trial = native_cli
    events = []
    constructed = {}

    def bind(token):
        assert token == "synthetic-native-registration"
        events.append("bind")

    def backend(**kwargs):
        assert kwargs.get("feedback") is None and kwargs.get("checkpoint") is None
        constructed.update(kwargs)
        result = SimpleNamespace(image_exists=lambda image: True, feedback=None, checkpoint=None)

        def options(provider):
            assert provider is native_provider
            assert events == [], "the backend prepares before native registration or worker effects"
            events.append("prepare")
            if custom_registration:
                return {"host_registration_callback": bind}
            # Exercise the shipped default policy, rather than duplicating it.
            return RecordingDockerBackend.registration_options(result, provider)

        result.registration_options = options
        return result

    def native_provider(*, host_registration_callback=None, **kwargs):
        if custom_registration:
            assert events == ["prepare"]
        events.append("register")
        assert kwargs["agent"].profile == profile
        assert kwargs["eval_interval"] == 0
        assert kwargs["submission_cooldown"] == 3600
        assert kwargs["max_submissions"] is None
        assert kwargs["disable_auto_eval"] is False
        assert "restored_registration" not in kwargs
        if custom_registration:
            assert host_registration_callback is bind
            host_registration_callback("synthetic-native-registration")
        else:
            assert host_registration_callback is None, "ordinary runs gain no callback or observer"
        # A real provider may now create/stage its worker and call its model.
        # Stop at that boundary: this test grants none of those operations.
        raise RuntimeError("synthetic worker boundary")

    monkeypatch.setattr(run, "RecordingDockerBackend", backend)
    monkeypatch.setattr(run, "run_agent", native_provider)
    with pytest.raises(RuntimeError, match="synthetic worker boundary"):
        run.main([*args, "--worker", profile])
    if custom_registration:
        assert events == ["prepare", "register", "bind"]
    else:
        assert [event for event in events if event != "prepare"] == ["register"]
    assert constructed["research_sidecar"] is True
    receipt = json.loads((trial / "runtime-receipt.json").read_text())
    assert receipt["status"] == "runner_failed"
    assert receipt["feedback"] == "native"
    assert "checkpoint_export_directory" not in receipt


@pytest.mark.parametrize("profile", ["official", "native-goal"])
def test_registration_preparation_failure_prevents_native_admission(native_cli, monkeypatch, profile):
    run, args, trial = native_cli
    events = []

    def prepare(provider):
        events.append("prepare")
        raise RuntimeError("synthetic registration unavailable")

    monkeypatch.setattr(run, "RecordingDockerBackend", lambda **kwargs: SimpleNamespace(
        image_exists=lambda image: True, registration_options=prepare))
    monkeypatch.setattr(run, "run_agent", lambda **kwargs:
                        pytest.fail("failed preparation must prevent registration and all worker effects"))
    with pytest.raises(RuntimeError, match="synthetic registration unavailable"):
        run.main([*args, "--worker", profile])
    assert events == ["prepare"]
    receipt = json.loads((trial / "runtime-receipt.json").read_text())
    assert receipt["status"] == "runner_failed" and receipt["error_kind"] == "RuntimeError"
