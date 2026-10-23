"""Filename fidelity and explicit export placement before solver admission."""
from pathlib import Path
from types import SimpleNamespace
import json
import os

import pytest

from benchmark.edgebench.checkpoint_supervisor import qualify_export_storage
from benchmark.tests.test_checkpoint_worker import controllers, writer


def test_storage_probe_matches_real_filename_behavior_without_reserving_run(tmp_path):
    # Independent filesystem oracle: every distinct spelling must survive with
    # its own contents. Exercise the actual host volume, including macOS shares.
    names = ("A", "a", "\u00e9", "e\u0301")
    oracle = tmp_path / "oracle"
    oracle.mkdir()
    for index, name in enumerate(names):
        (oracle / name).write_bytes(bytes([index]))
    compatible = (set(p.name for p in oracle.iterdir()) == set(names) and
                  all((oracle / name).read_bytes() == bytes([i]) for i, name in enumerate(names)))
    destination = tmp_path / "future-run" / "task" / "checkpoints"
    if compatible:
        qualify_export_storage(destination)
    else:
        with pytest.raises(ValueError, match="filename distinctions"):
            qualify_export_storage(destination)
    assert not destination.parent.parent.exists(), "warm native registration still owns run reservation"
    assert list(tmp_path.iterdir()) == [oracle], "probe must remove temporary files on success and failure"


def test_storage_probe_rejects_symlink_and_relative_destinations(tmp_path):
    link = tmp_path / "alias"
    link.symlink_to(tmp_path, target_is_directory=True)
    for path in (Path("relative/checkpoints"), link / "checkpoints"):
        with pytest.raises(ValueError, match="physical and absolute"):
            qualify_export_storage(path)


@pytest.mark.skipif(os.environ.get("LOOPX_EDGEBENCH_DOCKER_SMOKE") != "1",
                    reason="Requires explicit isolated Docker writer-barrier qualification")
def test_full_export_preserves_linux_sibling_names(controllers, writer, tmp_path):
    from benchmark.edgebench.checkpoint_worker import capture_worker
    from benchmark.tests.test_checkpoint_controller import register
    source, _, url, _, files, _ = controllers
    container, request = writer
    names = {"Candidate": "upper", "candidate": "lower", "caf\u00e9": "nfc", "cafe\u0301": "nfd"}
    command = ("from pathlib import Path; "
               f"[(Path('/data') / name).write_text(value) for name,value in {names!r}.items()]")
    assert container.exec_run(["python", "-c", command]).exit_code == 0
    destination = tmp_path / "sealed"
    capture_id = "a" * 32
    native_token = register(source, url)
    result = capture_worker(files[0], token=native_token, capture_id=capture_id,
                            destination=destination, request_file=request, timeout=30)
    for name, value in names.items():
        assert (destination / "components/native_capsule" / name).read_text() == value
    assert result["integrity_verified"] is True
    recorded = json.loads((destination / "checkpoint.json").read_text())["artifact_witness"]["native_capsule"]
    assert set(names) <= {row["path"] for row in recorded}
    container.reload()
    assert container.attrs["State"]["Running"] and not container.attrs["State"]["Paused"]


@pytest.mark.parametrize("kind", ["relative", "missing", "file", "symlink"])
def test_invalid_export_root_fails_before_trial_reservation(tmp_path, kind):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.edgebench.run import main
    root = tmp_path / "export"
    if kind == "relative":
        root = Path("relative-export")
    elif kind == "file":
        root.write_text("retained")
    elif kind == "symlink":
        root.symlink_to(tmp_path, target_is_directory=True)
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"destination": "/logs/agent/checkpoints", "point_metadata": {},
        "components": [{"id": "code", "source_path": "/task/code"}], "trigger": "fresh_replan",
        "native_session": True, "loopx_state": True, "evaluation_opportunity": True,
        "controller_handoff": True}))
    with pytest.raises((ValueError, FileNotFoundError, SystemExit)):
        main(["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
              "--run-id", "invalid", "--worker", "heartbeat-explore", "--model", "fixture",
              "--effort", "xhigh", "--judge-url", "http://localhost:1",
              "--checkpoint-request", str(request), "--checkpoint-controller-file", str(tmp_path / "unused.json"),
              "--checkpoint-export-root", str(root)])
    assert not (tmp_path / "runs").exists()
    if kind == "file":
        assert root.read_text() == "retained"


@pytest.mark.parametrize("capture,external", [(False, False), (True, False), (True, True)])
def test_cli_routes_export_and_retains_capture_off_parity(tmp_path, monkeypatch, capture, external):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.edgebench import run, checkpoint_supervisor

    monkeypatch.setenv("LOOPX_SRC_DIR", str(tmp_path))
    monkeypatch.setenv("LOOPX_EXPECTED_COMMIT", "fixture")
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-login")
    monkeypatch.setattr(run, "source_pins", lambda *a: ("fixture", "fixture"))
    monkeypatch.setattr(run, "load_benchmark", lambda *a: None)
    monkeypatch.setattr(run, "make_task_spec", lambda *a: SimpleNamespace(
        cwd="/task", work_image_key="work", judge_image_key="judge", internet=False,
        game_mode=False, submit_paths=["solver.py"], work=SimpleNamespace(agent_query="fixture task"),
        judge=SimpleNamespace(selection="score_first", score_direction="maximize")))
    (tmp_path / "fixture.json").write_text("{}")
    export = tmp_path / "export"
    export.mkdir()
    calls = []
    monkeypatch.setattr(checkpoint_supervisor, "qualify_export_storage", lambda p: calls.append(p))
    trial = tmp_path / "runs/attempt/fixture"
    expected = export / "attempt/fixture/checkpoints" if external else trial / "checkpoints"

    def backend(**kwargs):
        if capture:
            assert calls == [expected]
            assert kwargs["checkpoint"].destination == expected
        else:
            assert not calls and "checkpoint" not in kwargs
        return SimpleNamespace(image_exists=lambda image: True, registration_options=lambda f: {})

    monkeypatch.setattr(run, "RecordingDockerBackend", backend)
    def before_solver(**kwargs):
        assert kwargs["eval_interval"] == 0 and kwargs["submission_cooldown"] == 3600
        raise RuntimeError("synthetic admission boundary")
    monkeypatch.setattr(run, "run_agent", before_solver)
    options = []
    if capture:
        controller = tmp_path / "controller.json"
        controller.write_text(json.dumps({"url": "http://localhost:1", "admin_secret": "synthetic",
                                          "bank_root": str(export)}))
        controller.chmod(0o600)
        request = tmp_path / "request.json"
        request.write_text(json.dumps({"destination": "/logs/agent/checkpoints", "point_metadata": {},
            "components": [{"id": "code", "source_path": "/task/code"}], "trigger": "fresh_replan",
            "native_session": True, "loopx_state": True, "evaluation_opportunity": True,
            "controller_handoff": True}))
        options = ["--checkpoint-controller-file", str(controller), "--checkpoint-request", str(request),
                   "--no-checkpoint-retention"]
    if external:
        options += ["--checkpoint-export-root", str(export)]
    with pytest.raises(RuntimeError, match="synthetic admission boundary"):
        run.main(["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
                  "--run-id", "attempt", "--worker", "heartbeat-explore", "--model", "fixture",
                  "--effort", "xhigh", "--judge-url", "http://localhost:1", *options])
    receipt = json.loads((trial / "runtime-receipt.json").read_text())
    if capture:
        assert receipt["checkpoint_export_directory"] == str(expected)
    else:
        assert "checkpoint_export_directory" not in receipt
