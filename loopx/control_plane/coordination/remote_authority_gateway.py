"""Private configuration and read-only observation for the remote authority candidate."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REMOTE_AUTHORITY_GATEWAY_SCHEMA = "loopx_remote_authority_gateway_v0"
_REQUIRED_BINDING = ("endpoint", "tenant_id", "store_identity", "actor_id", "probe_todo_id")


def remote_authority_config(
    goal: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """Opt in only through the selected registered Goal, never an environment default."""
    cfg = goal.get("remote_authority") if goal is not None else None
    return cfg if isinstance(cfg, Mapping) and cfg.get("enabled") is True else None


def _binding_complete(cfg: Mapping[str, Any]) -> bool:
    return all(isinstance(cfg.get(key), str) and bool(cfg[key].strip()) for key in _REQUIRED_BINDING)


def get_remote_authority_projection(goal_id: str, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Describe configuration without portraying it as connectivity or exposing its binding."""
    return {
        "schema_version": REMOTE_AUTHORITY_GATEWAY_SCHEMA,
        "goal_id": goal_id,
        "enabled": True,
        "handoff_mode": "hard_lease",
        "access": "opt_in_authenticated_only",
        "status": "unverified" if _binding_complete(cfg) else "unconfigured",
    }


def check_remote_authority_gateway_status(
    goal_id: str,
    agent_id: str | None,
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    """Read one known remote Todo through the existing TLS client; return no remote content."""
    projection = get_remote_authority_projection(goal_id, cfg)
    if projection["status"] == "unconfigured":
        return {**projection, "reason_code": "remote_authority_binding_incomplete"}
    if agent_id != cfg["actor_id"]:
        return {**projection, "status": "unavailable", "reason_code": "remote_authority_actor_mismatch"}
    credential = os.environ.get("LOOPX_AUTHORITY_CREDENTIAL") or os.environ.get("LOOPX_REMOTE_AUTHORITY_CREDENTIAL")
    if not credential:
        return {**projection, "status": "unavailable", "reason_code": "remote_authority_credential_required"}
    from ..effect_runtime import EffectRuntimeStartupError, _node_executable
    from ..runtime.node_probe import node_typescript_flags

    try:
        # The user-owned registry supplies the origin and optional private CA.
        # Pass the credential and binding through the private pipe, not argv.
        bridge = Path(__file__).with_name("remote_authority_probe.ts")
        completed = subprocess.run(
            [_node_executable(), "--no-warnings", "--experimental-strip-types", *node_typescript_flags(), str(bridge)],
            input=json.dumps({
                **{key: cfg[key] for key in _REQUIRED_BINDING},
                "goal_id": goal_id, "credential": credential, "ca_file": cfg.get("ca_file"),
            }),
            env={key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "TMPDIR", "TEMP")
                 if key in os.environ},
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
            timeout=5,
        )
        if completed.stdout == "verified\n":
            return {**projection, "status": "verified"}
    except (EffectRuntimeStartupError, OSError, subprocess.SubprocessError):
        pass
    return {**projection, "status": "unavailable", "reason_code": "remote_authority_read_unverified"}


def _inspection_io_failure(reason: str, message: str, action: str) -> dict[str, object]:
    return {
        "schema_version": "loopx_remote_authority_inspection_v1", "ok": False,
        "status": "unavailable", "stage": "runtime", "reason_code": reason,
        "message": message, "recommended_action": action, "execution_admitted": False,
    }


def inspect_remote_authority(registry_path: Path, goal_id: str, agent_id: str | None = None) -> dict[str, object]:
    from ...agent_registry import load_goal_from_registry, registered_agent_ids_for_goal
    from ..effect_runtime import EffectRuntimeStartupError, _node_executable
    from ..runtime.node_probe import node_typescript_flags

    try:
        goal = load_goal_from_registry(registry_path, goal_id)
    except (OSError, ValueError):
        return _inspection_io_failure(
            "remote_authority_registry_unreadable", "The selected registry could not be read.",
            "Check the registry's format and local access permissions.",
        )
    cfg = goal.get("remote_authority") if goal is not None else None
    # Copy only the existing binding, never remote content or unrelated Goal state.
    if isinstance(cfg, dict):
        cfg = {key: cfg[key] for key in (
            "enabled", "endpoint", "tenant_id", "store_identity", "actor_id", "probe_todo_id", "ca_file",
        ) if key in cfg}
    # The App inspects the existing bound actor; it cannot supply a credential,
    # endpoint or alternative binding. CLI callers keep their explicit actor.
    if agent_id is None and isinstance(cfg, dict):
        bound_actor = cfg.get("actor_id")
        agent_id = bound_actor if isinstance(bound_actor, str) else None
    request = {
        "goal_registered": goal is not None,
        "actor_registered": agent_id in registered_agent_ids_for_goal(goal),
        "goal_id": goal_id, "agent_id": agent_id, "config": cfg,
        "credential": os.environ.get("LOOPX_AUTHORITY_CREDENTIAL") or os.environ.get("LOOPX_REMOTE_AUTHORITY_CREDENTIAL"),
    }
    try:
        # Bindings and credentials travel through stdin, never argv or diagnostics.
        bridge = Path(__file__).with_name("remote_authority_probe.ts")
        completed = subprocess.run(
            [_node_executable(), "--no-warnings", "--experimental-strip-types",
             *node_typescript_flags(), str(bridge), "--inspect"],
            input=json.dumps(request),
            env={key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "TMPDIR", "TEMP")
                 if key in os.environ},
            text=True, encoding="utf-8", capture_output=True, check=True, timeout=5,
        )
        payload = json.loads(completed.stdout)
        if (isinstance(payload, dict)
                and payload.get("schema_version") == "loopx_remote_authority_inspection_v1"
                and payload.get("execution_admitted") is False
                and isinstance(payload.get("ok"), bool)):
            return payload
    except EffectRuntimeStartupError:
        return _inspection_io_failure(
            "remote_authority_node_unavailable", "The required Node.js runtime is unavailable.",
            "Run loopx doctor and repair the Node.js installation.",
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return _inspection_io_failure(
        "remote_authority_probe_unavailable", "The local inspection process did not return a valid result.",
        "Run loopx doctor and check the installed runtime files.",
    )
