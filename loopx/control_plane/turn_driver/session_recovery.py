"""Typed failed-Turn host session recovery."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .driver import (
    FailedTurnSessionRecoveryError,
    reconcile_failed_turn_session_request,
)


HOST_RECOVERY_SCHEMA_VERSION = "loopx_turn_host_recovery_v0"
HOST_RECOVERY_KINDS = frozenset({"resume_session"})

# Receive the checked complete decision for a current request, or its original
# envelope for historical input. The provider resolves its existing Session;
# the shared lineage owner below still decides whether that binding matches.
SessionBindingResolver = Callable[
    [Mapping[str, Any]],
    Mapping[str, Any] | None,
]


def require_host_recovery_kind(value: str) -> str:
    if value not in HOST_RECOVERY_KINDS:
        raise ValueError("unsupported built-in host recovery kind")
    return value


def build_host_recovery_record(kind: str) -> dict[str, str]:
    return {
        "schema_version": HOST_RECOVERY_SCHEMA_VERSION,
        "kind": require_host_recovery_kind(kind),
    }


def reconcile_failed_turn_retry_request(
    request: Mapping[str, Any],
    journal: Mapping[str, Any],
    *,
    session_binding_resolver: SessionBindingResolver | None,
) -> dict[str, Any]:
    resolved, _check, error = assess_failed_turn_retry_request(
        request,
        journal,
        session_binding_resolver=session_binding_resolver,
    )
    if error is not None:
        raise error
    return resolved


def assess_failed_turn_retry_request(
    request: Mapping[str, Any],
    journal: Mapping[str, Any],
    *,
    session_binding_resolver: SessionBindingResolver | None,
) -> tuple[dict[str, Any], dict[str, str] | None, ValueError | None]:
    """Return the retry request and the exact Host Session check it used."""

    def rejected(reason: str, message: str) -> tuple[
        dict[str, Any], dict[str, str], ValueError
    ]:
        return (
            dict(request),
            {
                "kind": "host_session_binding",
                "outcome": "failed",
                "reason": reason,
            },
            ValueError(message),
        )

    if "host_recovery" not in journal:
        return dict(request), None, None
    recovery_value = journal.get("host_recovery")
    if not isinstance(recovery_value, Mapping):
        return rejected(
            "host_recovery_shape_mismatch",
            "failed-Turn host recovery shape mismatch",
        )
    recovery = dict(recovery_value)
    if recovery.get("schema_version") != HOST_RECOVERY_SCHEMA_VERSION:
        return rejected(
            "host_recovery_schema_unsupported",
            "failed-Turn host recovery has an unsupported schema",
        )
    if recovery.get("kind") not in HOST_RECOVERY_KINDS:
        return rejected(
            "host_recovery_kind_unsupported",
            "failed-Turn host recovery has an unsupported kind",
        )
    receipt_value = journal.get("receipt")
    receipt = dict(receipt_value) if isinstance(receipt_value, Mapping) else {}
    if receipt.get("failed_phase") != "host_execute":
        return rejected(
            "host_failure_phase_mismatch",
            "failed-Turn session recovery requires a host execution failure",
        )
    if session_binding_resolver is None:
        return rejected(
            "binding_resolver_unavailable",
            "failed-Turn session recovery has no binding resolver",
        )
    try:
        from ..quota.turn_envelope import (
            COMPLETE_TURN_HOST_REQUEST_SCHEMA,
            complete_turn_host_source,
        )

        # Validate any supplied complete source before provider IO, but retain
        # the recorded v0 callback input even when it has a complete sidecar.
        complete_turn_host_source(request)
        source_value = (request["work_contract"]
            if request.get("schema_version") == COMPLETE_TURN_HOST_REQUEST_SCHEMA
            else request.get("turn_envelope"))
        source = dict(source_value) if isinstance(source_value, Mapping) else {}
        session_binding = session_binding_resolver(source)
    except Exception:  # noqa: BLE001 - provider boundary fails closed
        return rejected(
            "binding_resolution_failed",
            "failed-Turn recovery session binding could not be resolved",
        )
    try:
        resolved = reconcile_failed_turn_session_request(
            request,
            session_binding=session_binding,
        )
    except FailedTurnSessionRecoveryError as exc:
        return rejected(exc.reason, str(exc))
    return (
        resolved,
        {
            "kind": "host_session_binding",
            "outcome": "passed",
            "reason": "session_binding_matched",
        },
        None,
    )
