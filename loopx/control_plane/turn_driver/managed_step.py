"""The managed-step consumer of one bounded same-Turn continuation.

``loopx turn run-once`` commits a governed Turn and stops. When the host
failed for a retryable reason, the pure Turn Loop Controller already answers
``wait`` with a typed ``retry_continuation`` block, but nothing in production
consumed it: the outer scheduler was left to infer retryability from prose.

This module is that consumer. It rebuilds one validated receipt from the
canonical Turn journal, asks the controller for a disposition against a fresh
``loopx_turn_envelope_v0`` decision, and returns the typed answer. It is
read-only by construction:

- it never invokes a host, writes state, spends quota, or sleeps;
- the Turn journal stays the sole authority for attempt and max_attempts, so a
  caller-supplied observation is only ever reconciled, never believed;
- a disagreement between the observation and the journal is a ``ValueError`` at
  the typed-input boundary rather than a silently adopted number.

The caller decides whether to carry the existing ``--retry-failed-turn`` /
``--resume-turn-key`` flags on the next ``run-once``; this module grants no
execution authority of its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from .host_failure import normalize_host_failure_record, project_host_failure
from .loop_controller import (
    LOOP_CONTROLLER_DISPOSITION_SCHEMA_VERSION,
    ValidatedTurnReceipt,
    decide_loop_disposition,
)
from .turn_journal_runtime import interpret_turn_journal_projection
from .transaction import (
    LOOPX_TURN_EXECUTION_SCHEMA_VERSION,
    LOOPX_TURN_RECEIPT_VALIDATION_SCHEMA_VERSION,
    LoopXTurnResultKind,
)

LOOPX_TURN_MANAGED_STEP_SCHEMA_VERSION = "loopx_turn_managed_step_v0"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _require_positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _require_matching_attempts(journal_attempt: Any, failure_attempt: Any) -> None:
    """Refuse a journal whose two persisted attempt fields disagree.

    The executor increments ``host_attempt_count`` and ``record_host_failure``
    writes the same value into ``host_failure.attempt``. The controller reads
    the nested field for the retry ceiling while the projection reports the top
    level, so a divergence would present one attempt count and authorize on
    another.
    """

    authority = _require_positive_int(
        journal_attempt,
        field="Turn journal host_attempt_count",
    )
    nested = _require_positive_int(
        failure_attempt,
        field="Turn journal host_failure attempt",
    )
    if nested != authority:
        raise ValueError(
            "Turn journal host_failure attempt disagrees with host_attempt_count"
        )


def _validated_turn_receipt(journal: Mapping[str, Any]) -> ValidatedTurnReceipt:
    """Rebuild the controller receipt from the canonical journal alone.

    The journal stores the plan and the validated receipt rather than an
    execution payload, so the projection here is reconstructed from committed
    fields only. ``ValidatedTurnReceipt.from_execution`` then re-enforces the
    material-effect and lineage contracts, which keeps this reader honest
    without duplicating them.
    """

    plan = journal.get("plan")
    if not isinstance(plan, Mapping):
        raise TypeError("Turn journal has no stored plan to reconcile")
    stored_transaction = _mapping(plan.get("transaction"))
    turn_key = str(journal.get("turn_key") or "")
    if not turn_key or str(stored_transaction.get("turn_key") or "") != turn_key:
        raise ValueError("Turn journal turn_key does not match its stored transaction")

    receipt = journal.get("receipt")
    if not isinstance(receipt, Mapping):
        raise TypeError("Turn journal has no validated receipt to reconcile")
    result_kind = str(receipt.get("result_kind") or "")
    if receipt.get("schema_version") != LOOPX_TURN_RECEIPT_VALIDATION_SCHEMA_VERSION:
        raise ValueError("Turn journal receipt has an unsupported schema")
    if receipt.get("ok") is not True:
        raise ValueError("Turn journal receipt is not a validated receipt")

    execution: dict[str, Any] = {
        "ok": journal.get("status") == "committed",
        "schema_version": LOOPX_TURN_EXECUTION_SCHEMA_VERSION,
        "mode": "managed_step",
        "status": journal.get("status"),
        "result_kind": result_kind,
        "receipt": dict(receipt),
        "scheduler": _mapping(journal.get("scheduler")),
        **project_host_failure(journal),
    }
    settlement_result = journal.get("settlement_result")
    if isinstance(settlement_result, Mapping):
        execution["settlement_result"] = dict(settlement_result)
    writeback = _mapping(journal.get("writeback"))
    completion = writeback.get("completion")
    if isinstance(completion, Mapping):
        execution["todo_completion"] = dict(completion)
    return ValidatedTurnReceipt.from_execution(execution)


def managed_step_receipt_from_journal(
    journal: Mapping[str, Any],
    *,
    goal_id: str,
    agent_id: str,
    turn_key: str,
) -> ValidatedTurnReceipt:
    """Qualify one journaled Turn for the managed-step transition.

    Fails closed unless the journal is a finished failed Turn for exactly this
    goal/agent/turn identity whose typed host failure is retryable. The current
    snapshot must pass the canonical TS journal consistency check before the
    controller is called; historical recovery audits never establish eligibility.
    """

    if journal.get("status") != "failed":
        raise ValueError("managed step requires a failed Turn journal")
    if str(journal.get("turn_key") or "") != turn_key:
        raise ValueError("Turn journal turn_key does not match the requested Turn")
    result_kind = str(journal.get("result_kind") or "")
    if result_kind != LoopXTurnResultKind.HOST_FAILURE.value:
        raise ValueError("managed step requires a typed host failure Turn")

    failure = normalize_host_failure_record(journal.get("host_failure"))
    if failure.get("retryable") is not True:
        raise ValueError(
            f"host failure {failure.get('kind')} is not retryable; repair instead"
        )
    # The journal records the attempt twice: once as the top-level
    # `host_attempt_count` the executor increments, and once inside the typed
    # `host_failure` record the controller reads to decide whether the retry
    # ceiling is reached. `record_host_failure` writes them from one value, so a
    # disagreement means the journal was edited or corrupted. Preferring either
    # side would let the presentation report `3/3` while the controller still
    # authorized a retry, so refuse the Turn instead.
    _require_matching_attempts(
        journal.get("host_attempt_count"),
        failure.get("attempt"),
    )

    receipt = _validated_turn_receipt(journal)
    lineage = receipt.lineage
    if lineage["goal_id"] != goal_id or lineage["agent_id"] != agent_id:
        raise ValueError("Turn journal lineage does not match the requested goal/agent")
    # Historical recovery_audit is explanatory, not proof about this snapshot.
    # Reuse the same TS consistency rules as inspect-journal/run-once. This is
    # not a retry request: fresh host-session validation remains with run-once,
    # while the controller below owns the bounded retry disposition.
    inspection = interpret_turn_journal_projection(
        journal, goal_id=goal_id, agent_id=agent_id, turn_key=turn_key,
    )
    if inspection["journal_consistent"] is not True:
        raise ValueError(
            "Turn journal replay is blocked: " + ", ".join(cast(list[str], inspection["violations"]))
        )
    return receipt


def reconcile_observed_attempt(
    journal: Mapping[str, Any],
    *,
    observed_attempt: int | None,
    observed_max_attempts: int | None = None,
) -> None:
    """Reconcile a caller-supplied observation against the journal authority.

    The journal owns the attempt count. An observation that disagrees with it is
    a contract violation, not a correction, so this raises instead of preferring
    either side.
    """

    authority = _require_positive_int(
        journal.get("host_attempt_count"),
        field="Turn journal host_attempt_count",
    )
    failure = normalize_host_failure_record(journal.get("host_failure"))
    retry = _mapping(failure.get("retry"))
    # A non-retryable failure carries no retry policy, so there is no ceiling to
    # reconcile against; the caller is refused for the failure kind itself.
    max_attempts = (
        _require_positive_int(
            retry.get("max_attempts"),
            field="Turn journal retry max_attempts",
        )
        if failure.get("retryable") is True
        else None
    )
    if observed_attempt is not None:
        _require_positive_int(observed_attempt, field="observed_attempt")
        if observed_attempt != authority:
            raise ValueError(
                "observed_attempt disagrees with the Turn journal attempt authority"
            )
    if observed_max_attempts is not None:
        _require_positive_int(observed_max_attempts, field="observed_max_attempts")
        if max_attempts is not None and observed_max_attempts != max_attempts:
            raise ValueError(
                "observed_max_attempts disagrees with the Turn journal retry policy"
            )


def _managed_payload(
    disposition: Mapping[str, Any],
    *,
    turn_key: str,
    journal: Mapping[str, Any],
) -> dict[str, Any]:
    """Project the controller decision plus its journal-proven provenance."""

    payload: dict[str, Any] = {
        "schema_version": LOOPX_TURN_MANAGED_STEP_SCHEMA_VERSION,
        "disposition": disposition.get("disposition"),
        "reason": disposition.get("reason"),
        "turn_key": turn_key,
        "attempt": _require_positive_int(
            journal.get("host_attempt_count"),
            field="Turn journal host_attempt_count",
        ),
    }
    for key in (
        "lineage",
        "stop_scope",
        "goal_terminal",
        "continuation_required",
        "successor_todo_ids",
        "capability_action",
        "capability_action_required",
        "user_action",
    ):
        if key in disposition:
            payload[key] = disposition[key]
    retry_continuation = disposition.get("retry_continuation")
    if isinstance(retry_continuation, Mapping):
        payload["retry_continuation"] = dict(retry_continuation)
        # The journal is the authority for the budget; echoing it here keeps a
        # caller from having to trust the controller's copy.
        payload["max_attempts"] = _require_positive_int(
            retry_continuation.get("max_attempts"),
            field="retry_continuation max_attempts",
        )
    return payload


def decide_managed_step(
    journal: Mapping[str, Any],
    fresh_decision: Mapping[str, Any],
    *,
    goal_id: str,
    agent_id: str,
    turn_key: str,
    predecessor_turn_key: str | None = None,
    observed_attempt: int | None = None,
    observed_max_attempts: int | None = None,
) -> dict[str, Any]:
    """Decide the managed continuation for one failed Turn.

    ``fresh_decision`` is a fresh ``loopx_turn_envelope_v0`` from the control
    plane, exactly as ``run-once`` builds one. ``predecessor_turn_key`` carries
    the causal link to the failed Turn and is deliberately *not* written into
    the signed envelope; it defaults to the Turn under decision, which is the
    only successor relationship a managed step can prove on its own.

    The answer is typed and grants no execution authority: ``wait`` means "wake
    the same Turn after the bounded backoff", never "spend or write now".
    """

    journal = _mapping(journal)
    # Refuse a non-failed or non-host-failure Turn before any reconciliation, so
    # the caller's first error is about the input it actually supplied.
    if journal.get("status") != "failed":
        raise ValueError("managed step requires a failed Turn journal")
    if str(journal.get("result_kind") or "") != LoopXTurnResultKind.HOST_FAILURE.value:
        raise ValueError("managed step requires a typed host failure Turn")
    # Reconcile before deciding so a forged observation cannot influence which
    # branch is taken.
    reconcile_observed_attempt(
        journal,
        observed_attempt=observed_attempt,
        observed_max_attempts=observed_max_attempts,
    )
    receipt = managed_step_receipt_from_journal(
        journal,
        goal_id=goal_id,
        agent_id=agent_id,
        turn_key=turn_key,
    )
    envelope = _mapping(fresh_decision)
    if str(envelope.get("goal_id") or "") not in {"", goal_id}:
        raise ValueError("fresh decision goal_id does not match the managed step")
    if str(envelope.get("agent_id") or "") not in {"", agent_id}:
        raise ValueError("fresh decision agent_id does not match the managed step")
    disposition = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=envelope,
        predecessor_turn_key=(
            turn_key if predecessor_turn_key is None else predecessor_turn_key
        ),
    )
    if str(disposition.get("schema_version") or "") not in {
        "",
        LOOP_CONTROLLER_DISPOSITION_SCHEMA_VERSION,
    }:
        raise RuntimeError("Turn Loop Controller returned an unsupported schema")
    return _managed_payload(disposition, turn_key=turn_key, journal=journal)
