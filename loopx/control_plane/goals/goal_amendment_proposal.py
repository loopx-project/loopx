"""Governed goal amendment proposal admission and retention (RFC Stage 2).

This adapter implements RFC §5 steps 1–2 for
``goal_amendment_proposal_v0``: a registered proposer submits a proposal,
the TypeScript-owned reducer (``goal.amendment_proposal.admit``) validates
schema completeness, actor identity, a closed ``amendment_class`` enum,
bounded evidence pointers, the causal membership of the linked replan
obligation and affected Todos (against authoritative typed inventories
this adapter derives), and the relation between the proposal's
``base_state_event_basis_sequence``/``base_source_basis_digest`` and the
currently derived goal basis, and the resulting admission record is
retained in an append-only journal.

Stage 2 is proposal only: admission has **zero canonical effect**. No goal
state is written, no frontier changes, and the state event log is never
appended to. The journal lives beside ``task-leases`` under
``runtime/goals/<goal_id>/amendment-proposals/`` so retention can never
advance the basis head it reports against.

Causal obligation authority is read-time derived, never caller-supplied:
admission re-derives the goal's open replan obligations from the quota
run-history ledger through the same bound projection entry point the
status/quota producers use (``_derive_open_replan_obligation_inventory``),
so the module exposes no obligation writer and consults no separate
receipt store.

There is no approval field, no ``approved`` status, and no commit path.
Admission outcomes are fact tokens only: ``admitted`` (including the
``base_source_basis_unverifiable`` fact when no event log exists) or
``needs_rebase`` (a behind or digest-mismatched base stays admitted and
retained, never silently dropped or merged — RFC §7). A schema violation
or an invalid causal reference (a replan obligation or affected Todo that
is not an open member of this Goal) fails closed as a request rejection,
the equivalent ``rejected_schema`` outcome, and nothing is retained.
Governed commit belongs to Stage 3+ behind the
``GoalAmendmentAuthority``.

The proposal declares its own ``base_revision_basis`` — the type of the
Stage 1 basis it was produced against — so the reducer validates sequence
producibility against the *claimed* basis, never against the goal's
current derived basis. When a Goal's basis later evolves from markdown to
a typed event log, a proposal still carrying its real markdown base
(sequence 0 + the markdown source digest) is retained as ``needs_rebase``
with the ``base_revision_basis_superseded`` fact: an explicit, read-back
reconciliation outcome rather than a rejection that would call a produced
basis a fabricated history.

The derived basis (``state_event_basis_sequence``, ``revision_basis``,
``source_basis_digest``) comes from the Stage 1 read-only projection
(``project_shared_goal_alignment``), which also fails closed on unknown
goals and unregistered proposers. This is an event-log-derived source
projection basis, not a canonical intent revision/digest: the RFC §3.1
intent envelope has no typed storage yet, and the proposal binds to the
basis under Stage 1's downgraded naming.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...agent_registry import registered_agent_ids_for_goal
from ..runtime.time import now_utc_iso
from ...file_lock import exclusive_file_lock
from ...history import load_index, load_registry
from ...runtime import validate_goal_id_path_segment
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..runtime.time import chronology_key
from ..status.autonomous_replan_projection import (
    autonomous_replan_obligation_from_runs,
)
from ..todos.contract import normalize_todo_claimed_by
from ..work_items.autonomous_replan_obligation import (
    ensure_replan_novelty_policy,
    run_history_agent_id,
)
from .goal_frontier import (
    AUTONOMOUS_REPLAN_OBLIGATION_SCHEMA_VERSION,
    autonomous_replan_is_required,
    autonomous_replan_scope_decision,
)
from .shared_goal_work_source import read_shared_goal_work_source
from .shared_goal_alignment import (
    DEFAULT_REGISTRY_RELATIVE_PATH,
    _registered_goal,
    _project_shared_goal_alignment,
)

GOAL_AMENDMENT_PROPOSAL_EFFECT_METHOD = "goal.amendment_proposal.admit"
GOAL_AMENDMENT_PROPOSAL_REQUEST_SCHEMA_VERSION = "goal_amendment_proposal_request_v0"
GOAL_AMENDMENT_PROPOSAL_ADMISSION_SCHEMA_VERSION = (
    "goal_amendment_proposal_admission_v0"
)
GOAL_AMENDMENT_PROPOSAL_SCHEMA_VERSION = "goal_amendment_proposal_v0"
GOAL_AMENDMENT_CLASSES = (
    "lane_route",
    "shared_work_graph",
    "shared_acceptance",
    "protected_authority",
)
GOAL_AMENDMENT_PROPOSAL_ADMISSIONS = ("admitted", "needs_rebase")
GOAL_AMENDMENT_PROPOSAL_ADMISSION_FACTS = (
    "base_state_event_basis_sequence_behind_derived_head",
    "base_source_basis_digest_mismatch",
    "base_source_basis_unverifiable",
    "base_revision_basis_superseded",
)
AMENDMENT_PROPOSAL_JOURNAL_DIRNAME = "amendment-proposals"
AMENDMENT_PROPOSAL_JOURNAL_BASENAME = "journal.jsonl"
_SHA256_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def amendment_proposal_journal_path(
    *,
    runtime_root: Path,
    goal_id: str,
) -> Path:
    """Return the append-only proposal journal path for one goal."""

    safe_goal_id = validate_goal_id_path_segment(goal_id)
    return (
        runtime_root
        / "goals"
        / safe_goal_id
        / AMENDMENT_PROPOSAL_JOURNAL_DIRNAME
        / AMENDMENT_PROPOSAL_JOURNAL_BASENAME
    )


def read_goal_amendment_proposal_journal(
    *,
    runtime_root: Path,
    goal_id: str,
) -> list[dict[str, Any]]:
    """Read retained proposal admission rows in journal (append) order."""

    return _read_journal_rows(
        amendment_proposal_journal_path(
            runtime_root=runtime_root,
            goal_id=goal_id,
        )
    )


def admit_goal_amendment_proposal(
    *,
    proposal: Mapping[str, Any],
    project: Path,
    registry_path: Path | None = None,
    runtime_root: Path | None = None,
) -> dict[str, Any]:
    """Validate one proposal, retain its admission, and return the row.

    Admission also validates the proposal's causal membership (RFC §5
    "impact scope"): ``replan_obligation_id`` must resolve to an open,
    required replan obligation of the same Goal whose agent lane includes
    the proposer, and every ``affected_todo_ids`` entry must resolve to an
    actionable open Todo of the same Goal. The obligation inventory is not
    a caller input: it is derived here, inside the same submit call, from
    the authoritative quota run-history ledger
    (``runtime/goals/<goal_id>/runs/index.jsonl``) through the status
    projection's own derivation entry point — the same read-time
    projection the quota/status producers publish, and the one whose
    obligations disappear once a settlement ack run lands in the ledger.
    Todos come from the Goal's active state file. Both are handed to the
    reducer as typed facts. When the run history derives no open
    obligation the inventory is empty and any proposal referencing a
    replan obligation fails closed: admission never trusts a causal chain
    on string shape alone.

    Raises ``TypeError`` when ``proposal`` or the registry/JTS payload is
    not the expected object type, and ``ValueError`` for any other
    admission-blocking defect (unknown goal, unregistered proposer,
    schema violation, evidence over budget, an invalid replan obligation
    / affected Todo reference, a base basis sequence ahead of the derived
    head, or a conflicting replayed ``proposal_id``). Nothing is retained
    when admission fails.
    """

    if not isinstance(proposal, Mapping):
        raise TypeError("proposal must be a goal_amendment_proposal_v0 object")
    proposal_goal_id_raw = str(proposal.get("goal_id") or "").strip()
    if not proposal_goal_id_raw:
        raise ValueError("proposal.goal_id must be a non-empty registered goal id")
    proposal_goal_id = validate_goal_id_path_segment(proposal_goal_id_raw)
    proposer_agent_id = normalize_todo_claimed_by(proposal.get("proposer_agent_id"))
    if not proposer_agent_id:
        raise ValueError("proposal.proposer_agent_id must be a public-safe agent id")

    effective_registry_path = (
        registry_path
        if registry_path is not None
        else project / DEFAULT_REGISTRY_RELATIVE_PATH
    )
    try:
        registry_payload = load_registry(effective_registry_path)
    except (OSError, ValueError):
        raise ValueError(
            f"goal registry is unreadable: {effective_registry_path}"
        ) from None

    effective_runtime_root = (
        runtime_root
        if runtime_root is not None
        else _runtime_root_from_registry(registry_payload)
    )
    if effective_runtime_root is None:
        raise ValueError(
            "proposal admission requires a runtime root "
            "(registry common_runtime_root or an explicit runtime_root)"
        )

    goal = _registered_goal(registry_payload, goal_id=proposal_goal_id)
    work_source = read_shared_goal_work_source(goal=goal, project=project,
        runtime_root=effective_runtime_root)
    state_text = work_source.state_text

    # Causal authority is derived, never submitted: the open obligation
    # inventory comes from the same run-history projection the quota/status
    # producers own, so the caller cannot mint an "open" obligation and no
    # separate receipt store is consulted (see
    # ``_derive_open_replan_obligation_inventory``).
    derived_status_item = _derive_open_replan_obligation_inventory(
        runtime_root=effective_runtime_root,
        goal_id=proposal_goal_id,
        goal=goal,
        state_text=state_text,
    )

    # Stage 1 reuse: the read-only projection fails closed on unknown goals
    # and unregistered proposers, and derives the source basis (state event
    # log append sequence, or markdown fallback) the proposal's base binds
    # against — both its sequence and its digest.
    alignment = _project_shared_goal_alignment(
        goal_id=proposal_goal_id,
        agent_id=proposer_agent_id,
        project=project,
        registry_path=effective_registry_path,
        runtime_root=effective_runtime_root,
        status_item=derived_status_item,
        work_source=work_source,
    )
    source_basis = alignment.get("source_basis")
    if not isinstance(source_basis, Mapping):
        raise TypeError("TypeScript shared goal alignment shape mismatch")
    derived_basis = {
        "state_event_basis_sequence": source_basis.get("state_event_basis_sequence"),
        "revision_basis": source_basis.get("revision_basis"),
        "source_basis_digest": source_basis.get("source_basis_digest"),
    }

    open_replan_obligations = _open_replan_obligation_inventory(
        goal_id=proposal_goal_id,
        registered_agents=registered_agent_ids_for_goal(goal),
        status_item=derived_status_item,
    )
    request = {
        "schema_version": GOAL_AMENDMENT_PROPOSAL_REQUEST_SCHEMA_VERSION,
        "proposal": dict(proposal),
        "derived_basis": derived_basis,
        "open_replan_obligations": open_replan_obligations,
        "work_items": work_source.items,
        "observed_at": work_source.observed_at,
    }
    try:
        admission = effect_runtime_result(
            GOAL_AMENDMENT_PROPOSAL_EFFECT_METHOD,
            request,
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    _check_admission_shape(admission, proposal=proposal)
    return _retain_admission(
        amendment_proposal_journal_path(
            runtime_root=effective_runtime_root,
            goal_id=proposal_goal_id,
        ),
        admission=dict(admission),
    )


def _derive_open_replan_obligation_inventory(
    *,
    runtime_root: Path,
    goal_id: str,
    goal: Mapping[str, Any],
    state_text: str,
) -> dict[str, Any]:
    """Derive the goal's open replan obligations from the authoritative
    quota run-history ledger, reusing the status projection's own
    derivation entry point.

    This mirrors how ``attach_active_state_project_asset_fields`` builds
    the status projection's obligation fields: the goal's runs are read
    newest-first from ``runtime/goals/<goal_id>/runs/index.jsonl`` (the
    quota authority ledger, written only under the kernel file lock by the
    TS ``quota.spend.commit`` transaction), every agent lane seen in the
    history gets an obligation derived through the same bound
    ``autonomous_replan_obligation_from_runs`` entry point the
    status/quota producers use, and the goal-level obligation is derived
    goal-scoped. Because derivation stops at the first settlement ack run
    (``autonomous_replan_ack`` with an accepted semantic delta), an
    obligation is open exactly while the run history says so — there is no
    separate open/closed state store to consult or to forge. ``state_text``
    is accepted for signature parity with the status attachment path; the
    run-history derivation below does not consume todo summaries, matching
    the goal-scoped lane semantics used for the per-agent obligations.
    """

    runs, _ = load_index(runtime_root / "goals" / goal_id / "runs" / "index.jsonl")
    newest_first_runs = [
        run
        for _, run in sorted(
            enumerate(runs),
            key=lambda item: (
                *chronology_key(item[1].get("generated_at")),
                item[0],
            ),
            reverse=True,
        )
    ]
    agent_ids = sorted(
        {
            agent_id
            for run in newest_first_runs
            if isinstance(run, dict)
            if (agent_id := run_history_agent_id(run)) is not None
        }
    )
    obligations_by_agent = {
        agent_id: obligation
        for agent_id in agent_ids
        if (
            obligation := autonomous_replan_obligation_from_runs(
                newest_first_runs,
                # The shared todo summary is goal-scoped here. Quota derives
                # todo-based replans later from its agent-filtered summary.
                agent_todos=None,
                agent_id=agent_id,
            )
        )
    }
    derived: dict[str, Any] = {}
    if obligations_by_agent:
        derived["autonomous_replan_obligations_by_agent"] = obligations_by_agent
    goal_level = autonomous_replan_obligation_from_runs(
        newest_first_runs,
        agent_todos=None,
    )
    if goal_level:
        derived["autonomous_replan_obligation"] = goal_level
    return derived


def _open_replan_obligation_inventory(
    *,
    goal_id: str,
    registered_agents: list[str],
    status_item: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Derive the goal's open, required replan obligations as typed facts.

    Candidates come from every agent lane in ``autonomous_replan_
    obligations_by_agent`` plus the goal-level ``autonomous_replan_
    obligation`` of the derived status projection payload. Each candidate
    is normalized by ``ensure_replan_novelty_policy`` (which derives the
    deterministic ``obligation_id``), closed/settled ones (``required``
    false) are dropped, and the scope semantics are folded into an
    explicit ``bound_agent_ids`` list — agent-scoped obligations keep
    their owners, unscoped ones keep their deterministic peer assignment
    — so the TypeScript reducer only compares membership, never
    re-derives scope.
    """

    item = status_item if isinstance(status_item, Mapping) else {}
    candidates: list[dict[str, Any]] = []
    by_agent = item.get("autonomous_replan_obligations_by_agent")
    if isinstance(by_agent, Mapping):
        candidates.extend(
            dict(value) for value in by_agent.values() if isinstance(value, Mapping)
        )
    goal_level = item.get("autonomous_replan_obligation")
    if isinstance(goal_level, Mapping):
        candidates.append(dict(goal_level))

    inventory: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        normalized = ensure_replan_novelty_policy(candidate)
        if not autonomous_replan_is_required(normalized):
            continue
        obligation_id = normalized.get("obligation_id")
        if not obligation_id or obligation_id in inventory:
            continue
        bound_agent_ids = [
            agent_id
            for agent_id in registered_agents
            if autonomous_replan_scope_decision(
                normalized,
                agent_id=agent_id,
                registered_agent_ids=registered_agents,
            ).get("applies")
        ]
        inventory[str(obligation_id)] = {
            "schema_version": AUTONOMOUS_REPLAN_OBLIGATION_SCHEMA_VERSION,
            "obligation_id": str(obligation_id),
            "goal_id": goal_id,
            "required": True,
            "bound_agent_ids": bound_agent_ids,
        }
    return list(inventory.values())


