"""Real Windows descendant cleanup, including loss of the Python owner."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from loopx.control_plane.turn_driver import host_process_transport as transport

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object transport")


@pytest.mark.parametrize("suffix", ["", ".cmd", ".exe", ".bat", ".com"])
def test_missing_host_is_spawn_failure(tmp_path: Path, suffix: str) -> None:
    result = transport.run_host_process([str(tmp_path / ("absent" + suffix))],
        project=tmp_path, input_text="", timeout_seconds=5)
    assert result["outcome"] == "spawn_failed" and result["returncode"] is None


def test_empty_command_cannot_resolve_a_dot_exe(tmp_path: Path) -> None:
    shutil.copyfile(Path(os.environ["SYSTEMROOT"]) / "System32" / "cmd.exe", tmp_path / ".exe")
    result = transport.run_host_process(["", "/d", "/c", "echo wrong>marker"],
        project=tmp_path, input_text="", timeout_seconds=5)
    assert result["outcome"] == "spawn_failed" and not (tmp_path / "marker").exists()


@pytest.mark.parametrize("suffix", [".cmd", ".exe", ".com"])
def test_explicit_exe_never_executes_an_added_extension(tmp_path: Path, suffix: str) -> None:
    if suffix == ".cmd":
        (tmp_path / "absent.exe.cmd").write_text("@echo alternate>marker\n")
    else:
        shutil.copyfile(Path(os.environ["SYSTEMROOT"]) / "System32" / "cmd.exe", tmp_path / ("absent.exe" + suffix))
    result = transport.run_host_process([str(tmp_path / "absent.exe"), "/d", "/c", "echo alternate>marker"],
        project=tmp_path, input_text="", timeout_seconds=5)
    assert result["outcome"] == "spawn_failed"
    assert not (tmp_path / "marker").exists()


def test_bare_command_keeps_exe_priority_independent_of_parent_pathext(tmp_path: Path, monkeypatch) -> None:
    shutil.copyfile(Path(os.environ["SYSTEMROOT"]) / "System32" / "cmd.exe", tmp_path / "runner.exe")
    shutil.copyfile(Path(os.environ["SYSTEMROOT"]) / "System32" / "whoami.exe", tmp_path / "runner.com")
    (tmp_path / "runner.cmd").write_text("@echo wrong-batch\n")
    monkeypatch.setenv("PATHEXT", ".CMD;.COM;.EXE")
    output = []
    environment = {**os.environ, "PATHEXT": ".EXE"}
    before = environment.copy()
    result = transport.run_host_process(["runner", "/d", "/c", "echo expected-exe"],
        project=tmp_path, input_text="", timeout_seconds=5, environment=environment, on_stdout=output.append)
    assert result["outcome"] == "exited" and result["returncode"] == 0
    assert "".join(output).strip() == "expected-exe"
    assert environment == before and os.environ["PATHEXT"] == ".CMD;.COM;.EXE"


@pytest.mark.parametrize("suffix", [".cmd", ".bat"])
def test_batch_uses_host_relative_path_preserving_input_and_arguments(tmp_path: Path, suffix: str) -> None:
    scripts = tmp_path / "bin with spaces"
    scripts.mkdir()
    echo = scripts / "echo.py"
    echo.write_text("import json,sys;print(json.dumps({'input':json.load(sys.stdin),'args':sys.argv[1:]}))")
    (scripts / ("host" + suffix)).write_text(f'@echo off\n"{sys.executable}" "{echo}" %*\n')
    output = []
    result = transport.run_host_process(["host" + suffix, "two words", "", "汉字"],
        project=tmp_path, input_text='{"message":"hello"}', timeout_seconds=5,
        environment={**os.environ, "PATH": "bin with spaces" + os.pathsep + os.environ["PATH"]}, on_stdout=output.append)
    assert result["outcome"] == "exited" and result["returncode"] == 0
    assert json.loads("".join(output)) == {"input": {"message": "hello"}, "args": ["two words", "", "汉字"]}


def test_batch_selection_uses_host_cwd_not_owner(tmp_path: Path, monkeypatch) -> None:
    owner, project = tmp_path / "owner", tmp_path / "project"
    owner.mkdir()
    project.mkdir()
    (owner / "host.cmd").write_text("@echo wrong-owner\n")
    (project / "host.cmd").write_text("@echo correct-host\n")
    monkeypatch.chdir(owner)
    output = []
    result = transport.run_host_process(["host.cmd"], project=project,
        input_text="", timeout_seconds=5, on_stdout=output.append)
    assert result["returncode"] == 0 and "".join(output).strip() == "correct-host"


def test_selection_then_removal_does_not_restart_search(tmp_path: Path) -> None:
    from loopx.control_plane.turn_driver import windows_host_command
    target = tmp_path / "removed.exe"
    shutil.copyfile(Path(os.environ["SYSTEMROOT"]) / "System32" / "cmd.exe", target)
    shutil.copyfile(target, tmp_path / "removed.exe.exe")
    report = tmp_path / "created.json"
    script = (
        "import sys;from pathlib import Path;"
        f"sys.path.insert(0,{str(Path(windows_host_command.__file__).parents[3])!r});"
        "from loopx.control_plane.turn_driver import windows_host_command as relay;"
        "resolve=relay.resolve_executable;"
        "\ndef remove(name):\n path=resolve(name);Path(path).unlink();return path\n"
        "relay.resolve_executable=remove;sys.argv=['relay',*sys.argv[1:]];sys.exit(relay.main())"
    )
    result = subprocess.run([sys.executable, "-c", script, str(report), str(target), "/d", "/c", "echo wrong>marker"],
        cwd=tmp_path, timeout=10, capture_output=True)
    assert report.exists(), result.stderr.decode(errors="replace")
    assert result.returncode == 1 and json.loads(report.read_text()) is False
    assert not (tmp_path / "marker").exists()


def test_running_host_exit_and_caller_failure_are_distinct(tmp_path: Path) -> None:
    from loopx.control_plane.turn_driver.executor import _run_host
    result = transport.run_host_process([sys.executable, "-c", "import sys;sys.exit(7)"],
        project=tmp_path, input_text="", timeout_seconds=5)
    assert result["outcome"] == "exited" and result["returncode"] == 7
    failure = _run_host({}, argv=[str(tmp_path / "absent.exe")], project=tmp_path, timeout_seconds=5)
    assert failure["reason"] == "host process spawn_failed"
    assert failure["failure_kind"] == "transport_lost" and failure["returncode"] is None


@pytest.mark.parametrize("report_content", [None, '"true"'])
def test_missing_or_invalid_launch_report_cannot_use_business_stdout(tmp_path: Path, monkeypatch, report_content) -> None:
    popen = subprocess.Popen
    observed = []
    invalid = tmp_path / "invalid.json"
    if report_content is not None:
        invalid.write_text(report_content)

    def launch(argv, **kwargs):
        argv = list(argv)
        if "--windows-launch-status" not in argv:
            return popen(argv, **kwargs)
        argv[argv.index("--windows-launch-status") + 1] = str(invalid)
        process = popen(argv, **kwargs)
        observed.append(process)
        return process

    monkeypatch.setattr(transport.subprocess, "Popen", launch)
    with pytest.raises(RuntimeError, match="supervision returned no result"):
        transport.run_host_process([sys.executable, "-c", "print('true')"],
            project=tmp_path, input_text="", timeout_seconds=5)
    assert observed[0].poll() is not None and observed[0].stdout.closed


def host_source(marker: Path, pid: Path, *, exit_leader: bool = False) -> str:
    child = (
        "import os,time;from pathlib import Path;"
        f"Path({str(pid)!r}).write_text(str(os.getpid()));"
        f"p=Path({str(marker)!r});"
        "\nwhile True:\n with p.open('a') as f:f.write('x');f.flush()\n time.sleep(.02)"
    )
    return (
        "import subprocess,sys,time;from pathlib import Path;"
        f"subprocess.Popen([sys.executable,'-c',{child!r}],stdin=subprocess.DEVNULL,"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True);"
        f"p=Path({str(marker)!r});"
        "\nwhile not p.exists():time.sleep(.01)\n"
        "print('started',flush=True)\n" + ("" if exit_leader else "time.sleep(60)")
    )


def clean_fixture_child(pid: Path) -> None:
    # Only a child this test actually launched and recorded; never enumerate processes.
    if pid.exists():
        try:
            os.kill(int(pid.read_text()), 9)
        except (ProcessLookupError, PermissionError, OSError):
            pass


@pytest.mark.parametrize("boundary", ["leader_exit", "timeout", "consumer_failure"])
def test_windows_real_host_descendants_stop_before_return(tmp_path: Path, boundary: str) -> None:
    marker, pid, record = (tmp_path / name for name in ("writes", "child.pid", "host.json"))
    environment = {**os.environ, transport.HOST_PROCESS_RECORD_ENV: str(record)}
    def consume(_text: str) -> None:
        if boundary == "consumer_failure":
            raise ValueError("consumer rejected")
    try:
        def run():
            return transport.run_host_process(
                [sys.executable, "-c", host_source(marker, pid, exit_leader=boundary == "leader_exit")],
                project=tmp_path, input_text="", timeout_seconds=2,
                environment=environment, on_stdout=consume,
            )
        if boundary == "consumer_failure":
            with pytest.raises(ValueError, match="consumer rejected"):
                run()
        else:
            result = run()
            assert result["outcome"] == ("exited" if boundary == "leader_exit" else "timeout")
        assert marker.exists(), "The real descendant must have executed"
        assert transport.execution_host_drain(record) == transport.HOST_PROCESS_DRAINED
        before = marker.read_bytes()
        time.sleep(.2)
        assert marker.read_bytes() == before, "A descendant continued writing after cleanup"
    finally:
        clean_fixture_child(pid)


def test_windows_cold_readback_after_owner_kill_sees_whole_job_exit(tmp_path: Path) -> None:
    marker, pid, record = (tmp_path / name for name in ("writes", "child.pid", "host.json"))
    argv = [sys.executable, "-c", host_source(marker, pid)]
    source = (
        "from pathlib import Path;from loopx.control_plane.turn_driver.host_process_transport "
        f"import run_host_process;run_host_process({argv!r},project=Path({str(tmp_path)!r}),"
        "input_text='',timeout_seconds=60)"
    )
    owner = subprocess.Popen([sys.executable, "-c", source],
        env={**os.environ, transport.HOST_PROCESS_RECORD_ENV: str(record)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert marker.exists(), "Host never launched"
        assert transport.execution_host_drain(record) == transport.HOST_PROCESS_DRAINING
        owner.kill()
        owner.wait(timeout=5)
        deadline = time.monotonic() + 5
        while transport.execution_host_drain(record) != transport.HOST_PROCESS_DRAINED and time.monotonic() < deadline:
            time.sleep(.02)
        assert transport.execution_host_drain(record) == transport.HOST_PROCESS_DRAINED
        before = marker.read_bytes()
        time.sleep(.2)
        assert marker.read_bytes() == before
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        clean_fixture_child(pid)


def test_windows_legacy_finished_pid_does_not_prove_job_drain(tmp_path: Path) -> None:
    record = tmp_path / "old.json"
    from loopx.file_lock import lock_holder_host_label
    record.write_text(json.dumps({"schema_version": transport.HOST_PROCESS_RECORD_SCHEMA_VERSION,
        "host": lock_holder_host_label(), "supervises": "host", "supervision": "direct",
        "phase": "finished", "bridge_pid": 9999999, "process_group": None}))
    assert transport.execution_host_drain(record) == transport.HOST_PROCESS_UNSUPPORTED_PLATFORM


def test_windows_job_assignment_failure_launches_no_host(tmp_path: Path, monkeypatch) -> None:
    from loopx.control_plane.turn_driver.windows_host_job import WindowsHostJob
    marker = tmp_path / "must-not-execute"
    def deny(_self, _process):
        raise PermissionError("assignment rejected")
    monkeypatch.setattr(WindowsHostJob, "assign", deny)
    with pytest.raises(PermissionError, match="assignment rejected"):
        transport.run_host_process([sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).write_text('bad')"],
            project=tmp_path, input_text="", timeout_seconds=5)
    assert not marker.exists()
