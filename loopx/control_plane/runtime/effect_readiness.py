"""Readiness, Node/SQLite identity projection, and deep probe reporting."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias

from .effect_client_errors import (
    EffectRuntimeHostPermissionError,
    EffectRuntimeStartupError,
)
from .node_probe import (
    MINIMUM_NODE_VERSION_TEXT,
    NodeProbe,
)

EFFECT_RUNTIME_READINESS_SCHEMA_VERSION = "loopx_effect_runtime_readiness_v0"
EFFECT_RUNTIME_LIFECYCLE_SCHEMA_VERSION = "loopx_effect_runtime_lifecycle_v0"
RuntimeLocatorState: TypeAlias = Literal["missing", "valid", "invalid"]
_RUNTIME_IDENTITY_TEXT_FIELDS = (
    "node_version",
    "sqlite_version",
    "sqlite_source_id",
    "unavailable_reason",
)


@dataclass(frozen=True)
class EffectReadinessHooks:
    """Explicit runtime probe hooks preserving module-level test seams."""

    probe_node: Callable[[], NodeProbe]
    uses_stdio: Callable[[], bool]
    runtime_fingerprint: Callable[[], str]
    stdio_identity: Callable[[str], tuple[bool, dict[str, object] | None]]
    stdio_alive: Callable[[], bool]
    inspect_locator: Callable[
        [str],
        tuple[RuntimeLocatorState, bool | None, str | None, Mapping[str, object] | None],
    ]
    locator_after_probe: Callable[
        [str],
        tuple[RuntimeLocatorState, bool | None, str | None],
    ]
    rpc_result: Callable[[str, Mapping[str, object]], object]
    serving_identity: Callable[[], dict[str, object] | None]
    node_probe_remediation: Callable[[str], str | None]
    runtime_identity_from_info: Callable[
        [Mapping[str, object] | None], dict[str, object] | None
    ]


def runtime_identity_from_info(
    info: Mapping[str, object] | None,
) -> dict[str, object] | None:
    """Project the serving runtime's Node/SQLite identity from a runtime info file.

    The managed runtime is reused per source revision, so this identity can
    differ from the Node on the caller's PATH; status surfaces must show which
    runtime is actually serving before a qualification failure is diagnosed.
    """

    if not isinstance(info, Mapping):
        return None
    value = info.get("runtime_identity")
    if not isinstance(value, Mapping):
        return None
    sync_final = value.get("synchronous_statement_finalization")
    identity: dict[str, object] = {
        "schema_version": value.get("schema_version"),
        "sqlite_available": value.get("sqlite_available") is True,
        "sqlite_authority_qualified": value.get("sqlite_authority_qualified") is True,
        "synchronous_statement_finalization": (
            sync_final if isinstance(sync_final, bool) else None
        ),
    }
    for field in _RUNTIME_IDENTITY_TEXT_FIELDS:
        raw = value.get(field)
        identity[field] = raw if isinstance(raw, str) else None
    return identity


def sqlite_restart_recommendation(
    identity: Mapping[str, object] | None,
    *,
    process_scoped: bool = False,
) -> str | None:
    """Name the repair for a serving runtime that cannot run SQLite authority."""

    if identity is None or identity.get("sqlite_authority_qualified") is True:
        return None
    if process_scoped:
        return (
            "The stdio Effect runtime uses Node "
            f"{identity.get('node_version')} with SQLite "
            f"{identity.get('sqlite_version')}. Rerun this command on a PATH "
            "with qualified Node 22.22.3 or newer to use SQLite authority."
        )
    # The File path is ready but the SQLite authority lane is not: the serving
    # runtime keeps its own Node, so installing Node alone would not repair it.
    return (
        "The managed Effect runtime serving this revision runs Node "
        f"{identity.get('node_version')} with SQLite "
        f"{identity.get('sqlite_version')}, which lacks the WAL-reset fix. "
        "Install the qualified Node 22.22.3 runtime and run "
        "`loopx doctor --restart-runtime` so the next request starts a new "
        "runtime; the SQLite authority lane stays unavailable until then."
    )


def _failed_deep_readiness(
    result: dict[str, object],
    runtime_lifecycle: dict[str, object],
    *,
    diagnostic_code: str,
    locator_state: RuntimeLocatorState,
    recorded_pid_alive: bool | None,
    recommended_action: str,
) -> dict[str, object]:
    return {
        **result,
        "ready": False,
        "status": "probe_failed",
        "semantic_probe": "failed",
        "runtime_lifecycle": {
            **runtime_lifecycle,
            "state": "unavailable",
            "diagnostic_code": diagnostic_code,
            "locator_state": locator_state,
            "recorded_pid_alive": recorded_pid_alive,
        },
        "recommended_action": recommended_action,
    }


def collect_effect_runtime_readiness(
    hooks: EffectReadinessHooks,
    *,
    deep: bool = False,
) -> dict[str, object]:
    """Report whether the managed TS Effect runtime can serve control-plane work."""

    probe = hooks.probe_node()
    status, version, ready = probe.status, probe.version, probe.ready
    runtime_state = "unavailable"
    runtime_diagnostic_code: str | None = probe.diagnostic_code
    runtime_identity: dict[str, object] | None = None
    locator_state: RuntimeLocatorState = "missing"
    recorded_pid_alive: bool | None = None
    host_permission_error: EffectRuntimeHostPermissionError | None = None
    transport_recommendation: str | None = None
    locator_info: Mapping[str, object] | None = None
    use_stdio = False
    stdio_live = False
    stdio_identity: dict[str, object] | None = None
    fingerprint = ""
    try:
        use_stdio = hooks.uses_stdio()
        fingerprint = hooks.runtime_fingerprint()
        locator_read_diagnostic: str | None = None
        if use_stdio:
            stdio_live, stdio_identity = hooks.stdio_identity(fingerprint)
            if stdio_live:
                recorded_pid_alive = True
        else:
            (
                locator_state,
                recorded_pid_alive,
                locator_read_diagnostic,
                locator_info,
            ) = hooks.inspect_locator(fingerprint)
    except EffectRuntimeHostPermissionError as exc:
        locator_state = "invalid"
        if ready:
            ready = False
            status = "probe_failed"
            runtime_diagnostic_code = exc.diagnostic_code
            host_permission_error = exc
    except (OSError, EffectRuntimeStartupError) as exc:
        if ready:
            ready = False
            raw_code = getattr(exc, "diagnostic_code", None)
            runtime_diagnostic_code = (
                raw_code if isinstance(raw_code, str) else "packaged_runtime_source_unreadable"
            )
            if runtime_diagnostic_code in {
                "invalid_runtime_transport",
                "unsupported_runtime_transport",
            }:
                status = "probe_failed"
                transport_recommendation = str(exc)
            else:
                status = "package_invalid"
    else:
        if ready:
            runtime_diagnostic_code = locator_read_diagnostic or runtime_diagnostic_code

    if ready:
        runtime_state = "running" if stdio_live or locator_info is not None else "stopped"
        runtime_identity = (
            stdio_identity if stdio_live else hooks.runtime_identity_from_info(locator_info)
        )
    runtime_lifecycle: dict[str, object] = {
        "schema_version": EFFECT_RUNTIME_LIFECYCLE_SCHEMA_VERSION,
        "management": "on_demand_managed",
        "state": runtime_state,
        "manual_start_required": False,
        "restart_policy": "automatic_on_next_control_plane_request",
        "idle_shutdown": not use_stdio,
        "diagnostic_code": runtime_diagnostic_code,
        "locator_state": locator_state,
        "recorded_pid_alive": recorded_pid_alive,
    }
    if use_stdio:
        runtime_lifecycle["transport"] = "stdio"
    recommended_action: str | None
    if host_permission_error is not None:
        recommended_action = host_permission_error.recommended_action
    elif ready:
        recommended_action = sqlite_restart_recommendation(
            runtime_identity, process_scoped=use_stdio
        )
    else:
        recommended_action = (
            transport_recommendation
            or probe.recommended_action
            or "Repair the packaged LoopX runtime, then rerun `loopx doctor --deep`."
        )
    result: dict[str, object] = {
        "schema_version": EFFECT_RUNTIME_READINESS_SCHEMA_VERSION,
        "ready": ready,
        "status": status,
        "required_for": ["control_plane"],
        "default_cli_blocking": True,
        "minimum_node_version": MINIMUM_NODE_VERSION_TEXT,
        "detected_node_version": version,
        "runtime_identity": runtime_identity,
        "semantic_probe": "not_requested" if not deep else "not_run",
        "runtime_lifecycle": runtime_lifecycle,
        "recommended_action": recommended_action,
    }
    if not ready or not deep:
        return result

    probe_error: RuntimeError | None = None
    ping: object = None
    identity: object = None
    try:
        ping = hooks.rpc_result("runtime.ping", {})
        identity = hooks.rpc_result(
            "settlement.identity",
            {
                "goal_id": "doctor-probe",
                "agent_id": "doctor-probe",
                "todo_id": "doctor-probe",
                "turn_instance_id": "doctor-probe",
            },
        )
    except RuntimeError as exc:
        probe_error = exc

    if use_stdio:
        recorded_pid_alive = hooks.stdio_alive()
        post_probe_diagnostic: str | None = None
    else:
        (
            locator_state,
            recorded_pid_alive,
            post_probe_diagnostic,
        ) = hooks.locator_after_probe(fingerprint)

    if probe_error is not None:
        raw_probe_code = getattr(probe_error, "diagnostic_code", None)
        diagnostic_code = (
            raw_probe_code if isinstance(raw_probe_code, str) else "semantic_probe_failed"
        )
        failure_action = (
            probe_error.recommended_action
            if isinstance(probe_error, EffectRuntimeHostPermissionError)
            else hooks.node_probe_remediation(diagnostic_code)
            or "Run `loopx doctor --deep` again after any concurrent startup "
            "finishes. If the same diagnostic code remains, reinstall LoopX "
            "and verify Node.js before retrying."
        )
        return _failed_deep_readiness(
            result,
            runtime_lifecycle,
            diagnostic_code=diagnostic_code,
            locator_state=locator_state,
            recorded_pid_alive=recorded_pid_alive,
            recommended_action=failure_action,
        )
    if (
        not isinstance(ping, Mapping)
        or ping.get("ready") is not True
        or not isinstance(identity, Mapping)
        or identity.get("effect_id")
        != "doctor-probe:doctor-probe:doctor-probe:doctor-probe"
    ):
        return _failed_deep_readiness(
            result,
            runtime_lifecycle,
            diagnostic_code="semantic_probe_shape_mismatch",
            locator_state=locator_state,
            recorded_pid_alive=recorded_pid_alive,
            recommended_action=(
                "Reinstall LoopX and verify the packaged TypeScript runtime with "
                "`loopx doctor --deep`."
            ),
        )
    # The probe just started the runtime, so its identity is only readable now;
    # report the Node/SQLite pair that will serve the following requests.
    serving_identity = (
        hooks.runtime_identity_from_info(ping) if use_stdio else hooks.serving_identity()
    ) or runtime_identity
    return {
        **result,
        "semantic_probe": "passed",
        "runtime_identity": serving_identity,
        "runtime_lifecycle": {
            **runtime_lifecycle,
            "state": "running",
            "diagnostic_code": post_probe_diagnostic,
            "locator_state": locator_state,
            "recorded_pid_alive": recorded_pid_alive,
        },
        "recommended_action": (
            sqlite_restart_recommendation(serving_identity, process_scoped=use_stdio)
            or recommended_action
        ),
    }
