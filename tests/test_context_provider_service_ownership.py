"""Regression tests for the context-provider ownership liveness probe."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx import file_lock
from loopx.capabilities.context_providers import service_ownership


def write_receipt(path: Path, *, pid: int) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": "context_provider_service_ownership_receipt_v0",
                "provider": "contract-provider",
                "service_ref": "public-index-service",
                "ownership_mode": "persistent_external",
                "generation": "generation-1",
                "pid": pid,
                "observed_at": "2026-07-12T04:30:00Z",
            }
        ),
        encoding="utf-8",
    )
    return path


def reaped_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def load(path: Path) -> service_ownership.ContextProviderServiceOwnership:
    return service_ownership.load_context_provider_service_ownership(
        path,
        expected_provider="contract-provider",
    )


def test_exited_provider_pid_is_typed_unavailable(tmp_path: Path) -> None:
    ownership = load(write_receipt(tmp_path / "service.json", pid=reaped_pid()))
    assert ownership.verified is False
    assert ownership.status == "unavailable"
    assert ownership.reason_code == "provider_service_process_unavailable"
    assert ownership.process_alive is False
    packet = ownership.public_packet(required=True)
    assert packet["ok"] is False
    assert packet["progress_disposition"] == "not_started"


def test_ownership_probe_delegates_to_the_shared_non_signaling_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probed: list[int] = []

    def probe(pid: int) -> bool:
        probed.append(pid)
        return True

    monkeypatch.setattr(file_lock, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(file_lock, "_windows_process_is_alive", probe)

    ownership = load(write_receipt(tmp_path / "service.json", pid=os.getpid()))
    assert probed == [os.getpid()]
    assert ownership.verified is True


@pytest.mark.skipif(
    os.name != "nt",
    reason="signal 0 is a console control event on Windows, not a liveness probe",
)
def test_native_windows_ownership_probe_sends_no_process_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_signal(*args: object, **kwargs: object) -> None:
        raise AssertionError("the ownership probe must not send process signals")

    monkeypatch.setattr(os, "kill", refuse_signal)

    live = load(write_receipt(tmp_path / "live.json", pid=os.getpid()))
    assert live.verified is True
    assert live.process_alive is True

    exited = load(write_receipt(tmp_path / "exited.json", pid=reaped_pid()))
    assert exited.verified is False
    assert exited.reason_code == "provider_service_process_unavailable"
