"""Resolve the hard lease a stopped delegation's execution may hold, by its own identity.

A stop owes the release of the lease its execution acquired; it never owns a
lease another execution holds. Canonical authority decides whether that lease
is still held: the identity comes from the operation record, the version from
the canonical lease, and the native lifecycle remains the only writer. The
operation's own `task_lease` annotation is a hint, never proof that nothing is
owed. The result is one of the typed stop owner's lease facts.
"""
from __future__ import annotations

from .inbox import _write
from ..coordination.local_authority import local_authority_is_promoted
from ..coordination.coordination_state_contract_generated import LOCAL_COORDINATION_TODO_READ_REQUEST_SCHEMA
from ..effect_runtime import CANONICAL_AUTHORITY_READ_TIMEOUT_SECONDS, EffectRuntimeRemoteError, effect_runtime_result
from ..work_items.task_lease import inspect_task_lease, release_task_lease

_AUTHORITY_ERRORS = (ValueError, OSError, RuntimeError, EffectRuntimeRemoteError)
# The typed stop owner's lease facts (`StopLease` in delegation.ts).
LEASE_UNCHECKED = "unchecked"
LEASE_NOT_OWED = "not_owed"
LEASE_RELEASED = "released"
LEASE_RELEASE_UNPROVEN = "release_unproven"
LEASE_OBLIGATION_UNPROVEN = "obligation_unproven"


def obligation(service, row):
    """The execution identity canonical authority must be asked about, or None.

    `None` only when the Goal is unpromoted and the canonical reader proves
    its store absent. A missing cutover marker alone is not that proof: it
    may have been lost after acquisition. Otherwise the canonical lease
    decides, whatever the operation recorded: a native claim commits before
    its annotation is saved, and an empty, malformed or stale `required: false`
    annotation is no more proof than a missing one. A valid recorded epoch still
    fences the release against another generation under the same key.
    """

    recorded = row.get("task_lease") if isinstance(row.get("task_lease"), dict) else {}
    required = recorded.get("required") is True
    if not local_authority_is_promoted(runtime_root=service.root, goal_id=service.goal_id):
        if required:
            raise ValueError("canonical authority disappeared under a required delegation lease")
        # Use the existing provider-first reader, not a second Python test of
        # provider paths or the operation's optional lease annotation. A loaded
        # snapshot (even with this Todo missing) cannot qualify as never promoted.
        snapshot = effect_runtime_result("coordination.local_authority.todo_read", {
            "schema_version": LOCAL_COORDINATION_TODO_READ_REQUEST_SCHEMA,
            "runtime_root": str(service.root), "goal_id": service.goal_id,
            "todo_id": row["identity"]["binding"]["todo_id"],
        }, timeout=CANONICAL_AUTHORITY_READ_TIMEOUT_SECONDS)
        if (snapshot.get("status") != "missing" or snapshot.get("provider_revision") is not None
                or snapshot.get("decision_read_from_provider") is not True
                or snapshot.get("legacy_fallback_used") is not False):
            raise ValueError("canonical authority absence unproven; reconcile the original authority route")
        return None
    acquired = recorded.get("lease") if required and isinstance(recorded.get("lease"), dict) else {}
    epoch = acquired.get("lease_epoch")
    # Only a valid generation can fence another generation. Malformed optional
    # evidence is no stronger than its absence; canonical identity still decides.
    return {"idempotency_key": service._turn_instance_id(row),
            "lease_epoch": epoch if type(epoch) is int and epoch > 0 else None}


def release(service, row, binding):
    """Release the lease only while this execution still holds it, at its current version.

    Called only once the typed stop owner found the stopped execution gone.
    Renewal advances the version, so the acquisition's version is no CAS for a
    later release. The canonical lease is read first: the exact owner, key and
    (when recorded) epoch must match before its current version is released.
    A lease another execution holds, or none at all, is `not_owed`: it is not
    this stop's to release and blocks nothing on its behalf. An authority that
    cannot be read leaves the obligation unproven rather than assumed absent.
    """

    try:
        owed = obligation(service, row)
    except _AUTHORITY_ERRORS as exc:
        return {"state": LEASE_OBLIGATION_UNPROVEN,
                "error": ("lease obligation unreadable: " + str(exc))[:180]}
    if owed is None:
        return {"state": LEASE_NOT_OWED}
    key = owed["idempotency_key"]
    try:
        inspection = inspect_task_lease(
            registry_path=service.registry, runtime_root=service.root,
            goal_id=service.goal_id, todo_id=binding["todo_id"],
        )
        if inspection.get("ok") is not True:
            raise RuntimeError(str(inspection.get("error") or "lease inspection unavailable"))
    except _AUTHORITY_ERRORS as exc:
        return {"state": LEASE_OBLIGATION_UNPROVEN, "idempotency_key": key,
                "error": ("lease obligation unreadable: " + str(exc))[:180]}
    held = inspection.get("lease")
    if (not isinstance(held, dict)
            or held.get("owner") != binding["agent_id"]
            or held.get("idempotency_key") != key
            or (owed["lease_epoch"] is not None and held.get("lease_epoch") != owed["lease_epoch"])):
        return {"state": LEASE_NOT_OWED, "idempotency_key": key}
    if held.get("status") == "released":
        return {"state": LEASE_RELEASED, "idempotency_key": key, "version": held.get("version")}
    try:
        result = release_task_lease(
            runtime_root=service.root, goal_id=service.goal_id, todo_id=binding["todo_id"],
            owner=binding["agent_id"], idempotency_key=key,
            expected_version=held.get("version"), registry_path=service.registry,
        )
    except _AUTHORITY_ERRORS as exc:
        return {"state": LEASE_RELEASE_UNPROVEN, "idempotency_key": key,
                "version": held.get("version"), "error": str(exc)[:180]}
    # A committed `missing` means the Todo has no lease at all, so none is held for us.
    state = (LEASE_RELEASED if result.get("released") is True
             else LEASE_NOT_OWED if result.get("missing") is True else LEASE_RELEASE_UNPROVEN)
    return {"state": state, "idempotency_key": key, "version": held.get("version")}


def settle(service, path, row, binding, stop):
    """Resolve the lease once and record what it proved on the stop receipt.

    Returns the lease fact for the typed stop owner. A release already proven
    on the receipt is not attempted again. Callers hold the operation's
    dispatch lock.
    """

    recorded = stop.get("lease") if isinstance(stop.get("lease"), dict) else {}
    if recorded.get("state") == LEASE_RELEASED:
        return LEASE_RELEASED
    resolved = release(service, row, binding)
    if resolved != stop.get("lease"):
        stop["lease"] = resolved
        _write(service._stop_path(path), stop)
    return resolved["state"]
