from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.capabilities.performance_diagnosis import cli


def command(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json", *args],
                          env={**os.environ, "TMPDIR": str(tmp_path)}, text=True,
                          capture_output=True, timeout=45)


def test_real_cli_plan_and_v8_profile_readback(tmp_path: Path) -> None:
    argv = tmp_path / "command.json"
    argv.write_text(json.dumps(["node", "-e", "let n=0; for(let i=0;i<2000000;i++)n+=i"]), encoding="utf-8")
    capture = tmp_path / "capture"
    capture.mkdir()
    planned = command(tmp_path, "performance-diagnosis", "plan", "--tool", "node-cpu",
                      "--command-json", str(argv), "--output-directory", str(capture))
    assert planned.returncode == 0, planned.stderr + planned.stdout
    plan = json.loads(planned.stdout)
    assert plan["execution_performed"] is False
    assert not Path(plan["artifact"]).exists()  # A plan cannot secretly launch the target.
    executed = subprocess.run(plan["profile_argv"], capture_output=True, text=True, timeout=30)
    assert executed.returncode == 0, executed.stderr
    assert Path(plan["artifact"]).stat().st_size > 0
    observed = command(tmp_path, "performance-diagnosis", "inspect", "--profile-json", plan["artifact"])
    assert observed.returncode == 0, observed.stderr + observed.stdout
    result = json.loads(observed.stdout)
    assert result["format"] == "v8-cpu"
    assert result["benchmark_qualified"] is False
    assert result["root_cause_proven"] is False
    assert result["profiles"][0]["self_hotspots"]


@pytest.mark.parametrize("payload", [{}, {"nodes": [], "samples": []}, {"$schema": "unknown"}])
def test_real_cli_rejects_unusable_evidence(tmp_path: Path, payload) -> None:
    profile = tmp_path / "bad.json"
    profile.write_text(json.dumps(payload), encoding="utf-8")
    result = command(tmp_path, "performance-diagnosis", "inspect", "--profile-json", str(profile))
    assert result.returncode == 1
    assert json.loads(result.stdout)["ok"] is False


def test_file_limit_is_checked_before_json_decode(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "large.json"
    path.write_text(" " * 32, encoding="utf-8")
    monkeypatch.setattr(cli, "MAX_INPUT_BYTES", 16)
    with pytest.raises(ValueError, match="16 MiB"):
        cli._load(str(path))


def test_real_cli_reads_profile_larger_than_inline_transport(tmp_path: Path) -> None:
    profile = tmp_path / "large.speedscope.json"
    profile.write_text(json.dumps({
        "$schema": "https://www.speedscope.app/file-format-schema.json",
        "shared": {"frames": [{"name": "work"}]},
        "profiles": [{"type": "sampled", "name": "main", "unit": "milliseconds",
                      "startValue": 0, "endValue": 1, "samples": [[0]], "weights": [1]}],
        "exporter": "x" * (3 * 1024 * 1024),
    }), encoding="utf-8")
    result = command(tmp_path, "performance-diagnosis", "inspect", "--profile-json", str(profile))
    assert result.returncode == 0, result.stderr + result.stdout
    observed = json.loads(result.stdout)
    assert observed["profiles"][0]["self_hotspots"][0]["self_ms"] == 1
    assert "exporter" not in observed
    assert len(result.stdout) < 4096
    assert not [path for path in tmp_path.glob("loopx-effect-*")
                if not path.name.startswith("loopx-effect-runtime-")]  # Only the shared runtime persists.
