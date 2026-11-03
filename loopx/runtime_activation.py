"""Make an installed LoopX runtime serve LaunchAgent-managed services.

Replacing the release snapshot does not move the status/chat services that are
already running; this module owns that transition so the update boundary stays
about what was installed, not about how it starts serving.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import plistlib
import re
import shlex
import signal
import subprocess
import sys
import time
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


def _managed_service_plists() -> list[Path]:
    return [
        plist for plist in sorted((Path.home() / "Library/LaunchAgents").glob("*.plist"))
        if ("loopx" in plist.stem.lower() or "goal-harness" in plist.stem.lower())
        and plist.stem.lower().endswith((".status", ".chat"))
    ]


def _managed_service_state(plist: Path, *, label: str | None = None,
        payload: dict[str, Any] | None = None) -> tuple[bool | None, int | None]:
    result = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{label or plist.stem}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
    )
    if result.returncode != 0:
        return None, None
    payload = payload if payload is not None else plistlib.loads(plist.read_bytes())
    if not isinstance(payload, dict):
        return False, None
    configured = payload.get("ProgramArguments")
    arguments = re.findall(r"^\s*arguments = \{\n(.*?)^\s*\}", result.stdout, re.MULTILINE | re.DOTALL)
    if (not isinstance(configured, list) or not all(isinstance(arg, str) for arg in configured)
            or len(arguments) != 1
            or [line.strip() for line in arguments[0].splitlines()] != configured):
        return False, None  # An edited plist is not proof of the running process's route.
    pids = re.findall(r"^\s*pid = ([0-9]+)\s*$", result.stdout, re.MULTILINE)
    return True, int(pids[0]) if len(pids) == 1 else None


def _managed_service_pid(plist: Path, *, label: str | None = None) -> int | None:
    return _managed_service_state(plist, label=label)[1]


@contextmanager
def managed_chat_activation_guard(
    plists: list[Path], *, expected_services: dict[Path, tuple[str, int]] | None = None,
):
    """Keep accepted Chat work and new ingress safe through a client restart.

    Account-provider handoffs and runtime updates share the same store owner;
    an empty Session summary is not sufficient evidence of an idle service.
    The caller must still verify its loaded service/PID before stopping it.
    """
    from .chat_ingress import chat_runtime_activation_lock, chat_runtime_activation_state

    labels = [plist.stem for plist in plists]
    roots = sorted({_chat_runtime_root(plist) for plist in plists})
    with ExitStack() as locks:
        for root in roots:
            locks.enter_context(chat_runtime_activation_lock(root, timeout_seconds=2.0))
        for plist, (label, pid) in (expected_services or {}).items():
            if plist not in plists or _managed_service_pid(plist, label=label) != pid:
                raise ValueError("chat_service_route_unresolved")
        for root in roots:
            if chat_runtime_activation_state(root)["nonterminal_turns"]:
                from .chat_runtime import ChatRuntimeController
                from .chat_store import ChatSessionStore

                # Data/lifecycle recovery only: this controller opens no adapter,
                # starts no queue worker and never resends accepted input. Keep
                # unrelated completion/compaction out of this scoped repair.
                store = ChatSessionStore(root, startup_maintenance=False)
                recovery = ChatRuntimeController(store=store, codex_bin="codex")
                for session in store.list_sessions():
                    recovery.recover_exited_turn(str(session["session_id"]))
                if chat_runtime_activation_state(root)["nonterminal_turns"]:
                    raise RuntimeActivationDeferred("chat_work_pending", labels)
        yield


def replace_managed_loopx_chat(
    host: str, port: int, *, timeout_seconds: float = 5.0,
    restart_managed: bool = False,
) -> str:
    """Replace the exact managed listener only while its accepted work is idle.

    The listener's live manager supplies its route, never the new command's
    potentially different/empty runtime. Keep the existing ingress fence through
    SIGTERM and port release. An unresolved manager or store leaves it running.
    """
    from .chat_server import is_loopback_host
    from .chat_ingress import chat_runtime_activation_lock, chat_runtime_activation_state
    from .dashboard_launcher import (
        _is_same_user_loopx_chat_process, _listener_pids, _probe_existing_chat,
    )

    if not is_loopback_host(host):
        raise ValueError("managed LoopX Chat replacement requires an explicit loopback host")
    existing = _probe_existing_chat(host, port)
    if existing == "unavailable":
        return existing
    if existing == "foreign":
        raise RuntimeError(f"port {port} is owned by an unverified service; stopped none")
    pids = _listener_pids(port)
    if len(pids) != 1 or pids[0] == os.getpid() or not _is_same_user_loopx_chat_process(pids[0]):
        raise RuntimeError(f"expected one verified same-user LoopX Chat listener on port {port}; stopped none")
    pid = pids[0]
    labels: list[str] = []
    try:
        owners = [
            plist for plist in _managed_service_plists()
            if sys.platform == "darwin" and plist.stem.lower().endswith(".chat")
            and _managed_service_pid(plist) == pid
        ]
        if len(owners) != 1:
            raise ValueError("chat_service_route_unresolved")
        plist = owners[0]
        labels = [plist.stem]
        root = _chat_runtime_root(plist)
        with chat_runtime_activation_lock(root, timeout_seconds=2.0):
            if chat_runtime_activation_state(root)["nonterminal_turns"]:
                raise RuntimeActivationDeferred("chat_work_pending", labels)
            if (_managed_service_pid(plist) != pid or _chat_runtime_root(plist) != root
                    or _listener_pids(port) != [pid]
                    or not _is_same_user_loopx_chat_process(pid)):
                raise ValueError("chat_service_route_unresolved")
            # Signal the pinned PID, not a second lookup's possible replacement.
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + max(0.1, timeout_seconds)
            while time.monotonic() < deadline:
                if pid not in _listener_pids(port):
                    if restart_managed:
                        # Keep the same ingress fence through the original
                        # manager's restart. Desktop never starts a replacement
                        # consumer or infers ownership from a Python template.
                        started = subprocess.run(
                            ["launchctl", "kickstart", f"gui/{os.getuid()}/{plist.stem}"],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=5,
                        )
                        if started.returncode != 0:
                            raise RuntimeActivationFailed([], labels)
                    return existing
                time.sleep(0.1)
            raise RuntimeError("the managed Chat listener did not stop after SIGTERM; no stronger signal was sent")
    except RuntimeActivationDeferred:
        raise
    except (OSError, ValueError, plistlib.InvalidFileException, ExpatError,
            TimeoutError, subprocess.SubprocessError) as exc:
        raise RuntimeActivationDeferred("chat_state_unverified", labels) from exc


def activate_runtime_services(*, service: str = "all", port: int = 8767,
        runtime_executable: str | None = None) -> dict[str, Any]:
    """Retry installed-service adoption without reinstalling or running work.

    The desktop Chat caller uses the existing verified listener/manager/store
    owner. A version mismatch is not permission to kill a busy worker.
    Readiness and the selected artifact's identity still need a fresh HTTP read.
    """
    if service not in {"all", "chat"} or not 1 <= port <= 65535:
        raise ValueError("select all or chat with a valid loopback port")
    payload: dict[str, Any] = {
        "schema_version": "loopx_runtime_service_activation_v1",
        "service": service, "readiness_verified": False,
    }
    if sys.platform != "darwin":
        return {**payload, "ok": False, "status": "unsupported",
            "reason": "managed_service_activation_unavailable"}
    try:
        if runtime_executable is not None:
            if service != "all":
                raise ValueError("service_runtime_route_unverified")
            from .hosts.managed_service_runtime import align_managed_runtime
            return {**payload, "ok": True, **align_managed_runtime(runtime_executable)}
        if service == "chat":
            observed = replace_managed_loopx_chat("127.0.0.1", port, restart_managed=True)
            status = "not_running" if observed == "unavailable" else "restart_requested"
        else:
            restarted = restart_managed_loopx_services()
            payload["restarted_services"] = restarted
            status = "restart_requested" if restarted else "not_running"
        return {**payload, "ok": True, "status": status}
    except RuntimeActivationDeferred as exc:
        return {**payload, "ok": False, "status": "deferred", "reason": str(exc)}
    except (RuntimeActivationFailed, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        # Public diagnostics carry a coarse cause, never manager commands,
        # local paths or the contents of accepted messages.
        reason = str(exc) if str(exc) in {
            "service_runtime_unverified", "service_runtime_route_unverified",
            "service_runtime_alignment_failed", "service_runtime_rollback_failed",
        } else "chat_service_unverified"
        return {**payload, "ok": False, "status": "failed", "reason": reason}


def restart_managed_loopx_services(*, plists: list[Path] | None = None) -> list[str]:
    """Switch managed macOS services only with no accepted Chat work outstanding.

    After ``loopx update`` replaces the installed release, running status/chat
    services still belong to the previous release. Restarting the managed
    LaunchAgents makes them run the new ``loopx`` immediately, so the dashboard
    and desktop shell keep working without a release-identity mismatch.
    """
    if sys.platform != "darwin":
        return []
    labels: list[str] = []
    chat_plists: list[Path] = []
    for plist in _managed_service_plists() if plists is None else plists:
        stem = plist.stem.lower()
        labels.append(plist.stem)
        if stem.endswith(".chat"):
            chat_plists.append(plist)
    restarted: list[str] = []
    uid = os.getuid() if hasattr(os, "getuid") else 0
    try:
        with managed_chat_activation_guard(chat_plists):
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
    except RuntimeActivationDeferred as exc:
        # The shared Chat guard names its participants; an update still defers
        # the complete original status/chat activation batch.
        raise RuntimeActivationDeferred(str(exc), labels) from exc
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
        from .hosts.managed_service_runtime import align_managed_runtime, has_runtime_binding
        if sys.platform == "darwin" and has_runtime_binding():
            # Preserve the existing pending-work result even when the new
            # installation cannot yet be qualified. Alignment rechecks under
            # its own lock before any manager mutation.
            with managed_chat_activation_guard([p for p in _managed_service_plists() if p.stem.endswith(".chat")]):
                pass
            if installed_bin is None:
                from .doctor import current_script_invocation_path
                installed_bin = current_script_invocation_path()
            if installed_bin is None:
                raise RuntimeActivationDeferred("service_runtime_unverified", ["com.loopx.status", "com.loopx.chat"])
            aligned = align_managed_runtime(str(installed_bin))
            other = [p for p in _managed_service_plists() if p.stem not in {"com.loopx.status", "com.loopx.chat"}]
            restarted = restart_managed_loopx_services(plists=other) if other else []
            return {"restarted_services": [*aligned["restarted_services"], *restarted],
                "restart_status": "restarted", "activation_pending": False}
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
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return {"restarted_services": [], "restart_status": "failed",
            "restart_reason": "service_runtime_alignment_failed", "activation_pending": True}
