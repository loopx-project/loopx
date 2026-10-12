"""Make an installed LoopX runtime serve LaunchAgent-managed services.

Replacing the release snapshot does not move the status/chat services that are
already running; this module owns that transition so the update boundary stays
about what was installed, not about how it starts serving.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Any


def restart_managed_loopx_services() -> list[str]:
    """Best-effort restart of user LaunchAgent-managed LoopX services on macOS.

    After ``loopx update`` replaces the installed release, running status/chat
    services still belong to the previous release. Restarting the managed
    LaunchAgents makes them run the new ``loopx`` immediately, so the dashboard
    and desktop shell keep working without a release-identity mismatch.
    """
    if sys.platform != "darwin":
        return []
    agents_dir = Path.home() / "Library" / "LaunchAgents"
    if not agents_dir.is_dir():
        return []
    labels: list[str] = []
    for plist in sorted(agents_dir.glob("*.plist")):
        stem = plist.stem.lower()
        if "loopx" not in stem and "goal-harness" not in stem:
            continue
        if not (stem.endswith(".status") or stem.endswith(".chat")):
            continue
        labels.append(plist.stem)
    restarted: list[str] = []
    uid = os.getuid() if hasattr(os, "getuid") else 0
    for label in labels:
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        if result.returncode == 0:
            restarted.append(label)
    return restarted


def restart_services_for_runtime_activation(
    *,
    changes_applied: bool,
    runtime_ready: bool,
    installed_bin: Path | None = None,
) -> dict[str, Any]:
    """Report and perform the restart that moves services onto the new release.

    Restarting is what makes the installed behavior actually reach the operator,
    so it follows the runtime install plus its core doctor readback instead of
    the health of unrelated enabled extension providers. Tying the restart to
    full extension health left status/chat silently serving the previous release
    while the update reported a rollback-only outcome.

    Archive installs can select a separate bin directory. Those installs do not
    own the user's default managed services. Compare directories, not launcher
    targets: a symlink still belongs to the directory the installer updates.
    Package-manager callers retain their existing activation path.
    """

    if not (changes_applied and runtime_ready):
        return {
            "restarted_services": [],
            "restart_status": "skipped_runtime_not_activated",
        }
    if (
        installed_bin is not None
        and installed_bin.parent.resolve() != (Path.home() / ".local/bin").resolve()
    ):
        return {
            "restarted_services": [],
            "restart_status": "skipped_custom_installation",
        }
    return {
        "restarted_services": restart_managed_loopx_services(),
        "restart_status": "restarted",
    }
