"""Tests for guarded Antigravity reboot-survival watchdog and LaunchAgent installer."""

from __future__ import annotations

from argparse import Namespace
from datetime import datetime
import json
import os
from pathlib import Path
import platform
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import venv

import pytest

from loopx.hosts.agy import (
    antigravity_app_activation_extras,
    build_antigravity_hooks_template,
    build_antigravity_launchd_command,
    host_runtime,
    launchd_watchdog,
)
from loopx.capabilities.native_chat.antigravity_contracts import AntigravityEndpointDict


def _isolate_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "NODE_OPTIONS", os.environ.get("NODE_OPTIONS", "") + " --experimental-sqlite"
    )
    for variable in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(variable, str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def _args(tmp_path: Path, **overrides: object) -> Namespace:
    defaults: dict[str, object] = {
        "cli_bin": "loopx",
        "registry": None,
        "runtime_root": None,
        "goal_id": "goal-watchdog",
        "agent_id": "worker",
        "conversation_id": "conv-reboot-1",
        "state_dir": str(tmp_path / "watchdog-state"),
        "long_idle_seconds": 2400,
        "cooldown_seconds": 1800,
        "interval_seconds": 300,
        "label": None,
        "launch_agents_dir": str(tmp_path / "LaunchAgents"),
        "log_dir": None,
        "dry_run": False,
        "execute": False,
        "uninstall": False,
        "no_load": False,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


def _iso_local_offset(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).astimezone().isoformat()


def _install_fake_ls(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pid: int | None = 4242,
    ls_start_epoch: float = 1_764_100_000.0,
    status: str = "CASCADE_RUN_STATUS_IDLE",
    last_modified_epoch: float | None = 1_764_100_100.0,
    sent: list[dict[str, object]] | None = None,
    admitted: bool = True,
    admit_calls: list[bool] | None = None,
) -> dict[str, object]:
    state: dict[str, object] = {
        "pid": pid,
        "ls_start_epoch": ls_start_epoch,
        "status": status,
        "last_modified_epoch": last_modified_epoch,
        "admitted": admitted,
    }
    monkeypatch.setattr(
        host_runtime,
        "_discover_endpoint",
        lambda: ("http://127.0.0.1:4100", "csrf-synth", state["pid"]),
    )
    monkeypatch.setattr(
        host_runtime,
        "_process_start_epoch",
        lambda probed_pid: (
            state["ls_start_epoch"] if probed_pid == state["pid"] else None
        ),
    )

    def probe(_url: str, _token: str, _cid: str) -> tuple[str, float | None] | None:
        current_status = state["status"]
        if not isinstance(current_status, str):
            return None
        current_lm = state["last_modified_epoch"]
        return current_status, (
            float(current_lm) if isinstance(current_lm, (int, float)) else None
        )

    monkeypatch.setattr(host_runtime, "_probe_cascade_summary", probe)

    def admit(_args: Namespace) -> bool:
        if admit_calls is not None:
            admit_calls.append(True)
        return bool(state["admitted"])

    monkeypatch.setattr(host_runtime, "_admitted", admit)

    def send(url: str, token: str, cid: str, prompt: str) -> None:
        if sent is not None:
            sent.append({"url": url, "token": token, "cid": cid, "prompt": prompt})

    monkeypatch.setattr(host_runtime, "_send_wake_message", send)
    return state


def test_heartbeat_tick_skips_running_conversation_without_quota_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, object]] = []
    admits: list[bool] = []
    _install_fake_ls(
        monkeypatch,
        status="CASCADE_RUN_STATUS_RUNNING",
        last_modified_epoch=1_764_100_000.0,
        sent=sent,
        admit_calls=admits,
    )
    result = host_runtime.heartbeat_tick(_args(tmp_path))
    assert result["ok"] is True
    assert result["action"] == "skipped"
    assert result["reason"] == "not_idle"
    assert sent == []
    assert admits == []


def test_heartbeat_tick_skips_recent_idle_with_same_ls_such_as_swarm_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, object]] = []
    admits: list[bool] = []
    ls_started = 1_764_100_000.0
    last_turn = ls_started + 600.0
    now = last_turn + 300.0  # 5 minutes into healthy swarm_wait sleep
    monkeypatch.setattr(time, "time", lambda: now)
    _install_fake_ls(
        monkeypatch,
        pid=9001,
        ls_start_epoch=ls_started,
        status="CASCADE_RUN_STATUS_IDLE",
        last_modified_epoch=last_turn,
        sent=sent,
        admit_calls=admits,
    )
    result = host_runtime.heartbeat_tick(_args(tmp_path))
    assert result == {
        "ok": True,
        "action": "skipped",
        "reason": "active_wait_or_cooldown",
        "status": "CASCADE_RUN_STATUS_IDLE",
    }
    assert sent == []
    assert admits == []


def test_heartbeat_tick_wakes_once_after_ls_restart_and_dedups_per_ls_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, object]] = []
    admits: list[bool] = []
    last_turn = 1_764_100_000.0
    reboot_ls_start = last_turn + 20.0
    now = reboot_ls_start + 30.0
    monkeypatch.setattr(time, "time", lambda: now)
    ls = _install_fake_ls(
        monkeypatch,
        pid=5001,
        ls_start_epoch=reboot_ls_start,
        status="CASCADE_RUN_STATUS_IDLE",
        last_modified_epoch=last_turn,
        sent=sent,
        admit_calls=admits,
    )

    first = host_runtime.heartbeat_tick(_args(tmp_path))
    assert first == {
        "ok": True,
        "action": "woken",
        "reason": "ls_restarted_since_last_turn",
        "status": "CASCADE_RUN_STATUS_IDLE",
        "ls_pid": 5001,
    }
    assert len(sent) == 1
    assert "heartbeat-prompt --thin" in str(sent[0]["prompt"])
    assert len(admits) == 1

    # Next 5-minute tick with same LS PID stays quiet even though ls_start > last_modified.
    now += 300.0
    second = host_runtime.heartbeat_tick(_args(tmp_path))
    assert second["action"] == "skipped"
    assert second["reason"] == "active_wait_or_cooldown"
    assert len(sent) == 1
    assert len(admits) == 1

    # Another reboot starts a fresh LS PID: wakes once for the new PID.
    ls["pid"] = 6002
    ls["ls_start_epoch"] = last_turn + 400.0
    now = last_turn + 430.0
    third = host_runtime.heartbeat_tick(_args(tmp_path))
    assert third["action"] == "woken"
    assert third["reason"] == "ls_restarted_since_last_turn"
    assert third["ls_pid"] == 6002
    assert len(sent) == 2


def test_heartbeat_tick_long_idle_stall_fallback_and_min_cooldown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, object]] = []
    ls_started = 1_764_100_000.0
    last_turn = ls_started + 60.0
    clock = [last_turn + 2399.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    _install_fake_ls(
        monkeypatch,
        pid=7001,
        ls_start_epoch=ls_started,
        status="CASCADE_RUN_STATUS_IDLE",
        last_modified_epoch=last_turn,
        sent=sent,
    )
    # Even if caller passes a low cooldown, minimum 1800s stall cooldown holds.
    args = _args(tmp_path, long_idle_seconds=2400, cooldown_seconds=120)

    before_stall = host_runtime.heartbeat_tick(args)
    assert before_stall["action"] == "skipped"
    assert len(sent) == 0

    clock[0] = last_turn + 2400.0
    first_stall = host_runtime.heartbeat_tick(args)
    assert first_stall == {
        "ok": True,
        "action": "woken",
        "reason": "long_idle_fallback",
        "status": "CASCADE_RUN_STATUS_IDLE",
        "ls_pid": 7001,
    }
    assert len(sent) == 1

    clock[0] += 1799.0
    cooldown_skip = host_runtime.heartbeat_tick(args)
    assert cooldown_skip["action"] == "skipped"
    assert len(sent) == 1

    clock[0] += 1.0
    second_stall = host_runtime.heartbeat_tick(args)
    assert second_stall["action"] == "woken"
    assert second_stall["reason"] == "long_idle_fallback"
    assert len(sent) == 2


def test_heartbeat_tick_respects_quota_gate_missing_timestamp_and_toctou(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, object]] = []
    last_turn = 1_764_100_000.0
    now = last_turn + 3000.0
    monkeypatch.setattr(time, "time", lambda: now)
    ls = _install_fake_ls(
        monkeypatch,
        pid=8001,
        ls_start_epoch=last_turn + 10.0,
        status="CASCADE_RUN_STATUS_IDLE",
        last_modified_epoch=last_turn,
        sent=sent,
        admitted=False,
    )
    denied = host_runtime.heartbeat_tick(_args(tmp_path))
    assert denied == {
        "ok": True,
        "action": "skipped",
        "reason": "not_admitted",
        "status": "CASCADE_RUN_STATUS_IDLE",
    }
    assert not (tmp_path / "watchdog-state").exists()
    assert sent == []

    # Missing lastModifiedTime fails closed cleanly.
    ls["admitted"] = True
    ls["last_modified_epoch"] = None
    missing_ts = host_runtime.heartbeat_tick(_args(tmp_path))
    assert missing_ts["reason"] == "missing_last_modified_time"
    assert sent == []

    # Conversation transitions to RUNNING while quota should-run is running -> abort send.
    ls["last_modified_epoch"] = last_turn

    def flip_running(_args: Namespace) -> bool:
        ls["status"] = "CASCADE_RUN_STATUS_RUNNING"
        return True

    monkeypatch.setattr(host_runtime, "_admitted", flip_running)
    changed = host_runtime.heartbeat_tick(_args(tmp_path))
    assert changed["action"] == "skipped"
    assert changed["reason"] == "conversation_changed"
    assert sent == []


def test_heartbeat_tick_send_failure_keeps_cooldown_lock_and_returns_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from loopx.chat_agent import CodexChatAgentError

    last_turn = 1_764_100_000.0
    monkeypatch.setattr(time, "time", lambda: last_turn + 3000.0)
    _install_fake_ls(
        monkeypatch,
        pid=8100,
        ls_start_epoch=last_turn + 20.0,
        status="CASCADE_RUN_STATUS_IDLE",
        last_modified_epoch=last_turn,
    )

    def fail_send(*_a: object) -> None:
        raise CodexChatAgentError("rpc_timeout", gate=None)

    monkeypatch.setattr(host_runtime, "_send_wake_message", fail_send)
    failed = host_runtime.heartbeat_tick(_args(tmp_path))
    assert failed == {
        "ok": False,
        "action": "send_failed",
        "reason": "ls_restarted_since_last_turn",
        "error_kind": "CodexChatAgentError",
        "ls_pid": 8100,
    }
    # Lock reservation already persisted so a follow-up tick does not spam the dead LS.
    deduped = host_runtime.heartbeat_tick(_args(tmp_path))
    assert deduped["action"] == "skipped"


def test_heartbeat_tick_real_daemon_ls_ps_lstart_and_core_quota_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from examples.control_plane.quota_plan_fixtures import SCOPED_AGENT_ID, write_cli_fixture
    from loopx.capabilities.native_chat import antigravity_adapter as provider

    _isolate_runtime(tmp_path, monkeypatch)
    registry, runtime_root, _ = write_cli_fixture(
        tmp_path / "core", scoped_agents=True, provider="sqlite"
    )
    ls_started = 1_764_100_100.0
    last_turn = ls_started - 60.0
    lstart_str = datetime.fromtimestamp(ls_started).strftime("%a %b %e %H:%M:%S %Y")
    monkeypatch.setenv("ANTIGRAVITY_LS_URL", "http://127.0.0.1:4242")
    monkeypatch.setenv("ANTIGRAVITY_CSRF_TOKEN", "tok-synth")
    monkeypatch.setattr(
        provider,
        "discover_antigravity_language_server",
        lambda **_k: AntigravityEndpointDict(
            url="http://127.0.0.1:4242",
            csrf_token="tok-synth",
            pid=9191,
            source="daemon_discovery",
        ),
    )

    sent_payloads: list[dict[str, object]] = []

    def fake_rpc(_url: str, _token: str, method: str, payload: dict[str, object], **_k: object) -> dict[str, object]:
        if method == "GetAllCascadeTrajectories":
            return {
                "trajectorySummaries": {
                    "conv-real-1": {
                        "status": "CASCADE_RUN_STATUS_IDLE",
                        "lastModifiedTime": _iso_local_offset(last_turn),
                    }
                }
            }
        if method == "SendUserCascadeMessage":
            sent_payloads.append(payload)
            return {}
        raise AssertionError(f"unexpected rpc: {method}")

    monkeypatch.setattr(provider, "_rpc_post", fake_rpc)

    real_run = subprocess.run

    def intercept_run(
        cmd: list[str],
        *,
        timeout: float | None = None,
        check: bool = False,
        env: dict[str, str] | None = None,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if cmd[:3] == ["ps", "-o", "lstart="]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=lstart_str + "\n", stderr=""
            )
        return real_run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=check,
            env=env,
        )

    monkeypatch.setattr(subprocess, "run", intercept_run)
    monkeypatch.setattr(time, "time", lambda: ls_started + 15.0)

    args = _args(
        tmp_path,
        cli_bin=str(Path(sys.executable).with_name("loopx")),
        registry=str(registry),
        runtime_root=str(runtime_root),
        goal_id="full-speed",
        agent_id=SCOPED_AGENT_ID,
        conversation_id="conv-real-1",
    )
    woken = host_runtime.heartbeat_tick(args)
    assert woken["ok"] is True
    assert woken["action"] == "woken"
    assert woken["ls_pid"] == 9191
    assert len(sent_payloads) == 1
    assert sent_payloads[0]["cascadeId"] == "conv-real-1"


