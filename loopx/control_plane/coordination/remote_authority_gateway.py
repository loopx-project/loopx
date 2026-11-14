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

    try:
        # The user-owned registry supplies the origin and optional private CA.
        # Pass the credential and binding through the private pipe, not argv.
        bridge = Path(__file__).with_name("remote_authority_probe.ts")
        completed = subprocess.run(
            [_node_executable(), "--no-warnings", "--experimental-strip-types", str(bridge)],
            input=json.dumps({
                **{key: cfg[key] for key in _REQUIRED_BINDING},
                "goal_id": goal_id, "credential": credential, "ca_file": cfg.get("ca_file"),
            }),
            env={key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "TMPDIR", "TEMP")
                 if key in os.environ},
            text=True,
            capture_output=True,
            check=True,
            timeout=5,
        )
        if completed.stdout == "verified\n":
            return {**projection, "status": "verified"}
    except (EffectRuntimeStartupError, OSError, subprocess.SubprocessError):
        pass
    return {**projection, "status": "unavailable", "reason_code": "remote_authority_read_unverified"}
