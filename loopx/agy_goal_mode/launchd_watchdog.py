"""macOS LaunchAgent plist builder and installer for the Antigravity watchdog."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys

DEFAULT_LAUNCHD_INTERVAL_SECONDS = 300
MIN_LAUNCHD_INTERVAL_SECONDS = 60
DEFAULT_LONG_IDLE_SECONDS = 2400
MIN_STALL_COOLDOWN_SECONDS = 1800
_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def default_launchd_label(goal_id: str, agent_id: str, conversation_id: str) -> str:
    """Return a deterministic, path-safe LaunchAgent label for one bound session."""
    digest = hashlib.sha256(
        f"{goal_id}:{agent_id}:{conversation_id}".encode("utf-8")
    ).hexdigest()[:12]
    return f"com.loopx.agy-watchdog.{digest}"


def validated_launchd_label(
    label: str | None, *, goal_id: str, agent_id: str, conversation_id: str
) -> str:
    """Validate an explicit LaunchAgent label or derive the deterministic default."""
    resolved = (
        label.strip()
        if isinstance(label, str) and label.strip()
        else default_launchd_label(goal_id, agent_id, conversation_id)
    )
    if ".." in resolved or not _LABEL_PATTERN.fullmatch(resolved):
        raise ValueError("launchd label contains unsafe path characters")
    return resolved


def build_antigravity_launchd_plist(
    *,
    goal_id: str,
    agent_id: str,
    conversation_id: str,
    cli_bin: str = "loopx",
    registry: str | None = None,
    runtime_root: str | None = None,
    state_dir: str | None = None,
    log_dir: str | None = None,
    label: str | None = None,
    interval_seconds: int = DEFAULT_LAUNCHD_INTERVAL_SECONDS,
    long_idle_seconds: int = DEFAULT_LONG_IDLE_SECONDS,
    cooldown_seconds: int = MIN_STALL_COOLDOWN_SECONDS,
    resolve_local_executable: bool = True,
) -> dict[str, object]:
    """Build a reboot-surviving macOS LaunchAgent plist for `heartbeat-tick`."""
    resolved_label = validated_launchd_label(
        label, goal_id=goal_id, agent_id=agent_id, conversation_id=conversation_id
    )
    python_bin = sys.executable if resolve_local_executable else "python3"
    resolved_cli_bin = (
        (shutil.which(cli_bin) or cli_bin) if resolve_local_executable else cli_bin
    )
    resolved_state = str(
        Path(state_dir or str(Path.home() / ".loopx/host-state/antigravity")).expanduser()
    )
    resolved_logs = Path(log_dir or str(Path(resolved_state) / "logs")).expanduser()
    program_args = [
        python_bin,
        "-E",
        "-P",
        "-m",
        "loopx.agy_goal_mode.host_runtime",
        "heartbeat-tick",
        "--cli-bin",
        resolved_cli_bin,
        "--goal-id",
        goal_id,
        "--agent-id",
        agent_id,
        "--conversation-id",
        conversation_id,
        "--state-dir",
        resolved_state,
        "--long-idle-seconds",
        str(max(60, int(long_idle_seconds))),
        "--cooldown-seconds",
        str(max(MIN_STALL_COOLDOWN_SECONDS, int(cooldown_seconds))),
    ]
    if registry:
        program_args.extend(["--registry", registry])
    if runtime_root:
        program_args.extend(["--runtime-root", runtime_root])
    return {
        "Label": resolved_label,
        "ProgramArguments": program_args,
        "RunAtLoad": True,
        "StartInterval": max(MIN_LAUNCHD_INTERVAL_SECONDS, int(interval_seconds)),
        "ProcessType": "Background",
        "StandardOutPath": str(resolved_logs / f"{resolved_label}.stdout.log"),
        "StandardErrorPath": str(resolved_logs / f"{resolved_label}.stderr.log"),
        "EnvironmentVariables": {"LOOPX_USAGE_PING": "0"},
    }


def _run_launchctl(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *argv], capture_output=True, text=True, encoding="utf-8", timeout=5
    )


def _bootout_launchd_agent(label: str) -> None:
    uid = os.getuid() if hasattr(os, "getuid") else None
    target = f"gui/{uid}/{label}" if isinstance(uid, int) else label
    _run_launchctl(["bootout", target])


def _reload_launchd_agent(label: str, plist_path: Path) -> tuple[bool, str | None]:
    uid = os.getuid() if hasattr(os, "getuid") else None
    if not isinstance(uid, int):
        return False, "launchd_uid_unavailable"
    domain = f"gui/{uid}"
    _run_launchctl(["bootout", f"{domain}/{label}"])
    loaded = _run_launchctl(["bootstrap", domain, str(plist_path)])
    if loaded.returncode != 0:
        return False, "launchctl_bootstrap_failed"
    readback = _run_launchctl(["print", f"{domain}/{label}"])
    if readback.returncode != 0:
        return False, "launchctl_readback_failed"
    return True, None


def _write_plist_atomic(plist_path: Path, payload: dict[str, object]) -> None:
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    Path(str(payload["StandardOutPath"])).parent.mkdir(parents=True, exist_ok=True)
    temporary = plist_path.with_suffix(".tmp")
    with temporary.open("wb") as stream:
        temporary.chmod(0o644)
        stream.write(plistlib.dumps(payload))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(plist_path)


def install_launchd(args: argparse.Namespace) -> dict[str, object]:
    """Preview, write, load, or uninstall the Antigravity reboot-survival LaunchAgent."""
    label = validated_launchd_label(
        getattr(args, "label", None),
        goal_id=str(args.goal_id),
        agent_id=str(args.agent_id),
        conversation_id=str(args.conversation_id),
    )
    agents_dir = Path(
        str(getattr(args, "launch_agents_dir", None) or (Path.home() / "Library/LaunchAgents"))
    ).expanduser()
    plist_path = agents_dir / f"{label}.plist"
    execute = bool(getattr(args, "execute", False))
    no_load = bool(getattr(args, "no_load", False))
    uninstall = bool(getattr(args, "uninstall", False))
    operation = "uninstall-launchd" if uninstall else "install-launchd"

    if uninstall:
        if not execute:
            return {
                "ok": True,
                "operation": operation,
                "dry_run": True,
                "uninstalled": False,
                "exists": plist_path.exists(),
                "label": label,
                "plist_path": str(plist_path),
            }
        if not no_load:
            if sys.platform != "darwin":
                return {
                    "ok": False,
                    "operation": operation,
                    "dry_run": False,
                    "uninstalled": False,
                    "error_kind": "launchd_unsupported_platform",
                    "label": label,
                    "plist_path": str(plist_path),
                }
            _bootout_launchd_agent(label)
        plist_path.unlink(missing_ok=True)
        return {
            "ok": True,
            "operation": operation,
            "dry_run": False,
            "uninstalled": True,
            "label": label,
            "plist_path": str(plist_path),
        }

    plist_payload = build_antigravity_launchd_plist(
        goal_id=str(args.goal_id),
        agent_id=str(args.agent_id),
        conversation_id=str(args.conversation_id),
        cli_bin=str(args.cli_bin),
        registry=getattr(args, "registry", None),
        runtime_root=getattr(args, "runtime_root", None),
        state_dir=str(args.state_dir),
        log_dir=getattr(args, "log_dir", None),
        label=label,
        interval_seconds=int(
            getattr(args, "interval_seconds", DEFAULT_LAUNCHD_INTERVAL_SECONDS)
        ),
        long_idle_seconds=int(
            getattr(args, "long_idle_seconds", DEFAULT_LONG_IDLE_SECONDS)
        ),
        cooldown_seconds=int(
            getattr(args, "cooldown_seconds", MIN_STALL_COOLDOWN_SECONDS)
        ),
    )
    if not execute:
        return {
            "ok": True,
            "operation": operation,
            "dry_run": True,
            "written": False,
            "loaded": False,
            "label": label,
            "plist_path": str(plist_path),
            "plist": plist_payload,
        }
    if not no_load and sys.platform != "darwin":
        return {
            "ok": False,
            "operation": operation,
            "dry_run": False,
            "written": False,
            "loaded": False,
            "error_kind": "launchd_unsupported_platform",
            "label": label,
            "plist_path": str(plist_path),
        }
    _write_plist_atomic(plist_path, plist_payload)
    loaded = False
    if not no_load:
        loaded, error_kind = _reload_launchd_agent(label, plist_path)
        if error_kind is not None:
            return {
                "ok": False,
                "operation": operation,
                "dry_run": False,
                "written": True,
                "loaded": False,
                "error_kind": error_kind,
                "label": label,
                "plist_path": str(plist_path),
            }
    return {
        "ok": True,
        "operation": operation,
        "dry_run": False,
        "written": True,
        "loaded": loaded,
        "label": label,
        "plist_path": str(plist_path),
    }