def _make_venv_cli_stub(root: Path) -> Path:
    bin_dir = root / ("Scripts" if os.name == "nt" else "bin")
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / ("loopx.exe" if os.name == "nt" else "loopx")
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    return stub


def test_install_launchd_preview_execute_no_load_reload_and_uninstall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents_dir = tmp_path / "LaunchAgents"
    log_dir = tmp_path / "logs"
    venv_cli = _make_venv_cli_stub(tmp_path / "stub-venv")
    args = _args(
        tmp_path,
        cli_bin=str(venv_cli),
        launch_agents_dir=str(agents_dir),
        log_dir=str(log_dir),
        interval_seconds=20,  # clamped to >= 60s
        no_load=True,
    )

    preview = host_runtime.install_launchd(args)
    assert preview["ok"] is True
    assert preview["dry_run"] is True
    assert preview["written"] is False
    plist = preview["plist"]
    assert isinstance(plist, dict)
    assert plist["RunAtLoad"] is True
    assert plist["StartInterval"] == 60
    assert "heartbeat-tick" in plist["ProgramArguments"]
    assert not agents_dir.exists()

    # Dry-run safety rejects path-traversal labels.
    bad_args = _args(tmp_path, cli_bin=str(venv_cli), label="../../escape")
    with pytest.raises(ValueError, match="unsafe path"):
        host_runtime.install_launchd(bad_args)

    # Execute with --no-load writes valid binary/XML plist cleanly on any OS.
    args.execute = True
    written = host_runtime.install_launchd(args)
    assert written["ok"] is True
    assert written["written"] is True
    assert written["loaded"] is False
    plist_path = Path(str(written["plist_path"]))
    parsed = plistlib.loads(plist_path.read_bytes())
    assert parsed["Label"] == written["label"]
    assert parsed["ProgramArguments"][1:6] == [
        "-E",
        "-P",
        "-m",
        "loopx.hosts.agy.host_runtime",
        "heartbeat-tick",
    ]

    # Full darwin launchctl load + readback + uninstall round-trip.
    calls: list[list[str]] = []
    job_loaded = {"value": False}
    monkeypatch.setattr(sys, "platform", "darwin")

    def fake_launchctl(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        verb = argv[0]
        if verb == "bootstrap":
            job_loaded["value"] = True
            return subprocess.CompletedProcess(argv, 0, "", "")
        if verb == "bootout":
            was_loaded = job_loaded["value"]
            job_loaded["value"] = False
            return subprocess.CompletedProcess(
                argv, 0 if was_loaded else 3, "", "" if was_loaded else "No such process"
            )
        if verb == "print":
            return subprocess.CompletedProcess(
                argv,
                0 if job_loaded["value"] else 113,
                "",
                "" if job_loaded["value"] else "Could not find service",
            )
        raise AssertionError(f"unexpected launchctl argv: {argv}")

    monkeypatch.setattr(launchd_watchdog, "_run_launchctl", fake_launchctl)
    args.no_load = False
    loaded = host_runtime.install_launchd(args)
    assert loaded["ok"] is True and loaded["loaded"] is True
    assert [c[0] for c in calls] == ["bootout", "bootstrap", "print"]

    calls.clear()
    args.uninstall = True
    removed = host_runtime.install_launchd(args)
    assert removed["ok"] is True and removed["uninstalled"] is True
    assert removed["loaded"] is False
    assert [c[0] for c in calls] == ["bootout", "print"]
    assert not plist_path.exists()

    # Non-darwin fails closed before touching disk when --no-load is omitted.
    monkeypatch.setattr(sys, "platform", "linux")
    args.uninstall = False
    refused = host_runtime.install_launchd(args)
    assert refused["ok"] is False
    assert refused["error_kind"] == "launchd_unsupported_platform"
    assert not plist_path.exists()


def test_install_launchd_uninstall_verifies_launchd_readback_across_all_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    agents_dir = tmp_path / "LaunchAgents"
    state_dir = tmp_path / "state"
    venv_cli = _make_venv_cli_stub(tmp_path / "stub-venv")
    base_argv = [
        "host_runtime",
        "install-launchd",
        "--cli-bin",
        str(venv_cli),
        "--goal-id",
        "g-uninst",
        "--agent-id",
        "a-uninst",
        "--conversation-id",
        "c-uninst",
        "--launch-agents-dir",
        str(agents_dir),
        "--state-dir",
        str(state_dir),
    ]

    # 1. Bootout fails AND job remains loaded -> non-zero exit, plist preserved.
    monkeypatch.setattr(sys, "argv", [*base_argv, "--execute", "--no-load"])
    assert host_runtime.main() == 0
    installed = json.loads(capsys.readouterr().out)
    plist_path = Path(str(installed["plist_path"]))
    assert plist_path.is_file()

    def reject_bootout(argv: list[str]) -> subprocess.CompletedProcess[str]:
        if argv[0] == "bootout":
            return subprocess.CompletedProcess(argv, 1, "", "Operation not permitted")
        if argv[0] == "print":
            return subprocess.CompletedProcess(argv, 0, "state = running", "")
        raise AssertionError(argv)

    monkeypatch.setattr(launchd_watchdog, "_run_launchctl", reject_bootout)
    monkeypatch.setattr(sys, "argv", [*base_argv, "--uninstall", "--execute"])
    assert host_runtime.main() == 1
    failed = json.loads(capsys.readouterr().out)
    assert failed["ok"] is False
    assert failed["uninstalled"] is False
    assert failed["loaded"] is True
    assert failed["error_kind"] == "launchctl_bootout_failed"
    assert plist_path.is_file()

    # 2. Unverifiable failure (bootout=1, print=1 without absent diagnostic) -> plist preserved.
    def ambiguous_failure(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "launchd ipc error")

    monkeypatch.setattr(launchd_watchdog, "_run_launchctl", ambiguous_failure)
    assert host_runtime.main() == 1
    ambiguous = json.loads(capsys.readouterr().out)
    assert ambiguous["ok"] is False
    assert ambiguous["uninstalled"] is False
    assert ambiguous["error_kind"] == "launchctl_bootout_failed"
    assert plist_path.is_file()

    # 2b. Bootout succeeds (or claims absent) while print fails ambiguously ->
    # plist preserved and launchctl_readback_failed returned (both direct + subprocess CLI).
    for bootout_rc, bootout_stderr in ((0, ""), (3, "Boot-out failed: 3: No such process")):
        def ambiguous_readback(
            argv: list[str], *, _rc: int = bootout_rc, _err: str = bootout_stderr
        ) -> subprocess.CompletedProcess[str]:
            if argv[0] == "bootout":
                return subprocess.CompletedProcess(argv, _rc, "", _err)
            return subprocess.CompletedProcess(argv, 1, "", "launchd ipc error")

        monkeypatch.setattr(launchd_watchdog, "_run_launchctl", ambiguous_readback)
        assert host_runtime.main() == 1
        unreadable = json.loads(capsys.readouterr().out)
        assert unreadable["ok"] is False
        assert unreadable["uninstalled"] is False
        assert unreadable["loaded"] is None
        assert unreadable["error_kind"] == "launchctl_readback_failed"
        assert plist_path.is_file()

    if platform.system() == "Darwin":
        shim_dir = tmp_path / "shim-bin"
        shim_dir.mkdir()
        shim_launchctl = shim_dir / "launchctl"
        shim_launchctl.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "bootout" ]; then\n'
            "  exit 0\n"
            "fi\n"
            'echo "launchd ipc error" >&2\n'
            "exit 1\n",
            encoding="utf-8",
        )
        shim_launchctl.chmod(0o755)
        shim_env = {
            **os.environ,
            "LOOPX_USAGE_PING": "0",
            "PATH": f"{shim_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        }
        shim_proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.hosts.agy.host_runtime",
                "install-launchd",
                "--cli-bin",
                str(venv_cli),
                "--goal-id",
                "g-uninst",
                "--agent-id",
                "a-uninst",
                "--conversation-id",
                "c-uninst",
                "--launch-agents-dir",
                str(agents_dir),
                "--state-dir",
                str(state_dir),
                "--uninstall",
                "--execute",
            ],
            capture_output=True,
            text=True,
            env=shim_env,
            timeout=30,
            check=False,
        )
        assert shim_proc.returncode == 1, shim_proc.stderr
        shim_payload = json.loads(shim_proc.stdout)
        assert shim_payload["ok"] is False
        assert shim_payload["uninstalled"] is False
        assert shim_payload["loaded"] is None
        assert shim_payload["error_kind"] == "launchctl_readback_failed"
        assert plist_path.is_file()

    # 3. Successful unload (bootout=0, print=113) -> uninstalled=True, plist removed.
    def clean_bootout(argv: list[str]) -> subprocess.CompletedProcess[str]:
        if argv[0] == "bootout":
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 113, "", "Could not find service")

    monkeypatch.setattr(launchd_watchdog, "_run_launchctl", clean_bootout)
    assert host_runtime.main() == 0
    succeeded = json.loads(capsys.readouterr().out)
    assert succeeded["ok"] is True
    assert succeeded["uninstalled"] is True
    assert succeeded["loaded"] is False
    assert not plist_path.exists()

    # 4. Already absent (bootout=3, print=113) -> idempotent success, plist removed.
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text("stale", encoding="utf-8")

    def already_absent(argv: list[str]) -> subprocess.CompletedProcess[str]:
        if argv[0] == "bootout":
            return subprocess.CompletedProcess(argv, 3, "", "Boot-out failed: 3: No such process")
        return subprocess.CompletedProcess(argv, 113, "", "Could not find service")

    monkeypatch.setattr(launchd_watchdog, "_run_launchctl", already_absent)
    assert host_runtime.main() == 0
    absent = json.loads(capsys.readouterr().out)
    assert absent["ok"] is True
    assert absent["uninstalled"] is True
    assert absent["loaded"] is False
    assert not plist_path.exists()


