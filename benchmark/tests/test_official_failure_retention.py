"""Native Official retention runs at the final provider cleanup boundary."""

from contextlib import suppress
from dataclasses import replace
from importlib import import_module
import io
import json
import os
import tarfile
import uuid

import pytest

from benchmark.tests import test_checkpoint_controller as native_fixtures

controllers = native_fixtures.controllers


@pytest.mark.skipif(os.environ.get("LOOPX_EDGEBENCH_DOCKER_SMOKE") != "1",
                    reason="Requires isolated Docker and native HTTP evaluator")
@pytest.mark.parametrize("retain,final_exit,receipt_failure", [
    (True, 23, False), (False, 23, False), (True, 0, False), (True, 23, True),
])
def test_official_final_failure_preserves_only_the_owned_stopped_source(
    controllers, monkeypatch, retain, final_exit, receipt_failure,
):
    import docker
    from benchmark.runtime.sforge_backend import RecordingDockerBackend
    from sforge.harness.agent import Agent
    from sforge.harness.backend.docker_backend import DockerContainerHandle
    from sforge.harness.benchmark import load_benchmark
    from sforge.harness.task_spec import make_task_spec

    native_runner = import_module("sforge.harness.run_agent")
    source, _, judge_url, _, _, grades = controllers
    config = source.state.judge.config
    trial = config.log_dir / "runs/source/fixture"
    client = docker.from_env(timeout=15)
    image = os.environ.get("LOOPX_CHECKPOINT_WRITER_IMAGE", "python:3.12-slim")
    client.images.get(image)  # Never pull an unqualified test image.
    container = client.containers.run(image, ["tail", "-f", "/dev/null"],
        detach=True, name="loopx-official-retention-" + uuid.uuid4().hex[:12],
        network_disabled=True, mem_limit="64m", nano_cpus=250_000_000)
    try:
        assert container.exec_run(["mkdir", "-p", "/data"]).exit_code == 0
        backend = RecordingDockerBackend(log_dir=trial / "collected", logger=None,
            oauth_proxy=True, client=client, retain_failed_native_worker=retain)
        handle = DockerContainerHandle(container)
        if receipt_failure:
            def fail_receipt(*args, **kwargs):
                raise OSError("synthetic receipt storage failure")
            monkeypatch.setattr("benchmark.edgebench.terminal_capture.write_json_atomic", fail_receipt)
        # Only container creation and tool setup are preinstalled. Use the
        # provider's real exec, auto-resume, grading drain and final cleanup.
        monkeypatch.setattr(backend, "image_exists", lambda image: True)
        monkeypatch.setattr(backend, "remove_container_by_name", lambda name: None)
        monkeypatch.setattr(backend, "create_container", lambda *args, **kwargs: handle)
        monkeypatch.setattr(backend, "start_container", lambda handle: None)
        monkeypatch.setattr(native_runner, "_install_tools", lambda *args, **kwargs: None)
        monkeypatch.setattr("sforge.harness.network_isolation.cleanup_stale_chains", lambda *args: None)

        class SyntheticOfficial(Agent):
            name, run_cmd, resume_cmd, api_key_env = "fixture", "solver", "resume", "FIXTURE_API_KEY"
            install_cmds = []

            def format_run_cmd(self, prompt_path, *, resume=False, **kwargs):
                cmd = (f"test -f /data/unsubmitted && exit {final_exit}" if resume else
                       "printf 'solver-state' > /data/unsubmitted; printf 'candidate' > /data/candidate; sleep 1.15; exit 9")
                backend.execution_command = cmd
                return cmd

        def registered(token):
            assert native_fixtures.submit(judge_url, token, 7).status_code == 200

        task = make_task_spec(config.tasks_dir / "fixture.json", load_benchmark(config.tasks_dir))
        result = native_runner.run_agent(replace(task, cwd="/data", submit_paths=["candidate"]),
            SyntheticOfficial(config), config, backend, "source", timeout=60, judge_url=judge_url,
            eval_interval=0, disable_stop_hook=True, disable_auto_eval=True, disable_auto_resume=False,
            internet=True, submission_cooldown=3600, host_registration_callback=registered)
        segments = result.solver_execution["segments"]
        assert [(s["resume"], s["native_exit_code"]) for s in segments] == [(False, 9), (True, final_exit)]
        assert result.best_score == 7 and result.agent_submissions == 1 and len(grades) == 1
        assert json.loads((trial / "grading_status.json").read_text())["status"] == "complete"
        receipt_path = trial / "terminal-preservation.json"
        if retain and final_exit:
            container.reload()
            assert container.attrs["State"]["Running"] is False and container.attrs["State"]["Pid"] == 0
            if receipt_failure:
                assert not receipt_path.exists()
            else:
                receipt = json.loads(receipt_path.read_text())
                assert receipt["container_id"] == container.id and receipt["source_stopped"] and receipt["source_retained"]
                assert receipt["execution"]["native_exit_code"] == final_exit and receipt["execution"]["terminal_observed"]
                assert not receipt["capture_attempted"] and not receipt["restore_qualified"]
            stream, _ = container.get_archive("/data/unsubmitted")
            with tarfile.open(fileobj=io.BytesIO(b"".join(stream))) as archive:
                assert archive.extractfile("unsubmitted").read() == b"solver-state"
        else:
            assert not receipt_path.exists()
            with pytest.raises(docker.errors.NotFound):
                container.reload()
    finally:
        with suppress(docker.errors.NotFound):
            container.remove(force=True)
        client.close()
