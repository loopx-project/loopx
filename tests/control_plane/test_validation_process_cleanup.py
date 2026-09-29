"""Caller validation must not leave its owned children running after timeout."""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture

from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.runtime.validation_command import run_caller_validation
from loopx.control_plane.todos.completion_validation_projection import completion_validation_declaration_sha256
from loopx.control_plane.todos.completion_validation_store import read_completion_validation_declaration
from loopx.todos import add_goal_todo


def _validator(tmp_path: Path, *, parent_exits: bool = False) -> list[str]:
    # An inherited pipe keeps communicate() open even if the leader exits.
    # SIGUSR1 is a liveness oracle: a surviving child acknowledges after the
    # validation call returned, without relying on PID absence (zombies exist).
    child = (
        "import os,signal,time;from pathlib import Path;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        f"signal.signal(signal.SIGUSR1,lambda *_:Path({str(tmp_path / 'escaped')!r}).touch());"
        f"Path({str(tmp_path / 'ready')!r}).write_text(str(os.getpid()));"
        "time.sleep(30)"
    )
    parent = (
        "import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-c',{child!r}]);"
        f"time.sleep({0.1 if parent_exits else 30})"
    )
    return [sys.executable, "-c", parent]


def _owned_child_pid(tmp_path: Path) -> int:
    return int((tmp_path / "ready").read_text())


def _assert_child_stopped(tmp_path: Path) -> None:
    pid = _owned_child_pid(tmp_path)
    try:
        os.kill(pid, signal.SIGUSR1)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 0.3
    while time.monotonic() < deadline and not (tmp_path / "escaped").exists():
        time.sleep(0.01)
    assert not (tmp_path / "escaped").exists(), "owned child survived validation cancellation"


def _cleanup_child(tmp_path: Path) -> None:
    # Only a PID emitted by this test's fresh synthetic child is eligible.
    if not (tmp_path / "ready").exists():
        return
    try:
        os.kill(_owned_child_pid(tmp_path), signal.SIGKILL)
    except ProcessLookupError:
        pass


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
@pytest.mark.parametrize("parent_exits", [False, True])
def test_timeout_stops_children_even_after_leader_exit(
    tmp_path: Path, parent_exits: bool,
) -> None:
    started = time.monotonic()
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            run_caller_validation(
                tmp_path,
                validation_argv=_validator(tmp_path, parent_exits=parent_exits),
                validation_label="Synthetic timeout",
                timeout_seconds=1,
            )
        assert time.monotonic() - started < 3
        _assert_child_stopped(tmp_path)
    finally:
        _cleanup_child(tmp_path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
def test_cancellation_stops_children_and_preserves_original_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupt(_self, *args, **kwargs):
        deadline = time.monotonic() + 3
        while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (tmp_path / "ready").exists(), "synthetic child did not start"
        raise KeyboardInterrupt("synthetic caller cancellation")

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupt)
    try:
        with pytest.raises(KeyboardInterrupt, match="synthetic caller cancellation"):
            run_caller_validation(
                tmp_path,
                validation_argv=_validator(tmp_path),
                validation_label="Synthetic cancellation",
                timeout_seconds=5,
            )
        _assert_child_stopped(tmp_path)
    finally:
        _cleanup_child(tmp_path)


@pytest.mark.parametrize("exit_code", [0, 4])
@pytest.mark.parametrize("command_form", ["argv", "text"])
def test_exit_receipt_keeps_cwd_environment_and_privacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int,
    command_form: str,
) -> None:
    monkeypatch.setenv("VALIDATION_TEST_SENTINEL", "inherited")
    code = (
        "import os,sys;from pathlib import Path;"
        "assert Path.cwd().name==sys.argv[1];"
        "assert os.environ['VALIDATION_TEST_SENTINEL']=='inherited';"
        "assert os.environ['PYTHONDONTWRITEBYTECODE']=='1';"
        "print('private stdout');print('private stderr',file=sys.stderr);"
        f"raise SystemExit({exit_code})"
    )
    argv = [sys.executable, "-c", code, tmp_path.name]
    result = run_caller_validation(
        tmp_path,
        **({"validation_argv": argv} if command_form == "argv" else
           {"validation_command": shlex.join(argv)}),
        validation_label="Independent validation",
        timeout_seconds=5,
    )
    assert result == {
        "schema_version": "issue_fix_validation_command_v0",
        "command_label": "Independent validation",
        "exit_code": exit_code,
        "passed": exit_code == 0,
        "stdout_captured": False,
        "stderr_captured": False,
        "local_path_captured": False,
    }
    assert "private stdout" not in json.dumps(result)


def test_success_keeps_inherited_stdin() -> None:
    inner = "import sys;assert sys.stdin.read()=='synthetic input'"
    outer = (
        "import json,sys;from pathlib import Path;"
        "from loopx.control_plane.runtime.validation_command import run_caller_validation;"
        f"print(json.dumps(run_caller_validation(Path.cwd(),validation_argv=[sys.executable,'-c',{inner!r}],"
        "validation_label='Inherited stdin',timeout_seconds=3)))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", outer],
        input="synthetic input", text=True, capture_output=True,
        cwd=Path(__file__).resolve().parents[2], timeout=5,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["passed"] is True


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_cli_timeout_keeps_canonical_todo_open_and_declaration_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = promoted_create_fixture(tmp_path, provider=provider)
    created = add_goal_todo(
        registry_path=registry, goal_id="goal-a", role="agent",
        text="Validate before completion", claimed_by="agent-a", agent_id="agent-a",
        validation_command_json=json.dumps(_validator(tmp_path)),
        validation_label="Independent timeout", validation_timeout_seconds=1,
    )
    todo_id = created["todo_id"]
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    declaration = read_completion_validation_declaration(
        runtime_root=runtime, goal_id="goal-a", todo_id=todo_id,
    )
    command = [
        sys.executable, "-m", "loopx.cli", "--format", "json",
        "--registry", str(registry), "--runtime-root", str(runtime),
        "todo", "complete", "--goal-id", "goal-a", "--todo-id", todo_id,
        "--agent-id", "agent-a", "--claimed-by", "agent-a", "--no-follow-up",
        "--evidence", "Synthetic validation",
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=15)
        assert proc.returncode != 0
        result = json.loads(proc.stdout)
        assert result["validation_blocked_completion"] is True
        receipt = result["validation_failure"]["validation_receipt"]
        assert receipt["status"] == "timeout"
        assert receipt["passed"] is False
        assert receipt["exit_code"] is None
        assert receipt["stdout_captured"] is False
        assert receipt["stderr_captured"] is False
        assert receipt["local_path_captured"] is False
        assert before is not None
        assert declaration is not None
        assert completion_validation_declaration_sha256(declaration) == before["todos"][0]["completion_validation_sha256"]
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == before
        assert read_completion_validation_declaration(
            runtime_root=runtime, goal_id="goal-a", todo_id=todo_id,
        ) == declaration
        _assert_child_stopped(tmp_path)
    finally:
        _cleanup_child(tmp_path)
