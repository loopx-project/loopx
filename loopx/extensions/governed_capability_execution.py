"""Provider-neutral governed execution for material external capabilities.

The provider owns its domain operation and asynchronous run. LoopX owns the
selected-Todo admission, durable invocation journal, typed effect receipt,
writeback-before-spend ordering, and settlement replay identity.
"""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..control_plane.effect_program import settlement_result_payload
from ..control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..control_plane.host_adapter_settlement import (
    HostGuardState,
    classify_host_guard_snapshot,
)
from ..control_plane.runtime.document_io import fsync_state_directory
from ..control_plane.runtime.public_safety import validate_public_safe_value
from ..control_plane.runtime.runtime_projection_route import resolve_goal_source_runtime_route
from ..control_plane.turn_driver.settlement import execute_turn_driver_settlement
from ..control_plane.turn_driver.transaction import (
    TRANSACTION_PHASES,
    build_loopx_turn_transaction_plan,
)
from ..control_plane.work_items.governed_transition_proposal import (
    STEWARD_TEAM_PLAN_PREVIEW_KIND,
    GovernedTransitionSettlementPhase,
    settle_governed_transition_proposals,
    validate_governed_transition_receipts,
)
from ..control_plane.work_items.team_plan_adapter import team_plan_state_fingerprint
from ..file_lock import exclusive_file_lock, write_json_atomic
from ..paths import RESTORED_RUNTIME_MARKER, select_default_runtime_root
from .capability_admission import prepare_external_capability_invocation
from .runtime import _execute_extension_runtime_binding

GOVERNED_CAPABILITY_RUN_SCHEMA_VERSION = "loopx_governed_capability_run_v0"
GOVERNED_CAPABILITY_RECEIPT_SCHEMA_VERSION = (
    "loopx_governed_capability_execution_receipt_v0"
)
GOVERNED_CAPABILITY_LIFECYCLE_PACKET_SCHEMA_VERSION = (
    "loopx_governed_capability_lifecycle_packet_v0"
)
GOVERNED_CAPABILITY_LIFECYCLE_REDUCTION_SCHEMA_VERSION = (
    "loopx_governed_capability_lifecycle_reduction_v0"
)
MaterialEffect = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def default_governed_capability_run_dir(
    runtime_root: str | Path | None = None,
) -> Path:
    root = (
        Path(runtime_root).expanduser()
        if runtime_root is not None
        else select_default_runtime_root()
    )
    return root / "extensions" / "governed-capability-runs"


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    import hashlib

    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate_governed_capability_result(
    value: Mapping[str, Any],
    *,
    invocation_id: str,
    effect_id: str,
    operation: Mapping[str, Any],
) -> dict[str, Any]:
    """Adapt the TS-owned standalone provider-result validation."""

    payload = effect_runtime_result(
        "governed_capability.validate_result",
        {
            "value": dict(value),
            "invocation_id": invocation_id,
            "effect_id": effect_id,
            "result_schema": operation.get("result_schema"),
            "effect_class": operation.get("effect_class"),
            "transition_contract": operation.get("transition_contract"),
        },
    )
    if not isinstance(payload, Mapping):
        raise RuntimeError(  # noqa: TRY004 -- remote protocol failure, not caller input
            "TypeScript governed capability result shape mismatch"
        )
    result = _mapping(payload.get("result"), "external capability result")
    if str(payload.get("journal_status") or "") not in {
        "running",
        "ready_to_settle",
    }:
        raise RuntimeError("TypeScript governed capability status shape mismatch")
    validate_public_safe_value(result, path="provider_result")
    return result


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return {str(key): deepcopy(item) for key, item in value.items()}


def _journal_path(run_dir: str | Path, invocation_id: str) -> Path:
    suffix = invocation_id.removeprefix("capability-")
    if (
        not invocation_id.startswith("capability-")
        or len(suffix) != 24
        or any(character not in "0123456789abcdef" for character in suffix)
    ):
        raise ValueError("governed capability invocation_id is invalid")
    return Path(run_dir).expanduser() / f"{invocation_id}.json"


