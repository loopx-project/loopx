from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.canary.planner import REPO_ROOT
from loopx.canary.premerge import _gate_status, _run_gate_check, downgrade_inherited_baseline_failures
from loopx.canary.runner import run_canary_smoke_check


@pytest.mark.skipif(os.name != "posix", reason="POSIX subprocess partial timeout capture")
@pytest.mark.parametrize("entrypoint", ["catalog", "direct"])
def test_timeout_retains_bounded_utf8_diagnostics(tmp_path: Path, entrypoint: str) -> None:
    script = tmp_path / "examples" / "timeout.py"
    script.parent.mkdir()
    script.write_text(
        "import sys, time\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        "sys.stderr.reconfigure(encoding='utf-8')\n"
        "print('🔎' * 900 + 'validation started', flush=True)\n"
        "print('正在校验', file=sys.stderr, flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    if entrypoint == "catalog":
        result = run_canary_smoke_check(
            {"command": "python3 examples/timeout.py"},
            repo_root=tmp_path,
            timeout_seconds=2,
        )
    else:
        result = _run_gate_check(
            check_id="timeout",
            argv=[sys.executable, str(script)],
            reason="Timeout evidence fixture",
            execute=True,
            timeout_seconds=2,
            repo_root=tmp_path,
        )

    assert result["status"] == "timed_out"
    assert result["ok"] is False
    assert result["returncode"] is None
    assert result["stdout_tail"] == ("🔎" * 900 + "validation started\n")[-800:]
    assert result["stderr_tail"] == "正在校验\n"


@pytest.mark.parametrize(
    ("status", "diagnostic"),
    [("timed_out", "scanning started\n"), ("failed", "")],
)
def test_incomplete_ratchet_stays_failed(status: str, diagnostic: str) -> None:
    run = {
        "ok": False,
        "failure_count": 1,
        "selected_checks": [{
            "ok": False,
            "status": status,
            "returncode": None if status == "timed_out" else 1,
            "command": "python3 examples/control_plane/control-plane-maintainability-ratchet-smoke.py",
            "stdout_tail": diagnostic,
        }],
    }

    result = downgrade_inherited_baseline_failures(run, changed_files=["loopx/canary/premerge.py"])

    assert result is not None
    assert result["ok"] is False
    assert result["failure_count"] == 1
    assert result["selected_checks"][0]["status"] == status



def test_premerge_rejects_ratchet_config_error_before_report(tmp_path: Path) -> None:
    relative_script = Path("examples/control_plane/control-plane-maintainability-ratchet-smoke.py")
    script = tmp_path / relative_script
    script.parent.mkdir(parents=True)
    bad_baseline = tmp_path / "broken-baseline.json"
    bad_baseline.write_text("{", encoding="utf-8")
    script.write_text(
        "import runpy\n"
        "from pathlib import Path\n"
        "from loopx.canary import maintainability_ratchet\n"
        f"maintainability_ratchet.MODULE_METRIC_BASELINE_PATH = Path({str(bad_baseline)!r})\n"
        f"runpy.run_path({str(REPO_ROOT / relative_script)!r}, run_name='__main__')\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    result = run_canary_smoke_check(
        {"command": f"python3 {relative_script.as_posix()}"},
        repo_root=tmp_path,
    )
    assert result["returncode"] == 1
    assert "JSONDecodeError" in result["stderr_tail"]

    changed_files = ["loopx/canary/premerge.py"]
    run = downgrade_inherited_baseline_failures(
        {"ok": False, "failure_count": 1, "selected_checks": [result]},
        changed_files=changed_files,
    )
    assert run is not None
    gate = _gate_status(
        execute=True,
        changed_files=changed_files,
        direct_checks=[],
        catalog_run=run,
        risk_profile_run=None,
        boundary_run=None,
        manual_holds=[],
    )
    assert gate["merge_gate_passed"] is False
    assert result["status"] == "failed"
