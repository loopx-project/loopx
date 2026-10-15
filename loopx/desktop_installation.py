"""Read-only App/runtime pairing evidence; CLI health does not qualify the App."""

from __future__ import annotations

import json
from pathlib import Path
import plistlib
import re
import sys
from typing import Any


def desktop_installation_status(
    installed_revision: Any,
    *,
    applications: tuple[Path, ...] | None = None,
) -> dict[str, Any]:
    if applications is None:
        applications = (
            (Path("/Applications/LoopX.app"), Path.home() / "Applications/LoopX.app")
            if sys.platform == "darwin"
            else ()
        )
    rows = []
    for app in applications:
        if not app.exists():
            continue
        version = revision = None
        try:
            info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
            identity = json.loads(
                (app / "Contents/Resources/runtime/identity.json").read_text(encoding="utf-8")
            )
            if isinstance(info, dict):
                value = info.get("CFBundleShortVersionString")
                if isinstance(value, str) and re.fullmatch(
                    r"[0-9A-Za-z.+-]{1,80}", value
                ):
                    version = value
            if (
                isinstance(identity, dict)
                and identity.get("schema_version") == "desktop_runtime_bundle_v1"
            ):
                value = identity.get("source_revision")
                if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value):
                    revision = value
        except (OSError, ValueError, plistlib.InvalidFileException):
            pass
        valid_installed = isinstance(installed_revision, str) and re.fullmatch(
            r"[0-9a-f]{40}", installed_revision
        )
        pairing = (
            "unknown"
            if not revision or not valid_installed
            else "paired"
            if revision == installed_revision
            else "mismatch"
        )
        rows.append(
            {
                "app_version": version,
                "bundled_source_revision": revision,
                "pairing": pairing,
            }
        )
    status = (
        "not_detected"
        if not rows
        else "mismatch"
        if any(r["pairing"] == "mismatch" for r in rows)
        else "unknown"
        if any(r["pairing"] == "unknown" for r in rows)
        else "paired"
    )
    return {
        "schema_version": "loopx_desktop_installation_v0",
        "status": status,
        "apps": rows,
        "scope": "standard_macos_install_locations",
        "running_app_verified": False,
        "recommended_action": (
            "App bundle and CLI source revisions differ; this does not establish version order or compatibility. "
            "Current App builds automatically discover a verified runtime and can retain a newer CLI. "
            "Reopen the App and check its startup diagnostics and running service identity. "
            "Preserve an explicit LOOPX_BIN developer pin."
            if status == "mismatch"
            else "App/CLI source identity could not be fully read; runtime compatibility is unknown. "
            "Reopen the App to use normal runtime discovery, then check its startup diagnostics and running service identity. "
            "Preserve an explicit LOOPX_BIN developer pin."
            if status == "unknown"
            else None
        ),
    }