def _read_journal(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("governed capability invocation does not exist") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            "governed capability invocation journal is unreadable"
        ) from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != GOVERNED_CAPABILITY_RUN_SCHEMA_VERSION
    ):
        raise ValueError("governed capability invocation journal is invalid")
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise ValueError("governed capability invocation journal must use mode 0600")
    return value


def _write_journal(path: Path, value: Mapping[str, Any]) -> None:
    write_json_atomic(path, value, fsync_directory=True)


def _restored_runtime(registry_path: str | Path | None, goal_id: str, run_dir: str | Path) -> bool:
    runs = Path(run_dir).expanduser().resolve()
    # An explicit journal location may be outside the Goal's restored runtime.
    roots = {runs, *runs.parents}
    if registry_path is not None:
        route = resolve_goal_source_runtime_route(registry_path=Path(registry_path), goal_id=goal_id)
        roots.update((Path(route["source_runtime_root"]), Path(route["invoked_runtime_root"])))
    for root in roots:
        try:
            (root / RESTORED_RUNTIME_MARKER).lstat()
        except FileNotFoundError:
            continue
        return True
    return False


def _start_scope(journal: Mapping[str, Any]) -> str:
    # The handoff can change both actor and Turn. Neither identifies the work to repeat.
    request = journal["request"]
    return _canonical_digest([
        journal["goal_id"], journal["todo_id"], request["capability_id"], request["operation"],
    ]).removeprefix("sha256:")


def _restored_start_reservation(
    journal: Mapping[str, Any], reserve: Callable[[Mapping[str, Any]], Mapping[str, Any] | None],
) -> dict[str, Any]:
    request = journal["request"]
    target = journal["admitted_target_key"]
    proof = {
        "operation_key": _canonical_digest([journal["goal_id"], request["capability_id"], request["operation"], target]),
        "intent_digest": _canonical_digest([request["goal"], request["provider"], request["context_refs"], request["input"]]),
        "request_digest": journal["request_digest"],
        "invocation_id": journal["invocation_id"],
        "challenge": secrets.token_hex(16),
    }
    # The service must atomically fence the old work and grant this challenge once.
    receipt = reserve({**proof, "target_key": target, "request": deepcopy(request)})
    if (
        not isinstance(receipt, Mapping)
        or receipt.get("status") != "reserved_after_terminal_no_effect"
        or any(receipt.get(key) != value for key, value in proof.items())
    ):
        raise ValueError("restored external start lacks a current provider reservation after terminal no-effect")
    ref = receipt.get("reservation_ref")
    if not isinstance(ref, str) or not ref.strip() or len(ref) > 256 or not ref.isprintable():
        raise ValueError("restored external start requires a bounded provider reservation reference")
    checked = {**proof, "status": receipt["status"], "reservation_ref": ref}
    validate_public_safe_value(checked, path="provider_reservation")
    return checked