def test_launchd_plist_resolves_relative_paths_and_supports_cold_boot_from_root_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from examples.control_plane.quota_plan_fixtures import SCOPED_AGENT_ID, write_cli_fixture

    cold_tmp = tmp_path / "cold-tmp"
    cold_tmp.mkdir()
    _isolate_runtime(cold_tmp, monkeypatch)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    reg, rt, _ = write_cli_fixture(
        project_dir / "rel-core", scoped_agents=True, provider="sqlite"
    )
    rel_registry = os.fspath(reg.relative_to(project_dir))
    rel_runtime_root = os.fspath(rt.relative_to(project_dir))

    node_bin = shutil.which("node")
    assert node_bin is not None, "node is required to execute cold-boot launchd quota check"
    fake_node_dir = tmp_path / "isolated-node-bin"
    fake_node_dir.mkdir()
    (fake_node_dir / "node").symlink_to(node_bin)

    monkeypatch.chdir(project_dir)
    monkeypatch.setenv("PATH", f"{fake_node_dir}:/usr/bin:/bin")
    cli_bin = str(Path(sys.executable).with_name("loopx"))

    plist = launchd_watchdog.build_antigravity_launchd_plist(
        goal_id="full-speed",
        agent_id=SCOPED_AGENT_ID,
        conversation_id="conv-cold-boot",
        cli_bin=cli_bin,
        registry=rel_registry,
        runtime_root=rel_runtime_root,
        state_dir="rel-state",
        log_dir="rel-logs",
    )
    resolved_project = project_dir.resolve()
    program_args = plist["ProgramArguments"]
    assert isinstance(program_args, list)
    arg_map = {
        program_args[idx]: program_args[idx + 1]
        for idx in range(len(program_args) - 1)
        if isinstance(program_args[idx], str) and program_args[idx].startswith("--")
    }
    assert arg_map["--state-dir"] == str(resolved_project / "rel-state")
    assert arg_map["--registry"] == str(reg.resolve())
    assert arg_map["--runtime-root"] == str(rt.resolve())
    assert str(plist["StandardOutPath"]).startswith(str(resolved_project / "rel-logs") + os.sep)
    assert str(plist["StandardErrorPath"]).startswith(str(resolved_project / "rel-logs") + os.sep)

    # Global registry selector must remain verbatim rather than resolved relative to cwd.
    global_plist = launchd_watchdog.build_antigravity_launchd_plist(
        goal_id="full-speed",
        agent_id=SCOPED_AGENT_ID,
        conversation_id="conv-cold-boot",
        registry="@host-global",
        state_dir="rel-state",
    )
    global_args = global_plist["ProgramArguments"]
    assert isinstance(global_args, list)
    global_idx = global_args.index("--registry")
    assert global_args[global_idx + 1] == "@host-global"

    env_vars = plist["EnvironmentVariables"]
    assert isinstance(env_vars, dict)
    assert env_vars["LOOPX_USAGE_PING"] == "0"
    assert str(fake_node_dir) in str(env_vars["PATH"]).split(os.pathsep)

    # Simulate launchd cold-boot invocation: cwd="/", isolated TMPDIR, and only plist env vars.
    monkeypatch.chdir("/")
    cold_env = {str(k): str(v) for k, v in env_vars.items()}
    cold_env["TMPDIR"] = str(cold_tmp)
    quota_check = subprocess.run(
        [
            arg_map["--cli-bin"],
            "--format",
            "json",
            "--registry",
            arg_map["--registry"],
            "--runtime-root",
            arg_map["--runtime-root"],
            "quota",
            "should-run",
            "--scheduler-observation",
            "--goal-id",
            "full-speed",
            "--agent-id",
            SCOPED_AGENT_ID,
            "--runtime-profile",
            "antigravity_app",
        ],
        cwd="/",
        env=cold_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=True,
    )
    assert json.loads(quota_check.stdout)["should_run"] is True

    # Also verify ProgramArguments entrypoint launches cleanly from "/" under plist env.
    tick_proc = subprocess.run(
        program_args,
        cwd="/",
        env=cold_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=True,
    )
    tick_payload = json.loads(tick_proc.stdout)
    assert tick_payload["ok"] is True


def test_activation_extras_and_main_cli_json_contracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cmd = build_antigravity_launchd_command(
        goal_id="g1", agent_id="a1", conversation_id="c1", resolve_local_executable=False
    )
    assert cmd[:6] == [
        "python3",
        "-E",
        "-P",
        "-m",
        "loopx.hosts.agy.host_runtime",
        "install-launchd",
    ]
    venv_cli = _make_venv_cli_stub(tmp_path / "stub-venv")
    extras = antigravity_app_activation_extras(
        goal_id="g1", agent_id="a1", cli_bin=str(venv_cli)
    )
    assert "install-launchd" in extras["extra_host_mutation"]["reboot_watchdog_command"]

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "host_runtime",
            "install-launchd",
            "--cli-bin",
            str(venv_cli),
            "--goal-id",
            "g1",
            "--agent-id",
            "a1",
            "--conversation-id",
            "c1",
            "--launch-agents-dir",
            str(tmp_path / "LaunchAgents"),
            "--state-dir",
            str(tmp_path / "state"),
        ],
    )
    assert host_runtime.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["dry_run"] is True


def _make_release_snapshot(release_dir: Path, repo_root: Path) -> None:
    """Create a release-snapshot tree matching ``scripts/install-local.sh`` layout."""
    scripts_dir = release_dir / "scripts"
    pkg_dir = release_dir / "loopx"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    pkg_dir.mkdir(parents=True, exist_ok=True)
    launcher = scripts_dir / "loopx"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    source_pkg = repo_root / "loopx"
    for entry in source_pkg.iterdir():
        if entry.name in {"__pycache__", "hosts"}:
            continue
        target = pkg_dir / entry.name
        if entry.is_dir():
            target.symlink_to(entry, target_is_directory=True)
        else:
            target.symlink_to(entry)
    shutil.copytree(
        source_pkg / "hosts",
        pkg_dir / "hosts",
        ignore=shutil.ignore_patterns("__pycache__"),
    )


