"""Tests for guarded Antigravity reboot-survival watchdog and LaunchAgent installer."""

from __future__ import annotations

from argparse import Namespace
from datetime import datetime
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

from loopx.agy_goal_mode import (
    antigravity_app_activation_extras,
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


def test_install_launchd_preview_execute_no_load_reload_and_uninstall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents_dir = tmp_path / "LaunchAgents"
    log_dir = tmp_path / "logs"
    args = _args(
        tmp_path,
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
    bad_args = _args(tmp_path, label="../../escape")
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
        "loopx.agy_goal_mode.host_runtime",
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
    base_argv = [
        "host_runtime",
        "install-launchd",
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
                "loopx.agy_goal_mode.host_runtime",
                "install-launchd",
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
        "loopx.agy_goal_mode.host_runtime",
        "install-launchd",
    ]
    extras = antigravity_app_activation_extras(goal_id="g1", agent_id="a1")
    assert "install-launchd" in extras["extra_host_mutation"]["reboot_watchdog_command"]

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "host_runtime",
            "install-launchd",
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
