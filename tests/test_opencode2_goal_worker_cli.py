from __future__ import annotations

import argparse
import json
import os
from types import SimpleNamespace

from loopx.cli_commands.opencode2_goal_worker import (
    handle_opencode2_goal_worker_command,
)


class FakeProcess:
    """Minimal subprocess.Popen stand-in for CLI handler contract tests."""

    def __init__(self, stdout_text: str, rc: int, stderr_text: str = "") -> None:
        self._stdout = stdout_text
        self._rc = rc
        self.stderr = iter(stderr_text.splitlines(keepends=True))

    def communicate(self) -> tuple[str, None]:
        return self._stdout, None

    @property
    def returncode(self) -> int:
        return self._rc

    def kill(self) -> None:
        pass

    def wait(self) -> int:
        return self._rc


def _worker_args() -> argparse.Namespace:
    return SimpleNamespace(
        format="json",
        registry=None,
        goal_id="goal-cli-guard",
        directory="/tmp",
        agent_id=None,
        capability=[],
        session_id=None,
        task_body="Task.",
        max_turns=None,
        max_duration_minutes=None,
        force_resume=False,
        state_dir=None,
    )


def _run_handler(process: FakeProcess, monkeypatch) -> tuple[int, dict]:
    captured: list[dict] = []

    def printer(payload: dict, fmt: str, renderer) -> None:
        captured.append(payload)

    monkeypatch.setattr(
        "loopx.cli_commands.opencode2_goal_worker.subprocess.Popen",
        lambda *args, **kwargs: process,
    )
    code = handle_opencode2_goal_worker_command(
        _worker_args(), printer
    )
    return code, captured[0]


def test_empty_stdout_exit_zero_is_reported_as_failure(monkeypatch) -> None:
    code, payload = _run_handler(FakeProcess("", 0), monkeypatch)
    assert code == 1
    assert payload["ok"] is False
    assert payload["error_kind"] == "worker_result_missing"


def test_garbage_stdout_is_reported_as_failure(monkeypatch) -> None:
    code, payload = _run_handler(FakeProcess("not-json", 0), monkeypatch)
    assert code == 1
    assert payload["ok"] is False
    assert payload["error_kind"] == "worker_result_missing"
    assert "not-json" in str(payload.get("raw_stdout"))


def test_valid_worker_result_is_reported_as_success(monkeypatch) -> None:
    worker_stdout = json.dumps(
        {
            "version": 1,
            "operation": "opencode2_goal_worker",
            "ok": True,
            "result": {"kind": "standby"},
        }
    )
    code, payload = _run_handler(FakeProcess(worker_stdout, 0), monkeypatch)
    assert code == 0
    assert payload["ok"] is True
    assert payload["result"] == {"kind": "standby"}


def test_worker_reported_failure_keeps_ok_false(monkeypatch) -> None:
    worker_stdout = json.dumps(
        {
            "version": 1,
            "operation": "opencode2_goal_worker",
            "ok": False,
            "error": "boom",
        }
    )
    code, payload = _run_handler(FakeProcess(worker_stdout, 1), monkeypatch)
    assert code == 1
    assert payload["ok"] is False
    assert payload["error"] == "boom"


def test_missing_node_is_reported_as_node_unavailable(monkeypatch) -> None:
    def raise_not_found(*args, **kwargs):
        raise FileNotFoundError("node")

    monkeypatch.setattr(
        "loopx.cli_commands.opencode2_goal_worker.subprocess.Popen",
        raise_not_found,
    )
    captured: list[dict] = []

    def printer(payload: dict, fmt: str, renderer) -> None:
        captured.append(payload)

    code = handle_opencode2_goal_worker_command(_worker_args(), printer)
    assert code == 1
    assert captured[0]["ok"] is False
    assert captured[0]["error_kind"] == "node_unavailable"


def test_mkstemp_file_descriptor_closed(monkeypatch, tmp_path) -> None:
    closed_fds: list[int] = []
    real_close = os.close

    def tracking_close(fd: int):
        closed_fds.append(fd)
        real_close(fd)

    monkeypatch.setattr(
        "loopx.cli_commands.opencode2_goal_worker.os.close",
        tracking_close,
    )
    worker_stdout = json.dumps(
        {
            "version": 1,
            "operation": "opencode2_goal_worker",
            "ok": True,
            "result": {"kind": "standby"},
        }
    )
    code, payload = _run_handler(FakeProcess(worker_stdout, 0), monkeypatch)
    assert code == 0
    assert payload["ok"] is True
    assert len(closed_fds) >= 1
