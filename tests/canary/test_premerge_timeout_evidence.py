from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from loopx.canary.premerge import _run_gate_check, downgrade_inherited_baseline_failures
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
            "command": "python3 examples/control_plane/control-plane-maintainability-ratchet-smoke.py",
            "stdout_tail": diagnostic,
        }],
    }

    result = downgrade_inherited_baseline_failures(run, changed_files=["loopx/canary/premerge.py"])

    assert result is not None
    assert result["ok"] is False
    assert result["failure_count"] == 1
    assert result["selected_checks"][0]["status"] == status
