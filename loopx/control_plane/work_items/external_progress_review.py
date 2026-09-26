"""Turn typed external progress-review receipts into a replan trigger.

Receipts are written outside every core transaction by an optional observer
that evaluates scoped file deltas. This module reads only the normalized
receipt contract: no prose, no provider call, no authority. Its single output
is evidence for the existing autonomous replan obligation, and only when the
goal policy is `assist` and pins the goal contract revision the receipts must
be bound to.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .progress_observation import (
    _progress_turn_instance_id,
    progress_observation_from_run,
)

EXTERNAL_PROGRESS_REVIEW_TRIGGER_KIND = "external_progress_review_drift"
EXTERNAL_PROGRESS_REVIEW_TRIGGER_SCHEMA_VERSION = "external_progress_review_trigger_v0"
EXTERNAL_PROGRESS_REVIEW_SIGNALS: tuple[str, ...] = ("noul", "choice")
EXTERNAL_PROGRESS_REVIEW_FRONTIER_PREFIX = "progress_review:"
EXTERNAL_PROGRESS_REVIEW_PENDING_REASON = "pending_evaluation"
# Why a captured transition carries no verdict. None of these is drift and none
# is progress: they neither form a streak nor dissolve one that already formed.
EXTERNAL_PROGRESS_REVIEW_UNEVALUATED_REASONS: tuple[str, ...] = (
    "pending",  # queued by the observer, evaluation not finished
    "not_evaluated",  # not evaluated for another typed reason
    "failed",  # evaluation failed closed
    "abstained",  # no decided answer
    "stale",  # the observer invalidated the event
    "undecided",  # completed, but the selected signal is null
    "missing",  # no receipt for this transition
    "unattributed",  # ambiguous fallback identity
    "identity_conflict",  # receipt names another Agent or Todo
    "other_revision",  # receipt bound to a revision that is not pinned
)

RunKey = tuple[str, str, str]
AckRecorded = Callable[[dict[str, Any]], bool]
Verdict = tuple[str, str | None]


def _receipt_recency(receipt: Mapping[str, Any]) -> tuple[float, int]:
    """Order receipts for one transition: later evaluation wins.

    `sequence` is a per-observer-state counter and is not comparable across
    observer states; `recorded_at` is the evaluation clock and breaks ties
    between an observer that re-evaluated the same transition.
    """

    recorded = receipt.get("recorded_at")
    return (
        float(recorded)
        if isinstance(recorded, (int, float)) and not isinstance(recorded, bool)
        else 0.0,
        int(receipt.get("sequence") or 0),
    )


def _run_key(run: Mapping[str, Any]) -> RunKey:
    return (
        str(run.get("generated_at") or "").strip(),
        str(run.get("agent_id") or "").strip(),
        str(run.get("todo_id") or "").strip(),
    )


def _review_turn(run: Mapping[str, Any]) -> tuple[str | None, bool]:
    """Distinguish legacy absence from malformed or contradictory Turn claims."""
    settlement = run.get("settlement_identity")
    settled = (
        settlement.get("turn_instance_id") if isinstance(settlement, Mapping) else None
    )
    claims = [
        value for value in (run.get("turn_instance_id"), settled) if value is not None
    ]
    valid = [
        _progress_turn_instance_id({"turn_instance_id": value}) for value in claims
    ]
    invalid = any(value is None for value in valid) or len(set(valid)) > 1
    return (None if invalid else next(iter(valid), None)), invalid


def index_progress_review_receipts(
    receipts: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Mapping[str, Any] | None], dict[RunKey, Mapping[str, Any] | None]]:
    """Index receipts by Turn or exact (generated_at, agent_id, todo_id) fallback.

    Conflicting Turn attribution or different evidence under one fallback key
    maps to None. Later evaluations of the same identity remain comparable.
    """

    by_turn: dict[str, Mapping[str, Any] | None] = {}
    by_key: dict[RunKey, Mapping[str, Any] | None] = {}
    for receipt in receipts:
        if not isinstance(receipt, Mapping):
            continue
        run = receipt.get("run")
        if not isinstance(run, Mapping):
            continue
        sequence = receipt.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            continue
        turn, invalid = _review_turn(run)
        if invalid:
            continue
        if turn:
            if turn not in by_turn:
                by_turn[turn] = receipt
                continue
            previous = by_turn[turn]
            if previous is None or _identity_conflict(run, previous):
                # Recency only orders judgments about the *same* work identity.
                by_turn[turn] = None
            elif _receipt_recency(previous) < _receipt_recency(receipt):
                by_turn[turn] = receipt
            continue
        key = _run_key(run)
        if not key[0]:
            continue
        if key in by_key:
            existing = by_key[key]
            if existing is None or str(existing.get("evidence_id")) != str(
                receipt.get("evidence_id")
            ):
                by_key[key] = None
            elif _receipt_recency(existing) < _receipt_recency(receipt):
                by_key[key] = receipt
        else:
            by_key[key] = receipt
    return by_turn, by_key


def _identity_conflict(run: Mapping[str, Any], receipt: Mapping[str, Any]) -> bool:
    """Agent must be attributable; Todo absence matches only another absence."""

    receipt_run = receipt.get("run")
    if (
        not isinstance(receipt_run, Mapping)
        or not str(run.get("agent_id") or "").strip()
    ):
        return True
    for field in ("agent_id", "todo_id"):
        mine = str(run.get(field) or "").strip()
        theirs = str(receipt_run.get(field) or "").strip()
        if mine != theirs:
            return True
    return False


def _single_agent_id(runs: list[dict[str, Any]]) -> str | None:
    agent_ids = {
        str(run.get("agent_id") or "").strip() for run in runs if run.get("agent_id")
    }
    agent_ids.discard("")
    return next(iter(agent_ids)) if len(agent_ids) == 1 else None


def _verdict(
    run: Mapping[str, Any],
    receipt: Mapping[str, Any] | None,
    *,
    ambiguous: bool,
    signal: str,
    pinned: str,
) -> Verdict:
    """Classify one captured transition as drift, on_goal or unevaluated."""

    if receipt is None:
        return "unevaluated", "unattributed" if ambiguous else "missing"
    if _identity_conflict(run, receipt):
        return "unevaluated", "identity_conflict"
    status = str(receipt.get("status") or "")
    if status != "completed":
        if (
            status == "not_evaluated"
            and receipt.get("reason") == EXTERNAL_PROGRESS_REVIEW_PENDING_REASON
        ):
            return "unevaluated", "pending"
        if status in {"failed", "abstained", "stale"}:
            return "unevaluated", status
        return "unevaluated", "not_evaluated"
    if str(receipt.get("contract_revision") or "") != pinned:
        return "unevaluated", "other_revision"
    drift_signal = receipt.get("drift_signal")
    value = drift_signal.get(signal) if isinstance(drift_signal, Mapping) else None
    if value is True:
        return "drift", None
    if value is False:
        return "on_goal", None
    return "unevaluated", "undecided"


@dataclass(frozen=True)
class _ReviewScan:
    """Attribution-safe inputs prepared before the streak reducer runs."""

    normalized_agent_id: str
    lane: list[dict[str, Any]]
    fallback_counts: Counter[RunKey]
    run_turn_owners: dict[str, set[tuple[str, str]]]
    by_turn: dict[str, Mapping[str, Any] | None]
    by_key: dict[RunKey, Mapping[str, Any] | None]


def _prepare_review_scan(
    newest_first_runs: Iterable[dict[str, Any]],
    receipts: Iterable[Mapping[str, Any]],
    *,
    agent_id: str | None,
    neutral: set[str],
    ack_recorded: AckRecorded,
) -> _ReviewScan | None:
    """Select one accountable lane and pre-index only attributable evidence."""

    runs = [run for run in newest_first_runs if isinstance(run, dict)]
    normalized_agent_id = str(agent_id or "").strip() or _single_agent_id(runs)
    if not normalized_agent_id:
        return None
    lane: list[dict[str, Any]] = []
    for run in runs:
        owner = str(run.get("agent_id") or "").strip()
        if owner and owner != normalized_agent_id:
            continue
        _, invalid = _review_turn(run)
        if owner == normalized_agent_id and not invalid and ack_recorded(run):
            break
        if str(run.get("classification") or "").strip() in neutral:
            continue
        lane.append(run)
    fallback_counts = Counter(
        _run_key(run) for run in lane if _review_turn(run) == (None, False)
    )
    run_turn_owners: dict[str, set[tuple[str, str]]] = {}
    for run in lane:
        turn, _ = _review_turn(run)
        if turn:
            run_turn_owners.setdefault(turn, set()).add(_run_key(run)[1:])
    by_turn, by_key = index_progress_review_receipts(receipts)
    return _ReviewScan(
        normalized_agent_id=normalized_agent_id,
        lane=lane,
        fallback_counts=fallback_counts,
        run_turn_owners=run_turn_owners,
        by_turn=by_turn,
        by_key=by_key,
    )


def external_progress_review_trigger(
    newest_first_runs: Iterable[dict[str, Any]],
    *,
    receipts: Iterable[Mapping[str, Any]],
    agent_id: str | None,
    threshold: int,
    signal: str,
    contract_revision: str | None,
    ack_recorded: AckRecorded,
    neutral_classifications: Iterable[str] = (),
) -> dict[str, Any] | None:
    """Return a trigger when a drift streak formed and was not discharged.

    Formation and persistence are two rules over the same newest-first scan:

    - nothing triggers without a pinned goal contract revision;
    - the scan ends at an acknowledged autonomous replan (re-arm) or at a
      newer `completed` receipt whose selected signal is False: the policy's
      condition no longer holds, so nothing is open;
    - every transition is `drift` (completed, signal True, pinned revision),
      `on_goal` (completed, signal False) or `unevaluated` for one typed
      reason in EXTERNAL_PROGRESS_REVIEW_UNEVALUATED_REASONS;
    - a streak forms only from `threshold` consecutive drift transitions with
      no unevaluated transition between them (conservative formation);
    - once formed, newer unevaluated transitions neither extend nor dissolve
      it: absence of evaluation is not evidence that the problem was handled;
      they are reported so status can show how many verdicts are outstanding;
    - retries of one logical turn are one transition, one evidence id counts
      once, neutral bookkeeping rows are neither counted nor gaps;
    - the newest typed progress observation in the window, evaluated or not,
      becomes the obligation's `progress_baseline`, and every distinct typed
      observation in the window is carried as `progress_window`, so no claim
      the Agent has already made can acknowledge; a window without any typed
      observation raises nothing.
    """

    if signal not in EXTERNAL_PROGRESS_REVIEW_SIGNALS:
        return None
    neutral = {str(item) for item in neutral_classifications}
    pinned = str(contract_revision or "").strip()
    if not pinned:
        return None
    required = max(2, int(threshold))
    scan = _prepare_review_scan(
        newest_first_runs,
        receipts,
        agent_id=agent_id,
        neutral=neutral,
        ack_recorded=ack_recorded,
    )
    if scan is None:
        return None
    normalized_agent_id = scan.normalized_agent_id
    lane = scan.lane
    fallback_counts = scan.fallback_counts
    run_turn_owners = scan.run_turn_owners
    by_turn = scan.by_turn
    by_key = scan.by_key
    segment: list[tuple[str, dict[str, Any], Mapping[str, Any] | None, str | None]] = []
    window_runs: list[dict[str, Any]] = []
    seen_turns: set[str] = set()
    seen_evidence: set[str] = set()
    consecutive = longest = 0
    for run in lane:
        attributable = str(run.get("agent_id") or "").strip() == normalized_agent_id
        turn, invalid = _review_turn(run)
        if invalid:
            receipt, ambiguous = None, True
        elif turn:
            if turn in seen_turns:
                # A retry is not another transition, but its typed claim is
                # still part of this Turn's history until the ACK boundary.
                if attributable:
                    window_runs.append(run)
                continue
            seen_turns.add(turn)
            receipt = by_turn.get(turn)
            ambiguous = (turn in by_turn and receipt is None) or len(
                run_turn_owners[turn]
            ) > 1
            if ambiguous:
                receipt = None
        else:
            key = _run_key(run)
            receipt = by_key.get(key)
            ambiguous = (key in by_key and receipt is None) or fallback_counts[key] > 1
            if ambiguous:
                receipt = None
        verdict, reason = _verdict(
            run, receipt, ambiguous=ambiguous, signal=signal, pinned=pinned
        )
        if verdict == "on_goal":
            break
        if verdict == "drift":
            assert receipt is not None
            evidence_id = str(receipt.get("evidence_id") or "")
            if evidence_id in seen_evidence:
                window_runs.append(run)
                continue
            seen_evidence.add(evidence_id)
            consecutive += 1
            longest = max(longest, consecutive)
        elif longest >= required:
            # The streak above this gap already formed; older history, including
            # transitions captured before the observer existed, is not its concern.
            break
        else:
            consecutive = 0
        segment.append((verdict, run, receipt, reason))
        if attributable:
            window_runs.append(run)
    if longest < required:
        return None
    drift_rows = [
        (run, receipt) for verdict, run, receipt, _ in segment if verdict == "drift"
    ]
    latest_run, latest_receipt = drift_rows[0]
    assert latest_receipt is not None
    oldest_run = drift_rows[-1][0]
    # Carry every distinct typed claim in the window, newest first, whether or
    # not its evaluation finished: an acknowledgement must go beyond everything
    # already claimed, not only beyond the newest claim. Without any typed
    # observation to bind, an acknowledgement could not be told apart from a
    # repeat of the evaluated work, so nothing is raised.
    window: list[dict[str, Any]] = []
    window_fingerprints: set[str] = set()
    baseline_run: dict[str, Any] | None = None
    for run in window_runs:
        observation = progress_observation_from_run(run)
        if observation is None or observation["fingerprint"] in window_fingerprints:
            continue
        if baseline_run is None:
            baseline_run = run
        window_fingerprints.add(observation["fingerprint"])
        window.append(observation)
    if baseline_run is None or not window:
        return None
    baseline = window[0]
    by_reason: dict[str, int] = {}
    newer_than_latest_drift = 0
    for verdict, _, _, reason in segment:
        if verdict == "drift":
            break
        newer_than_latest_drift += 1
    for verdict, _, _, reason in segment:
        if verdict == "unevaluated" and reason:
            by_reason[reason] = by_reason.get(reason, 0) + 1
    return {
        "kind": EXTERNAL_PROGRESS_REVIEW_TRIGGER_KIND,
        "schema_version": EXTERNAL_PROGRESS_REVIEW_TRIGGER_SCHEMA_VERSION,
        "section": "run_history",
        "signal": signal,
        "run_count": len(drift_rows),
        "threshold": required,
        "consecutive_drift": longest,
        "unevaluated_transitions": {
            "total": sum(by_reason.values()),
            "newer_than_latest_drift": newer_than_latest_drift,
            "by_reason": by_reason,
        },
        "agent_id": normalized_agent_id
        or _single_agent_id([run for run, _ in drift_rows]),
        "contract_revision": pinned,
        "evidence_ids": [
            str(receipt["evidence_id"]) for _, receipt in drift_rows if receipt
        ],
        "receipt_ids": [
            str(receipt["receipt_id"]) for _, receipt in drift_rows if receipt
        ],
        "latest_generated_at": str(latest_run.get("generated_at") or ""),
        "oldest_counted_generated_at": str(oldest_run.get("generated_at") or ""),
        "latest_judgments": latest_receipt.get("judgments"),
        "progress_baseline": baseline,
        "progress_fingerprint": baseline["fingerprint"],
        "baseline_generated_at": str(baseline_run.get("generated_at") or ""),
        "progress_window": window,
        "frontier_identity": EXTERNAL_PROGRESS_REVIEW_FRONTIER_PREFIX
        + str(latest_receipt["evidence_id"]),
        # The model holds no authority; the Goal owner's assist policy does.
        "model_authority": "none",
        "effect": "required_obligation_under_goal_policy",
    }


def external_progress_review_obligation(
    newest_first_runs: list[dict[str, Any]],
    *,
    external_progress_review: Mapping[str, Any] | None,
    agent_id: str | None,
    ack_recorded: AckRecorded,
    build_obligation: Callable[..., dict[str, Any] | None],
    agent_todos: dict[str, Any] | None,
    neutral_classifications: Iterable[str] = (),
) -> dict[str, Any] | None:
    """Raise the existing obligation from receipts only under an `assist` policy."""

    if not isinstance(external_progress_review, Mapping):
        return None
    policy = external_progress_review.get("policy")
    if not isinstance(policy, Mapping) or policy.get("mode") != "assist":
        return None
    raw_receipts = external_progress_review.get("receipts")
    trigger = external_progress_review_trigger(
        newest_first_runs,
        receipts=raw_receipts if isinstance(raw_receipts, list) else [],
        agent_id=agent_id,
        threshold=int(policy.get("drift_threshold") or 2),
        signal=str(policy.get("signal") or "noul"),
        contract_revision=(
            str(policy["contract_revision"])
            if policy.get("contract_revision")
            else None
        ),
        ack_recorded=ack_recorded,
        neutral_classifications=neutral_classifications,
    )
    if not trigger:
        return None
    return build_obligation([trigger], agent_todos=agent_todos)


__all__ = [
    "EXTERNAL_PROGRESS_REVIEW_FRONTIER_PREFIX",
    "EXTERNAL_PROGRESS_REVIEW_PENDING_REASON",
    "EXTERNAL_PROGRESS_REVIEW_SIGNALS",
    "EXTERNAL_PROGRESS_REVIEW_TRIGGER_KIND",
    "EXTERNAL_PROGRESS_REVIEW_TRIGGER_SCHEMA_VERSION",
    "EXTERNAL_PROGRESS_REVIEW_UNEVALUATED_REASONS",
    "external_progress_review_obligation",
    "external_progress_review_trigger",
    "index_progress_review_receipts",
]
