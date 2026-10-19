"""Compose the existing installer and native App updater without taking ownership.

This is an installation/process adapter. Package-manager policy stays in
self_update; signature verification, App replacement and service qualification
stay in the native updater. No App is installed on a CLI-only machine.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time
from typing import Any
import uuid
from collections.abc import Callable

from ..desktop_installation import desktop_installation_status
from ..self_update import UpdateAction, build_update_plan, collect_doctor

APP_IDENTIFIER = "io.loopx.control-plane"


def _applications() -> tuple[Path, ...]:
    return (
        (Path("/Applications/LoopX.app"), Path.home() / "Applications/LoopX.app")
        if sys.platform == "darwin"
        else ()
    )


def desktop_upgrade_plan(ref: str | None) -> dict[str, Any]:
    apps = [app for app in _applications() if app.exists()]
    if not apps:
        return {"ok": True, "status": "not_installed", "changes_applied": False}
    if len(apps) != 1:
        return {
            "ok": False,
            "status": "ambiguous_installation",
            "changes_applied": False,
            "reason": "Multiple LoopX Apps are installed. Keep one installation before upgrading.",
        }
    app = apps[0]
    try:
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
        if info.get("CFBundleIdentifier") != APP_IDENTIFIER:
            raise ValueError("unexpected App identifier")
        evidence = desktop_installation_status(None, applications=(app,))["apps"][0]
        version = evidence["app_version"]
        if not version or not evidence["bundled_source_revision"]:
            raise ValueError("App version or bundled runtime identity is unavailable")
    except (OSError, ValueError, plistlib.InvalidFileException) as error:
        return {
            "ok": False,
            "status": "invalid_installation",
            "changes_applied": False,
            "reason": str(error),
        }
    channel = (
        ref
        if ref in {"main", "stable"}
        else "main"
        if "-main." in version
        else "stable"
    )
    return {
        "ok": True,
        "status": "planned",
        "changes_applied": False,
        "application": str(app),
        "channel": channel,
        **evidence,
        "reason": "The native App verifies its signed channel update, restarts and qualifies both services.",
    }


def build_upgrade_plan(
    *,
    repo: str | None = None,
    ref: str | None = None,
    archive_url: str | None = None,
    action: UpdateAction = UpdateAction.APPLY,
    doctor_payload: dict[str, Any] | None = None,
    registry_path: Path | None = None,
    runtime_root: str | None = None,
) -> dict[str, Any]:
    doctor = (
        doctor_payload
        if doctor_payload is not None
        else collect_doctor(
            registry_path=registry_path,
            runtime_root_override=runtime_root,
        )
    )
    freshness = doctor.get("install_freshness", {})
    selected_repo = repo or os.environ.get("LOOPX_REPO")
    selected_ref = ref or os.environ.get("LOOPX_REF")
    # Only archive snapshots have a source channel. pip/pipx stay with their
    # owning package index, and a pinned snapshot stays pinned.
    if doctor.get("package", {}).get("release_root"):
        selected_repo = selected_repo or freshness.get("manifest_source_repo")
        selected_ref = selected_ref or freshness.get("manifest_source_ref")
    payload = build_update_plan(
        repo=selected_repo,
        ref=selected_ref,
        archive_url=archive_url,
        action=action,
        doctor_payload=doctor,
        registry_path=registry_path,
        runtime_root=runtime_root,
    )
    desktop = desktop_upgrade_plan(selected_ref)
    payload["desktop_upgrade"] = desktop
    next_action = payload.get("next_action", {})
    command = next_action.get("command")
    if isinstance(command, str):
        next_action["command"] = command.replace(
            "loopx update apply", "loopx upgrade", 1
        )
    if (
        selected_ref
        and payload["install_lifecycle"]["owner"] == "python_package_manager"
    ):
        payload["ok"] = False
        payload["plan"]["apply_supported"] = False
        payload["recommended_action"] = (
            "This installation belongs to its Python package manager; --ref cannot switch it to a Git archive."
        )
    if not desktop["ok"]:
        payload["ok"] = False
        payload["plan"]["apply_supported"] = False
        payload["recommended_action"] = desktop["reason"]
    return payload


def _receipt_directory() -> Path:
    return Path.home() / "Library/Application Support" / APP_IDENTIFIER / "upgrades"


def _running_receipt(receipt: dict[str, Any]) -> bool:
    pid = receipt.get("pid")
    if type(pid) is not int or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _restart_app(
    app: Path, request_id: str, channel: str, timeout_seconds: int
) -> None:
    # AppleScript receives the path as an argv value, never interpolated code.
    # A normal quit lets Desktop save navigation and stop its owned services.
    subprocess.run(
        [
            "/usr/bin/osascript",
            "-e",
            "on run argv\n if application (item 1 of argv) is running then\n tell application (item 1 of argv) to quit\n end if\nend run",
            str(app),
        ],
        check=True,
        capture_output=True,
        timeout=min(timeout_seconds, 30),
    )
    # A running QA/copy can share the bundle id. LaunchServices must start
    # this installation after quit, rather than activate that other process.
    launch = ["/usr/bin/open", "-n", "-a", str(app)]
    if os.environ.get("LOOPX_BIN"):
        launch += ["--env", f"LOOPX_BIN={os.environ['LOOPX_BIN']}"]
    subprocess.run(
        [
            *launch,
            "--args",
            f"--loopx-upgrade-request={request_id}",
            f"--loopx-upgrade-channel={channel}",
        ],
        check=True,
        capture_output=True,
        timeout=min(timeout_seconds, 30),
    )


def _upgrade_desktop(plan: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    app = Path(plan["application"])
    request_id = uuid.uuid4().hex
    receipt = _receipt_directory() / f"{request_id}.json"
    started = time.monotonic()
    _restart_app(app, request_id, plan["channel"], timeout_seconds)
    last = None
    try:
        while time.monotonic() - started < timeout_seconds:
            try:
                if receipt.stat().st_size <= 16 * 1024:
                    candidate = json.loads(receipt.read_text(encoding="utf-8"))
                    if (
                        isinstance(candidate, dict)
                        and candidate.get("request_id") == request_id
                        and candidate.get("channel") == plan["channel"]
                    ):
                        last = candidate
            except (OSError, ValueError):
                pass
            if last:
                if last.get("phase") in {
                    "error",
                    "service_error",
                    "runtime_required",
                    "runtime_pairing_required",
                }:
                    return {
                        **plan,
                        "ok": False,
                        "status": "failed",
                        "readback": last,
                        "changes_applied": last.get("app_version")
                        != plan["app_version"],
                    }
                if (
                    last.get("phase") == "ready"
                    and last.get("update_checked") is True
                    and _running_receipt(last)
                    and isinstance(last.get("runtime_identity"), dict)
                ):
                    observed = desktop_installation_status(None, applications=(app,))[
                        "apps"
                    ]
                    if (
                        observed
                        and observed[0]["app_version"] == last.get("app_version")
                        and observed[0]["bundled_source_revision"]
                        == last.get("bundled_source_revision")
                    ):
                        return {
                            **plan,
                            "ok": True,
                            "status": "ready",
                            "readback": last,
                            "changes_applied": last["app_version"]
                            != plan["app_version"],
                        }
            time.sleep(0.5)
        return {
            **plan,
            "ok": False,
            "status": "readback_timeout",
            "readback": last,
            "reason": "App update/readiness could not be verified. Older Apps need a build with upgrade readback; inspect Recovery & updates.",
        }
    finally:
        receipt.unlink(missing_ok=True)


def finish_upgrade(
    payload: dict[str, Any],
    *,
    timeout_seconds: int,
    progress: Callable[[str], None] = lambda _stage: None,
) -> dict[str, Any]:
    identity = payload.get("execution", {}).get("installed_runtime_identity", {})
    if payload.get("ok") and not identity:
        payload["ok"] = False
        payload["upgrade_complete"] = False
        payload["recommended_action"] = (
            "CLI install finished but its runtime identity could not be read back. Run `loopx doctor --installation-only`."
        )
    desktop = payload["desktop_upgrade"]
    if desktop["status"] == "not_installed":
        payload["ok"] = bool(
            payload.get("ok") and payload.get("upgrade_complete", payload.get("ok"))
        )
        return payload
    execution = payload.get("execution", {})
    runtime_ready = payload.get("ok") or (
        execution.get("install_returncode") == 0
        and execution.get("doctor_returncode") == 0
    )
    if not runtime_ready:
        payload["desktop_upgrade"] = {
            **desktop,
            "status": "skipped_runtime_update_failed",
        }
        return payload
    try:
        progress(
            "Updating and restarting App; waiting for native update and service readback"
        )
        desktop = _upgrade_desktop(desktop, timeout_seconds)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        desktop = {
            **desktop,
            "ok": False,
            "status": "failed",
            "reason": type(error).__name__,
        }
    payload["desktop_upgrade"] = desktop
    payload["changes_applied"] = bool(
        payload.get("changes_applied") or desktop.get("changes_applied")
    )
    payload["upgrade_complete"] = (
        payload.get("upgrade_complete", payload.get("ok")) and desktop["ok"]
    )
    payload["ok"] = bool(payload.get("ok") and payload["upgrade_complete"])
    if not desktop["ok"]:
        payload["recommended_action"] = (
            "CLI installation completed; App upgrade is incomplete. Inspect Recovery & updates, then retry `loopx upgrade`."
        )
        payload["next_action"] = {
            "kind": "review_or_rollback",
            "command": "loopx upgrade",
            "mutating": True,
            "requires_explicit_approval": True,
            "reason": payload["recommended_action"],
        }
    return payload


def render_upgrade_markdown(payload: dict[str, Any]) -> str:
    from ..self_update import render_update_plan_markdown

    desktop = payload.get("desktop_upgrade", {})
    if not payload.get("execute_requested"):
        return (
            render_update_plan_markdown(payload)
            + f"\n- Desktop App: `{desktop.get('status')}`\n"
        )
    identity = payload.get("execution", {}).get("installed_runtime_identity", {})
    readback = desktop.get("readback") or {}
    lines = [
        "LoopX upgrade completed."
        if payload.get("ok")
        else "LoopX upgrade incomplete.",
        "",
        f"- CLI: {identity.get('package_version', 'unverified')} @ {identity.get('source_revision') or 'revision unavailable'}",
        f"- App: {desktop.get('status', 'not checked')}"
        + (
            f" · {readback['app_version']} · services ready"
            if desktop.get("status") == "ready"
            else ""
        ),
    ]
    if not payload.get("ok"):
        lines.append(
            f"- Next: {payload.get('recommended_action') or payload.get('error')}"
        )
        if readback.get("details", {}).get("code"):
            lines.append(f"- App error: {readback['details']['code']}")
    return "\n".join(lines) + "\n"
