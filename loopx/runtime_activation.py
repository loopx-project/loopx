"""Make an installed LoopX runtime serve LaunchAgent-managed services.

Replacing the release snapshot does not move the status/chat services that are
already running; this module owns that transition so the update boundary stays
about what was installed, not about how it starts serving.
"""

from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
from typing import Any
from xml.parsers.expat import ExpatError


class RuntimeActivationDeferred(Exception):
    """The installed runtime is ready, but its existing consumers must survive."""

    def __init__(self, reason: str, labels: list[str]) -> None:
        super().__init__(reason)
        self.labels = labels


class RuntimeActivationFailed(Exception):
    def __init__(self, restarted: list[str], pending: list[str]) -> None:
        super().__init__("service_restart_unverified")
        self.restarted = restarted
        self.pending = pending


def _chat_runtime_root(plist: Path) -> Path:
    """Read an explicit LaunchAgent route, including the installer's shell wrapper.

    Never execute a plist's shell or expand arbitrary shell expressions while
    discovering the state protected by an update. Unresolved routes stay running.
    """
    payload = plistlib.loads(plist.read_bytes())
    if not isinstance(payload, dict):
        raise ValueError("chat_service_route_unresolved")
    arguments = payload.get("ProgramArguments")
    if not isinstance(arguments, list) or not all(
        isinstance(arg, str) for arg in arguments
    ):
        raise ValueError("chat_service_route_unresolved")
    if arguments and Path(arguments[0]).name in {"sh", "bash", "zsh"}:
        for index, argument in enumerate(arguments[1:], 1):
            if (
                argument.startswith("-")
                and "c" in argument[1:]
                and index + 1 < len(arguments)
            ):
                arguments = shlex.split(arguments[index + 1])
                break
        else:
            raise ValueError("chat_service_route_unresolved")
        if "exec" not in arguments:
            raise ValueError("chat_service_route_unresolved")
        arguments = arguments[arguments.index("exec") + 1 :]
    candidates = [
        argument.split("=", 1)[1]
        if argument.startswith("--runtime-root=")
        else arguments[index + 1]
        if index + 1 < len(arguments)
        else ""
        for index, argument in enumerate(arguments)
        if argument == "--runtime-root" or argument.startswith("--runtime-root=")
    ]
    if (
        len(candidates) != 1
        or not candidates[0]
        or any(c in candidates[0] for c in "$`\n")
    ):
        raise ValueError("chat_service_route_unresolved")
    root = Path(candidates[0]).expanduser()
    if not root.is_absolute():
        raise ValueError("chat_service_route_unresolved")
    return root.resolve()


def restart_managed_loopx_services() -> list[str]:
    """Switch managed macOS services only with no accepted Chat work outstanding.

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
    chat_plists: list[Path] = []
    for plist in sorted(agents_dir.glob("*.plist")):
        stem = plist.stem.lower()
        if "loopx" not in stem and "goal-harness" not in stem:
            continue
        if not (stem.endswith(".status") or stem.endswith(".chat")):
            continue
        labels.append(plist.stem)
        if stem.endswith(".chat"):
            chat_plists.append(plist)
    restarted: list[str] = []
    uid = os.getuid() if hasattr(os, "getuid") else 0
    from .chat_ingress import (
        chat_runtime_activation_lock,
        chat_runtime_activation_state,
    )

    try:
        roots = sorted({_chat_runtime_root(plist) for plist in chat_plists})
        with ExitStack() as locks:
            for root in roots:
                locks.enter_context(
                    chat_runtime_activation_lock(root, timeout_seconds=2.0)
                )
            for root in roots:
                if chat_runtime_activation_state(root)["nonterminal_turns"]:
                    raise RuntimeActivationDeferred("chat_work_pending", labels)
            # Keep admission fenced until launchctl has replaced the old process.
            # Existing workers need no new lock: any accepted queued/active work
            # was checked above, including completing and orphaned queued Turns.
            for label in labels:
                try:
                    result = subprocess.run(
                        ["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=30,
                    )
                    if result.returncode != 0:
                        raise RuntimeActivationFailed(
                            restarted, [s for s in labels if s not in restarted]
                        )
                    restarted.append(label)
                except (OSError, subprocess.SubprocessError) as exc:
                    raise RuntimeActivationFailed(
                        restarted, [s for s in labels if s not in restarted]
                    ) from exc
    except RuntimeActivationDeferred:
        raise
    except (
        OSError,
        ValueError,
        plistlib.InvalidFileException,
        ExpatError,
        TimeoutError,
    ) as exc:
        raise RuntimeActivationDeferred("chat_state_unverified", labels) from exc
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
    try:
        return {
            "restarted_services": restart_managed_loopx_services(),
            "restart_status": "restarted",
            "activation_pending": False,
        }
    except RuntimeActivationDeferred as exc:
        return {
            "restarted_services": [],
            "deferred_services": exc.labels,
            "restart_status": "deferred",
            "restart_reason": str(exc),
            "activation_pending": True,
        }
    except RuntimeActivationFailed as exc:
        return {
            "restarted_services": exc.restarted,
            "deferred_services": exc.pending,
            "restart_status": "failed",
            "restart_reason": str(exc),
            "activation_pending": True,
        }