def _record_start_intent(
    path: Path, journal: dict[str, Any],
    reserve_restored_start: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None,
) -> None:
    """Reserve a Todo operation across Turns before letting its provider start."""
    index = path.parent / ".start-reservations-v1"
    index.mkdir(mode=0o700, exist_ok=True)
    with exclusive_file_lock(index / "catalog", operation="reserve_external_start"):
        initialized = index / "initialized"
        if not initialized.exists():
            # Older journals have only a per-Turn name. Index them once without changing them.
            for previous_path in path.parent.glob("capability-" + "[0-9a-f]" * 24 + ".json"):
                previous = _read_journal(previous_path)
                if _journal_path(path.parent, previous["invocation_id"]) != previous_path:
                    raise ValueError("external write journal identity mismatch during indexing")
                group = index / _start_scope(previous)
                group.mkdir(mode=0o700, exist_ok=True)
                (group / previous_path.stem).touch(mode=0o600, exist_ok=True)
                fsync_state_directory(group / previous_path.stem)
            fsync_state_directory(initialized)
            initialized.touch(mode=0o600, exist_ok=False)

    group = index / _start_scope(journal)
    # A service lookup serializes only other starts of this operation.
    with exclusive_file_lock(group, operation="reserve_external_start"):
        group.mkdir(mode=0o700, exist_ok=True)
        for reservation in group.iterdir():
            previous_path = _journal_path(path.parent, reservation.name)
            if not previous_path.exists():
                raise ValueError(f"external start has no original journal: {reservation.name}")
            previous = _read_journal(previous_path)
            if (_start_scope(previous) != group.name or previous["invocation_id"] != reservation.name):
                raise ValueError("external start reservation does not match the original request")
            undispatched = previous.get("dispatch_armed") is False and previous.get("provider_result") is None
            if not undispatched and previous.get("status") != "committed":
                raise ValueError(f"external write is unsettled; reconcile original invocation {reservation.name}")
            _reduce_governed_capability_journal(
                previous, invocation_id=reservation.name, phase="inspect", dry_run=False,
            )
            reservation.unlink()
        # Only the armed checkpoint permits delivery. A grant request alone cannot deliver.
        observed = journal.get("provider_result") is not None
        journal["dispatch_armed"] = observed
        _write_journal(path, journal)
        (group / path.stem).touch(mode=0o600, exist_ok=False)
        fsync_state_directory(group / path.stem)
        fsync_state_directory(group)
        if not observed:
            if reserve_restored_start is not None:
                journal["restored_start_reservation"] = _restored_start_reservation(journal, reserve_restored_start)
            journal["dispatch_armed"] = True
            _write_journal(path, journal)


def _settlement_identity(transaction_plan: Mapping[str, Any]) -> dict[str, Any]:
    settlement = _mapping(transaction_plan.get("settlement_plan"), "settlement plan")
    return _mapping(settlement.get("identity"), "settlement identity")


def _may_propose_team_plan(operation_profile: Mapping[str, Any]) -> bool:
    """Whether this operation's declared contract admits a team-plan proposal.

    A provider result is validated against the operation's own contract, so a
    team plan can only arrive for an operation that declares the kind.
    """

    contract = operation_profile.get("transition_contract")
    kinds = contract.get("proposal_kinds") if isinstance(contract, Mapping) else None
    return isinstance(kinds, list) and STEWARD_TEAM_PLAN_PREVIEW_KIND in kinds


def _journal_registry_path(journal: Mapping[str, Any]) -> Path | None:
    if journal.get("kernel_context") is None:
        if journal.get("kernel_context_digest") is not None:
            raise ValueError("governed capability kernel context is invalid")
        return None
    context = _mapping(journal.get("kernel_context"), "governed kernel context")
    if journal.get("kernel_context_digest") != _canonical_digest(context):
        raise ValueError("governed capability kernel context digest is invalid")
    registry_path = str(context.get("registry_path") or "")
    if not registry_path:
        raise ValueError("governed capability kernel context is incomplete")
    return Path(registry_path)


def _require_admission(
    admission: Mapping[str, Any],
    *,
    expected_identity: Mapping[str, Any],
    require_should_run: bool,
    original_readback: bool = False,
) -> None:
    if require_should_run and admission.get("should_run") is not True:
        raise ValueError("governed capability admission requires should_run=true")
    guard = classify_host_guard_snapshot(
        json.dumps(dict(admission), ensure_ascii=False, sort_keys=True)
    )
    if guard.state is not HostGuardState.SELECTED:
        raise ValueError(guard.reason or "quota guard did not select a Todo")
    if guard.settlement_identity is None:
        raise ValueError("quota guard did not return a typed settlement identity")
    observed = guard.settlement_identity.as_dict()
    fields = ("goal_id", "todo_id") if original_readback else (
        "goal_id", "todo_id", "agent_id", "turn_instance_id", "effect_id",
    )
    if any(observed.get(key) != expected_identity.get(key) for key in fields):
        raise ValueError(
            "quota guard settlement identity does not match the invocation"
        )


