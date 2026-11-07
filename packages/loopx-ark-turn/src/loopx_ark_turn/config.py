"""Operator configuration and host-bound identity, never model-authored options."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import hashlib
import json
import math
import os
import re
from urllib.parse import urlsplit


class AdapterError(ValueError):
    """A bounded reason safe to show without provider response bodies."""


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Config:
    model: str
    environment_id: str
    workspace: Path
    state_dir: Path
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    mcp_command: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()
    mcp_env: tuple[str, ...] = ()
    timeout_seconds: float = 180
    tool_timeout_seconds: float = 60
    poll_interval_seconds: float = 1
    max_tool_calls: int = 32

    def __post_init__(self) -> None:
        if (not isinstance(self.model, str) or not self.model.strip()
                or not isinstance(self.environment_id, str) or not self.environment_id.strip() or not self.workspace.is_dir()):
            raise AdapterError("model_environment_and_workspace_required")
        if self.state_dir.resolve().is_relative_to(self.workspace.resolve()):
            raise AdapterError("host_receipts_must_be_outside_task_workspace")
        endpoint = urlsplit(self.base_url)
        if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise AdapterError("endpoint_must_be_https_without_embedded_credentials")
        for value in (self.timeout_seconds, self.tool_timeout_seconds, self.poll_interval_seconds):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise AdapterError("timeouts_must_be_positive_and_finite")
        if isinstance(self.max_tool_calls, bool) or not isinstance(self.max_tool_calls, int) or not 1 <= self.max_tool_calls <= 256:
            raise AdapterError("tool_call_limit_out_of_range")
        if bool(self.mcp_command) != bool(self.tool_names):
            raise AdapterError("mcp_command_requires_explicit_tool_selection")
        if len(set(self.tool_names)) != len(self.tool_names) or len(self.tool_names) > 8:
            raise AdapterError("select_at_most_eight_unique_tools")
        if any(not arg or "\x00" in arg for arg in self.mcp_command):
            raise AdapterError("invalid_mcp_argv")
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in self.mcp_env):
            raise AdapterError("invalid_environment_variable_name")
        if "ARK_API_KEY" in self.mcp_env or any(name.startswith("LOOPX_TURN_") for name in self.mcp_env):
            raise AdapterError("provider_credentials_and_bound_identity_cannot_be_forwarded")


def require_request(request: Mapping[str, Any]) -> dict[str, str]:
    from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority

    if request.get("schema_version") != "loopx_turn_host_request_v0":
        raise AdapterError("request_schema_mismatch")
    extract_turn_authority(request)
    key = request.get("turn_key")
    if not isinstance(key, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", key):
        raise AdapterError("invalid_turn_key")
    if request.get("session", {}).get("context_policy", {}).get("mode") != "fresh":
        raise AdapterError("ark_turn_requires_iteration_context_fresh")
    envelope = request["turn_envelope"]
    identity = {
        "KEY": key,
        "GOAL_ID": envelope.get("goal_id"),
        "AGENT_ID": envelope.get("agent_id"),
        "TODO_ID": envelope.get("action", {}).get("selected_todo", {}).get("todo_id"),
    }
    if any(not isinstance(v, str) or not v or "\x00" in v for v in identity.values()):
        raise AdapterError("signed_work_identity_required")
    return identity


def tool_environment(config: Config, identity: Mapping[str, str]) -> dict[str, str]:
    names = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "LANG", *config.mcp_env)
    env = {name: os.environ[name] for name in names if name in os.environ}
    env.update({"LOOPX_TURN_" + key: value for key, value in identity.items()})
    env["LOOPX_TURN_WORKSPACE"] = str(config.workspace.resolve())
    return env