def test_release_snapshot_hooks_and_launchd_survive_upgrades_without_site_packages_loopx(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    provider_venv = tmp_path / "provider-venv"
    venv.EnvBuilder(symlinks=True, with_pip=False).create(provider_venv)
    provider_python = provider_venv / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )

    # Confirm the provider Python has no `loopx` in site-packages under `-E -P`.
    missing_mod = subprocess.run(
        [
            str(provider_python),
            "-E",
            "-P",
            "-m",
            "loopx.hosts.agy.host_runtime",
            "--help",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert missing_mod.returncode != 0
    assert "No module named 'loopx'" in missing_mod.stderr

    r1 = tmp_path / "releases" / "r1"
    r2 = tmp_path / "releases" / "r2"
    _make_release_snapshot(r1, repo_root)
    _make_release_snapshot(r2, repo_root)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli_symlink = bin_dir / "loopx"
    cli_symlink.symlink_to(r1 / "scripts" / "loopx")

    # Simulate generating hooks.json, LaunchAgent plist, and launchd install command
    # from inside the live r1 release-snapshot process (where PYTHONPATH=r1 makes
    # loopx importable in-process, even though provider_python -E -P lacks it).
    state_dir = tmp_path / "state"
    log_dir = tmp_path / "logs"
    build_script = (
        "import json; from loopx.hosts.agy import "
        "build_antigravity_hooks_template, build_antigravity_launchd_command, launchd_watchdog; "
        "hooks = build_antigravity_hooks_template("
        "cli_bin='loopx', goal_id='g-rel', agent_id='a-rel', conversation_id='c-rel'); "
        "plist = launchd_watchdog.build_antigravity_launchd_plist("
        "goal_id='g-rel', agent_id='a-rel', conversation_id='c-rel', "
        f"cli_bin='loopx', state_dir={str(state_dir)!r}, log_dir={str(log_dir)!r}); "
        "install_cmd = build_antigravity_launchd_command("
        "cli_bin='loopx', goal_id='g-rel', agent_id='a-rel', conversation_id='c-rel'); "
        "print(json.dumps({'hooks': hooks, 'plist': plist, 'install_cmd': install_cmd}))"
    )
    build_env = {
        **os.environ,
        "LOOPX_RELEASE_ROOT": str(r1),
        "PYTHONPATH": str(r1),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
    }
    generated = json.loads(
        subprocess.check_output(
            [str(provider_python), "-c", build_script],
            env=build_env,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    )
    hook_cmd = shlex.split(generated["hooks"]["loopx"]["Stop"][0]["command"])
    program_args = [str(arg) for arg in generated["plist"]["ProgramArguments"]]
    install_cmd = [str(arg) for arg in generated["install_cmd"]]

    for cmd in (hook_cmd, program_args, install_cmd):
        assert "--cli-bin" in cmd
        assert cmd[cmd.index("--cli-bin") + 1] == str(cli_symlink)
        assert str(r1) not in " ".join(cmd)

    # Works against r1 first.
    stop_event = json.dumps({"terminationReason": "USER_CANCELED"})
    r1_hook = subprocess.run(
        hook_cmd,
        input=stop_event,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=True,
    )
    assert json.loads(r1_hook.stdout) == {}

    # Upgrade symlink to r2 and remove r1 completely; poison ambient PYTHONPATH and cwd.
    cli_symlink.unlink()
    cli_symlink.symlink_to(r2 / "scripts" / "loopx")
    shutil.rmtree(r1)

    foreign_pkg = tmp_path / "foreign" / "loopx"
    foreign_pkg.mkdir(parents=True)
    (foreign_pkg / "__init__.py").write_text(
        'raise RuntimeError("poisoned PYTHONPATH")\n', encoding="utf-8"
    )
    poison_cwd = tmp_path / "work"
    cwd_pkg = poison_cwd / "loopx"
    cwd_pkg.mkdir(parents=True)
    (cwd_pkg / "__init__.py").write_text(
        'raise RuntimeError("poisoned cwd")\n', encoding="utf-8"
    )
    isolated_env = {
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(foreign_pkg.parent),
        "TMPDIR": str(tmp_path),
    }

    r2_hook = subprocess.run(
        hook_cmd,
        input=stop_event,
        cwd=poison_cwd,
        env=isolated_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=True,
    )
    assert json.loads(r2_hook.stdout) == {}

    plist_env = {
        **{str(k): str(v) for k, v in generated["plist"]["EnvironmentVariables"].items()},
        "HOME": str(tmp_path),
        "PYTHONPATH": str(foreign_pkg.parent),
        "TMPDIR": str(tmp_path),
    }
    r2_tick = subprocess.run(
        program_args,
        cwd="/",
        env=plist_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=True,
    )
    assert json.loads(r2_tick.stdout)["ok"] is True

    # Even inside an editable/pip venv, a symlinked release launcher still uses the
    # launcher bootstrap so editable installs do not shadow `cli_symlink` or `loopx upgrade`.
    symlink_hook_cmd = shlex.split(
        build_antigravity_hooks_template(
            cli_bin=str(cli_symlink),
            goal_id="g-rel",
            agent_id="a-rel",
            conversation_id="c-rel",
        )["loopx"]["Stop"][0]["command"]
    )
    if os.name == "nt":
        assert symlink_hook_cmd[:4] == [sys.executable, "-E", "-P", "-c"]
    else:
        assert symlink_hook_cmd[:2] == ["/bin/sh", "-c"]
        assert symlink_hook_cmd[4] == sys.executable

    # Ordinary pip/venv console scripts (and matching checkout scripts) keep standard `-m`.
    venv_cli = Path(sys.executable).with_name(
        "loopx.exe" if os.name == "nt" else "loopx"
    )
    if venv_cli.is_file() and (os.name == "nt" or os.access(venv_cli, os.X_OK)):
        venv_hook_cmd = shlex.split(
            build_antigravity_hooks_template(
                cli_bin=str(venv_cli),
                goal_id="g-rel",
                agent_id="a-rel",
                conversation_id="c-rel",
            )["loopx"]["Stop"][0]["command"]
        )
        assert venv_hook_cmd[:5] == [
            sys.executable,
            "-E",
            "-P",
            "-m",
            "loopx.hosts.agy.host_runtime",
        ]
    checkout_cli = repo_root / "scripts" / "loopx"
    if (
        checkout_cli.is_file()
        and Path(host_runtime.__file__).resolve().is_relative_to(repo_root.resolve())
        and not (repo_root / "release.json").is_file()
    ):
        checkout_hook_cmd = shlex.split(
            build_antigravity_hooks_template(
                cli_bin=str(checkout_cli),
                goal_id="g-rel",
                agent_id="a-rel",
                conversation_id="c-rel",
            )["loopx"]["Stop"][0]["command"]
        )
        assert checkout_hook_cmd[:5] == [
            sys.executable,
            "-E",
            "-P",
            "-m",
            "loopx.hosts.agy.host_runtime",
        ]

    r2_install = subprocess.run(
        [
            *install_cmd,
            "--dry-run",
            "--state-dir",
            str(state_dir),
            "--launch-agents-dir",
            str(tmp_path / "LaunchAgents"),
        ],
        cwd=poison_cwd,
        env=isolated_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=True,
    )
    r2_install_payload = json.loads(r2_install.stdout)
    assert r2_install_payload["ok"] is True
    assert str(cli_symlink) in r2_install_payload["plist"]["ProgramArguments"]

    # Fail closed when only the target launcher file (`scripts/loopx`) disappears while
    # the release package directory (`r2/loopx`) and symlink remain intact.
    (r2 / "scripts" / "loopx").unlink()
    missing_launcher_install = subprocess.run(
        [
            *install_cmd,
            "--dry-run",
            "--state-dir",
            str(state_dir),
            "--launch-agents-dir",
            str(tmp_path / "LaunchAgents"),
        ],
        cwd=poison_cwd,
        env=isolated_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert missing_launcher_install.returncode != 0
    assert "release root not found" in missing_launcher_install.stderr
    assert missing_launcher_install.stdout.strip() == ""

    missing_launcher_hook = subprocess.run(
        hook_cmd,
        input=stop_event,
        cwd=poison_cwd,
        env=isolated_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert missing_launcher_hook.returncode != 0
    assert "release root not found" in missing_launcher_hook.stderr
    assert "poisoned" not in missing_launcher_hook.stderr

    # Direct in-process builders inside the retained r2 package tree and editable venv
    # also fail closed rather than emitting a dangling launcher into hooks or plists.
    dangling_build_probe = subprocess.run(
        [str(provider_python), "-c", build_script],
        env={
            **os.environ,
            "LOOPX_RELEASE_ROOT": str(r2),
            "PYTHONPATH": str(r2),
            "PATH": f"{bin_dir}{os.pathsep}/usr/bin:/bin",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert dangling_build_probe.returncode != 0
    assert "FileNotFoundError" in dangling_build_probe.stderr
    with pytest.raises(FileNotFoundError):
        build_antigravity_hooks_template(
            cli_bin=str(cli_symlink),
            goal_id="g-rel",
            agent_id="a-rel",
            conversation_id="c-rel",
        )
    with pytest.raises(FileNotFoundError):
        launchd_watchdog.build_antigravity_launchd_plist(
            goal_id="g-rel",
            agent_id="a-rel",
            conversation_id="c-rel",
            cli_bin=str(cli_symlink),
            state_dir=str(state_dir),
            log_dir=str(log_dir),
        )

    # Reject non-release layout even when the pointed file exists inside the release tree
    # (parents[1] is `r2`, with `r2/loopx` present, but parent dir is not `scripts`/`bin`).
    bad_layout_dir = r2 / "other"
    bad_layout_dir.mkdir()
    bad_exec = bad_layout_dir / "loopx"
    bad_exec.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    bad_exec.chmod(0o755)
    bad_layout_link = tmp_path / "bad-loopx"
    bad_layout_link.symlink_to(bad_exec)
    bad_layout_probe = subprocess.run(
        [
            str(provider_python),
            "-c",
            "from loopx.hosts.agy import build_antigravity_launchd_command; "
            f"build_antigravity_launchd_command(cli_bin={str(bad_layout_link)!r}, "
            "goal_id='g', agent_id='a', conversation_id='c')",
        ],
        env={**os.environ, "PYTHONPATH": str(r2)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert bad_layout_probe.returncode != 0
    assert "FileNotFoundError" in bad_layout_probe.stderr
    bad_layout_hook_cmd = list(hook_cmd)
    bad_layout_hook_cmd[bad_layout_hook_cmd.index("--cli-bin") + 1] = str(bad_layout_link)
    bad_layout_runtime = subprocess.run(
        bad_layout_hook_cmd,
        input=stop_event,
        cwd=poison_cwd,
        env=isolated_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert bad_layout_runtime.returncode != 0
    assert "release root not found" in bad_layout_runtime.stderr
    assert "poisoned" not in bad_layout_runtime.stderr

    # Fail closed if the entire release symlink target directory disappears.
    shutil.rmtree(r2)
    broken_hook = subprocess.run(
        hook_cmd,
        input=stop_event,
        cwd=poison_cwd,
        env=isolated_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert broken_hook.returncode != 0
    assert "release root not found" in broken_hook.stderr
    assert "poisoned" not in broken_hook.stderr


def test_release_snapshot_not_shadowed_by_older_editable_pth_install(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]

    # 1. Build an older importable editable LoopX tree supporting only `stop-hook`
    #    (rejecting `heartbeat-tick` with exit 2) and attach it via `.pth` to provider_venv.
    old_editable = tmp_path / "old-editable"
    old_pkg = old_editable / "loopx" / "hosts" / "agy"
    old_pkg.mkdir(parents=True)
    (old_editable / "loopx" / "__init__.py").write_text("", encoding="utf-8")
    (old_editable / "loopx" / "hosts" / "__init__.py").write_text("", encoding="utf-8")
    (old_pkg / "__init__.py").write_text("", encoding="utf-8")
    (old_pkg / "host_runtime.py").write_text(
        "import argparse, sys\n"
        "def main() -> int:\n"
        "    parser = argparse.ArgumentParser(prog='loopx.hosts.agy.host_runtime')\n"
        "    sub = parser.add_subparsers(dest='operation', required=True)\n"
        "    sub.add_parser('stop-hook')\n"
        "    parser.parse_args()\n"
        "    return 0\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n",
        encoding="utf-8",
    )

    provider_venv = tmp_path / "provider-venv"
    venv.EnvBuilder(symlinks=True, with_pip=False).create(provider_venv)
    provider_python = provider_venv / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    provider_purelib = Path(
        subprocess.check_output(
            [str(provider_python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            text=True,
            encoding="utf-8",
            timeout=15,
        ).strip()
    )
    (provider_purelib / "old_loopx.pth").write_text(
        f"{old_editable}\n", encoding="utf-8"
    )

    shadow_check = subprocess.run(
        [
            str(provider_python),
            "-E",
            "-P",
            "-m",
            "loopx.hosts.agy.host_runtime",
            "heartbeat-tick",
            "--help",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert shadow_check.returncode == 2
    assert "stop-hook" in shadow_check.stderr

    # 2. Create healthy newer release snapshot + symlinked CLI shim, non-symlink packaged
    #    snapshot (with release.json), and non-symlink checkout (without release.json).
    r_sym = tmp_path / "releases" / "r-sym"
    _make_release_snapshot(r_sym, repo_root)
    cli_symlink = tmp_path / "bin" / "loopx"
    cli_symlink.parent.mkdir()
    cli_symlink.symlink_to(r_sym / "scripts" / "loopx")

    r_pkg = tmp_path / "releases" / "r-pkg"
    _make_release_snapshot(r_pkg, repo_root)
    (r_pkg / "release.json").write_text('{"schema_version":"v1"}\n', encoding="utf-8")

    r_direct = tmp_path / "checkouts" / "r-direct"
    _make_release_snapshot(r_direct, repo_root)

    state_dir = tmp_path / "state"
    log_dir = tmp_path / "logs"
    inspect_script = (
        "import json; from loopx.hosts.agy import "
        "build_antigravity_host_runtime_args, launchd_watchdog; "
        f"args_sym = build_antigravity_host_runtime_args('heartbeat-tick', cli_bin={str(cli_symlink)!r}, "
        "goal_id='g-e2e', agent_id='a-e2e', conversation_id='c-e2e'); "
        "plist_sym = launchd_watchdog.build_antigravity_launchd_plist("
        f"goal_id='g-e2e', agent_id='a-e2e', conversation_id='c-e2e', cli_bin={str(cli_symlink)!r}, "
        f"state_dir={str(state_dir)!r}, log_dir={str(log_dir)!r}); "
        f"args_pkg = build_antigravity_host_runtime_args('heartbeat-tick', cli_bin={str(r_pkg / 'scripts' / 'loopx')!r}, "
        "goal_id='g-e2e', agent_id='a-e2e', conversation_id='c-e2e'); "
        f"args_direct = build_antigravity_host_runtime_args('heartbeat-tick', cli_bin={str(r_direct / 'scripts' / 'loopx')!r}, "
        "goal_id='g-e2e', agent_id='a-e2e', conversation_id='c-e2e'); "
        "print(json.dumps({'args_sym': args_sym, 'plist_args': plist_sym['ProgramArguments'], "
        "'args_pkg': args_pkg, 'args_direct': args_direct}))"
    )
    generated = json.loads(
        subprocess.check_output(
            [str(provider_python), "-c", inspect_script],
            env={**os.environ, "PYTHONPATH": str(r_sym)},
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    )
    for key in ("args_sym", "plist_args", "args_pkg", "args_direct"):
        argv = [str(item) for item in generated[key]]
        if os.name == "nt":
            assert argv[:4] == [str(provider_python), "-E", "-P", "-c"], (key, argv[:6])
        else:
            assert argv[:2] == ["/bin/sh", "-c"], (key, argv[:6])
            assert argv[4] == str(provider_python), (key, argv[:6])
        help_run = subprocess.run(
            [*argv, "--help"],
            cwd=tmp_path,
            env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path)},
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        assert help_run.returncode == 0, (key, help_run.stderr)
        assert "--cli-bin" in help_run.stdout

    # 3. Preserve `-m` fast-path for BOTH matching editable checkouts (Case B without
    #    release.json) AND ordinary pip/venv console scripts (Case C).
    matching_venv = tmp_path / "matching-venv"
    venv.EnvBuilder(symlinks=True, with_pip=False).create(matching_venv)
    matching_python = matching_venv / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    matching_purelib = Path(
        subprocess.check_output(
            [str(matching_python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            text=True,
            encoding="utf-8",
            timeout=15,
        ).strip()
    )
    (matching_purelib / "matching_loopx.pth").write_text(
        f"{r_direct}\n", encoding="utf-8"
    )
    venv_console_cli = matching_venv / (
        "Scripts/loopx.exe" if os.name == "nt" else "bin/loopx"
    )
    venv_console_cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    venv_console_cli.chmod(0o755)

    matching_script = (
        "import json; from loopx.hosts.agy import build_antigravity_host_runtime_args; "
        f"direct_args = build_antigravity_host_runtime_args('heartbeat-tick', cli_bin={str(r_direct / 'scripts' / 'loopx')!r}, "
        "goal_id='g-m', agent_id='a-m', conversation_id='c-m'); "
        f"venv_args = build_antigravity_host_runtime_args('heartbeat-tick', cli_bin={str(venv_console_cli)!r}, "
        "goal_id='g-m', agent_id='a-m', conversation_id='c-m'); "
        "print(json.dumps({'direct_args': direct_args, 'venv_args': venv_args}))"
    )
    matching_out = json.loads(
        subprocess.check_output(
            [str(matching_python), "-c", matching_script],
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    )
    for key in ("direct_args", "venv_args"):
        argv = [str(item) for item in matching_out[key]]
        assert argv[:5] == [
            str(matching_python),
            "-E",
            "-P",
            "-m",
            "loopx.hosts.agy.host_runtime",
        ], (key, argv[:6])
        help_run = subprocess.run(
            [*argv, "--help"],
            cwd=tmp_path,
            env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path)},
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        assert help_run.returncode == 0, (key, help_run.stderr)


def test_activation_pack_and_onboard_commands_execute_under_clean_provider_python(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    provider_venv = tmp_path / "provider-venv"
    venv.EnvBuilder(symlinks=True, with_pip=False).create(provider_venv)
    provider_python = provider_venv / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    missing_mod = subprocess.run(
        [str(provider_python), "-E", "-P", "-m", "loopx.hosts.agy.host_runtime", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert missing_mod.returncode != 0
    assert "No module named 'loopx'" in missing_mod.stderr

    r_act = tmp_path / "releases" / "r-act"
    _make_release_snapshot(r_act, repo_root)
    (r_act / "release.json").write_text('{"schema_version":"v1"}\n', encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli_symlink = bin_dir / "loopx"
    cli_symlink.symlink_to(r_act / "scripts" / "loopx")

    project = tmp_path / "project"
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (project / "ACTIVE_GOAL_STATE.md").write_text("# Goal\n\nActivate.\n", encoding="utf-8")
    registry_path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": "g-act",
                        "domain": "test",
                        "status": "active",
                        "repo": str(project),
                        "state_file": "ACTIVE_GOAL_STATE.md",
                        "coordination": {
                            "registered_agents": ["worker-act"],
                            "agent_model": "peer_v1",
                        },
                        "adapter": {
                            "kind": "generic_project_goal_v0",
                            "status": "connected",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    poison_cwd = tmp_path / "poison"
    poison_pkg = poison_cwd / "loopx"
    poison_pkg.mkdir(parents=True)
    (poison_pkg / "__init__.py").write_text(
        'raise RuntimeError("poisoned cwd")\n', encoding="utf-8"
    )
    isolated_provider_env = {
        "HOME": str(tmp_path),
        "LOOPX_USAGE_PING": "0",
        "PATH": "/usr/bin:/bin",
        "TMPDIR": str(tmp_path),
    }

    node_bin = shutil.which("node")
    assert node_bin is not None, "node is required to execute agent-onboard CLI probe"
    node_only_dir = tmp_path / "node-only-bin"
    node_only_dir.mkdir()
    (node_only_dir / ("node.exe" if os.name == "nt" else "node")).symlink_to(node_bin)

    cli_runner = "import sys; from loopx.cli import main; raise SystemExit(main(sys.argv[1:]))"
    invocations = [
        (
            "bootstrap-command-pack-with-path",
            f"{bin_dir}{os.pathsep}{node_only_dir}{os.pathsep}/usr/bin:/bin",
            str(cli_symlink),
            [
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "--runtime-root",
                str(runtime_root),
                "bootstrap-command-pack",
                "--project",
                str(project),
                "--host-surface",
                "antigravity-app",
                "--goal-id",
                "g-act",
                "--agent-id",
                "worker-act",
            ],
        ),
        (
            "agent-onboard-with-path",
            f"{bin_dir}{os.pathsep}{node_only_dir}{os.pathsep}/usr/bin:/bin",
            str(cli_symlink),
            [
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "--runtime-root",
                str(runtime_root),
                "agent-onboard",
                "--project",
                str(project),
                "--agent-type",
                "antigravity-app",
                "--goal-id",
                "g-act",
                "--agent-id",
                "worker-act",
            ],
        ),
        (
            "bootstrap-command-pack-release-root-fallback",
            f"{node_only_dir}{os.pathsep}/usr/bin:/bin",
            str(r_act / "scripts" / "loopx"),
            [
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "--runtime-root",
                str(runtime_root),
                "bootstrap-command-pack",
                "--project",
                str(project),
                "--host-surface",
                "antigravity-app",
                "--goal-id",
                "g-act",
                "--agent-id",
                "worker-act",
            ],
        ),
    ]

    stop_event = json.dumps({"terminationReason": "USER_CANCELED"})
    agents_dir = tmp_path / "LaunchAgents"
    wd_state_dir = tmp_path / "wd-state"

    for label, cli_path_env, expected_cli, cli_args in invocations:
        cli_proc = subprocess.run(
            [str(provider_python), "-c", cli_runner, *cli_args],
            cwd=project,
            env={
                "HOME": str(tmp_path),
                "LOOPX_USAGE_PING": "0",
                "LOOPX_RELEASE_ROOT": str(r_act),
                "PYTHONPATH": str(r_act),
                "PATH": cli_path_env,
                "TMPDIR": str(tmp_path),
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        assert cli_proc.returncode == 0, (label, cli_proc.stderr, cli_proc.stdout)
        packet = json.loads(cli_proc.stdout)
        mutation = packet["host_loop_activation"]["host_mutation"]
        stop_cmd = shlex.split(mutation["hooks_template"]["loopx"]["Stop"][0]["command"])
        watchdog_cmd = shlex.split(mutation["reboot_watchdog_command"])

        for generated_cmd in (stop_cmd, watchdog_cmd):
            if os.name == "nt":
                assert generated_cmd[:4] == [str(provider_python), "-E", "-P", "-c"], (
                    label,
                    generated_cmd[:6],
                )
            else:
                assert generated_cmd[:2] == ["/bin/sh", "-c"], (
                    label,
                    generated_cmd[:6],
                )
                assert generated_cmd[4] == str(provider_python), (
                    label,
                    generated_cmd[:6],
                )
            assert generated_cmd[generated_cmd.index("--cli-bin") + 1] == expected_cli

        stop_exec = subprocess.run(
            stop_cmd,
            input=stop_event,
            cwd=poison_cwd,
            env=isolated_provider_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        assert stop_exec.returncode == 0, (label, stop_exec.stderr)
        assert json.loads(stop_exec.stdout) == {}

        dry_watchdog_cmd = [
            *(arg for arg in watchdog_cmd if arg != "--execute"),
            "--dry-run",
            "--state-dir",
            str(wd_state_dir),
            "--launch-agents-dir",
            str(agents_dir),
        ]
        watchdog_exec = subprocess.run(
            dry_watchdog_cmd,
            cwd=poison_cwd,
            env=isolated_provider_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        assert watchdog_exec.returncode == 0, (label, watchdog_exec.stderr)
        wd_payload = json.loads(watchdog_exec.stdout)
        assert wd_payload["ok"] is True
        assert wd_payload["dry_run"] is True
        plist_args = [str(item) for item in wd_payload["plist"]["ProgramArguments"]]
        if os.name == "nt":
            assert plist_args[:4] == [str(provider_python), "-E", "-P", "-c"], (
                label,
                plist_args[:6],
            )
        else:
            assert plist_args[:2] == ["/bin/sh", "-c"], (label, plist_args[:6])
            assert plist_args[4] == str(provider_python), (label, plist_args[:6])
        tick_help = subprocess.run(
            [*plist_args, "--help"],
            cwd=poison_cwd,
            env=isolated_provider_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        assert tick_help.returncode == 0, (label, tick_help.stderr)


def test_incomplete_release_snapshot_missing_host_runtime_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    provider_venv = tmp_path / "provider-venv"
    venv.EnvBuilder(symlinks=True, with_pip=False).create(provider_venv)
    provider_python = provider_venv / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )

    r_bad = tmp_path / "releases" / "r-bad"
    _make_release_snapshot(r_bad, repo_root)
    cli_symlink = tmp_path / "bin" / "loopx"
    cli_symlink.parent.mkdir()
    cli_symlink.symlink_to(r_bad / "scripts" / "loopx")

    # Capture pre-corruption Stop hook and LaunchAgent argv while snapshot is intact.
    state_dir = tmp_path / "state"
    log_dir = tmp_path / "logs"
    capture_script = (
        "import json; from loopx.hosts.agy import "
        "build_antigravity_hooks_template, launchd_watchdog; "
        f"hooks = build_antigravity_hooks_template(cli_bin={str(cli_symlink)!r}, "
        "goal_id='g-inc', agent_id='a-inc', conversation_id='c-inc'); "
        f"plist = launchd_watchdog.build_antigravity_launchd_plist("
        "goal_id='g-inc', agent_id='a-inc', conversation_id='c-inc', "
        f"cli_bin={str(cli_symlink)!r}, state_dir={str(state_dir)!r}, log_dir={str(log_dir)!r}); "
        "print(json.dumps({'hooks': hooks, 'plist': plist}))"
    )
    captured = json.loads(
        subprocess.check_output(
            [str(provider_python), "-c", capture_script],
            env={**os.environ, "PYTHONPATH": str(r_bad)},
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    )
    hook_cmd = shlex.split(captured["hooks"]["loopx"]["Stop"][0]["command"])
    program_args = [str(arg) for arg in captured["plist"]["ProgramArguments"]]

    # Remove host_runtime.py while retaining executable scripts/loopx, loopx/__init__.py,
    # and loopx/hosts/agy/__init__.py.
    (r_bad / "loopx" / "hosts" / "agy" / "host_runtime.py").unlink()
    assert (r_bad / "scripts" / "loopx").is_file()
    assert (r_bad / "loopx" / "__init__.py").is_file()
    assert (r_bad / "loopx" / "hosts" / "agy" / "__init__.py").is_file()

    agents_dir = tmp_path / "LaunchAgents"
    for cli_candidate in (str(cli_symlink), str(r_bad / "scripts" / "loopx")):
        with pytest.raises(FileNotFoundError, match="host_runtime missing"):
            build_antigravity_hooks_template(
                cli_bin=cli_candidate,
                goal_id="g-inc",
                agent_id="a-inc",
                conversation_id="c-inc",
            )
        with pytest.raises(FileNotFoundError, match="host_runtime missing"):
            build_antigravity_launchd_command(
                cli_bin=cli_candidate,
                goal_id="g-inc",
                agent_id="a-inc",
                conversation_id="c-inc",
            )
        with pytest.raises(FileNotFoundError, match="host_runtime missing"):
            launchd_watchdog.build_antigravity_launchd_plist(
                goal_id="g-inc",
                agent_id="a-inc",
                conversation_id="c-inc",
                cli_bin=cli_candidate,
                state_dir=str(state_dir),
                log_dir=str(log_dir),
            )
        for execute_flag in (False, True):
            install_args = _args(
                tmp_path,
                cli_bin=cli_candidate,
                launch_agents_dir=str(agents_dir),
                log_dir=str(log_dir),
                execute=execute_flag,
                no_load=True,
            )
            with pytest.raises(FileNotFoundError, match="host_runtime missing"):
                launchd_watchdog.install_launchd(install_args)
            assert not agents_dir.exists()

    # Main CLI `install-launchd` preview AND `--execute --no-load` fail closed with no plist written.
    for extra in ([], ["--execute", "--no-load"]):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "host_runtime",
                "install-launchd",
                "--cli-bin",
                str(cli_symlink),
                "--goal-id",
                "g-inc",
                "--agent-id",
                "a-inc",
                "--conversation-id",
                "c-inc",
                "--launch-agents-dir",
                str(agents_dir),
                "--state-dir",
                str(state_dir),
                "--log-dir",
                str(log_dir),
                *extra,
            ],
        )
        assert host_runtime.main() == 1
        err_payload = json.loads(capsys.readouterr().out)
        assert err_payload == {"ok": False, "error": "FileNotFoundError"}
        assert not agents_dir.exists()

    # Pre-existing runtime commands also fail closed without falling back to poisoned imports.
    poison_cwd = tmp_path / "poison"
    poison_pkg = poison_cwd / "loopx"
    poison_pkg.mkdir(parents=True)
    (poison_pkg / "__init__.py").write_text(
        'raise RuntimeError("poisoned cwd")\n', encoding="utf-8"
    )
    isolated_env = {
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "TMPDIR": str(tmp_path),
    }
    broken_hook = subprocess.run(
        hook_cmd,
        input=json.dumps({"terminationReason": "USER_CANCELED"}),
        cwd=poison_cwd,
        env=isolated_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert broken_hook.returncode != 0
    assert "release root not found" in broken_hook.stderr
    assert "poisoned" not in broken_hook.stderr

    broken_tick = subprocess.run(
        program_args,
        cwd=poison_cwd,
        env=isolated_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert broken_tick.returncode != 0
    assert "release root not found" in broken_tick.stderr


def _make_runnable_release_snapshot(
    release_dir: Path,
    repo_root: Path,
    *,
    python_bin: Path,
    include_python_selector: bool = True,
) -> None:
    _make_release_snapshot(release_dir, repo_root)
    real_launcher = release_dir / "scripts" / "loopx"
    shutil.copy2(repo_root / "scripts" / "loopx", real_launcher)
    real_launcher.chmod(0o755)
    if include_python_selector:
        selector = release_dir / "scripts" / "loopx-python.sh"
        shutil.copy2(repo_root / "scripts" / "loopx-python.sh", selector)
        selector.chmod(0o755)
    (release_dir / ".loopx-python").write_text(f"{python_bin}\n", encoding="utf-8")
    (release_dir / "release.json").write_text('{"schema_version":"v1"}\n', encoding="utf-8")


def _connected_agy_project(tmp_path: Path, name: str = "project") -> tuple[Path, Path, Path]:
    project = tmp_path / name
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    runtime_root = tmp_path / f"{name}-runtime"
    runtime_root.mkdir()
    (project / "ACTIVE_GOAL_STATE.md").write_text("# Goal\n\nUpgrade test.\n", encoding="utf-8")
    registry_path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": "g-upg",
                        "domain": "test",
                        "status": "active",
                        "repo": str(project),
                        "state_file": "ACTIVE_GOAL_STATE.md",
                        "coordination": {
                            "registered_agents": ["worker-upg"],
                            "agent_model": "peer_v1",
                        },
                        "adapter": {
                            "kind": "generic_project_goal_v0",
                            "status": "connected",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return project, registry_path, runtime_root


def test_real_cli_shim_activation_survives_old_snapshot_deletion_with_or_without_path(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("requires POSIX scripts/loopx launcher")
    repo_root = Path(__file__).resolve().parents[1]
    node_bin = shutil.which("node")
    assert node_bin is not None, "node is required for agent-onboard CLI probe"
    node_dir = tmp_path / "node-only-bin"
    node_dir.mkdir()
    (node_dir / "node").symlink_to(node_bin)

    provider_venv = tmp_path / "provider-venv"
    venv.EnvBuilder(symlinks=True, with_pip=False).create(provider_venv)
    provider_python = provider_venv / "bin" / "python"

    v1 = tmp_path / "releases" / "v1"
    v2 = tmp_path / "releases" / "v2"
    _make_runnable_release_snapshot(v1, repo_root, python_bin=provider_python)
    _make_runnable_release_snapshot(v2, repo_root, python_bin=provider_python)

    custom_bin = tmp_path / "custom-bin"
    custom_bin.mkdir()
    cli_symlink = custom_bin / "loopx"
    cli_symlink.symlink_to(v1 / "scripts" / "loopx")

    home_dir = tmp_path / "fake-home"
    local_bin = home_dir / ".local" / "bin"
    local_bin.mkdir(parents=True)
    local_shim = local_bin / "loopx"
    local_shim.symlink_to(v1 / "scripts" / "loopx")

    project, registry_path, runtime_root = _connected_agy_project(tmp_path)
    poison_cwd = tmp_path / "poison"
    (poison_cwd / "loopx").mkdir(parents=True)
    (poison_cwd / "loopx" / "__init__.py").write_text(
        'raise RuntimeError("poisoned cwd")\n', encoding="utf-8"
    )

    saved_artifacts: list[tuple[str, list[str], list[str], Path]] = []
    agents_dir = tmp_path / "LaunchAgents"
    state_dir = tmp_path / "wd-state"
    log_dir = tmp_path / "wd-logs"

    cases = [
        ("bootstrap-with-path", f"{custom_bin}:{node_dir}:/usr/bin:/bin", str(cli_symlink), [
            "--format", "json", "--registry", str(registry_path), "--runtime-root", str(runtime_root),
            "bootstrap-command-pack", "--project", str(project),
            "--host-surface", "antigravity-app", "--goal-id", "g-upg", "--agent-id", "worker-upg",
        ], str(cli_symlink)),
        ("bootstrap-without-path", f"{node_dir}:/usr/bin:/bin", str(cli_symlink), [
            "--format", "json", "--registry", str(registry_path), "--runtime-root", str(runtime_root),
            "bootstrap-command-pack", "--project", str(project),
            "--host-surface", "antigravity-app", "--goal-id", "g-upg", "--agent-id", "worker-upg",
        ], str(cli_symlink)),
        ("onboard-without-path", f"{node_dir}:/usr/bin:/bin", str(cli_symlink), [
            "--format", "json", "--registry", str(registry_path), "--runtime-root", str(runtime_root),
            "agent-onboard", "--project", str(project),
            "--agent-type", "antigravity-app", "--goal-id", "g-upg", "--agent-id", "worker-upg",
        ], str(cli_symlink)),
        ("direct-script-prefers-home-local-shim", f"{node_dir}:/usr/bin:/bin", str(v1 / "scripts" / "loopx"), [
            "--format", "json", "--registry", str(registry_path), "--runtime-root", str(runtime_root),
            "bootstrap-command-pack", "--project", str(project),
            "--host-surface", "antigravity-app", "--goal-id", "g-upg", "--agent-id", "worker-upg",
        ], str(local_shim)),
    ]

    for idx, (label, path_env, invoked_bin, cli_args, expected_cli) in enumerate(cases):
        cli_proc = subprocess.run(
            [invoked_bin, *cli_args],
            cwd=project,
            env={
                "HOME": str(home_dir),
                "LOOPX_USAGE_PING": "0",
                "PATH": path_env,
                "TMPDIR": str(tmp_path),
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        assert cli_proc.returncode == 0, (label, cli_proc.stderr, cli_proc.stdout)
        packet = json.loads(cli_proc.stdout)
        mutation = packet["host_loop_activation"]["host_mutation"]
        stop_cmd = shlex.split(mutation["hooks_template"]["loopx"]["Stop"][0]["command"])
        watchdog_cmd = shlex.split(mutation["reboot_watchdog_command"])
        assert stop_cmd[stop_cmd.index("--cli-bin") + 1] == expected_cli
        assert watchdog_cmd[watchdog_cmd.index("--cli-bin") + 1] == expected_cli

        case_label = f"com.loopx.test.shim.{idx}"
        install_cmd = [
            *watchdog_cmd,
            "--no-load",
            "--label",
            case_label,
            "--launch-agents-dir",
            str(agents_dir),
            "--state-dir",
            str(state_dir),
            "--log-dir",
            str(log_dir),
        ]
        installed = subprocess.run(
            install_cmd,
            cwd=poison_cwd,
            env={"HOME": str(home_dir), "LOOPX_USAGE_PING": "0", "PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path)},
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        assert installed.returncode == 0, (label, installed.stderr, installed.stdout)
        plist_path = Path(str(json.loads(installed.stdout)["plist_path"]))
        assert plist_path.is_file()
        dry_cmd = [
            *(arg for arg in watchdog_cmd if arg != "--execute"),
            "--dry-run",
            "--state-dir",
            str(state_dir),
            "--launch-agents-dir",
            str(agents_dir),
        ]
        saved_artifacts.append((label, stop_cmd, dry_cmd, plist_path))

    # Upgrade both shims to v2 and delete v1 completely.
    for shim in (cli_symlink, local_shim):
        shim.unlink()
        shim.symlink_to(v2 / "scripts" / "loopx")
    shutil.rmtree(v1)
    assert not v1.exists()

    stop_event = json.dumps({"terminationReason": "USER_CANCELED"})
    clean_runtime_env = {
        "HOME": str(home_dir),
        "LOOPX_USAGE_PING": "0",
        "PATH": "/usr/bin:/bin",
        "TMPDIR": str(tmp_path),
    }
    for label, stop_cmd, dry_cmd, plist_path in saved_artifacts:
        stop_exec = subprocess.run(
            stop_cmd,
            input=stop_event,
            cwd=poison_cwd,
            env=clean_runtime_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        assert stop_exec.returncode == 0, (label, stop_exec.stderr)
        assert json.loads(stop_exec.stdout) == {}

        dry_exec = subprocess.run(
            dry_cmd,
            cwd=poison_cwd,
            env=clean_runtime_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        assert dry_exec.returncode == 0, (label, dry_exec.stderr)
        assert json.loads(dry_exec.stdout)["ok"] is True

        parsed_plist = plistlib.loads(plist_path.read_bytes())
        plist_env = {str(k): str(v) for k, v in parsed_plist["EnvironmentVariables"].items()}
        plist_env["HOME"] = str(home_dir)
        plist_env["TMPDIR"] = str(tmp_path)
        tick_help = subprocess.run(
            [*[str(x) for x in parsed_plist["ProgramArguments"]], "--help"],
            cwd="/",
            env=plist_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        assert tick_help.returncode == 0, (label, tick_help.stderr)
        assert "--cli-bin" in tick_help.stdout


def test_saved_launchd_and_stop_hook_follow_active_interpreter_after_old_python_retired(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("requires POSIX shell trampoline")
    repo_root = Path(__file__).resolve().parents[1]

    venv1 = tmp_path / "venv1"
    venv2 = tmp_path / "venv2"
    venv.EnvBuilder(symlinks=True, with_pip=False).create(venv1)
    venv.EnvBuilder(symlinks=True, with_pip=False).create(venv2)
    py1 = venv1 / "bin" / "python"
    real_py2 = venv2 / "bin" / "python"

    py2_log = tmp_path / "py2-invocations.log"
    py2_wrapper = tmp_path / "venv2-py-probe"
    py2_wrapper.write_text(
        f"#!/bin/sh\nprintf 'ran:%s\\n' \"$0\" >> {shlex.quote(str(py2_log))}\n"
        f"exec {shlex.quote(str(real_py2))} \"$@\"\n",
        encoding="utf-8",
    )
    py2_wrapper.chmod(0o755)

    v1 = tmp_path / "releases" / "v1"
    v2 = tmp_path / "releases" / "v2"
    _make_runnable_release_snapshot(v1, repo_root, python_bin=py1)
    _make_runnable_release_snapshot(v2, repo_root, python_bin=py2_wrapper)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli_symlink = bin_dir / "loopx"
    cli_symlink.symlink_to(v1 / "scripts" / "loopx")

    project, registry_path, runtime_root = _connected_agy_project(tmp_path)
    agents_dir = tmp_path / "LaunchAgents"
    state_dir = tmp_path / "wd-state"
    log_dir = tmp_path / "wd-logs"

    # Generate Stop hook and write real LaunchAgent plist while running on v1 + venv1.
    cli_pack = subprocess.run(
        [
            str(cli_symlink),
            "--format",
            "json",
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime_root),
            "bootstrap-command-pack",
            "--project",
            str(project),
            "--host-surface",
            "antigravity-app",
            "--goal-id",
            "g-upg",
            "--agent-id",
            "worker-upg",
        ],
        cwd=project,
        env={"HOME": str(tmp_path), "LOOPX_USAGE_PING": "0", "PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=True,
    )
    mutation = json.loads(cli_pack.stdout)["host_loop_activation"]["host_mutation"]
    stop_cmd = shlex.split(mutation["hooks_template"]["loopx"]["Stop"][0]["command"])
    watchdog_cmd = shlex.split(mutation["reboot_watchdog_command"])
    assert stop_cmd[4] == str(py1)

    installed = subprocess.run(
        [
            *watchdog_cmd,
            "--no-load",
            "--label",
            "com.loopx.test.retired-py",
            "--launch-agents-dir",
            str(agents_dir),
            "--state-dir",
            str(state_dir),
            "--log-dir",
            str(log_dir),
        ],
        cwd=tmp_path,
        env={"HOME": str(tmp_path), "LOOPX_USAGE_PING": "0", "PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=True,
    )
    plist_path = Path(str(json.loads(installed.stdout)["plist_path"]))
    parsed_plist = plistlib.loads(plist_path.read_bytes())
    assert parsed_plist["ProgramArguments"][4] == str(py1)

    # Upgrade symlink to v2 (which uses py2_wrapper), and retire BOTH v1 and venv1 completely.
    cli_symlink.unlink()
    cli_symlink.symlink_to(v2 / "scripts" / "loopx")
    shutil.rmtree(v1)
    shutil.rmtree(venv1)
    assert not v1.exists()
    assert not py1.exists()

    poison_cwd = tmp_path / "poison"
    (poison_cwd / "loopx").mkdir(parents=True)
    (poison_cwd / "loopx" / "__init__.py").write_text(
        'raise RuntimeError("poisoned cwd")\n', encoding="utf-8"
    )

    plist_env = {str(k): str(v) for k, v in parsed_plist["EnvironmentVariables"].items()}
    plist_env["HOME"] = str(tmp_path)
    plist_env["TMPDIR"] = str(tmp_path)
    tick_help = subprocess.run(
        [*[str(x) for x in parsed_plist["ProgramArguments"]], "--help"],
        cwd="/",
        env=plist_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert tick_help.returncode == 0, tick_help.stderr
    assert "--cli-bin" in tick_help.stdout

    stop_exec = subprocess.run(
        stop_cmd,
        input=json.dumps({"terminationReason": "USER_CANCELED"}),
        cwd=poison_cwd,
        env={"HOME": str(tmp_path), "LOOPX_USAGE_PING": "0", "PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert stop_exec.returncode == 0, stop_exec.stderr
    assert json.loads(stop_exec.stdout) == {}

    invocations_logged = [
        line for line in py2_log.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(invocations_logged) >= 2, invocations_logged
    assert all(str(py2_wrapper) in line for line in invocations_logged)


def test_invoked_release_shim_overrides_different_install_on_path(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("requires POSIX scripts/loopx launcher")
    repo_root = Path(__file__).resolve().parents[1]
    node_bin = shutil.which("node")
    assert node_bin is not None, "node is required for agent-onboard CLI probe"
    node_dir = tmp_path / "node-only-bin"
    node_dir.mkdir()
    (node_dir / "node").symlink_to(node_bin)

    provider_venv = tmp_path / "provider-venv"
    venv.EnvBuilder(symlinks=True, with_pip=False).create(provider_venv)
    provider_python = provider_venv / "bin" / "python"

    install_a = tmp_path / "install-a" / "release"
    install_b = tmp_path / "install-b" / "release"
    _make_runnable_release_snapshot(install_a, repo_root, python_bin=provider_python)
    _make_runnable_release_snapshot(install_b, repo_root, python_bin=provider_python)

    a_bin = tmp_path / "install-a" / "bin"
    b_bin = tmp_path / "install-b" / "bin"
    a_bin.mkdir(parents=True)
    b_bin.mkdir(parents=True)
    a_shim = a_bin / "loopx"
    b_shim = b_bin / "loopx"
    a_shim.symlink_to(install_a / "scripts" / "loopx")
    b_shim.symlink_to(install_b / "scripts" / "loopx")

    project, registry_path, runtime_root = _connected_agy_project(tmp_path)
    poison_cwd = tmp_path / "poison"
    (poison_cwd / "loopx").mkdir(parents=True)
    (poison_cwd / "loopx" / "__init__.py").write_text(
        'raise RuntimeError("poisoned cwd")\n', encoding="utf-8"
    )

    # B_bin precedes everything on PATH while A is invoked directly.
    competing_path = f"{b_bin}:{node_dir}:/usr/bin:/bin"
    run_env = {
        "HOME": str(tmp_path),
        "LOOPX_USAGE_PING": "0",
        "PATH": competing_path,
        "TMPDIR": str(tmp_path),
    }

    agents_dir = tmp_path / "LaunchAgents"
    state_dir = tmp_path / "wd-state"
    log_dir = tmp_path / "wd-logs"

    # Explicit --cli-bin <B_shim> must still take precedence over invoked A while B exists.
    explicit_b = subprocess.run(
        [
            str(a_shim),
            "--format",
            "json",
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime_root),
            "bootstrap-command-pack",
            "--project",
            str(project),
            "--host-surface",
            "antigravity-app",
            "--goal-id",
            "g-upg",
            "--agent-id",
            "worker-upg",
            "--cli-bin",
            str(b_shim),
        ],
        cwd=project,
        env=run_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=True,
    )
    b_mut = json.loads(explicit_b.stdout)["host_loop_activation"]["host_mutation"]
    b_stop = shlex.split(b_mut["hooks_template"]["loopx"]["Stop"][0]["command"])
    b_wd = shlex.split(b_mut["reboot_watchdog_command"])
    assert b_stop[b_stop.index("--cli-bin") + 1] == str(b_shim)
    assert b_wd[b_wd.index("--cli-bin") + 1] == str(b_shim)

    a_invocations = [
        ("a-shim-bootstrap", str(a_shim), [
            "--format", "json", "--registry", str(registry_path), "--runtime-root", str(runtime_root),
            "bootstrap-command-pack", "--project", str(project),
            "--host-surface", "antigravity-app", "--goal-id", "g-upg", "--agent-id", "worker-upg",
        ], str(a_shim)),
        ("a-shim-onboard", str(a_shim), [
            "--format", "json", "--registry", str(registry_path), "--runtime-root", str(runtime_root),
            "agent-onboard", "--project", str(project),
            "--agent-type", "antigravity-app", "--goal-id", "g-upg", "--agent-id", "worker-upg",
        ], str(a_shim)),
        ("a-direct-bootstrap", str(install_a / "scripts" / "loopx"), [
            "--format", "json", "--registry", str(registry_path), "--runtime-root", str(runtime_root),
            "bootstrap-command-pack", "--project", str(project),
            "--host-surface", "antigravity-app", "--goal-id", "g-upg", "--agent-id", "worker-upg",
        ], str(install_a / "scripts" / "loopx")),
    ]

    saved_a: list[tuple[str, list[str], list[str], Path]] = []
    for idx, (label, invoked_a, cli_args, expected_cli) in enumerate(a_invocations):
        proc = subprocess.run(
            [invoked_a, *cli_args],
            cwd=project,
            env=run_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        assert proc.returncode == 0, (label, proc.stderr, proc.stdout)
        mut = json.loads(proc.stdout)["host_loop_activation"]["host_mutation"]
        stop_cmd = shlex.split(mut["hooks_template"]["loopx"]["Stop"][0]["command"])
        wd_cmd = shlex.split(mut["reboot_watchdog_command"])
        assert stop_cmd[stop_cmd.index("--cli-bin") + 1] == expected_cli
        assert wd_cmd[wd_cmd.index("--cli-bin") + 1] == expected_cli
        assert str(install_b) not in " ".join(stop_cmd)
        assert str(b_shim) not in " ".join(stop_cmd)
        assert str(install_b) not in " ".join(wd_cmd)
        assert str(b_shim) not in " ".join(wd_cmd)

        installed = subprocess.run(
            [
                *wd_cmd,
                "--no-load",
                "--label",
                f"com.loopx.test.installa.{idx}",
                "--launch-agents-dir",
                str(agents_dir),
                "--state-dir",
                str(state_dir),
                "--log-dir",
                str(log_dir),
            ],
            cwd=poison_cwd,
            env=run_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        assert installed.returncode == 0, (label, installed.stderr, installed.stdout)
        plist_path = Path(str(json.loads(installed.stdout)["plist_path"]))
        assert plist_path.is_file()
        dry_cmd = [
            *(arg for arg in wd_cmd if arg != "--execute"),
            "--dry-run",
            "--state-dir",
            str(state_dir),
            "--launch-agents-dir",
            str(agents_dir),
        ]
        saved_a.append((label, stop_cmd, dry_cmd, plist_path))

    # Delete install B completely (both release tree and B_bin shim).
    shutil.rmtree(install_b.parent)
    assert not b_shim.exists()
    assert not install_b.exists()

    a_ver = subprocess.run(
        [str(a_shim), "--version"],
        cwd=project,
        env=run_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert a_ver.returncode == 0, a_ver.stderr

    stop_event = json.dumps({"terminationReason": "USER_CANCELED"})
    for label, stop_cmd, dry_cmd, plist_path in saved_a:
        stop_exec = subprocess.run(
            stop_cmd,
            input=stop_event,
            cwd=poison_cwd,
            env=run_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        assert stop_exec.returncode == 0, (label, stop_exec.stderr)
        assert json.loads(stop_exec.stdout) == {}

        dry_exec = subprocess.run(
            dry_cmd,
            cwd=poison_cwd,
            env=run_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        assert dry_exec.returncode == 0, (label, dry_exec.stderr)
        assert json.loads(dry_exec.stdout)["ok"] is True

        parsed_plist = plistlib.loads(plist_path.read_bytes())
        plist_env = {str(k): str(v) for k, v in parsed_plist["EnvironmentVariables"].items()}
        plist_env["HOME"] = str(tmp_path)
        plist_env["TMPDIR"] = str(tmp_path)
        tick_help = subprocess.run(
            [*[str(x) for x in parsed_plist["ProgramArguments"]], "--help"],
            cwd="/",
            env=plist_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        assert tick_help.returncode == 0, (label, tick_help.stderr)


def test_direct_release_invocation_picks_later_matching_shim_on_path(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("requires POSIX scripts/loopx launcher")
    repo_root = Path(__file__).resolve().parents[1]
    node_bin = shutil.which("node")
    assert node_bin is not None, "node is required for agent-onboard CLI probe"
    node_dir = tmp_path / "node-only-bin"
    node_dir.mkdir()
    (node_dir / "node").symlink_to(node_bin)

    provider_venv = tmp_path / "provider-venv"
    venv.EnvBuilder(symlinks=True, with_pip=False).create(provider_venv)
    provider_python = provider_venv / "bin" / "python"

    install_a_v1 = tmp_path / "install-a" / "releases" / "v1"
    install_a_v2 = tmp_path / "install-a" / "releases" / "v2"
    install_b = tmp_path / "install-b" / "releases" / "v1"
    _make_runnable_release_snapshot(install_a_v1, repo_root, python_bin=provider_python)
    _make_runnable_release_snapshot(install_a_v2, repo_root, python_bin=provider_python)
    _make_runnable_release_snapshot(install_b, repo_root, python_bin=provider_python)

    a_bin = tmp_path / "install-a" / "bin"
    b_bin = tmp_path / "install-b" / "bin"
    a_bin.mkdir(parents=True)
    b_bin.mkdir(parents=True)
    a_shim = a_bin / "loopx"
    b_shim = b_bin / "loopx"
    a_shim.symlink_to(install_a_v1 / "scripts" / "loopx")
    b_shim.symlink_to(install_b / "scripts" / "loopx")

    project, registry_path, runtime_root = _connected_agy_project(tmp_path)
    poison_cwd = tmp_path / "poison"
    (poison_cwd / "loopx").mkdir(parents=True)
    (poison_cwd / "loopx" / "__init__.py").write_text(
        'raise RuntimeError("poisoned cwd")\n', encoding="utf-8"
    )

    # Install B's shim is FIRST on PATH; Install A's custom shim is SECOND on PATH
    # (and ~/.local/bin/loopx does not exist under HOME=tmp_path).
    # Invoking install_a_v1/scripts/loopx directly must find a_shim and survive v1 retirement.
    layered_path = f"{b_bin}:{a_bin}:{node_dir}:/usr/bin:/bin"
    run_env = {
        "HOME": str(tmp_path),
        "LOOPX_USAGE_PING": "0",
        "PATH": layered_path,
        "TMPDIR": str(tmp_path),
    }

    agents_dir = tmp_path / "LaunchAgents"
    state_dir = tmp_path / "wd-state"
    log_dir = tmp_path / "wd-logs"

    direct_invocations = [
        (
            "direct-v1-bootstrap",
            [
                str(install_a_v1 / "scripts" / "loopx"),
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "--runtime-root",
                str(runtime_root),
                "bootstrap-command-pack",
                "--project",
                str(project),
                "--host-surface",
                "antigravity-app",
                "--goal-id",
                "g-upg",
                "--agent-id",
                "worker-upg",
            ],
        ),
        (
            "direct-v1-onboard",
            [
                str(install_a_v1 / "scripts" / "loopx"),
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "--runtime-root",
                str(runtime_root),
                "agent-onboard",
                "--project",
                str(project),
                "--agent-type",
                "antigravity-app",
                "--goal-id",
                "g-upg",
                "--agent-id",
                "worker-upg",
            ],
        ),
    ]

    saved: list[tuple[str, list[str], list[str], Path]] = []
    for idx, (label, cmd_argv) in enumerate(direct_invocations):
        proc = subprocess.run(
            cmd_argv,
            cwd=project,
            env=run_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        assert proc.returncode == 0, (label, proc.stderr, proc.stdout)
        mut = json.loads(proc.stdout)["host_loop_activation"]["host_mutation"]
        stop_cmd = shlex.split(mut["hooks_template"]["loopx"]["Stop"][0]["command"])
        wd_cmd = shlex.split(mut["reboot_watchdog_command"])
        assert stop_cmd[stop_cmd.index("--cli-bin") + 1] == str(a_shim)
        assert wd_cmd[wd_cmd.index("--cli-bin") + 1] == str(a_shim)

        installed = subprocess.run(
            [
                *wd_cmd,
                "--no-load",
                "--label",
                f"com.loopx.test.layered.{idx}",
                "--launch-agents-dir",
                str(agents_dir),
                "--state-dir",
                str(state_dir),
                "--log-dir",
                str(log_dir),
            ],
            cwd=poison_cwd,
            env=run_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        assert installed.returncode == 0, (label, installed.stderr, installed.stdout)
        plist_path = Path(str(json.loads(installed.stdout)["plist_path"]))
        assert plist_path.is_file()
        dry_cmd = [
            *(arg for arg in wd_cmd if arg != "--execute"),
            "--dry-run",
            "--state-dir",
            str(state_dir),
            "--launch-agents-dir",
            str(agents_dir),
        ]
        saved.append((label, stop_cmd, dry_cmd, plist_path))

    # Upgrade install-a's custom shim from v1 -> v2, delete v1, and delete install-b.
    a_shim.unlink()
    a_shim.symlink_to(install_a_v2 / "scripts" / "loopx")
    shutil.rmtree(install_a_v1)
    shutil.rmtree(tmp_path / "install-b")
    assert not install_a_v1.exists()

    a_ver = subprocess.run(
        [str(a_shim), "--version"],
        cwd=project,
        env=run_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert a_ver.returncode == 0, a_ver.stderr

    stop_event = json.dumps({"terminationReason": "USER_CANCELED"})
    for label, stop_cmd, dry_cmd, plist_path in saved:
        stop_exec = subprocess.run(
            stop_cmd,
            input=stop_event,
            cwd=poison_cwd,
            env=run_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        assert stop_exec.returncode == 0, (label, stop_exec.stderr)
        assert json.loads(stop_exec.stdout) == {}

        dry_exec = subprocess.run(
            dry_cmd,
            cwd=poison_cwd,
            env=run_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        assert dry_exec.returncode == 0, (label, dry_exec.stderr)
        assert json.loads(dry_exec.stdout)["ok"] is True

        parsed_plist = plistlib.loads(plist_path.read_bytes())
        plist_env = {str(k): str(v) for k, v in parsed_plist["EnvironmentVariables"].items()}
        plist_env["HOME"] = str(tmp_path)
        plist_env["TMPDIR"] = str(tmp_path)
        tick_help = subprocess.run(
            [*[str(x) for x in parsed_plist["ProgramArguments"]], "--help"],
            cwd="/",
            env=plist_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        assert tick_help.returncode == 0, (label, tick_help.stderr)
        assert "--cli-bin" in tick_help.stdout


def _make_pre_consolidation_release_snapshot(
    release_dir: Path,
    repo_root: Path,
    *,
    python_bin: Path,
) -> None:
    """Build a runnable pre-#9510 snapshot using ``loopx/agy_goal_mode`` (no ``hosts/agy``)."""
    _make_runnable_release_snapshot(release_dir, repo_root, python_bin=python_bin)
    pkg_dir = release_dir / "loopx"
    source_pkg = repo_root / "loopx"

    # Replace symlinked files that reference `hosts.agy` inside `prev_release` only.
    for rel, old, new in (
        ("doctor.py", "from .hosts.agy import", "from .agy_goal_mode import"),
        ("host_loop_activation.py", "from .hosts.agy import", "from .agy_goal_mode import"),
        ("slash_command_install.py", "from .hosts.agy import", "from .agy_goal_mode import"),
        ("workflow_skill_install.py", "from .hosts.agy import", "from .agy_goal_mode import"),
    ):
        target = pkg_dir / rel
        if target.is_symlink():
            target.unlink()
        target.write_text(
            (source_pkg / rel).read_text(encoding="utf-8").replace(old, new),
            encoding="utf-8",
        )
    facade_copy = pkg_dir / "hosts" / "skill_facade.py"
    facade_copy.write_text(
        facade_copy.read_text(encoding="utf-8").replace(
            "from .agy import", "from ..agy_goal_mode import"
        ),
        encoding="utf-8",
    )
    caps_copy = pkg_dir / "hosts" / "capabilities.py"
    caps_copy.write_text(
        caps_copy.read_text(encoding="utf-8").replace(
            '"agy": "loopx.hosts.agy"', '"agy": "loopx.agy_goal_mode"'
        ),
        encoding="utf-8",
    )

    # Convert canonical `hosts/agy/*.py` back to pre-#9510 `agy_goal_mode/*.py`, then delete `hosts/agy`.
    legacy_dir = pkg_dir / "agy_goal_mode"
    legacy_dir.unlink()
    legacy_dir.mkdir(parents=True)
    for name in ("__init__.py", "host_runtime.py", "launchd_watchdog.py"):
        src = (source_pkg / "hosts" / "agy" / name).read_text(encoding="utf-8")
        converted = (
            src.replace("from ...command_invocation", "from ..command_invocation")
            .replace("from ...entrypoint", "from ..entrypoint")
            .replace("from ...file_lock", "from ..file_lock")
            .replace("from ...chat_agent", "from ..chat_agent")
            .replace(
                "from ...capabilities.native_chat.antigravity_adapter",
                "from ..capabilities.native_chat.antigravity_adapter",
            )
            .replace("from ..capabilities import (", "from ..hosts.capabilities import (")
            .replace("loopx.hosts.agy.host_runtime", "loopx.agy_goal_mode.host_runtime")
            .replace("loopx.hosts.agy", "loopx.agy_goal_mode")
            .replace("loopx/hosts/agy", "loopx/agy_goal_mode")
            .replace('"hosts" / "agy"', '"agy_goal_mode"')
            .replace('"hosts"/"agy"', '"agy_goal_mode"')
        )
        (legacy_dir / name).write_text(converted, encoding="utf-8")
    shutil.rmtree(pkg_dir / "hosts" / "agy")


def test_archive_rollback_to_pre_consolidation_release_preserves_saved_commands(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("requires POSIX scripts/loopx launcher")
    repo_root = Path(__file__).resolve().parents[1]
    node_bin = shutil.which("node")
    assert node_bin is not None, "node is required for agent-onboard CLI probe"
    node_dir = tmp_path / "node-only-bin"
    node_dir.mkdir()
    (node_dir / "node").symlink_to(node_bin)

    provider_venv = tmp_path / "provider-venv"
    venv.EnvBuilder(symlinks=True, with_pip=False).create(provider_venv)
    provider_python = provider_venv / "bin" / "python"

    home_dir = tmp_path / "fake-home"
    releases_dir = home_dir / ".local" / "share" / "loopx" / "releases"
    prev_release = releases_dir / "20261127T044107Z"
    curr_release = releases_dir / "20261127T054107Z"
    _make_pre_consolidation_release_snapshot(
        prev_release, repo_root, python_bin=provider_python
    )
    _make_runnable_release_snapshot(
        curr_release, repo_root, python_bin=provider_python
    )
    assert not (prev_release / "loopx" / "hosts" / "agy").exists()
    assert (prev_release / "loopx" / "agy_goal_mode" / "host_runtime.py").is_file()

    local_bin = home_dir / ".local" / "bin"
    local_bin.mkdir(parents=True)
    cli_shim = local_bin / "loopx"
    cli_shim.symlink_to(curr_release / "scripts" / "loopx")

    project, registry_path, runtime_root = _connected_agy_project(tmp_path)
    poison_cwd = tmp_path / "poison"
    (poison_cwd / "loopx").mkdir(parents=True)
    (poison_cwd / "loopx" / "__init__.py").write_text(
        'raise RuntimeError("poisoned cwd")\n', encoding="utf-8"
    )

    run_env = {
        "HOME": str(home_dir),
        "LOOPX_USAGE_PING": "0",
        "PATH": f"{local_bin}:{node_dir}:/usr/bin:/bin",
        "TMPDIR": str(tmp_path),
    }

    agents_dir = tmp_path / "LaunchAgents"
    state_dir = tmp_path / "wd-state"
    log_dir = tmp_path / "wd-logs"

    activations = [
        (
            "curr-bootstrap",
            [
                str(cli_shim),
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "--runtime-root",
                str(runtime_root),
                "bootstrap-command-pack",
                "--project",
                str(project),
                "--host-surface",
                "antigravity-app",
                "--goal-id",
                "g-upg",
                "--agent-id",
                "worker-upg",
            ],
        ),
        (
            "curr-onboard",
            [
                str(cli_shim),
                "--format",
                "json",
                "--registry",
                str(registry_path),
                "--runtime-root",
                str(runtime_root),
                "agent-onboard",
                "--project",
                str(project),
                "--agent-type",
                "antigravity-app",
                "--goal-id",
                "g-upg",
                "--agent-id",
                "worker-upg",
            ],
        ),
    ]

    saved: list[tuple[str, list[str], list[str], Path]] = []
    for idx, (label, argv) in enumerate(activations):
        proc = subprocess.run(
            argv,
            cwd=project,
            env=run_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        assert proc.returncode == 0, (label, proc.stderr, proc.stdout)
        mut = json.loads(proc.stdout)["host_loop_activation"]["host_mutation"]
        stop_cmd = shlex.split(mut["hooks_template"]["loopx"]["Stop"][0]["command"])
        wd_cmd = shlex.split(mut["reboot_watchdog_command"])
        assert stop_cmd[stop_cmd.index("--cli-bin") + 1] == str(cli_shim)
        assert wd_cmd[wd_cmd.index("--cli-bin") + 1] == str(cli_shim)

        installed = subprocess.run(
            [
                *wd_cmd,
                "--no-load",
                "--label",
                f"com.loopx.test.rollback.{idx}",
                "--launch-agents-dir",
                str(agents_dir),
                "--state-dir",
                str(state_dir),
                "--log-dir",
                str(log_dir),
            ],
            cwd=poison_cwd,
            env=run_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        assert installed.returncode == 0, (label, installed.stderr, installed.stdout)
        plist_path = Path(str(json.loads(installed.stdout)["plist_path"]))
        assert plist_path.is_file()
        dry_cmd = [
            *(arg for arg in wd_cmd if arg != "--execute"),
            "--dry-run",
            "--state-dir",
            str(state_dir),
            "--launch-agents-dir",
            str(agents_dir),
        ]
        saved.append((label, stop_cmd, dry_cmd, plist_path))

    # Perform native `loopx update --rollback previous` back to pre-#9510 archive layout.
    rollback = subprocess.run(
        [str(cli_shim), "--format", "json", "update", "--rollback", "previous"],
        cwd=project,
        env=run_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert rollback.returncode == 0, (rollback.stderr, rollback.stdout)
    rb_payload = json.loads(rollback.stdout)
    assert rb_payload["ok"] is True, rb_payload
    assert rb_payload["mode"] == "rollback", rb_payload
    assert rb_payload["plan"]["selected_release_id"] == prev_release.name, rb_payload
    assert cli_shim.resolve() == (prev_release / "scripts" / "loopx").resolve()

    # All three saved entrypoints generated by curr_release must work after rollback to prev_release.
    stop_event = json.dumps({"terminationReason": "USER_CANCELED"})
    for label, stop_cmd, dry_cmd, plist_path in saved:
        stop_exec = subprocess.run(
            stop_cmd,
            input=stop_event,
            cwd=poison_cwd,
            env=run_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        assert stop_exec.returncode == 0, (label, stop_exec.stderr)
        assert json.loads(stop_exec.stdout) == {}

        dry_exec = subprocess.run(
            dry_cmd,
            cwd=poison_cwd,
            env=run_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        assert dry_exec.returncode == 0, (label, dry_exec.stderr)
        assert json.loads(dry_exec.stdout)["ok"] is True

        parsed_plist = plistlib.loads(plist_path.read_bytes())
        plist_env = {str(k): str(v) for k, v in parsed_plist["EnvironmentVariables"].items()}
        plist_env["HOME"] = str(home_dir)
        plist_env["TMPDIR"] = str(tmp_path)
        tick_help = subprocess.run(
            [*[str(x) for x in parsed_plist["ProgramArguments"]], "--help"],
            cwd="/",
            env=plist_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        assert tick_help.returncode == 0, (label, tick_help.stderr)
        assert "loopx.agy_goal_mode.host_runtime" in tick_help.stdout

    # Incomplete pre-#9510 snapshot missing host_runtime.py must still fail closed.
    (prev_release / "loopx" / "agy_goal_mode" / "host_runtime.py").unlink()
    failed_stop = subprocess.run(
        saved[0][1],
        input=stop_event,
        cwd=poison_cwd,
        env=run_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert failed_stop.returncode != 0
    assert "release root not found" in failed_stop.stderr

    failed_plist_payload = plistlib.loads(saved[0][3].read_bytes())
    failed_plist_env = {
        str(k): str(v) for k, v in failed_plist_payload["EnvironmentVariables"].items()
    }
    failed_plist_env["HOME"] = str(home_dir)
    failed_plist_env["TMPDIR"] = str(tmp_path)
    failed_tick = subprocess.run(
        [*[str(x) for x in failed_plist_payload["ProgramArguments"]], "--help"],
        cwd="/",
        env=failed_plist_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert failed_tick.returncode != 0
    assert "release root not found" in failed_tick.stderr