def _reduce_governed_capability_journal(
    journal: Mapping[str, Any],
    *,
    invocation_id: str,
    phase: str,
    dry_run: bool,
    admission: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    """Validate one lifecycle state and project its canonical public receipt."""

    transaction_plan = _mapping(journal.get("transaction_plan"), "transaction plan")
    identity = _settlement_identity(transaction_plan)
    registry_path = _journal_registry_path(journal)
    transition_receipts = validate_governed_transition_receipts(
        journal.get("transition_receipts", [])
    )
    if transition_receipts and registry_path is None:
        raise ValueError("governed transition receipts require Kernel context")
    request = _mapping(journal.get("request"), "provider request")
    validate_public_safe_value(request, path="provider_request")
    operation_profile = _mapping(journal.get("operation_profile"), "operation profile")
    _mapping(journal.get("provider_binding"), "provider binding")
    raw_result = journal.get("provider_result")
    try:
        payload = effect_runtime_result(
            "governed_capability.validate_result",
            {
                "value": {
                    "schema_version": GOVERNED_CAPABILITY_LIFECYCLE_PACKET_SCHEMA_VERSION,
                    "phase": phase,
                    "dry_run": dry_run,
                    "canonical_request_digest": _canonical_digest(request),
                    "admission": dict(admission) if admission is not None else None,
                    "journal": dict(journal),
                },
                "invocation_id": invocation_id,
                "effect_id": identity.get("effect_id"),
                "result_schema": operation_profile.get("result_schema"),
                "effect_class": operation_profile.get("effect_class"),
                "transition_contract": operation_profile.get("transition_contract"),
            },
        )
    except EffectRuntimeRejected as exc:
        if phase != "inspect" or admission is not None:
            raise
        raise ValueError(str(exc)) from exc
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version")
        != GOVERNED_CAPABILITY_LIFECYCLE_REDUCTION_SCHEMA_VERSION
    ):
        raise RuntimeError("TypeScript governed capability lifecycle shape mismatch")
    journal_status = str(payload.get("journal_status") or "")
    if journal_status not in {
        "ready",
        "starting",
        "running",
        "ready_to_settle",
        "settlement_failed",
        "committed",
    }:
        raise RuntimeError("TypeScript governed capability status shape mismatch")
    reduced_result = payload.get("provider_result")
    result = (
        _mapping(reduced_result, "external capability result")
        if reduced_result is not None
        else None
    )
    if phase == "inspect" and isinstance(raw_result, Mapping) and result != raw_result:
        raise ValueError("governed capability journal provider result is invalid")
    if (
        phase == "inspect"
        and not isinstance(raw_result, Mapping)
        and result is not None
    ):
        raise ValueError("governed capability journal provider state is invalid")
    if result is not None:
        validate_public_safe_value(result, path="provider_result")
    receipt = _mapping(
        payload.get("public_receipt"),
        "governed capability public receipt",
    )
    if receipt.get("schema_version") != GOVERNED_CAPABILITY_RECEIPT_SCHEMA_VERSION:
        raise RuntimeError("TypeScript governed capability receipt shape mismatch")
    return result, journal_status, receipt


def _receipt_with_local_effects(
    receipt: Mapping[str, Any],
    journal: Mapping[str, Any],
) -> dict[str, Any]:
    """Overlay outcomes produced by Python-owned I/O and effect adapters."""

    projected = deepcopy(dict(receipt))
    projected["status"] = journal.get("status")
    provider_result = journal.get("provider_result")
    # Preserve the shipped Python canonical digest after managed-runtime number normalization.
    projected["provider_result_digest"] = (
        _canonical_digest(provider_result)
        if isinstance(provider_result, Mapping)
        else None
    )
    projected["transition_receipts"] = deepcopy(journal.get("transition_receipts", []))
    projected["settlement_result"] = deepcopy(journal.get("settlement_result"))
    effects = _mapping(projected.get("effects"), "governed capability effects")
    effects.update(
        {
            "loopx_transitions_written": bool(journal.get("transition_receipts")),
            "loopx_state_written": isinstance(journal.get("writeback"), Mapping),
            "quota_spent": isinstance(journal.get("quota_spend"), Mapping),
        }
    )
    projected["effects"] = effects
    return projected


