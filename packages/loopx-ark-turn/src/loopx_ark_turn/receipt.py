"""Private host receipt; never a second work, acceptance or quota authority."""
from __future__ import annotations

from pathlib import Path
from enum import StrEnum
from typing import Any
import json
import os
import tempfile

from .config import AdapterError, digest


class Stage(StrEnum):
    PREPARED = "prepared"
    CREATING_AGENT = "creating_agent"
    AGENT_CREATED = "agent_created"
    CREATING_SESSION = "creating_session"
    SESSION_CREATED = "session_created"
    SENDING_INPUT = "sending_input"
    RUNNING = "running"
    TERMINAL = "terminal"
    FINISHED = "finished"


class ToolStage(StrEnum):
    EXECUTING = "executing"
    SENDING = "sending"
    SENT = "sent"


class CleanupStatus(StrEnum):
    ABSENT = "absent"
    PENDING = "pending"
    RECONCILE_REQUIRED = "reconcile_required"


_STAGES = list(Stage)


class Receipt:
    def __init__(self, state_dir: Path, turn_key: str) -> None:
        self.path = state_dir / (digest(turn_key) + ".json")
        self.data: dict[str, Any] = {}

    def load(self, binding: str) -> None:
        if self.path.exists():
            self.read()
            if self.data.get("binding") != binding:
                raise AdapterError("host_receipt_binding_mismatch")
        else:
            self.data = {"schema_version": "loopx_ark_turn_receipt_v0", "binding": binding, "stage": "prepared", "tools": {}}
            self.save()

    def read(self) -> None:
        self.data = json.loads(self.path.read_text())
        if self.data.get("schema_version") != "loopx_ark_turn_receipt_v0":
            raise AdapterError("host_receipt_schema_mismatch")
        try:
            Stage(self.data["stage"])
            for tool in self.data.get("tools", {}).values():
                ToolStage(tool["stage"])
            for status in self.data.get("cleanup", {}).values():
                CleanupStatus(status)
        except (ValueError, KeyError, TypeError) as exc:
            raise AdapterError("host_receipt_state_invalid") from exc

    def update(self, **fields: Any) -> None:
        if "stage" in fields:
            current, following = Stage(self.data["stage"]), Stage(fields["stage"])
            if current != following and _STAGES.index(following) != _STAGES.index(current) + 1:
                raise AdapterError("host_receipt_transition_invalid")
        self.data.update(fields)
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".receipt-")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.data, f, ensure_ascii=False, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    def projection(self) -> dict[str, Any]:
        return {key: self.data.get(key) for key in (
            "schema_version", "stage", "error", "provider_usage", "cleanup", "resource_label",
            "tool_boundary_revision", "session_boundary_digest", "creation_rejection",
        )} | {"tool_calls": len(self.data.get("tools", {})),
              "builtin_tool_calls": len(self.data.get("builtin_tools", {})), "has_candidate": "candidate" in self.data} | (
                  {"cleanup_interrupt_attempted": self.data["cleanup_interrupt_attempted"]}
                  if "cleanup_interrupt_attempted" in self.data else {})