def _check_admission_shape(
    admission: object,
    *,
    proposal: Mapping[str, Any],
) -> None:
    """Fail closed on any reducer output that stops being the contract.

    This mirrors the Stage 1 adapter's defensive shape check: the reducer
    output must stay a non-authoritative admission record with
    ``canonical_effect: none``, a closed admission/fact/class enum, a
    ``sha256`` proposal digest, and the submitted identities echoed back.
    """

    if not isinstance(admission, Mapping):
        raise TypeError("TypeScript goal amendment admission shape mismatch")
    if (
        admission.get("schema_version")
        != GOAL_AMENDMENT_PROPOSAL_ADMISSION_SCHEMA_VERSION
        or admission.get("canonical_effect") != "none"
        or admission.get("admission") not in GOAL_AMENDMENT_PROPOSAL_ADMISSIONS
        or admission.get("amendment_class") not in GOAL_AMENDMENT_CLASSES
        or admission.get("goal_id") != str(proposal.get("goal_id") or "").strip()
        or admission.get("proposer_agent_id")
        != normalize_todo_claimed_by(proposal.get("proposer_agent_id"))
        or admission.get("proposal_id")
        != str(proposal.get("proposal_id") or "").strip().lower()
        or admission.get("base_revision_basis")
        != str(proposal.get("base_revision_basis") or "").strip()
        or not _SHA256_DIGEST_PATTERN.fullmatch(
            str(admission.get("proposal_digest") or "")
        )
    ):
        raise RuntimeError("TypeScript goal amendment admission shape mismatch")
    facts = admission.get("admission_facts")
    if not isinstance(facts, list) or any(
        fact not in GOAL_AMENDMENT_PROPOSAL_ADMISSION_FACTS for fact in facts
    ):
        raise RuntimeError("TypeScript goal amendment admission shape mismatch")