def _settle_journal_transition_proposals(
    *,
    journal: dict[str, Any],
    path: Path,
    phase: GovernedTransitionSettlementPhase,
) -> None:
    provider_result = _mapping(
        journal.get("provider_result"), "external capability result"
    )
    raw_proposals = provider_result.get("transition_proposals")
    if not isinstance(raw_proposals, list):
        raise ValueError("external capability transition_proposals must be an array")
    if not raw_proposals:
        return
    registry_path = _journal_registry_path(journal)
    if registry_path is None:
        raise ValueError("governed transition proposals require Kernel context")

    def checkpoint(receipts: list[dict[str, Any]]) -> None:
        journal["transition_receipts"] = deepcopy(receipts)
        _write_journal(path, journal)

    journal["transition_receipts"] = settle_governed_transition_proposals(
        registry_path=registry_path,
        goal_id=str(journal["goal_id"]),
        agent_id=str(journal["agent_id"]),
        effect_id=str(journal["effect_id"]),
        proposals=[
            _mapping(item, "governed transition proposal") for item in raw_proposals
        ],
        existing_receipts=journal.get("transition_receipts", []),
        checkpoint=checkpoint,
        phase=phase,
        team_plan_state_basis=journal.get("team_plan_state_basis"),
    )


def _has_unsettled_start_transition(journal: Mapping[str, Any]) -> bool:
    """Return whether replaying start can still write a monitor transition."""

    provider_result = _mapping(
        journal.get("provider_result"), "external capability result"
    )
    raw_proposals = provider_result.get("transition_proposals")
    if not isinstance(raw_proposals, list):
        raise ValueError("external capability transition_proposals must be an array")
    receipt_ids = {
        str(receipt["proposal_id"])
        for receipt in validate_governed_transition_receipts(
            journal.get("transition_receipts", [])
        )
    }
    return any(
        str(proposal.get("kind") or "") == "continuous_monitor_upsert"
        and str(proposal.get("proposal_id") or "") not in receipt_ids
        for proposal in (
            _mapping(item, "governed transition proposal") for item in raw_proposals
        )
    )


