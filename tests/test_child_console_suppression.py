"""Console-suppression spawn options for Windows console-less hosts (issue #9430).

Windows allocates a visible conhost window for every console-subsystem child of
a parent without a console. These tests never require Windows: each one inspects
the options handed to the spawn call and asserts the computed creationflags
(0 on POSIX and on a Windows host attached to a console; CREATE_NO_WINDOW under
an emulated Windows ``os.name`` on a host without one). The emulation rebinds
only the ``os`` name each site reads, so ``pathlib`` keeps resolving POSIX paths
on this host, and it answers the parent-console query directly. The visible
window and pipe behavior itself is owned by CI's Windows lanes and interactive
Windows verification, not by these host-agnostic assertions.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import loopx.entrypoint as entrypoint_module
import loopx.usage_ping as usage_ping
from loopx import registry as registry_module
from loopx.control_plane import effect_runtime as effect_runtime_module
from loopx.control_plane import state_recovery_io as state_recovery_module
from loopx.control_plane.agents import workspace_guard as workspace_guard_module
from loopx.control_plane.effect_runtime import EffectRuntimeStartupError
from loopx.control_plane.effect_runtime import _start_runtime as start_runtime
from loopx.control_plane.goals import planning_batch as planning_batch_module
from loopx.control_plane.heartbeat import installed_prompt_update as prompt_update_module
from loopx.control_plane.quota import scheduler_ack as scheduler_ack_module
from loopx.control_plane.runtime import node_probe as node_probe_module
from loopx.control_plane.runtime import validation_command as validation_command_module
from loopx.control_plane.state_recovery_io import _audit_owners as audit_owners
from loopx.control_plane.status import goal_source as goal_source_module
from loopx.control_plane.todos import completion_validation as completion_module
from loopx.control_plane.turn_driver import codex_execution_preparation as preparation_module
from loopx.control_plane.turn_driver import codex_stdio as codex_stdio_module
from loopx.control_plane.turn_driver import command_validation as command_validation_module
from loopx.control_plane.turn_driver import host_process_transport as host_transport_module
import loopx.extensions.process_runtime as process_runtime_module
from loopx.extensions.process_runtime import no_console_creationflags

WINDOWS_CREATE_NO_WINDOW = 0x08000000
WINDOWS_CREATE_NEW_PROCESS_GROUP = 0x200
WINDOWS_DETACHED_PROCESS = 0x8


class WindowsNameOnly:
    """Report the Windows platform name while delegating everything else to os."""

    def __getattr__(self, name: str) -> Any:
        return getattr(os, name)

    name = "nt"


@pytest.fixture()
def windows_spawn_constants(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide the Windows creation-flag constants on any host."""
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", WINDOWS_CREATE_NO_WINDOW, raising=False)
    monkeypatch.setattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", WINDOWS_CREATE_NEW_PROCESS_GROUP, raising=False)
    monkeypatch.setattr(subprocess, "DETACHED_PROCESS", WINDOWS_DETACHED_PROCESS, raising=False)


def emulate_windows_spawn(
    monkeypatch: pytest.MonkeyPatch,
    *site_modules: Any,
    constants: bool = True,
    console_attached: bool = False,
) -> None:
    """Make the helper and the named site modules observe ``os.name == 'nt'``.

    ``console_attached`` selects the host the spawn runs from: a console-less
    host (the default, the case issue #9430 reports) or one attached to a
    console, as when the user runs the command in PowerShell or cmd.
    """
    proxy = WindowsNameOnly()
    monkeypatch.setattr(process_runtime_module, "os", proxy)
    monkeypatch.setattr(
        process_runtime_module, "_parent_has_console", lambda: console_attached)
    for module in site_modules:
        monkeypatch.setattr(module, "os", proxy)
    if constants:
        monkeypatch.setattr(
            subprocess, "CREATE_NO_WINDOW", WINDOWS_CREATE_NO_WINDOW, raising=False)
        monkeypatch.setattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", WINDOWS_CREATE_NEW_PROCESS_GROUP,
            raising=False)
        monkeypatch.setattr(
            subprocess, "DETACHED_PROCESS", WINDOWS_DETACHED_PROCESS, raising=False)


