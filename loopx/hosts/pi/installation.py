"""Managed Pi extension locations and read-only installation state."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ...slash_command_files import managed_marker as _managed_marker
from . import extension_source as pi_extension_source
from . import runtime_source as pi_runtime_source


def _pi_agent_dir(user_home: str | None = None) -> Path:
    if user_home is not None:
        return Path(user_home).expanduser().resolve() / ".pi" / "agent"
    configured = os.environ.get("PI_CODING_AGENT_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / ".pi" / "agent"


def _pi_extension_root(project_root: Path, *, scope: str, agent_dir: Path) -> Path:
    if scope == "user":
        return agent_dir / "extensions" / "loopx"
    return project_root / ".pi" / "extensions"


def _pi_extension_path(extension_root: Path, *, scope: str) -> Path:
    # Pi discovers direct project files, but only index.ts/index.js (or a
    # package manifest) inside a global extension subdirectory.
    return extension_root / ("index.ts" if scope == "user" else "loopx-goal.ts")


def _pi_runtime_path(extension_root: Path) -> Path:
    return extension_root / "pi-goal-loop-runtime.mjs"


def inspect_pi_installations(
    *, pi_project: str | None = None, pi_user_home: str | None = None
) -> dict[str, Any]:
    """Read both Pi discovery locations without mutating either installation."""
    project_root = Path(pi_project or ".").expanduser().resolve()
    agent_dir = _pi_agent_dir(pi_user_home)
    scopes: dict[str, dict[str, Any]] = {}
    for scope in ("project", "user"):
        root = _pi_extension_root(project_root, scope=scope, agent_dir=agent_dir)
        extension = _pi_extension_path(root, scope=scope)
        runtime = _pi_runtime_path(root)
        files = (extension, runtime)
        present = [path.exists() for path in files]
        if not any(present):
            status = "absent"
        elif any(present) and not all(present):
            status = "partial"
        elif any(
            _managed_marker(command="/loopx", surface=surface)
            not in path.read_text(encoding="utf-8")
            for path, surface in ((extension, "pi-extension"), (runtime, "pi-extension-runtime"))
        ):
            status = "user_owned"
        elif (
            extension.read_text(encoding="utf-8") == pi_extension_source()
            and runtime.read_text(encoding="utf-8") == pi_runtime_source()
        ):
            status = "current"
        else:
            status = "stale"
        scopes[scope] = {
            "status": status,
            "extension_path": str(extension),
            "runtime_path": str(runtime),
        }

    project_entry = Path(scopes["project"]["extension_path"]).exists()
    user_entry = Path(scopes["user"]["extension_path"]).exists()
    duplicate_load = project_entry and user_entry
    if duplicate_load:
        location = "dual-scope"
    elif any(row["status"] in {"stale", "partial", "user_owned"} for row in scopes.values()):
        location = "stale"
    elif scopes["user"]["status"] == "current":
        location = "user-global"
    elif scopes["project"]["status"] == "current":
        location = "project-local"
    else:
        location = "absent"
    return {
        "ok": all(row["status"] in {"absent", "current"} for row in scopes.values())
        and not duplicate_load,
        "schema_version": "loopx_pi_installation_readback_v0",
        "location": location,
        "scopes": scopes,
        "duplicate_load_warning": (
            "Pi discovers both project and user LoopX entries; uninstall one scope to avoid duplicate command/tool registration."
            if duplicate_load else None
        ),
    }


def render_pi_installation_markdown(payload: dict[str, Any]) -> str:
    lines = ["# Pi installation readback", "", f"Location: `{payload['location']}`"]
    for scope, row in payload["scopes"].items():
        lines.extend(
            [
                f"- {scope}: `{row['status']}`",
                f"  - extension: `{row['extension_path']}`",
                f"  - runtime: `{row['runtime_path']}`",
            ]
        )
    if payload["duplicate_load_warning"]:
        lines.append(f"\n{payload['duplicate_load_warning']}")
    return "\n".join(lines) + "\n"
