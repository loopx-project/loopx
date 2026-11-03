"""Pi execution for the existing signed generic-cli Turn host contract.

The operator selects this host explicitly. Pi supplies its native tools and
provider configuration; the signed Turn remains the work authority. This
transport neither supplies an OS sandbox nor changes admission or writeback.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

from ...control_plane.turn_driver.host_candidate import (
    LOOPX_TURN_HOST_REQUEST_SCHEMA,
    build_result,
    extract_turn_authority,
    parse_model_json,
    render_prompt,
)
from ...control_plane.turn_driver.host_failure import BuiltInHostError
from .bridge import run_oar_turn


@dataclass(frozen=True)
class OarTurnHostConfig:
    cwd: Path
    runtime: str = "pi"
    model: str | None = None
    effort: str | None = None
    timeout_seconds: float = 300.0
    env: Mapping[str, str | None] | None = None


def _failure_kind(observed: Mapping[str, Any]) -> str:
    if observed.get("outcome") == "timeout" or bool(
        isinstance(observed.get("oar_result"), Mapping)
        and observed["oar_result"].get("timedOut")
    ):
        return "executor_timeout"
    if not observed.get("output_complete"):
        return "transport_lost"
    outcome = observed.get("turn_outcome")
    failure = outcome.get("failure") if isinstance(outcome, Mapping) else None
    return {
        "auth": "auth_failed",
        "quota": "quota_exhausted",
        "rate_limited": "rate_limited",
        "overloaded": "provider_overloaded",
        "provider": "provider_capacity",
        "runtime_exited": "transport_lost",
        "limit_reached": "output_budget_exhausted",
        "invalid_request": "contract_rejected",
        "model_unavailable": "contract_rejected",
        "input_too_large": "contract_rejected",
        "output_schema": "contract_rejected",
    }.get(str(failure), "unknown")


def run_oar_host(
    request: Mapping[str, Any], *, config: OarTurnHostConfig,
) -> dict[str, Any]:
    """Translate one verified request; only a completed root answer is a candidate."""
    if not isinstance(request, Mapping) or request.get("schema_version") != LOOPX_TURN_HOST_REQUEST_SCHEMA:
        raise BuiltInHostError("oar_request_schema_mismatch", failure_kind="contract_rejected")
    try:
        authority = extract_turn_authority(request)
    except (TypeError, ValueError) as exc:
        raise BuiltInHostError("oar_turn_authority_rejected", failure_kind="contract_rejected") from exc
    if config.runtime != "pi":
        raise BuiltInHostError("oar_turn_runtime_rejected", failure_kind="contract_rejected")
    if not math.isfinite(config.timeout_seconds) or config.timeout_seconds <= 0:
        raise BuiltInHostError("oar_turn_timeout_rejected", failure_kind="contract_rejected")
    workspace = config.cwd.expanduser().resolve()
    if not workspace.is_dir():
        raise BuiltInHostError("oar_turn_workspace_unavailable", failure_kind="contract_rejected")
    if config.model is not None and ("/" not in config.model or not all(config.model.split("/", 1))):
        raise BuiltInHostError("oar_pi_model_requires_provider", failure_kind="contract_rejected")

    envelope = request["turn_envelope"]
    action = envelope.get("action") or {}
    selected = action.get("selected_todo") or {}
    if not isinstance(selected, Mapping):
        selected = {}
    native_request: dict[str, Any] = {
        "runtime": "pi",
        "cwd": str(workspace),
        "prompt": render_prompt(authority),
        "env": {
            **dict(config.env or {}),
            "LOOPX_TURN_GOAL_ID": str(envelope.get("goal_id") or ""),
            "LOOPX_TURN_AGENT_ID": str(envelope.get("agent_id") or ""),
            "LOOPX_TURN_TODO_ID": str(selected.get("todo_id") or ""),
            "LOOPX_TURN_WORKSPACE": str(workspace),
        },
    }
    if config.model is not None:
        native_request["model"] = config.model
    if config.effort is not None:
        native_request["effort"] = config.effort
    try:
        observed = run_oar_turn(
            native_request, project=workspace, timeout_seconds=config.timeout_seconds,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        # Native failures can quote provider keys, requests and paths. Only the
        # stable local classification crosses the generic-cli stderr boundary.
        raise BuiltInHostError("oar_pi_execution_unavailable", failure_kind="unknown") from exc
    outcome = observed.get("turn_outcome")
    final = observed.get("final_response")
    if (
        observed.get("outcome") != "exited" or observed.get("returncode") != 0
        or observed.get("output_complete") is not True
        or not isinstance(outcome, Mapping) or outcome.get("kind") != "completed"
        or not isinstance(final, str)
    ):
        raise BuiltInHostError("oar_pi_execution_failed", failure_kind=_failure_kind(observed))
    return build_result(
        request, parse_model_json(final), host_name="Pi through OAR",
        fallback_reason="Pi returned no typed JSON result; inspect the native session before another attempt.",
    )
