"""Compose machine defaults into omitted native CLI options, without Goal authority.

The machine-configuration transaction owns persistence; packet semantics stay
with the existing TypeScript transport. This layer performs native path IO.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat

from ..control_plane.turn_driver.codex_context import context_delivery_for_arguments


def _private_transport_root(runtime_root: Path) -> str:
    """Create only our leaf; never repair permissions or follow a replaced leaf."""
    if os.name != "posix":
        raise ValueError("native context continuity is unavailable on this platform")
    runtime_root = runtime_root.expanduser().resolve()
    runtime_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = runtime_root / "context-delivery"
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("machine context transport requires a private physical directory owned by this user")
    return str(root)


def apply_context_machine_options(
    args: argparse.Namespace, *, registry_path: Path, runtime_root_arg: str | None,
    heartbeat: bool = False,
) -> None:
    """Resolve each invocation afresh; explicit options skip this default lookup.

    Saved capture pages stay registry/configuration independent. Other native
    commands share the machine-configuration owner's registry runtime route.
    No hook, trust, tracing or automation is installed.
    """
    explicit = getattr(args, "context_delivery", None)
    if explicit == "off":
        return
    args.context_delivery = explicit or "off"
    # Existing benchmark workers retain their explicit opt-out. A machine
    # default must not silently change a frozen worker's disabled transport.
    # An environment on alone still supplies no activation or host proof.
    if explicit is None and os.environ.get("LOOPX_CONTEXT_DELIVERY") == "off":
        return
    command = getattr(args, "quota_command", None)
    if not heartbeat and command not in {"should-run", "context-config", "context-project", "context-hook", "context-read"}:
        return
    if command == "context-read" and any(getattr(args, key, None) for key in (
        "capture_file", "capture_sha256", "codex_context_dir", "codex_context_root"
    )):
        return
    from ..paths import effective_runtime_root
    runtime_root = effective_runtime_root(registry_path, runtime_root_arg)
    if explicit is None:
        from ..capabilities.machine_configuration.builtins import build_builtin_machine_configuration_registry
        from ..capabilities.machine_configuration.store import read_machine_configuration

        configuration = read_machine_configuration(runtime_root, registry=build_builtin_machine_configuration_registry())
        value = (configuration or {}).get("namespaces", {}).get("context_delivery")
        if value is None or value["mode"] == "off":
            return
    # Recovery does not activate a run mode or create a missing session.
    if command != "context-read":
        args.context_delivery = "on"
        if not context_delivery_for_arguments(args, codex_machine_preference=explicit is None)["enabled"]:
            args.context_delivery = "off"
            return
        if command == "should-run" and args.format != "json":
            raise ValueError("native Codex context delivery needs JSON and a capture destination")
    if explicit is None:
        args._context_delivery_machine_runtime = str(runtime_root.expanduser().resolve())
    # An off/on round trip can miss lifecycle events. Store replacement
    # invalidates reuse even when its content hash returns to the old value.
    # This metadata only narrows reuse; it is not retention or work authority.
    from ..capabilities.machine_configuration.store import machine_configuration_store_path
    import hashlib

    try:
        info = machine_configuration_store_path(runtime_root).stat()
        generation = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
        args._context_delivery_generation = hashlib.sha256(repr(generation).encode()).hexdigest()
    except FileNotFoundError:
        args._context_delivery_generation = None
    location_missing = not (getattr(args, "codex_context_dir", None) or getattr(args, "codex_context_root", None)
        or getattr(args, "decision_handoff_socket", None))
    capture_missing = (heartbeat or command == "should-run") and not (
        getattr(args, "decision_output_dir", None) or getattr(args, "decision_output_root", None)
    )
    if command == "context-read":
        if location_missing:
            args.codex_context_root = str(runtime_root.expanduser().resolve() / "context-delivery")
        return
    if location_missing or capture_missing:
        root = _private_transport_root(runtime_root)
        if location_missing:
            args.codex_context_root = root
        if capture_missing:
            args.decision_output_root = root
