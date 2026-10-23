"""Real refresh-state subprocess, durable run, and isolated shadow readback."""

import json
import os
from pathlib import Path
import subprocess
import sys

from loopx_jev import drift
from loopx_jev.store import atomic_json
from drift_fixtures import DRIFT_NOULS, response
from test_drift import git
from tests.control_plane.test_quota_settlement_cli import (
    GOAL_ID,
    _initialize_fixture_authority,
    _write_fixture,
)


def test_actual_refresh_process_capture_and_default_off(tmp_path):
    project, runtime, registry = _write_fixture(tmp_path / "fixture")
    _initialize_fixture_authority(project, runtime)
    work = tmp_path / "delivery"
    work.mkdir()
    git(work, "init", "-q")
    git(work, "config", "user.name", "Fixture")
    git(work, "config", "user.email", "fixture@example.invalid")
    (work / "retry.py").write_text("TIMEOUT = 1\n")
    git(work, "add", "retry.py")
    git(work, "commit", "-qm", "baseline")
    config, basis, root = (
        tmp_path / "config.json",
        tmp_path / "basis.json",
        tmp_path / "shadow",
    )
    atomic_json(
        config,
        {
            "schema_version": "loopx_jev_drift_config_v0",
            "mode": "shadow",
            "scenarios": ["progress_review"],
            "model": "fixture-v1",
            "allow_egress": True,
        },
    )
    atomic_json(
        basis,
        {
            "goal_id": GOAL_ID,
            "objective": "Retry a transient exception",
            "acceptance": ["A transient exception triggers one retry"],
            "evidence": [],
        },
    )
    source = Path(__file__).resolve().parents[3]
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(source / "packages/loopx-jev/src"), str(source)]
        ),
        "LOOPX_GLOBAL_REGISTRY": str(tmp_path / "global.json"),
    }

    def run(*args):
        process = subprocess.run(
            [sys.executable, "-m", "loopx_jev", *args],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert process.returncode == 0, process.stderr + process.stdout
        return json.loads(process.stdout), process.stderr

    initial, _ = run(
        "drift",
        "init",
        "--state-dir",
        str(root),
        "--workspace",
        str(work),
        "--basis",
        str(basis),
        "--config",
        str(config),
        "--path",
        "retry.py",
    )
    assert initial["status"] == "baseline_created"
    (work / "retry.py").write_text("RENAMED_TIMEOUT = 1\n")
    args = [
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime),
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--no-global-sync",
        "--suppress-external-sinks",
        "--format",
        "json",
    ]
    result, err = run(
        "drift",
        "refresh",
        "--state-dir",
        str(root),
        "--config",
        str(config),
        "--",
        *args,
    )
    assert result["appended"] is True
    diagnostic = json.loads(err)["jev_drift"]
    assert diagnostic["status"] == "queued" and diagnostic["model_called"] is False
    assert diagnostic["timing_ns"]["owner_command"] > 0
    record = Path(result["json_path"])
    before = record.read_bytes()

    def provider(request, *args):
        assert "RENAMED_TIMEOUT" in json.dumps(request)
        return {"response": response(request, ["off_goal", "no_new_evidence"], nouls=DRIFT_NOULS)}

    drift.drain(root, config, transport=provider, credential=lambda: "fixture")
    report, _ = run("drift", "status", "--state-dir", str(root))
    assert report["counts"] == {"completed": 1}
    assert report["events"][0]["judgments"]["relation"] == "off_goal"
    assert record.read_bytes() == before
    # The original command still runs with no initialized observer and no key.
    off, err = run(
        "drift", "refresh", "--state-dir", str(tmp_path / "absent"), "--", *args
    )
    assert off["appended"] is True and not err
    assert not (tmp_path / "absent").exists()
