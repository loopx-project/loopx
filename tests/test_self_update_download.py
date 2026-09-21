import subprocess
from pathlib import Path

import pytest

from loopx.self_update_download import run_archive_installer


@pytest.mark.parametrize(
    "failure", [(22, "403"), (56, "403"), (18, "200"), (28, "000")]
)
def test_transient_download_discards_partial_bytes_before_execution(
    monkeypatch, failure
):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[0] == "curl":
            script = Path(args[args.index("--output") + 1])
            assert script.read_bytes() == b""
            assert script.stat().st_mode & 0o777 == 0o600
            if len(calls) == 1:
                script.write_text("echo PARTIAL_SECRET")
                return subprocess.CompletedProcess(
                    args, failure[0], failure[1], "SECRET"
                )
            script.write_text("printf complete")
            return subprocess.CompletedProcess(args, 0, "200", "")
        assert Path(args[1]).read_text() == "printf complete"
        return subprocess.CompletedProcess(args, 0, "complete", "")

    monkeypatch.setattr("loopx.self_update_download.subprocess.run", run)
    monkeypatch.setattr("loopx.self_update_download.time.sleep", lambda _: None)
    result, diagnostic = run_archive_installer(
        "https://example.invalid/install?secret=SECRET", env={}, timeout_seconds=90
    )
    assert result.stdout == "complete"
    assert [call[0] for call in calls] == ["curl", "curl", "bash"]
    assert diagnostic["stage"] == "installer_execution"
    assert len(diagnostic["attempts"]) == 2
    assert "SECRET" not in str(diagnostic)
    assert not Path(calls[-1][1]).exists()


@pytest.mark.parametrize(
    "status,attempts", [("403", 3), ("503", 3), ("401", 1), ("404", 1)]
)
def test_failed_download_never_executes_and_reports_only_safe_fields(
    monkeypatch, status, attempts
):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert args[0] == "curl"
        Path(args[args.index("--output") + 1]).write_text("PRIVATE_RESPONSE_BODY")
        return subprocess.CompletedProcess(
            args, 22, status, "PRIVATE_PROXY_CREDENTIALS"
        )

    monkeypatch.setattr("loopx.self_update_download.subprocess.run", run)
    monkeypatch.setattr("loopx.self_update_download.time.sleep", lambda _: None)
    result, diagnostic = run_archive_installer(
        "https://example.invalid/", env={}, timeout_seconds=90
    )
    assert result.returncode == 22
    assert len(calls) == attempts
    assert diagnostic["stage"] == "installer_download"
    assert diagnostic["attempts"][-1]["http_status"] == int(status)
    assert "PRIVATE" not in str(diagnostic) + result.stdout + result.stderr


def test_download_timeout_honors_total_budget(monkeypatch):
    now = [0.0]
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        now[0] += kwargs["timeout"]
        raise subprocess.TimeoutExpired(args, kwargs["timeout"], stderr="SECRET")

    monkeypatch.setattr("loopx.self_update_download.time.monotonic", lambda: now[0])
    monkeypatch.setattr("loopx.self_update_download.subprocess.run", run)
    result, diagnostic = run_archive_installer(
        "https://example.invalid/", env={}, timeout_seconds=2
    )
    assert result.returncode == 28
    assert len(calls) == 1
    assert now[0] == 2
    assert diagnostic["attempts"][0]["http_status"] == 0


def test_installer_receives_remaining_outer_timeout_budget(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "curl":
            Path(args[args.index("--output") + 1]).write_text("exit 0")
            return subprocess.CompletedProcess(args, 0, "200", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("loopx.self_update_download.time.monotonic", lambda: 0.0)
    monkeypatch.setattr("loopx.self_update_download.subprocess.run", run)
    result, diagnostic = run_archive_installer(
        "https://example.invalid/", env={}, timeout_seconds=600
    )

    assert result.returncode == 0
    assert diagnostic["stage"] == "installer_execution"
    assert calls[-1][1]["env"]["LOOPX_INSTALLER_TIMEOUT_SECONDS"] == "600"


@pytest.mark.parametrize("timeout", [False, True])
def test_installer_failure_is_not_retried(monkeypatch, timeout):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[0] == "curl":
            Path(args[args.index("--output") + 1]).write_text("exit 9")
            return subprocess.CompletedProcess(args, 0, "200", "")
        if timeout:
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        return subprocess.CompletedProcess(args, 9, "", "installer failed")

    monkeypatch.setattr("loopx.self_update_download.subprocess.run", run)
    result, diagnostic = run_archive_installer(
        "https://example.invalid/", env={}, timeout_seconds=90
    )
    assert result.returncode == (124 if timeout else 9)
    assert len(calls) == 2
    assert diagnostic["stage"] == "installer_execution"
