"""Owner-local Agent endpoint registry for LoopX Chat."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any

from .chat_store import utc_now
from .file_lock import exclusive_file_lock, write_json_atomic
from .kiro_cli_goal_mode import KIRO_CLI_CHAT_AGENT_ID


CHAT_ENDPOINT_REGISTRY_SCHEMA_VERSION = "loopx_chat_endpoint_registry_v1"
CHAT_ENDPOINT_SCHEMA_VERSION = "loopx_chat_endpoint_v1"
# Built-in agent ids. An owner-local endpoint may not claim one, or the
# registry row would silently shadow the built-in adapter the capability list
# already advertises.
RESERVED_AGENT_IDS = {
    "codex",
    "claude-code",
    "anthropic-api",
    "openai-api",
    KIRO_CLI_CHAT_AGENT_ID,
}
SUPPORTED_TRANSPORTS = {"stdio"}
SUPPORTED_LOCATIONS = {"local", "remote"}
SUPPORTED_TRUST_SCOPES = {"read_only"}

_OPAQUE_ID = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def _compact_id(value: Any, *, field: str) -> str:
    token = str(value or "").strip()
    if not _OPAQUE_ID.fullmatch(token):
        raise ValueError(f"{field} must be a compact opaque id")
    return token


def _compact_label(value: Any, *, field: str, limit: int = 120) -> str:
    label = " ".join(str(value or "").split())[:limit].strip()
    if not label:
        raise ValueError(f"{field} is required")
    return label


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    write_json_atomic(
        path,
        payload,
        mode=0o600,
        directory_mode=0o700,
        separators=(",", ":"),
        indent=None,
    )


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": CHAT_ENDPOINT_REGISTRY_SCHEMA_VERSION, "endpoints": []}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Agent endpoint registry is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != CHAT_ENDPOINT_REGISTRY_SCHEMA_VERSION:
        raise ValueError("Agent endpoint registry has an unsupported schema")
    endpoints = payload.get("endpoints")
    if not isinstance(endpoints, list):
        raise ValueError("Agent endpoint registry endpoints must be a list")
    return payload


@dataclass(frozen=True)
class AgentEndpoint:
    agent_id: str
    display_name: str
    command: tuple[str, ...]
    transport: str = "stdio"
    location: str = "local"
    trust_scope: str = "read_only"
    enabled: bool = True
    workspace_mapping: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "AgentEndpoint":
        unknown = set(payload) - {
            "schema_version",
            "agent_id",
            "display_name",
            "adapter_kind",
            "transport",
            "location",
            "trust_scope",
            "enabled",
            "command",
            "workspace_mapping",
            "created_at",
            "updated_at",
        }
        if unknown:
            raise ValueError(f"unknown Agent endpoint fields: {sorted(unknown)}")
        agent_id = _compact_id(payload.get("agent_id"), field="agent_id")
        if agent_id in RESERVED_AGENT_IDS:
            raise ValueError("agent_id is reserved for a built-in Agent")
        if str(payload.get("adapter_kind") or "acp") != "acp":
            raise ValueError("custom Agent endpoints must use adapter_kind=acp")
        transport = str(payload.get("transport") or "stdio")
        if transport not in SUPPORTED_TRANSPORTS:
            raise ValueError("transport must be stdio")
        location = str(payload.get("location") or "local")
        if location not in SUPPORTED_LOCATIONS:
            raise ValueError("location must be local or remote")
        trust_scope = str(payload.get("trust_scope") or "read_only")
        if trust_scope not in SUPPORTED_TRUST_SCOPES:
            raise ValueError("trust_scope must be read_only")
        enabled_raw = payload.get("enabled", True)
        if not isinstance(enabled_raw, bool):
            raise ValueError("enabled must be a boolean")
        command_raw = payload.get("command")
        if not isinstance(command_raw, list) or not command_raw or len(command_raw) > 32:
            raise ValueError("command must be a non-empty argv list")
        command: list[str] = []
        for item in command_raw:
            argument = str(item)
            if not argument or "\x00" in argument or "\n" in argument or len(argument) > 2048:
                raise ValueError("command contains an invalid argument")
            command.append(argument)
        mapping_raw = payload.get("workspace_mapping") or []
        if not isinstance(mapping_raw, list) or len(mapping_raw) > 16:
            raise ValueError("workspace_mapping must be a bounded list")
        mapping: list[tuple[str, str]] = []
        for item in mapping_raw:
            if not isinstance(item, dict) or set(item) != {"local_root", "agent_root"}:
                raise ValueError("workspace_mapping entries require local_root and agent_root")
            local_root = str(item.get("local_root") or "").strip()
            agent_root = str(item.get("agent_root") or "").strip()
            if (
                len(local_root) > 2048
                or len(agent_root) > 2048
                or any(ord(character) < 32 for character in local_root + agent_root)
            ):
                raise ValueError("workspace_mapping roots must be bounded paths")
            if not local_root or not agent_root or not Path(local_root).expanduser().is_absolute():
                raise ValueError("workspace_mapping roots must be absolute paths")
            if not Path(agent_root).is_absolute():
                raise ValueError("workspace_mapping roots must be absolute paths")
            mapping.append((str(Path(local_root).expanduser().resolve()), agent_root))
        return cls(
            agent_id=agent_id,
            display_name=_compact_label(payload.get("display_name"), field="display_name"),
            command=tuple(command),
            transport=transport,
            location=location,
            trust_scope=trust_scope,
            enabled=enabled_raw,
            workspace_mapping=tuple(mapping),
        )

    def private_payload(self, *, created_at: str | None = None) -> dict[str, Any]:
        now = utc_now()
        return {
            "schema_version": CHAT_ENDPOINT_SCHEMA_VERSION,
            "agent_id": self.agent_id,
            "display_name": self.display_name,
            "adapter_kind": "acp",
            "transport": self.transport,
            "location": self.location,
            "trust_scope": self.trust_scope,
            "enabled": self.enabled,
            "command": list(self.command),
            "workspace_mapping": [
                {"local_root": local_root, "agent_root": agent_root}
                for local_root, agent_root in self.workspace_mapping
            ],
            "created_at": created_at or now,
            "updated_at": now,
        }

    def mapped_work_dir(self, work_dir: Path) -> Path:
        selected = work_dir.expanduser().resolve()
        matches: list[tuple[int, Path]] = []
        for local_root_raw, agent_root_raw in self.workspace_mapping:
            local_root = Path(local_root_raw)
            try:
                relative = selected.relative_to(local_root)
            except ValueError:
                continue
            matches.append((len(local_root.parts), Path(agent_root_raw) / relative))
        return max(matches, key=lambda item: item[0])[1] if matches else selected

    def available(self) -> bool:
        if not self.enabled:
            return False
        executable = self.command[0]
        if Path(executable).is_absolute():
            return Path(executable).is_file() and os.access(executable, os.X_OK)
        return bool(shutil.which(executable))

    def public_summary(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "display_name": self.display_name,
            "adapter_kind": "acp",
            "transport": self.transport,
            "location": self.location,
            "trust_scope": self.trust_scope,
            "available": self.available(),
            "streaming": True,
            "resume": True,
            "interrupt": True,
            "tool_calls": True,
            "source": "owner_local_registry",
        }


class AgentEndpointRegistry:
    """Persist custom Agent launch details outside repositories and public projections."""

    def __init__(self, chat_root: Path) -> None:
        self.root = chat_root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.path = self.root / "endpoints.json"

    def list(self) -> list[AgentEndpoint]:
        payload = _read(self.path)
        rows: list[AgentEndpoint] = []
        for item in payload.get("endpoints") or []:
            if not isinstance(item, dict):
                continue
            rows.append(AgentEndpoint.from_payload(item))
        return sorted(rows, key=lambda item: item.agent_id)

    def get(self, agent_id: str) -> AgentEndpoint | None:
        selected = _compact_id(agent_id, field="agent_id")
        return next((item for item in self.list() if item.agent_id == selected), None)

    def upsert(self, payload: dict[str, Any]) -> AgentEndpoint:
        endpoint = AgentEndpoint.from_payload(payload)
        with exclusive_file_lock(self.path, agent_id="loopx-chat", operation="update_agent_endpoints"):
            registry = _read(self.path)
            rows = [item for item in registry.get("endpoints") or [] if isinstance(item, dict)]
            previous = next((item for item in rows if item.get("agent_id") == endpoint.agent_id), None)
            rows = [item for item in rows if item.get("agent_id") != endpoint.agent_id]
            rows.append(endpoint.private_payload(created_at=str((previous or {}).get("created_at") or "") or None))
            _atomic_write(
                self.path,
                {
                    "schema_version": CHAT_ENDPOINT_REGISTRY_SCHEMA_VERSION,
                    "endpoints": sorted(rows, key=lambda item: str(item.get("agent_id") or "")),
                },
            )
        return endpoint

    def delete(self, agent_id: str) -> bool:
        selected = _compact_id(agent_id, field="agent_id")
        if selected in RESERVED_AGENT_IDS:
            raise ValueError("built-in Agent endpoints cannot be deleted")
        with exclusive_file_lock(self.path, agent_id="loopx-chat", operation="delete_agent_endpoint"):
            registry = _read(self.path)
            rows = [item for item in registry.get("endpoints") or [] if isinstance(item, dict)]
            kept = [item for item in rows if item.get("agent_id") != selected]
            if len(kept) == len(rows):
                return False
            _atomic_write(
                self.path,
                {"schema_version": CHAT_ENDPOINT_REGISTRY_SCHEMA_VERSION, "endpoints": kept},
            )
        return True