class Captured:
    """Record the options handed to one spawn call."""

    def __init__(self, stdout: str = '{"ok": true}') -> None:
        self.stdout = stdout
        self.kwargs: dict[str, object] | None = None

    def record(self, *_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.kwargs = kwargs
        return subprocess.CompletedProcess(list(_args), 0, self.stdout, "")


def test_node_probe_passes_no_console_creationflags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = Captured(stdout="v22.22.3\n")
    monkeypatch.setattr(node_probe_module.shutil, "which", lambda _name: str(tmp_path / "node"))
    monkeypatch.setattr(node_probe_module.subprocess, "run", captured.record)
    assert node_probe_module.probe_node().ready
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == no_console_creationflags()

    with monkeypatch.context() as patched:
        emulate_windows_spawn(patched)
        assert node_probe_module.probe_node().ready
        assert captured.kwargs["creationflags"] == WINDOWS_CREATE_NO_WINDOW


def test_usage_ping_control_passes_no_console_creationflags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = Captured()
    monkeypatch.setattr(usage_ping.shutil, "which", lambda _name: sys.executable)
    monkeypatch.setattr(usage_ping.subprocess, "run", captured.record)
    assert usage_ping.control("status", tmp_path / "usage-ping.json") == {"ok": True}
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == no_console_creationflags()


def test_usage_ping_detached_child_keeps_windows_detachment_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = Captured()
    monkeypatch.setattr(usage_ping, "_environment_suppressed", lambda *_a, **_k: False)
    monkeypatch.setattr(usage_ping.shutil, "which", lambda _name: sys.executable)
    emulate_windows_spawn(monkeypatch, usage_ping)

    class Child:
        def __init__(self, *_args: object, **kwargs: object) -> None:
            captured.record(*_args, **kwargs)
            self.stdin = io.BytesIO()

    monkeypatch.setattr(usage_ping.subprocess, "Popen", Child)
    usage_ping._detach({"action": "observe", "path": str(tmp_path)})
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == (
        WINDOWS_DETACHED_PROCESS | WINDOWS_CREATE_NEW_PROCESS_GROUP
    )
    assert "start_new_session" not in captured.kwargs


def test_usage_ping_detached_child_closing_its_pipe_stays_best_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child that exits first closes the pipe; that race is not a launch error."""
    monkeypatch.setattr(usage_ping, "_environment_suppressed", lambda *_a, **_k: False)
    monkeypatch.setattr(usage_ping.shutil, "which", lambda _name: sys.executable)

    class ClosingStdin:
        def write(self, _data: bytes) -> int:
            raise OSError(232, "The pipe is being closed")

        def close(self) -> None:
            raise BrokenPipeError

    class Child:
        stdin = ClosingStdin()

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    monkeypatch.setattr(usage_ping.subprocess, "Popen", Child)
    usage_ping._detach({"action": "observe", "path": str(tmp_path)})


def test_effect_runtime_host_popen_passes_no_console_creationflags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = Captured()

    class ExitedHost:
        def __init__(self, *_args: object, **kwargs: object) -> None:
            captured.record(*_args, **kwargs)

        def poll(self) -> int:
            return 1

        def terminate(self) -> None:
            raise AssertionError("an exited host needs no termination")

    monkeypatch.setattr(effect_runtime_module, "_acquire_start_lock", lambda *a, **k: None)
    monkeypatch.setattr(effect_runtime_module, "_read_info", lambda *a, **k: None)
    monkeypatch.setattr(effect_runtime_module, "_node_executable", lambda: sys.executable)
    monkeypatch.setattr(effect_runtime_module, "_runtime_environment", lambda _token: {})
    monkeypatch.setattr(effect_runtime_module.subprocess, "Popen", ExitedHost)
    with pytest.raises(EffectRuntimeStartupError):
        start_runtime(fingerprint="a" * 32, info_path=tmp_path / "runtime" / "info.json")
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == no_console_creationflags()

    with monkeypatch.context() as patched:
        emulate_windows_spawn(patched, effect_runtime_module)
        with pytest.raises(EffectRuntimeStartupError):
            start_runtime(fingerprint="a" * 32, info_path=tmp_path / "runtime2" / "info.json")
        assert captured.kwargs["creationflags"] == WINDOWS_CREATE_NO_WINDOW


def test_registry_git_probe_combines_process_group_with_no_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = Captured()
    emulate_windows_spawn(monkeypatch, registry_module)

    class GitProbe:
        def __init__(self, *_args: object, **kwargs: object) -> None:
            captured.record(*_args, **kwargs)
            self.returncode = 0

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            return "", ""

    monkeypatch.setattr(registry_module, "_definitely_outside_git_worktree", lambda _p: False)
    monkeypatch.setattr(registry_module.subprocess, "Popen", GitProbe)
    registry_module._registry_git_probe(tmp_path / "registry.json")
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == (
        WINDOWS_CREATE_NEW_PROCESS_GROUP | WINDOWS_CREATE_NO_WINDOW
    )


def test_scheduler_ack_native_followup_passes_no_console_creationflags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = Captured()
    monkeypatch.setattr(scheduler_ack_module, "_node_executable", lambda: sys.executable)
    monkeypatch.setattr(scheduler_ack_module.subprocess, "run", captured.record)
    result = scheduler_ack_module._run_native_followup(
        runtime_root=tmp_path, before={}, host_facts={}, use_current_hint=False,
        reason_summary=None,
    )
    assert result == {"ok": True}
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == no_console_creationflags()


def test_direct_validation_passes_no_console_creationflags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = Captured()

    class ValidatorProcess:
        pid = 1111

        def __init__(self, *_args: object, **kwargs: object) -> None:
            captured.record(*_args, **kwargs)
            self.returncode = 0

        def __enter__(self) -> "ValidatorProcess":
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
            return b"", b""

    monkeypatch.setattr(validation_command_module.subprocess, "Popen", ValidatorProcess)
    exit_code = validation_command_module._direct_validation(
        [sys.executable, "-c", "pass"], tmp_path, 5,
    )
    assert exit_code == 0
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == no_console_creationflags()


def test_stdio_native_goal_transport_spawn_passes_no_console_creationflags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = Captured()

    class HostProcess:
        pid = 4242

        def __init__(self, *_args: object, **kwargs: object) -> None:
            captured.record(*_args, **kwargs)
            self.stdin = io.StringIO()
            self.stdout = io.StringIO("")
            self.returncode = 0

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def terminate(self) -> None:
            return None

        def kill(self) -> None:
            return None

    monkeypatch.setattr(codex_stdio_module.subprocess, "Popen", HostProcess)
    transport = codex_stdio_module.StdioNativeGoalTransport.spawn(
        [sys.executable, "-c", "pass"], cwd=str(Path.cwd()),
    )
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == no_console_creationflags()
    transport.process.stdin.close()
    transport.process.stdout.close()


def test_codex_execution_preparation_runner_passes_no_console_creationflags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = Captured()

    def record(command: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured.record(command, **kwargs)
        return subprocess.CompletedProcess(command, 0, b"{}", b"")

    monkeypatch.setattr(preparation_module.subprocess, "run", record)
    result = preparation_module._default_runner(
        [sys.executable, "-c", "pass"], cwd=tmp_path, timeout=1.0,
    )
    assert result.returncode == 0
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == no_console_creationflags()


def test_turn_command_validator_passes_no_console_creationflags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = Captured()
    monkeypatch.setattr(command_validation_module.subprocess, "run", captured.record)
    validate = command_validation_module.build_loopx_turn_command_validator(
        [sys.executable, "-c", "pass"], project=tmp_path, timeout_seconds=1.0,
    )
    assert validate({}, {})["status"] == "passed"
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == no_console_creationflags()


def test_planning_batch_cli_call_passes_no_console_creationflags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = Captured()
    monkeypatch.setattr(planning_batch_module.subprocess, "run", captured.record)
    backend = planning_batch_module.CliPlanningBatchBackend(
        cli=[sys.executable], cwd=tmp_path, env=dict(os.environ),
        packet_reader=lambda: {}, project="p",
    )
    assert backend._call("status") == {"ok": True}
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == no_console_creationflags()


def test_goal_source_run_passes_no_console_creationflags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = Captured()

    def record(argv: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured.record(argv, **kwargs)
        return subprocess.CompletedProcess(argv, 0, b"text", b"")

    monkeypatch.setattr(goal_source_module.subprocess, "run", record)
    assert goal_source_module._run([sys.executable, "-c", "pass"]) == "text"
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == no_console_creationflags()


def test_completion_validation_git_status_passes_no_console_creationflags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = Captured(stdout="")
    monkeypatch.setattr(completion_module.subprocess, "run", captured.record)
    assert completion_module._git_workspace_is_clean(tmp_path) is True
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == no_console_creationflags()


@pytest.mark.parametrize("probe", ["output", "origin"])
def test_workspace_guard_git_probes_pass_no_console_creationflags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probe: str,
) -> None:
    captured = Captured()

    def record(command: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.record(command, **kwargs)
        return subprocess.CompletedProcess(command, 1, "", "")

    monkeypatch.setattr(workspace_guard_module.subprocess, "run", record)
    if probe == "output":
        assert workspace_guard_module._git_command_output(tmp_path, "rev-parse", "HEAD") is None
    else:
        assert workspace_guard_module._git_origin_is_absent(tmp_path) is True
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == no_console_creationflags()


def test_state_recovery_audit_owners_passes_no_console_creationflags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = Captured()
    monkeypatch.setattr(state_recovery_module, "_node_executable", lambda: sys.executable)
    monkeypatch.setattr(state_recovery_module.subprocess, "run", captured.record)
    assert audit_owners({"path": str(tmp_path)}) == {"ok": True}
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == no_console_creationflags()


def test_installed_prompt_reconciliation_passes_no_console_creationflags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = Captured(stdout='{"ok": true, "results": []}')
    monkeypatch.setattr(prompt_update_module, "snapshot",
                        lambda **_k: {"ok": True, "entries": [{"name": "managed"}]})
    import loopx.upgrade as upgrade_module

    monkeypatch.setattr(upgrade_module, "codex_home", lambda: tmp_path / "codex-home")
    monkeypatch.setattr(prompt_update_module.subprocess, "run", captured.record)
    updated = prompt_update_module.update_with_prompts(
        {}, registry=tmp_path / "registry.json", runtime_root=None,
        timeout_seconds=5, runtime_update=lambda _payload, timeout_seconds: {"ok": True},
    )
    assert updated["upgrade_complete"] is True
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == no_console_creationflags()



class HandleCarryingBridgeProcess:
    """A Popen stand-in that survives WindowsHostJob.assign's handle read."""

    pid = 8484
    _handle = 0x2468  # what Popen retains on Windows; assign() reads it.

    def __init__(self, captured: "Captured") -> None:
        self._captured = captured

    def __call__(self, *_args: object, **kwargs: object) -> "HandleCarryingBridgeProcess":
        self._captured.record(*_args, **kwargs)
        self.stdin = io.StringIO()
        self.stdout = (line for line in ('{"kind": "result", "outcome": "exited"}\n',))
        self.returncode = 0
        return self

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


def install_windows_job_stand_in(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Replace the WindowsHostJob boundary with a recording stand-in.

    The real module cannot load off Windows (module-level ctypes.WinDLL), so
    tests that may take the Windows launch branch inject this instead. On a
    POSIX host the branch is not selected and the stand-in stays dormant.
    Returns the list the assign calls are recorded in.
    """
    assigned: list[object] = []

    class RecordingJob:
        name = "Local\\LoopXHost-test"

        def __enter__(self):
            return self

        def assign(self, process) -> None:
            # The real assign reads process._handle before any Win32 call; a
            # process without one cannot be supervised.
            assert process._handle is not None
            assigned.append(process)

        def finish(self) -> None:
            return None

        def __exit__(self, *_exc):
            self.finish()

    fake_job_module = types.ModuleType("loopx.control_plane.turn_driver.windows_host_job")
    fake_job_module.WindowsHostJob = RecordingJob
    monkeypatch.setitem(
        sys.modules, "loopx.control_plane.turn_driver.windows_host_job", fake_job_module)
    return assigned


def test_run_host_process_bridge_passes_no_console_creationflags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = Captured()
    # The stand-in keeps this test correct wherever it runs: dormant on POSIX
    # (the branch is not selected), and on Windows the launch assigns the
    # spawned process through the recording boundary instead of a real Job.
    install_windows_job_stand_in(monkeypatch)
    monkeypatch.setattr(host_transport_module, "_node_executable", lambda: sys.executable)
    monkeypatch.setattr(
        host_transport_module.subprocess, "Popen", HandleCarryingBridgeProcess(captured))
    observation = host_transport_module.run_host_process(
        [sys.executable, "-c", "pass"], project=tmp_path, input_text="", timeout_seconds=1.0,
    )
    assert observation.get("outcome") == "exited"
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == no_console_creationflags()


def test_entrypoint_windows_native_followup_call_passes_no_console_creationflags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = Captured()
    monkeypatch.delenv("LOOPX_KUNLUNCODE_OUTER_CONTROLLER", raising=False)
    monkeypatch.setattr(entrypoint_module.shutil, "which", lambda _name: sys.executable)
    emulate_windows_spawn(monkeypatch, entrypoint_module)
    monkeypatch.setattr(
        subprocess, "call", lambda *_args, **kwargs: (captured.record(*_args, **kwargs), 0)[1]
    )
    monkeypatch.setattr(
        sys, "argv",
        ["loopx", "quota", "scheduler-ack-current",
         "--scheduler-host-facts-chunk", "chunk", "--turn-instance-id", "turn"],
    )
    assert entrypoint_module.main(None) == 0
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == WINDOWS_CREATE_NO_WINDOW


@pytest.mark.parametrize(
    ("platform_name", "console_attached", "expected"),
    [
        ("posix", False, 0),
        ("nt", False, WINDOWS_CREATE_NO_WINDOW),
        ("nt", True, 0),
    ],
)
def test_no_console_creationflags_follows_the_parent_console(
    monkeypatch: pytest.MonkeyPatch, platform_name: str, console_attached: bool,
    expected: int,
) -> None:
    """The flag is returned for a console-less Windows host and nothing else."""
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", WINDOWS_CREATE_NO_WINDOW, raising=False)
    monkeypatch.setattr(process_runtime_module, "os", SimpleNamespace(name=platform_name))
    monkeypatch.setattr(
        process_runtime_module, "_parent_has_console", lambda: console_attached)
    assert no_console_creationflags() == expected


@pytest.mark.parametrize(
    ("code_page", "window", "attached"),
    [(0, 0, False), (65001, 0, True), (0, 0x1234, True), (437, 0x1234, True)],
)
def test_console_attached_accepts_either_win32_query(
    code_page: int, window: int, attached: bool,
) -> None:
    """A console-less process fails both queries; either success means attached."""
    kernel32 = SimpleNamespace(
        GetConsoleCP=lambda: code_page, GetConsoleWindow=lambda: window)
    assert process_runtime_module._console_attached(kernel32) is attached


def _run_entrypoint_native_followup(
    monkeypatch: pytest.MonkeyPatch, captured: Captured,
) -> int:
    monkeypatch.delenv("LOOPX_KUNLUNCODE_OUTER_CONTROLLER", raising=False)
    monkeypatch.setattr(entrypoint_module.shutil, "which", lambda _name: sys.executable)
    monkeypatch.setattr(
        subprocess, "call", lambda *_args, **kwargs: (captured.record(*_args, **kwargs), 0)[1])
    monkeypatch.setattr(
        sys, "argv",
        ["loopx", "quota", "scheduler-ack-current",
         "--scheduler-host-facts-chunk", "chunk", "--turn-instance-id", "turn"],
    )
    return entrypoint_module.main(None)


def test_entrypoint_windows_native_followup_keeps_the_console_receipt_when_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user running the follow-up in PowerShell or cmd still reads its receipt.

    The scheduler child writes its receipt to an inherited stdout. Attached to a
    console, the child inherits that console and the receipt is printed; the
    creation flags stay 0 and the streams are not redirected. Passing
    CREATE_NO_WINDOW here would detach the child from the console and the
    receipt would vanish while every write still reported success.
    """
    captured = Captured()
    emulate_windows_spawn(monkeypatch, entrypoint_module, console_attached=True)
    assert _run_entrypoint_native_followup(monkeypatch, captured) == 0
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == 0
    assert "stdout" not in captured.kwargs
    assert "stderr" not in captured.kwargs
    assert captured.kwargs.get("capture_output") is None


def test_entrypoint_windows_native_followup_is_suppressed_on_a_consoleless_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same call from a host without a console still suppresses the window."""
    captured = Captured()
    emulate_windows_spawn(monkeypatch, entrypoint_module, console_attached=False)
    assert _run_entrypoint_native_followup(monkeypatch, captured) == 0
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == WINDOWS_CREATE_NO_WINDOW


def test_consoleless_host_receipt_arrives_through_a_redirected_pipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A redirected child keeps its receipt: the pipe, not a console, carries it."""
    captured = Captured()
    monkeypatch.setattr(usage_ping, "_environment_suppressed", lambda *_a, **_k: False)
    monkeypatch.setattr(usage_ping.shutil, "which", lambda _name: sys.executable)
    emulate_windows_spawn(monkeypatch, usage_ping, console_attached=False)
    monkeypatch.setattr(usage_ping.subprocess, "run", captured.record)
    assert usage_ping.control("status", tmp_path / "usage-ping.json") == {"ok": True}
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == WINDOWS_CREATE_NO_WINDOW
    assert captured.kwargs["capture_output"] is True


def test_run_host_process_bridge_windows_branch_assigns_job_and_passes_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Windows spawn path supervises the child through its Host Job.

    The production branch imports ``WindowsHostJob`` lazily and assigns the
    spawned process before any request is written. This test selects that
    branch on any host (``sys.platform`` patched to ``win32``), replaces the
    Job boundary with the shared recording stand-in, and drives the same
    handle-carrying process fixture as the POSIX-branch test. Asserts the
    assignment happened for THIS process and that the console-less creation
    flags reached the spawn options.
    """
    captured = Captured()
    assigned = install_windows_job_stand_in(monkeypatch)
    monkeypatch.setattr(sys, "platform", "win32")
    emulate_windows_spawn(monkeypatch, host_transport_module)
    monkeypatch.setattr(host_transport_module, "_node_executable", lambda: sys.executable)
    monkeypatch.setattr(
        host_transport_module.subprocess, "Popen", HandleCarryingBridgeProcess(captured))
    observation = host_transport_module.run_host_process(
        [sys.executable, "-c", "pass"], project=tmp_path, input_text="", timeout_seconds=1.0,
    )
    assert observation.get("outcome") == "exited"
    assert captured.kwargs is not None
    assert captured.kwargs["creationflags"] == WINDOWS_CREATE_NO_WINDOW
    assert len(assigned) == 1
    assert isinstance(assigned[0], HandleCarryingBridgeProcess)