def _retain_admission(
    journal_path: Path,
    *,
    admission: Mapping[str, Any],
) -> dict[str, Any]:
    """Append one admission row; replaying identical content is idempotent."""

    proposal_id = str(admission.get("proposal_id") or "")
    proposal_digest = str(admission.get("proposal_digest") or "")
    with exclusive_file_lock(journal_path):
        rows = _read_journal_rows(journal_path)
        for row in rows:
            if row.get("proposal_id") != proposal_id:
                continue
            if row.get("proposal_digest") != proposal_digest:
                raise ValueError(
                    "conflicting proposal_id already retained with different "
                    f"content: {proposal_id}"
                )
            return dict(row)
        record = {
            **dict(admission),
            "recorded_at": now_utc_iso(),
            "journal_append_sequence": len(rows) + 1,
        }
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with journal_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    return record


def _read_journal_rows(journal_path: Path) -> list[dict[str, Any]]:
    if not journal_path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    text = journal_path.read_text(encoding="utf-8")
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid proposal journal JSONL at line {line_number}: {exc}"
            ) from None
        if not isinstance(row, dict):
            raise TypeError(f"invalid proposal journal row at line {line_number}")
        rows.append(row)
    return rows


def _runtime_root_from_registry(
    registry_payload: Mapping[str, Any],
) -> Path | None:
    raw = registry_payload.get("common_runtime_root")
    text = str(raw or "").strip()
    return Path(text).expanduser() if text else None
