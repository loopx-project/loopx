"""Domain owner for the deep-research capability.

State machine, ledger transitions, packet, and report for the
`/loopx-deepresearch` bounded research loop. The CLI
(`loopx/cli_commands/deepresearch.py`) is a thin adapter over this module;
nothing else may mutate `.loopx/deepresearch/` state.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...control_plane.content_digest import ENVELOPED_SHA256_PATTERN
from ...file_lock import exclusive_file_lock

COMMAND = "/loopx-deepresearch"
STATE_SCHEMA_VERSION = "loopx_deepresearch_state_v0"
PACKET_SCHEMA_VERSION = "loopx_deepresearch_packet_v0"
REPORT_SCHEMA_VERSION = "loopx_deepresearch_report_v0"

DEFAULT_MAX_SOURCES = 12
DEFAULT_MAX_SUBQUESTIONS = 8
COVERAGE_WINDOW_SOURCES = 3

_STATE_FILENAME = "research.json"
_REPORT_FILENAME = "report.md"
_LEDGER_DIR = Path(".loopx") / "deepresearch"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_path(project: Path) -> Path:
    return project / _LEDGER_DIR / _STATE_FILENAME


def report_path(project: Path) -> Path:
    return project / _LEDGER_DIR / _REPORT_FILENAME


def _require_within_project(project: Path, target: Path) -> Path:
    """Constrain every ledger write to the resolved project root.

    `--project` is caller-chosen, so the only trustworthy boundary is that the
    resolved write target stays inside the resolved project directory: this is
    the auditable answer to "a user-controlled component reaches the write
    path" — the component selects the root, never a filename.
    """
    root = project.expanduser().resolve()
    resolved = target.expanduser().resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"refusing to touch a path outside the project root: {resolved}")
    return resolved


def load_state(project: Path) -> dict[str, Any] | None:
    path = state_path(project)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError(f"unsupported deepresearch state at {path}")
    return data


def _save_state(project: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = _now_iso()
    path = _require_within_project(project, state_path(project))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _require_state(project: Path) -> dict[str, Any]:
    state = load_state(project)
    if state is None:
        raise ValueError(
            "no active deepresearch state in this project; run "
            "`loopx deepresearch start --question <text>` first"
        )
    return state


def _require_active_state(project: Path) -> dict[str, Any]:
    """Single, unbypassable precondition for every ledger mutation.

    `closed` is a terminal receipt: a closed run's ledger is immutable, so a
    mutation after close would silently diverge the archived audit trail from
    the close summary. Enforced here in the domain owner — not in argparse —
    so Python callers cannot bypass it.
    """
    state = _require_state(project)
    if state.get("status", "active") == "closed":
        raise ValueError(
            "research run is closed"
            + (f" (closed_at {state.get('closed_at')})" if state.get("closed_at") else "")
            + "; the ledger is immutable after close — start a new run instead"
        )
    return state


def _next_id(items: list[dict[str, Any]], prefix: str) -> str:
    used = {str(item.get("id", "")) for item in items}
    n = 1
    while f"{prefix}{n}" in used:
        n += 1
    return f"{prefix}{n}"


def _normalize_source_ref(ref: str) -> str:
    return ref.strip().rstrip("/").lower()


def archive_root(project: Path) -> Path:
    return project / _LEDGER_DIR / "archive"


def _archive_current_run(project: Path, state: dict[str, Any]) -> Path:
    """Move the closed run's state (and report) under archive/<closed_at>/.

    A typed terminal rotation — the only path besides uninstall that removes
    research.json, so a finished run never blocks the next question and the
    audit trail stays on disk.
    """
    stamp = str(state.get("closed_at") or state.get("updated_at") or _now_iso())
    safe = "".join(ch if ch.isalnum() or ch in "-+" else "-" for ch in stamp)
    target = _require_within_project(project, archive_root(project) / safe)
    suffix = 2
    while target.exists():  # same-second closes must not overwrite each other
        target = _require_within_project(project, archive_root(project) / f"{safe}-{suffix}")
        suffix += 1
    target.mkdir(parents=True, exist_ok=True)
    state_path(project).rename(target / _STATE_FILENAME)
    report = report_path(project)
    if report.exists():
        report.rename(target / _REPORT_FILENAME)
    return target


def close_research(project: Path, *, note: str | None = None) -> dict[str, Any]:
    """Terminal transition: an explicit operator decision to end this run.

    Closing is allowed at any point (budget blown, question abandoned, or
    research complete) because it is the run owner saying "done" — unlike
    stop_conditions, which the packet computes from ledger state. A closed run
    never blocks `start`; the next start archives it and begins fresh.
    """
    with exclusive_file_lock(state_path(project), operation="deepresearch_close"):
        state = _require_state(project)
        if state.get("status", "active") == "closed":
            raise ValueError("research run is already closed")
        state["status"] = "closed"
        state["closed_at"] = _now_iso()
        if note and note.strip():
            state["close_note"] = note.strip()
        stop = evaluate_stop(state)
        state["close_summary"] = stop
        _save_state(project, state)
    return {"status": "closed", "closed_at": state["closed_at"], "state": state}


def start_research(
    project: Path,
    *,
    question: str,
    max_sources: int,
    max_subquestions: int,
    new_run: bool = False,
) -> dict[str, Any]:
    question = question.strip()
    if not question:
        raise ValueError("--question must be a non-empty research question")
    if max_sources < 1:
        raise ValueError("--max-sources must be >= 1")
    if max_subquestions < 0:
        raise ValueError("--max-subquestions must be >= 0")
    with exclusive_file_lock(state_path(project), operation="deepresearch_start"):
        existing = load_state(project)
        if existing is not None:
            if existing.get("status", "active") == "closed":
                _archive_current_run(project, existing)
            elif new_run and evaluate_stop(existing)["stopped"]:
                existing["status"] = "closed"
                existing["closed_at"] = _now_iso()
                existing["close_note"] = "auto-closed by start --new-run"
                _save_state(project, existing)
                _archive_current_run(project, existing)
            else:
                if evaluate_stop(existing)["stopped"]:
                    raise ValueError(
                        "a finished research run exists for this project; close it with "
                        "`loopx deepresearch close` (or rerun start with --new-run) "
                        "before starting a new question"
                    )
                raise ValueError(
                    f"an active research run exists for this project (question: "
                    f"{existing['question']!r}); finish and `loopx deepresearch close` it "
                    "before starting a new question"
                )
        state = {
            "schema_version": STATE_SCHEMA_VERSION,
            "status": "active",
            "question": question,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "budget": {
                "max_sources": max_sources,
                "max_subquestions": max_subquestions,
            },
            "questions": [
                {
                    "id": "q1",
                    "text": question,
                    "status": "open",
                    "priority": "P0",
                    "parent_claim": None,
                    "answer": None,
                    "evidence_claims": [],
                    "resolved_at": None,
                }
            ],
            "sources": [],
            "claims": [],
            "contradictions": [],
        }
        _save_state(project, state)
    return state


def add_source(
    project: Path,
    *,
    url_or_path: str,
    tool: str,
    title: str | None,
    claims: list[dict[str, Any]],
    external_evidence: dict[str, str] | None = None,
    expected_question: str | None = None,
) -> dict[str, Any]:
    # Load, validate the whole batch, allocate ids, mutate, and save all under
    # one project-level lock: concurrent deepresearch commands are a normal
    # agent-host shape, and an unlocked read-modify-write loses updates even
    # when the atomic rename itself succeeds.
    with exclusive_file_lock(state_path(project), operation="deepresearch_add_source"):
        state = _require_active_state(project)
        if expected_question is not None and state["question"] != expected_question:
            raise ValueError("external evidence objective does not match the active research question")
        if external_evidence is not None:
            fields = {"plan_id", "admission_id", "receipt_digest", "content_digest"}
            if set(external_evidence) != fields or any(
                not isinstance(value, str) or not ENVELOPED_SHA256_PATTERN.fullmatch(value)
                for value in external_evidence.values()
            ):
                raise ValueError("external evidence lineage requires exact content-addressed identities")
        url_or_path = url_or_path.strip()
        tool = tool.strip() or "unspecified"
        if not url_or_path:
            raise ValueError("--url-or-path must be non-empty")
        normalized = _normalize_source_ref(url_or_path)
        for source in state["sources"]:
            same_source = (
                str(source["url_or_path"]).strip().rstrip("/") == url_or_path.rstrip("/")
                if external_evidence is not None else
                _normalize_source_ref(str(source["url_or_path"])) == normalized
            )
            if same_source:
                if (external_evidence is not None and source.get("external_evidence") == external_evidence
                    and [claim["text"] for claim in state["claims"] if claim["id"] in source["claims"]]
                    == [str(claim.get("text", "")).strip() for claim in claims]):
                    return {"source_id": source["id"], "claim_ids": source["claims"], "state": state}
                raise ValueError(
                    f"source already recorded as {source['id']} "
                    f"({source['url_or_path']}); reuse its claims instead of re-reading"
                )
        if len(state["sources"]) >= int(state["budget"]["max_sources"]):
            raise ValueError(
                f"source budget exhausted ({state['budget']['max_sources']}); "
                "resolve open questions with existing evidence or raise --max-sources at start"
            )
        # Validate every claim against the pre-existing ledger before any
        # mutation: a mid-batch ValueError must not leave partial state behind,
        # and a claim can only relate to a claim recorded before this batch —
        # which also makes self-reference (c2 contradicting itself) impossible.
        known_before = {c["id"] for c in state["claims"]}
        prepared: list[dict[str, Any]] = []
        for claim in claims:
            text = str(claim.get("text", "")).strip()
            if not text:
                raise ValueError("each claim needs non-empty 'text'")
            stance = str(claim.get("stance") or "neutral")
            if stance not in {"supports", "neutral", "contradicts", "refines"}:
                raise ValueError(
                    f"claim stance must be supports|neutral|contradicts|refines, got {stance!r}"
                )
            relates_claim = claim.get("relates_claim")
            if stance == "contradicts" and not relates_claim:
                raise ValueError(
                    "a contradicting claim must cite the claim it contradicts via 'relates_claim'"
                )
            if relates_claim is not None and relates_claim not in known_before:
                raise ValueError(
                    f"relates_claim {relates_claim!r} is not a recorded claim (self-reference "
                    "is not a contradiction)"
                )
            prepared.append({"text": text, "stance": stance, "relates_claim": relates_claim})
        # Mutation phase: nothing below can fail on user input anymore.
        source_id = _next_id(state["sources"], "s")
        claim_ids: list[str] = []
        for claim in prepared:
            claim_id = _next_id(state["claims"], "c")
            if claim["relates_claim"] == claim_id:  # defensive: unreachable given known_before
                raise ValueError(f"claim {claim_id} cannot reference itself")
            state["claims"].append(
                {
                    "id": claim_id,
                    "text": claim["text"],
                    "source_id": source_id,
                    "stance": claim["stance"],
                    "relates_claim": claim["relates_claim"],
                    "added_at": _now_iso(),
                }
            )
            claim_ids.append(claim_id)
            if claim["stance"] == "contradicts":
                state["contradictions"].append(
                    {
                        "id": _next_id(state["contradictions"], "x"),
                        "claim_a": claim_id,
                        "claim_b": claim["relates_claim"],
                        "status": "open",
                        "sides_with": None,
                        "resolution": None,
                        "resolved_at": None,
                    }
                )
        state["sources"].append(
            {
                "id": source_id,
                "url_or_path": url_or_path,
                "tool": tool,
                "title": (title or "").strip() or None,
                "accessed_at": _now_iso(),
                "claims": claim_ids,
                **({"external_evidence": dict(external_evidence)} if external_evidence is not None else {}),
            }
        )
        _save_state(project, state)
    return {
        "source_id": source_id,
        "claim_ids": claim_ids,
        "state": state,
    }


def add_subquestion(
    project: Path,
    *,
    text: str,
    priority: str,
    from_claim: str | None,
) -> dict[str, Any]:
    with exclusive_file_lock(state_path(project), operation="deepresearch_add_subquestion"):
        state = _require_active_state(project)
        text = text.strip()
        if not text:
            raise ValueError("--text must be non-empty")
        if priority not in {"P0", "P1", "P2"}:
            raise ValueError("--priority must be P0|P1|P2")
        # Machine-enforced lineage: a subquestion without a recorded parent
        # claim is free-roaming, whatever the packet prose says. Enforced here
        # so Python callers cannot bypass the argparse layer.
        if not from_claim:
            raise ValueError(
                "from_claim is required: every subquestion must derive from a recorded claim"
            )
        known = {c["id"] for c in state["claims"]}
        if from_claim not in known:
            raise ValueError(
                f"--from-claim {from_claim!r} is not a recorded claim; subquestions must "
                "derive from evidence, not open in free space"
            )
        open_subquestions = [q for q in state["questions"] if q["id"] != "q1"]
        if len(open_subquestions) >= int(state["budget"]["max_subquestions"]):
            raise ValueError(
                f"subquestion budget exhausted ({state['budget']['max_subquestions']})"
            )
        question = {
            "id": _next_id(state["questions"], "q"),
            "text": text,
            "status": "open",
            "priority": priority,
            "parent_claim": from_claim,
            "answer": None,
            "evidence_claims": [],
            "resolved_at": None,
        }
        state["questions"].append(question)
        _save_state(project, state)
    return {"question_id": question["id"], "state": state}


def resolve_question(
    project: Path,
    *,
    question_id: str,
    answer: str,
    evidence_claims: list[str],
    contradiction_resolutions: list[dict[str, Any]],
) -> dict[str, Any]:
    with exclusive_file_lock(state_path(project), operation="deepresearch_resolve_question"):
        state = _require_active_state(project)
        return _resolve_question_unlocked(
            project,
            state,
            question_id=question_id,
            answer=answer,
            evidence_claims=evidence_claims,
            contradiction_resolutions=contradiction_resolutions,
        )


def _adjudicate_touching_contradictions(
    state: dict[str, Any],
    evidence_claims: list[str],
    contradiction_resolutions: list[dict[str, Any]],
) -> None:
    """Enforce the adjudication invariant for one answer transition.

    Any contradiction touching the evidence — open or already resolved — must
    not end up adjudicated against a cited claim: otherwise the citation report
    would back the answer with a side the ledger itself ruled out. Open ones
    are resolved in this transition, but only toward a side that belongs to the
    answer's evidence, and the overruled side must not be cited.
    """
    touching = [
        x
        for x in state["contradictions"]
        if x["claim_a"] in evidence_claims or x["claim_b"] in evidence_claims
    ]
    for contradiction in touching:
        if contradiction["status"] == "resolved":
            loser = (
                contradiction["claim_b"]
                if contradiction["sides_with"] == contradiction["claim_a"]
                else contradiction["claim_a"]
            )
            if loser in evidence_claims:
                raise ValueError(
                    f"contradiction {contradiction['id']} was resolved against "
                    f"{loser}; an answer cannot cite the overruled side — cite the "
                    f"adjudicated {contradiction['sides_with']} instead or drop "
                    f"{loser} from --evidence-claims"
                )
    by_id = {r.get("contradiction_id"): r for r in contradiction_resolutions}
    validated_resolutions: list[tuple[dict[str, Any], str, str]] = []
    for contradiction in touching:
        if contradiction["status"] != "open":
            continue
        cid = contradiction["id"]
        resolution = by_id.get(cid)
        if resolution is None:
            raise ValueError(
                f"open contradiction {cid} involves your evidence; "
                "resolve it via --contradiction-resolution with an explicit sides-with "
                "claim id and rationale"
            )
        claim_a, claim_b = contradiction["claim_a"], contradiction["claim_b"]
        sides_with = resolution.get("sides_with")
        if sides_with not in {claim_a, claim_b}:
            raise ValueError(
                f"contradiction {cid} resolution must side with "
                f"{claim_a} or {claim_b}"
            )
        if sides_with not in evidence_claims:
            raise ValueError(
                f"contradiction {cid} resolution sides with "
                f"{sides_with}, which is not among this question's evidence claims "
                f"{evidence_claims}; the adjudicated side must back the answer — "
                "cite it in --evidence-claims"
            )
        loser = claim_b if sides_with == claim_a else claim_a
        if loser in evidence_claims:
            raise ValueError(
                f"contradiction {cid} was resolved against "
                f"{loser}; an answer cannot cite the overruled side — cite the "
                f"adjudicated {sides_with} instead or drop "
                f"{loser} from --evidence-claims"
            )
        rationale = str(resolution.get("rationale", "")).strip()
        if not rationale:
            raise ValueError(
                f"contradiction {cid} resolution needs a non-empty rationale"
            )
        validated_resolutions.append((contradiction, sides_with, rationale))

    for contradiction, sides_with, rationale in validated_resolutions:
        contradiction["status"] = "resolved"
        contradiction["sides_with"] = sides_with
        contradiction["resolution"] = rationale
        contradiction["resolved_at"] = _now_iso()


def _resolve_question_unlocked(
    project: Path,
    state: dict[str, Any],
    *,
    question_id: str,
    answer: str,
    evidence_claims: list[str],
    contradiction_resolutions: list[dict[str, Any]],
) -> dict[str, Any]:
    answer = answer.strip()
    if not answer:
        raise ValueError("--answer must be non-empty")
    question = next((q for q in state["questions"] if q["id"] == question_id), None)
    if question is None:
        raise ValueError(f"--question-id {question_id!r} is not a recorded question")
    if question["status"] != "open":
        raise ValueError(f"question {question_id} is already {question['status']}")
    known_claims = {c["id"] for c in state["claims"]}
    unknown = [cid for cid in evidence_claims if cid not in known_claims]
    if unknown:
        raise ValueError(
            f"evidence claims not recorded: {unknown}; record sources/claims first — "
            "an answer without ledger evidence is exactly the drift this command exists to stop"
        )
    if not evidence_claims:
        raise ValueError("resolving a question requires at least one recorded evidence claim")
    _adjudicate_touching_contradictions(state, evidence_claims, contradiction_resolutions)
    # The in-call adjudications above may overrule claims cited by OTHER
    # already-answered questions; the current question's own citations were
    # validated by the membership rules inside the adjudication.
    _reject_overruled_answer_evidence(state, exclude_question_id=question_id)
    question["status"] = "answered"
    question["answer"] = answer
    question["evidence_claims"] = list(evidence_claims)
    question["resolved_at"] = _now_iso()
    _save_state(project, state)
    return {"question_id": question_id, "state": state}


def _reject_overruled_answer_evidence(
    state: dict[str, Any],
    *,
    exclude_question_id: str | None = None,
    prospective: tuple[dict[str, Any], str] | None = None,
) -> None:
    """Ledger invariant: no answered question may cite an overruled claim.

    A resolved contradiction adjudicates one of its two claims as the loser;
    the citation report must never back an already-recorded answer with a side
    the same ledger ruled against. Enforced on every transition that
    adjudicates a contradiction — in-call (resolve-question) via the resolved
    scan, standalone (resolve-contradiction) via `prospective`, which lets the
    check run before any mutation. `exclude_question_id` covers the question
    being resolved right now: its own citations are governed by the
    sides-with-membership rules in _adjudicate_touching_contradictions.
    """
    resolved = [x for x in state["contradictions"] if x["status"] == "resolved"]
    if prospective is not None:
        contradiction, sides_with = prospective
        resolved.append({**contradiction, "status": "resolved", "sides_with": sides_with})
    for contradiction in resolved:
        sides_with = contradiction["sides_with"]
        loser = (
            contradiction["claim_b"]
            if sides_with == contradiction["claim_a"]
            else contradiction["claim_a"]
        )
        for question in state["questions"]:
            if question["status"] != "answered" or question["id"] == exclude_question_id:
                continue
            if loser in question.get("evidence_claims", []):
                raise ValueError(
                    f"contradiction {contradiction['id']} overrules {loser}, which is cited "
                    f"as evidence by answered question {question['id']}; the ledger cannot "
                    "silently invalidate an answer it already recorded — re-resolve toward "
                    "the cited side, or close the run and reopen the question in a new run"
                )


def resolve_contradiction(
    project: Path,
    *,
    contradiction_id: str,
    sides_with: str,
    rationale: str,
) -> dict[str, Any]:
    """Close a contradiction standalone with an explicit sides-with rationale.

    Question resolution closes contradictions touching its evidence, but a
    contradiction can outlive the question that produced its claims; without
    this path the closeout gate would deadlock. Still a typed, audited
    transition — not a manual state edit — and it cannot overrule evidence an
    already-answered question cites.
    """
    rationale = rationale.strip()
    if not rationale:
        raise ValueError("--rationale must be non-empty")
    with exclusive_file_lock(
        state_path(project), operation="deepresearch_resolve_contradiction"
    ):
        state = _require_active_state(project)
        contradiction = next(
            (x for x in state["contradictions"] if x["id"] == contradiction_id), None
        )
        if contradiction is None:
            raise ValueError(f"--contradiction-id {contradiction_id!r} is not recorded")
        if contradiction["status"] != "open":
            raise ValueError(f"contradiction {contradiction_id} is already resolved")
        if sides_with not in {contradiction["claim_a"], contradiction["claim_b"]}:
            raise ValueError(
                f"resolution must side with {contradiction['claim_a']} or "
                f"{contradiction['claim_b']}, got {sides_with!r}"
            )
        _reject_overruled_answer_evidence(state, prospective=(contradiction, sides_with))
        contradiction["status"] = "resolved"
        contradiction["sides_with"] = sides_with
        contradiction["resolution"] = rationale
        contradiction["resolved_at"] = _now_iso()
        _save_state(project, state)
    return {"contradiction_id": contradiction_id, "state": state}


def evaluate_stop(state: dict[str, Any]) -> dict[str, Any]:
    budget = state["budget"]
    open_questions = [q for q in state["questions"] if q["status"] == "open"]
    blocking_open = [q for q in open_questions if q["priority"] in {"P0", "P1"}]
    open_contradictions = [x for x in state["contradictions"] if x["status"] == "open"]
    reasons: list[str] = []
    stopped = False
    if not open_questions:
        stopped = True
        reasons.append("all questions answered")
    elif not blocking_open:
        stopped = True
        reasons.append("only P2 questions remain open; complete with declared open questions")
    if len(state["sources"]) >= int(budget["max_sources"]):
        stopped = True
        reasons.append(
            f"source budget exhausted ({len(state['sources'])}/{budget['max_sources']})"
        )
    recent = state["sources"][-COVERAGE_WINDOW_SOURCES:]
    if (
        len(recent) == COVERAGE_WINDOW_SOURCES
        and not any(source["claims"] for source in recent)
    ):
        stopped = True
        reasons.append(
            f"coverage stop: last {COVERAGE_WINDOW_SOURCES} sources contributed no claims"
        )
    # Conservative v0 closeout gate: an unresolved contradiction makes the
    # ledger internally inconsistent, so `stopped: true` must never claim the
    # research closed out over one — even when every question is answered and
    # the resolution path used evidence the contradiction does not touch.
    if open_contradictions:
        stopped = False
        reasons.append(
            f"{len(open_contradictions)} open contradiction(s) must be resolved before "
            "completion (deepresearch resolve-contradiction)"
        )
    return {
        "stopped": stopped,
        "reasons": reasons,
        "open_questions": len(open_questions),
        "open_contradictions": len(open_contradictions),
    }


def _priority_rank(priority: str) -> int:
    return {"P0": 0, "P1": 1, "P2": 2}.get(priority, 3)


def _closed_run_guidance(cli_bin: str, project: Path) -> dict[str, Any]:
    """Typed terminal projection replacing the expedition on a closed run.

    A closed run rejects every ledger mutation, so an expedition would be an
    instruction the ledger cannot accept.
    """
    return {
        "run_status": "closed",
        "ledger_immutable": True,
        "why": (
            "this run is closed and its ledger is immutable; every evidence "
            "mutation is rejected until the next start"
        ),
        "next_start": (
            f"{cli_bin} deepresearch start --project {project} "
            "--question <new-question> (archives this closed run and begins fresh)"
        ),
    }


def build_packet(state: dict[str, Any], *, cli_bin: str, project: Path) -> dict[str, Any]:
    stop = evaluate_stop(state)
    run_status = state.get("status", "active")
    open_questions = sorted(
        (q for q in state["questions"] if q["status"] == "open"),
        key=lambda q: (_priority_rank(q["priority"]), q["id"]),
    )
    next_expedition: dict[str, Any] | None = None
    terminal_guidance: dict[str, Any] | None = None
    if run_status == "closed":
        terminal_guidance = _closed_run_guidance(cli_bin, project)
    elif not stop["stopped"]:
        open_contradictions = [x for x in state["contradictions"] if x["status"] == "open"]
        if open_questions:
            target = open_questions[0]
            next_expedition = {
                "question_id": target["id"],
                "text": target["text"],
                "derived_from_claim": target["parent_claim"],
                "guidance": (
                    "gather evidence for exactly this question within a small budget, then record "
                    "findings with the evidence commands below; do not free-roam other topics"
                ),
            }
        elif open_contradictions:
            target = open_contradictions[0]
            next_expedition = {
                "question_id": None,
                "kind": "contradiction_resolution",
                "text": (
                    f"resolve contradiction {target['id']}: {target['claim_a']} vs "
                    f"{target['claim_b']}"
                ),
                "guidance": (
                    "the ledger cannot close out over an unresolved contradiction; pick the "
                    "claim the evidence actually supports via `deepresearch resolve-contradiction` "
                    "with an explicit sides-with rationale"
                ),
            }
    return {
        "schema_version": PACKET_SCHEMA_VERSION,
        "command": COMMAND,
        "question": state["question"],
        "run_status": run_status,
        "research_contract": {
            "question": state["question"],
            "budget": state["budget"],
            "created_at": state["created_at"],
            "rules": [
                "claims come only from tool output the caller actually saw; --tool is "
                "caller-declared provenance the CLI records, not an execution "
                "attestation; never fabricate URLs or content",
                "every subquestion derives from a recorded claim",
                "every resolution cites recorded evidence claim ids",
                "contradictions block resolution until an explicit sides-with rationale is "
                "recorded, and no adjudication — in-call or standalone — may overrule a "
                "claim an already-answered question cites as evidence",
            ],
        },
        "ledger_summary": {
            "questions": {
                "open": len(open_questions),
                "answered": len([q for q in state["questions"] if q["status"] == "answered"]),
            },
            "sources": len(state["sources"]),
            "claims": len(state["claims"]),
            "open_contradictions": stop["open_contradictions"],
        },
        "open_questions": [
            {
                "id": q["id"],
                "priority": q["priority"],
                "text": q["text"],
                "parent_claim": q["parent_claim"],
            }
            for q in open_questions
        ],
        "contradictions": [
            {
                "id": x["id"],
                "claim_a": x["claim_a"],
                "claim_b": x["claim_b"],
                "status": x["status"],
                "sides_with": x["sides_with"],
            }
            for x in state["contradictions"]
        ],
        "stop_conditions": stop,
        "next_expedition": next_expedition,
        "terminal_guidance": terminal_guidance,
        "evidence_commands": {
            "add_source": (
                f"{cli_bin} deepresearch add-source --project {project} "
                "--url-or-path <url-or-file> --tool <web_search|web_fetch|local_read|...> "
                "--claims-json '[{\"text\": \"...\", \"stance\": \"supports|neutral|contradicts|refines\", \"relates_claim\": null}]'"
            ),
            "add_subquestion": (
                f"{cli_bin} deepresearch add-subquestion --project {project} "
                "--text '<sub>' --priority P1 --from-claim <claim-id>"
            ),
            "resolve_question": (
                f"{cli_bin} deepresearch resolve-question --project {project} "
                "--question-id <id> --answer '<answer>' --evidence-claims c1 c2 "
                "[--contradiction-resolution contradiction-id=X sides-with=cY rationale='...']"
            ),
            "resolve_contradiction": (
                f"{cli_bin} deepresearch resolve-contradiction --project {project} "
                "--contradiction-id <id> --sides-with <claim-id> --rationale '<why>'"
            ),
            "report": f"{cli_bin} deepresearch report --project {project}",
        },
    }


def render_report(state: dict[str, Any]) -> str:
    lines: list[str] = [
        "# Deep research report",
        "",
        f"- question: {state['question']}",
        f"- state schema: `{STATE_SCHEMA_VERSION}`",
        f"- created: {state['created_at']} · updated: {state['updated_at']}",
        "",
        "## Answers",
        "",
    ]
    answered = [q for q in state["questions"] if q["status"] == "answered"]
    if not answered:
        lines.append("_No question has been answered yet._")
    for question in answered:
        lines.append(f"### {question['id']}: {question['text']}")
        lines.append("")
        lines.append(str(question["answer"]))
        lines.append("")
        lines.append(
            "evidence: "
            + ", ".join(f"`{cid}`" for cid in question.get("evidence_claims", []))
        )
        lines.append("")
    open_questions = [q for q in state["questions"] if q["status"] == "open"]
    if open_questions:
        lines.append("## Open questions")
        lines.append("")
        for question in open_questions:
            lines.append(f"- **{question['priority']}** {question['id']}: {question['text']}")
        lines.append("")
    if state["contradictions"]:
        lines.append("## Contradictions")
        lines.append("")
        for contradiction in state["contradictions"]:
            if contradiction["status"] == "resolved":
                lines.append(
                    f"- {contradiction['id']}: {contradiction['claim_a']} vs "
                    f"{contradiction['claim_b']} — resolved, sides with "
                    f"{contradiction['sides_with']}: {contradiction['resolution']}"
                )
            else:
                lines.append(
                    f"- {contradiction['id']}: {contradiction['claim_a']} vs "
                    f"{contradiction['claim_b']} — **OPEN**"
                )
        lines.append("")
    lines.append("## Claims")
    lines.append("")
    for claim in state["claims"]:
        lines.append(f"- `{claim['id']}` [{claim['stance']}] {claim['text']} — source `{claim['source_id']}`")
    lines.append("")
    lines.append("## Sources")
    lines.append("")
    lines.append("| id | tool | url / path | claims |")
    lines.append("| --- | --- | --- | --- |")
    for source in state["sources"]:
        claims = ", ".join(source["claims"]) or "—"
        title = f" — {source['title']}" if source.get("title") else ""
        lines.append(
            f"| `{source['id']}` | {source['tool']} | {source['url_or_path']}{title} | {claims} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_report(project: Path) -> dict[str, Any]:
    # Snapshot, render, and write share the lifecycle lock with close/start
    # rotation: otherwise a report that read the previous run can land next to
    # the next run's state file — a cross-run citation mismatch the archive
    # cannot explain.
    with exclusive_file_lock(state_path(project), operation="deepresearch_report"):
        state = _require_state(project)
        content = render_report(state)
        path = _require_within_project(project, report_path(project))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        stop = evaluate_stop(state)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "path": str(path),
        "content": content,
        "stop_conditions": stop,
    }