def start_governed_external_capability(
    *,
    state_file: str | Path,
    run_dir: str | Path,
    registry_path: str | Path,
    goal_id: str,
    agent_id: str,
    todo_id: str,
    turn_instance_id: str,
    capability_id: str,
    operation: str,
    provider_input: Mapping[str, Any],
    admission: Mapping[str, Any],
    execute: bool = False,
    environment: Mapping[str, str] | None = None,
    read_start_result: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None,
    reserve_restored_start: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Start once, or recover the result by identity using a trusted read-only provider lookup."""

    transaction_plan = build_loopx_turn_transaction_plan(
        planned=True,
        lineage={"goal_id": goal_id, "agent_id": agent_id, "todo_id": todo_id},
        host="external-capability-provider",
        execution_mode="governed-asynchronous",
        session_action="resume",
        turn_instance_id=turn_instance_id,
    )
    identity = _settlement_identity(transaction_plan)
    prepared = prepare_external_capability_invocation(
        state_file=state_file,
        capability_id=capability_id,
        operation=operation,
        registry_path=registry_path,
        goal_id=goal_id,
        provider_input=provider_input,
        authority=identity,
    )
    operation_profile = _mapping(prepared["operation_profile"], "operation profile")
    if operation_profile.get("effect_class") != "external_write":
        raise ValueError("governed capability execution requires external_write")
    if read_start_result is not None and reserve_restored_start is not None:
        raise ValueError("choose original receipt readback or conditional restored start")
    _require_admission(
        admission, expected_identity=identity, require_should_run=not execute or read_start_result is not None,
        original_readback=read_start_result is not None,
    )
    restored = _restored_runtime(registry_path, goal_id, run_dir)
    if reserve_restored_start is not None and not restored:
        raise ValueError("conditional restored start requires a restored runtime")
    request = _mapping(prepared["request"], "provider request")
    request["lifecycle"] = {
        "phase": "start",
        "idempotency_key": identity["effect_id"],
    }
    validate_public_safe_value(request, path="provider_request")
    request_digest = _canonical_digest(request)
    binding = _mapping(prepared["binding"], "provider binding")
    invocation_id = str(prepared["invocation_id"])
    kernel_context = {
        "registry_path": str(Path(registry_path).expanduser().resolve()),
    }
    journal: dict[str, Any] = {
        "schema_version": GOVERNED_CAPABILITY_RUN_SCHEMA_VERSION,
        "status": "ready" if not execute else "starting",
        "invocation_id": invocation_id,
        "request_digest": request_digest,
        "request": request,
        "provider_binding": binding,
        "operation_profile": operation_profile,
        "transaction_plan": transaction_plan,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "todo_id": todo_id,
        "turn_instance_id": turn_instance_id,
        "effect_id": identity["effect_id"],
        "kernel_context": kernel_context,
        "kernel_context_digest": _canonical_digest(kernel_context),
        "completed_phases": [],
        "provider_result": None,
        "transition_receipts": [],
        "writeback": None,
        "quota_spend": None,
        "settlement_result": None,
    }
    if not execute:
        if restored and read_start_result is None and reserve_restored_start is None:
            raise ValueError("restored runtime requires provider reconciliation before external start")
        _provider_result, _journal_status, receipt = (
            _reduce_governed_capability_journal(
                journal,
                invocation_id=invocation_id,
                phase="inspect",
                dry_run=True,
                admission=admission,
            )
        )
        return _receipt_with_local_effects(receipt, journal)

    path = _journal_path(run_dir, invocation_id)
    with exclusive_file_lock(path, operation="start_governed_external_capability"):
        original_present = path.exists()
        if original_present:
            current = _read_journal(path)
            current_result, _current_status, receipt = (
                _reduce_governed_capability_journal(
                    current,
                    invocation_id=invocation_id,
                    phase="inspect",
                    dry_run=False, admission=admission if read_start_result is not None else None,
                )
            )
            if (
                current.get("request_digest") != request_digest
                or current.get("effect_id") != identity["effect_id"]
            ):
                raise ValueError("governed capability invocation replay does not match")
            if (
                read_start_result is not None
                and current.get("admitted_target_key") is not None
                and current["admitted_target_key"] != admission["selected_todo"].get("target_key")
            ):
                raise ValueError("current Todo target does not match original external write")
            if current_result is not None:
                if _has_unsettled_start_transition(current):
                    _require_admission(
                        admission,
                        expected_identity=identity,
                        require_should_run=True, original_readback=read_start_result is not None,
                    )
                    _reduce_governed_capability_journal(
                        current,
                        invocation_id=invocation_id,
                        phase="inspect",
                        dry_run=False,
                        admission=admission,
                    )
                _settle_journal_transition_proposals(
                    journal=current,
                    path=path,
                    phase=GovernedTransitionSettlementPhase.PRE_SETTLEMENT,
                )
                return _receipt_with_local_effects(receipt, current)
            if read_start_result is None and current.get("dispatch_armed") is not False:
                raise ValueError(
                    "external write outcome unknown; preserve the original invocation "
                    "and supply provider readback before any new start"
                )
            journal = current
        elif read_start_result is not None:
            if not restored:
                raise ValueError("no original external write invocation for provider readback")
            if _may_propose_team_plan(operation_profile):
                raise ValueError("original team plan state basis required for restored readback")

        _require_admission(
            admission,
            expected_identity=identity,
            require_should_run=True, original_readback=read_start_result is not None,
        )
        _reduce_governed_capability_journal(
            journal,
            invocation_id=invocation_id,
            phase="inspect",
            dry_run=False,
            admission=admission,
        )
        if read_start_result is not None:
            target_key = admission["selected_todo"]["target_key"]
            provider_result = read_start_result({
                "invocation_id": invocation_id,
                "idempotency_key": identity["effect_id"],
                "request_digest": request_digest,
                "provider": request["provider"],
                "operation": operation,
                "goal": request["goal"],
                "target_key": target_key,
            })
            if provider_result is None:
                raise ValueError("external write outcome unknown after provider readback")
            if restored:
                if (provider_result.get("request_digest") != request_digest
                        or provider_result.get("target_key") != target_key):
                    raise ValueError("provider readback does not attest the original request digest and target")
                provider_result = _mapping(provider_result.get("result"), "provider readback result")
        else:
            # A team plan the provider proposes is settled against the state
            # this agent saw before the provider ran, not against whatever the
            # settlement later finds. The basis is bound once here, stored with
            # the journal and never recomputed, so a replay keeps the same
            # operation identity and a plan the provider shaped after the state
            # moved is refused as stale. The provider never supplies it. Only
            # an operation whose contract admits a team plan binds one; no
            # shipped contract does yet, so other starts are unchanged.
            if _may_propose_team_plan(operation_profile):
                journal["team_plan_state_basis"] = team_plan_state_fingerprint(
                    registry_path=Path(registry_path),
                    goal_id=goal_id,
                    basis_agent_id=agent_id,
                )
            if restored and reserve_restored_start is None:
                raise ValueError("restored runtime requires provider reconciliation before external start")
            journal["admitted_target_key"] = admission["selected_todo"]["target_key"]
            _record_start_intent(path, journal, reserve_restored_start)
            dispatch_request = request
            if reserve_restored_start is not None:
                dispatch_request = {**request, "lifecycle": {
                    **request["lifecycle"], "restored_start_reservation": journal["restored_start_reservation"],
                }}
            provider_result = _execute_extension_runtime_binding(binding, request=dispatch_request, environment=environment)
        journal["provider_result"] = _mapping(
            provider_result,
            "external capability result",
        )
        validated, journal_status, receipt = _reduce_governed_capability_journal(
            journal,
            invocation_id=invocation_id,
            phase="observe_result",
            dry_run=False,
        )
        if validated is None:
            raise RuntimeError("TypeScript governed capability result shape mismatch")
        journal["provider_result"] = validated
        journal["status"] = journal_status
        if read_start_result is not None and not original_present:
            journal["admitted_target_key"] = admission["selected_todo"]["target_key"]
            _record_start_intent(path, journal)
        else:
            _write_journal(path, journal)
        _settle_journal_transition_proposals(
            journal=journal,
            path=path,
            phase=GovernedTransitionSettlementPhase.PRE_SETTLEMENT,
        )
        return _receipt_with_local_effects(receipt, journal)


def _settlement_callback(
    effect: MaterialEffect,
    *,
    context: Mapping[str, Any],
    effect_receipt_digest: str,
    effect_id: str,
    require_receipt_digest: bool,
) -> dict[str, Any]:
    payload = effect_runtime_result(
        "governed_capability.validate_settlement_callback",
        {
            "payload": dict(effect(context)),
            "effect_id": effect_id,
            "effect_receipt_digest": effect_receipt_digest,
            "require_receipt_digest": require_receipt_digest,
        },
    )
    return _mapping(payload, "governed capability settlement callback")


def reconcile_governed_external_capability(
    *,
    run_dir: str | Path,
    invocation_id: str,
    writeback: MaterialEffect,
    spend: MaterialEffect,
    environment: Mapping[str, str] | None = None,
    read_running_result: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Poll and idempotently settle one asynchronous material invocation."""

    path = _journal_path(run_dir, invocation_id)
    with exclusive_file_lock(path, operation="reconcile_governed_external_capability"):
        journal = _read_journal(path)
        provider_result, _journal_status, receipt = _reduce_governed_capability_journal(
            journal,
            invocation_id=invocation_id,
            phase="inspect",
            dry_run=False,
        )
        if journal.get("status") == "committed":
            return _receipt_with_local_effects(receipt, journal)
        identity = _settlement_identity(
            _mapping(journal.get("transaction_plan"), "transaction plan")
        )
        if provider_result is None:
            raise ValueError("governed capability provider start has no receipt")
        if provider_result.get("status") == "running":
            request = _mapping(journal.get("request"), "provider request")
            request["lifecycle"] = {
                "phase": "reconcile",
                "idempotency_key": identity["effect_id"],
                "follow_up": deepcopy(provider_result.get("follow_up")),
            }
            if read_running_result is not None:
                observed = read_running_result(request)
                if observed is None:
                    raise ValueError("provider has no verified result for the running external write")
            else:
                if _restored_runtime(_journal_registry_path(journal), str(journal["goal_id"]), run_dir):
                    raise ValueError("restored runtime requires provider readback to reconcile a running external write")
                observed = _execute_extension_runtime_binding(
                    _mapping(journal.get("provider_binding"), "provider binding"),
                    request=request, environment=environment,
                )
            journal["provider_result"] = _mapping(
                observed,
                "external capability result",
            )
            provider_result, journal_status, receipt = (
                _reduce_governed_capability_journal(
                    journal,
                    invocation_id=invocation_id,
                    phase="observe_result",
                    dry_run=False,
                )
            )
            if provider_result is None:
                raise RuntimeError(
                    "TypeScript governed capability result shape mismatch"
                )
            journal["provider_result"] = provider_result
            journal["status"] = journal_status
            _write_journal(path, journal)
            _settle_journal_transition_proposals(
                journal=journal,
                path=path,
                phase=GovernedTransitionSettlementPhase.PRE_SETTLEMENT,
            )
            if provider_result["status"] == "running":
                return _receipt_with_local_effects(receipt, journal)

        _settle_journal_transition_proposals(
            journal=journal,
            path=path,
            phase=GovernedTransitionSettlementPhase.PRE_SETTLEMENT,
        )

        effect_receipt = _mapping(
            provider_result.get("effect_receipt"), "external effect receipt"
        )
        effect_receipt_digest = _canonical_digest(effect_receipt)
        context = {
            "settlement_identity": identity,
            "invocation_id": invocation_id,
            "provider_result_digest": _canonical_digest(provider_result),
            "effect_receipt": effect_receipt,
            "effect_receipt_digest": effect_receipt_digest,
            "transition_receipts": deepcopy(journal["transition_receipts"]),
        }

        def checkpoint(
            step_kind: object,
            payload: Mapping[str, Any],
            phases: tuple[str, ...],
        ) -> None:
            step_name = str(getattr(step_kind, "value", step_kind))
            journal["writeback" if step_name == "durable_writeback" else step_name] = (
                dict(payload)
            )
            journal["completed_phases"] = list(phases)
            _write_journal(path, journal)

        settlement = execute_turn_driver_settlement(
            _mapping(journal.get("transaction_plan"), "transaction plan"),
            transaction_phases=TRANSACTION_PHASES,
            completed_phases=(
                tuple(str(item) for item in journal["completed_phases"])
                if isinstance(journal.get("completed_phases"), list)
                and journal["completed_phases"]
                else ("host_execute", "typed_result", "validation")
            ),
            writeback_payload=(
                journal.get("writeback")
                if isinstance(journal.get("writeback"), Mapping)
                else None
            ),
            quota_spend_payload=(
                journal.get("quota_spend")
                if isinstance(journal.get("quota_spend"), Mapping)
                else None
            ),
            writeback=lambda: _settlement_callback(
                writeback,
                context=context,
                effect_receipt_digest=effect_receipt_digest,
                effect_id=str(identity["effect_id"]),
                require_receipt_digest=True,
            ),
            spend=lambda: _settlement_callback(
                spend,
                context=context,
                effect_receipt_digest=effect_receipt_digest,
                effect_id=str(identity["effect_id"]),
                require_receipt_digest=False,
            ),
            checkpoint=checkpoint,
            committed_effect_id=str(identity["effect_id"]),
        )
        journal["settlement_result"] = settlement_result_payload(settlement)
        if settlement.failure is not None:
            journal["status"] = "settlement_failed"
            _write_journal(path, journal)
            return {**_receipt_with_local_effects(receipt, journal), "ok": False}
        _settle_journal_transition_proposals(
            journal=journal,
            path=path,
            phase=GovernedTransitionSettlementPhase.POST_SETTLEMENT,
        )
        journal["status"] = "committed"
        _write_journal(path, journal)
        return _receipt_with_local_effects(receipt, journal)
