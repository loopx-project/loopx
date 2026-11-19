"""Native host adaptation of already-authorized manager evidence.

The shared capture reader owns integrity and JSON Pointer selection. This
provider does not create a live reader, grant or retained-context baseline.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any


NATIVE_MANAGER_READ_INSTRUCTION = (
    "This native manager has no loopx_manager_read or loopx_context_read custom tool. "
    "When Core includes context_read, current Goal/Todo/attention facts remain inline; "
    "delivery histories marked not_read are in the same complete private snapshot. "
    "Use their exact read_reference.command through the host's already-permitted shell "
    "before relying on that history. JSON Pointer selects an address in that snapshot; "
    "use the supplied index command only when the address is unknown. Follow reader "
    "pages through EOF for a selected section, preserving errors and source identity. "
    "After context loss use the same recovery reference, never resend the user request "
    "or rerun admission. A capture, source-read status or EOF is not model retention, "
    "a fresh live observation or execution authority. If reading is denied or unavailable, "
    "report that exact unread coverage; do not infer no progress or expand permissions.\n\n"
)


def _evidence_bytes(value: object) -> int:
    # Match the frozen evidence serialization in Chat's real manager input.
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def native_manager_context(context: dict[str, Any], adapter: object, *, runtime_root: Path,
                           capture_root: Path) -> dict[str, Any]:
    """Move only known supporting histories; unknown/current facts stay whole.

    Text-only hosts, explicit off, malformed configuration and unsupported private
    IO preserve the incumbent complete inline input. Tool approvals remain native.
    """
    from .antigravity_adapter import AntigravityAdapter

    if (not isinstance(adapter, AntigravityAdapter) or adapter.runtime_profile != "trusted_owner"
            or getattr(adapter, "_read_only_review", False) or "context_read" in context):
        return context
    goals = context.get("goals")
    if not isinstance(goals, list):
        return context
    histories = {
        index: row["recent_delivery_history"]
        for index, row in enumerate(goals)
        if isinstance(row, dict) and isinstance(row.get("recent_delivery_history"), dict)
        and row["recent_delivery_history"].get("status") == "read"
        and isinstance(row["recent_delivery_history"].get("deliveries"), list)
        and row["recent_delivery_history"]["deliveries"]
    }
    if not histories or os.environ.get("LOOPX_CONTEXT_DELIVERY") == "off":
        return context
    from ..machine_configuration.builtins import build_builtin_machine_configuration_registry
    from ..machine_configuration.store import read_machine_configuration
    from ...control_plane.turn_driver.decision_capture import (
        capture_context_projection, context_capture_read_command,
        private_capture_uid, write_context_read_reference,
    )

    directory = None
    try:
        registry = build_builtin_machine_configuration_registry()
        configuration = read_machine_configuration(runtime_root, registry=registry, namespace_ids=("context_delivery",))
        setting = (configuration or {}).get("namespaces", {}).get("context_delivery")
        if setting is None:
            setting = registry.resolve("context_delivery").public_descriptor()["configuration_template"]
        if setting.get("antigravity_mode", "on") == "off":
            return context
        # Even the reader instructions alone must fit inside the removed text.
        # Avoid private capture IO entirely for common short histories.
        inline_bytes = {index: _evidence_bytes({"status": history["status"], "deliveries": history["deliveries"]})
                        for index, history in histories.items()}
        reader_cost = len(NATIVE_MANAGER_READ_INSTRUCTION.encode("utf-8"))
        if sum(inline_bytes.values()) <= reader_cost:
            return context
        private_capture_uid()
        # A new 0700 directory in Chat's existing private store, not the selected
        # project's checkout. Do not change another directory's permissions.
        directory = Path(tempfile.mkdtemp(prefix="manager-context-", dir=capture_root))
        recovery = capture_context_projection(context, directory=directory)
        path, digest = Path(recovery["path"]), recovery["sha256"]
        reference = write_context_read_reference(path, digest)
        rows = list(goals)
        savings = 0
        for index, history in histories.items():
            pointer = f"/goals/{index}/recent_delivery_history"
            if "read_reference" in history:
                continue  # Preserve a history with an existing binding.
            read = {"pointer": pointer, "command": context_capture_read_command(
                path, digest, reference_file=reference, select=[pointer])}
            deferred = {"status": "not_read", "deliveries": [], "read_reference": read}
            saved = inline_bytes[index] - _evidence_bytes(deferred)
            if saved > 0:
                savings += saved
                rows[index] = {**goals[index], "recent_delivery_history": {**history, **deferred}}
        reader = {
            "reference_file": str(reference),
            "recovery_command": context_capture_read_command(path, digest, reference_file=reference),
            "index_command": context_capture_read_command(path, digest, reference_file=reference,
                list_fields=True, list_depth=2),
        }
        if savings <= reader_cost + _evidence_bytes({"context_read": reader}):
            shutil.rmtree(directory)
            return context
        return {**context, "goals": rows, "context_read": reader}
    except (OSError, ValueError, TypeError):
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
        return context
