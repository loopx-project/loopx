"""Real child-process and typed-bridge contract tests; no paid model calls."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time

import pytest

from loopx.capabilities.benchmark_toolkit.trajectory_assessment import (
    execute,
    prepare,
    read_job,
    request_cancel,
)


@pytest.fixture
def prepared(tmp_path):
    path = tmp_path / "input.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "trajectory_assessment_input_v1",
                "scope": dict(
                    goal_id="goal",
                    case_id="case",
                    run_id="run",
                    revision="r1",
                    window="one episode",
                ),
                "objective": "Improve native utility",
                "selection": dict(
                    rule="synthetic lifecycle fixture",
                    population_episodes=1,
                    exhaustive=True,
                ),
                "sources": [
                    dict(
                        id="source",
                        artifact="result.txt",
                        artifact_sha256="a" * 64,
                        first_line=1,
                        text="No utility measurement is available.",
                    )
                ],
                "episodes": [
                    dict(
                        id="episode",
                        question="Does it help?",
                        source_ids=["source"],
                        eligible_metrics=["benefit_test"],
                    )
                ],
            }
        )
    )
    return prepare(path, model="fixture", effort="max", service_tier="fast")


def provider(tmp_path: Path, *, delay=0, code=0, invalid=False) -> str:
    script = tmp_path / "codex-fixture"
    output = {
        "assessments": [
            dict(
                episode_id="episode",
                metric="benefit_test",
                verdict="unknown",
                reason="Measurement is unavailable",
                citations=[],
            )
        ],
        "limitations": ["Synthetic provider; no model qualification"],
    }
    if invalid:
        output["assessments"] = []
    script.write_text(f"""#!{sys.executable}
import json,sys,time
from pathlib import Path
sys.stdin.read()
time.sleep({delay})
if {code}: sys.exit({code})
Path(sys.argv[sys.argv.index('-o')+1]).write_text({json.dumps(json.dumps(output))})
print(json.dumps({{'type':'turn.completed','usage':{{'input_tokens':12,'output_tokens':3}}}}))
""")
    script.chmod(0o700)
    return str(script)


def test_actual_child_reuse_and_repair_of_derived_output(prepared, tmp_path):
    first = execute(prepared, tmp_path / "jobs", codex=provider(tmp_path))
    assert first["status"] == "succeeded" and first["usage"] == {
        "input_tokens": 12,
        "output_tokens": 3,
    }
    assert first["served_service_tier"] is None
    directory = Path(first["directory"])
    (directory / "assessment.json").write_text("{}")
    second = execute(prepared, tmp_path / "jobs", codex="must-not-run")
    assert second["reused"] is True
    assert (
        json.loads((directory / "assessment.json").read_text())["metrics"][1]["unknown"]
        == 1
    )
    assert second["analysis_wall_seconds"] == first["analysis_wall_seconds"]


def test_failed_model_output_needs_explicit_retry_and_preserves_failure(
    prepared, tmp_path
):
    with pytest.raises(Exception, match="missing eligible"):
        execute(prepared, tmp_path / "jobs", codex=provider(tmp_path, invalid=True))
    directory = tmp_path / "jobs" / prepared["analysis_key"]
    assert read_job(directory)["status"] == "failed"
    assert not (directory / "assessment.json").exists()
    assert execute(prepared, tmp_path / "jobs", codex="must-not-run")["ok"] is False
    success = execute(prepared, tmp_path / "jobs", retry=True, codex=provider(tmp_path))
    assert success["status"] == "succeeded" and success["attempt"] == 2
    assert (
        json.loads((directory / "attempt-1" / "receipt.json").read_text())["status"]
        == "failed"
    )


def test_concurrent_execution_and_cancel_do_not_signal_stored_pid(prepared, tmp_path):
    results = []
    worker = threading.Thread(
        target=lambda: results.append(
            execute(prepared, tmp_path / "jobs", codex=provider(tmp_path, delay=30))
        )
    )
    worker.start()
    directory = tmp_path / "jobs" / prepared["analysis_key"]
    deadline = time.monotonic() + 15
    while not (directory / "receipt.json").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        assert (
            execute(prepared, tmp_path / "jobs", codex="must-not-run")["status"]
            == "busy"
        )
        assert request_cancel(directory)["cancel_requested"] is True
    finally:
        (directory / "cancel.request").touch()
        worker.join(15)
    assert not worker.is_alive()
    assert results[0]["status"] == "cancelled"
    assert not (directory / "assessment.json").exists()


def test_preview_cli_is_read_only_and_renders_json(prepared, tmp_path):
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.entrypoint",
            "--format",
            "json",
            "benchmark",
            "trajectory-assess",
            "--input-json",
            str(tmp_path / "input.json"),
            "--output-dir",
            str(tmp_path / "never-created"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "preview"
    assert not (tmp_path / "never-created").exists()


def test_execute_rejects_forged_key_before_creating_files(prepared, tmp_path):
    prepared["analysis_key"] = "../escape"
    with pytest.raises(ValueError, match="key mismatch"):
        execute(prepared, tmp_path / "must-not-exist", codex="must-not-run")
    assert not (tmp_path / "must-not-exist").exists()
