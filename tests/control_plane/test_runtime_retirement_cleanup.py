"""Fixture cleanup retains the real write result when a PID probe is stale."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest
import test_effect_runtime_integration as integration
from runtime_retirement_diagnostics import reap_copied_runtime
from loopx.control_plane import effect_runtime


def test_drain_cleanup_keeps_io_error_after_stale_pid_probe(tmp_path, monkeypatch, request, capsys):
    processes = []
    popen, kill = effect_runtime.subprocess.Popen, os.kill
    observe, alive = integration.failure_trace, effect_runtime._pid_is_alive

    def launch(args, *positional, **options):
        process = popen(args, *positional, **options)
        if any(Path(str(arg)).name == "effect_runtime_server.ts" for arg in args):
            processes.append(process)
        return process

    def stale_probe_after_error(captures):
        events = observe(captures)
        assert len(processes) == 1
        pid = processes[0].pid
        # The hosted failure occurs after the original IO error: a liveness
        # sample can outlive the process, and Windows refuses the later signal.
        monkeypatch.setattr(effect_runtime, "_pid_is_alive", lambda value: True if value == pid else alive(value))
        return events

    def refuse_stale_signal(pid, sig):
        if sig == signal.SIGTERM and any(process.pid == pid for process in processes):
            raise PermissionError(errno.EACCES, "synthetic exited-process signal refusal")
        return kill(pid, sig)

    monkeypatch.setattr(effect_runtime.subprocess, "Popen", launch)
    monkeypatch.setattr(integration, "failure_trace", stale_probe_after_error)
    monkeypatch.setattr(os, "kill", refuse_stale_signal)
    try:
        integration.test_runtime_drain_io_failure_reports_cause_without_suppressing_error(
            tmp_path, monkeypatch, request, capsys, diagnostics_available=True,
        )
        assert len(processes) == 1 and processes[0].poll() is not None
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


@pytest.mark.parametrize("already_exited", [False, True])
def test_reap_owned_child_preserves_neighbor(already_exited):
    command = [sys.executable, "-c", "import time; print('ready',flush=True); time.sleep(60)"]
    with subprocess.Popen(command, stdout=subprocess.PIPE, text=True) as child, \
            subprocess.Popen(command, stdout=subprocess.PIPE, text=True) as neighbor:
        try:
            assert child.stdout.readline().strip() == neighbor.stdout.readline().strip() == "ready"
            if already_exited:
                child.terminate()
                child.wait(timeout=5)
            reap_copied_runtime([child, neighbor], child.pid)
            assert child.poll() is not None
            assert neighbor.poll() is None
        finally:
            for process in (child, neighbor):
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)


def test_reap_rejects_uncaptured_pid_and_preserves_termination_failure(monkeypatch):
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]) as child:
        try:
            with pytest.raises(AssertionError, match="exactly one matching runtime child"):
                reap_copied_runtime([], child.pid)
            assert child.poll() is None

            def denied():
                raise PermissionError("synthetic live-child termination refusal")

            monkeypatch.setattr(child, "terminate", denied)
            with pytest.raises(PermissionError, match="live-child termination refusal"):
                reap_copied_runtime([child], child.pid)
            assert child.poll() is None
        finally:
            child.kill()
            child.wait(timeout=5)
