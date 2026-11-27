"""Produce canonical synthetic events for the packaged Research readback."""
import hashlib
import json

from loopx.capabilities.explore.result_log import build_explore_finding_event, build_explore_node_event

GOAL = "research-verification-fixture"
BASELINE_REF = "artifact:baseline"
REJECTED_REF = "artifact:rejected-variant"
RESULT_REF = "artifact:result"
RECEIPT_REF = "receipt:external-verifier"
BASELINE_SHA = "a" * 64
RESULT_SHA = "b" * 64
EXACT = {
    "metric": "loss", "unit": "points", "higher_is_better": False,
    "comparison_scope": "scope:matched", "input_revision": "input-v1", "evaluation_ref": "check:exact",
}
PROXY = {**EXACT, "metric": "cost", "evaluation_ref": "check:proxy"}


def measurements(sha, ref, exact, proxy):
    return [{**profile, "candidate_ref": f"sha256:{sha}", "evidence_ref": ref, "value": value}
            for profile, value in ((EXACT, exact), (PROXY, proxy))]


def add_case(events, label, node_id, *, baseline_author="worker", late=False, revise=False, refuted=False, attempt=None):
    if attempt in (None, 0):
        events.append(build_explore_node_event(
            goal_id=GOAL, node_id=node_id, title=label, agent_id="worker", recorded_at="2026-11-21T11:00:00Z",
        ))
    case_id = node_id if attempt is None else f"{node_id}-{attempt}"
    baseline_ref = BASELINE_REF if attempt is None else f"{BASELINE_REF}-{attempt}"
    # A normal retained description makes repeated whole-row transport exceed
    # the inline Effect frame on the 151-event pagination regression.
    summary = "" if attempt is None else ("Measured on the same retained input and evaluation scope. " * 27).strip()
    control = {
        "goal_id": GOAL, "node_id": node_id, "finding_id": f"{case_id}-baseline",
        "title": "Reference measurements", "status": "confirmed", "agent_id": baseline_author, "summary": summary,
        "evidence_refs": [baseline_ref],
    }
    reference = build_explore_finding_event(
        **control, recorded_at="2026-11-21T11:10:00Z",
        measurements=measurements(BASELINE_SHA, baseline_ref, 9, 4),
    )
    if not late:
        events.append(reference)
    decision_refs = [baseline_ref]
    if refuted:
        events.append(build_explore_finding_event(
            goal_id=GOAL, node_id=node_id, finding_id=f"{case_id}-refuted",
            title="Rejected reference variant", status="refuted", agent_id="worker",
            recorded_at="2026-11-21T11:20:00Z", evidence_refs=[REJECTED_REF],
        ))
        decision_refs.append(REJECTED_REF)
    ids = json.dumps([event["event_id"] for event in events], ensure_ascii=False, separators=(",", ":"))
    binding = {"goal_id": GOAL, "agent_id": "worker", "todo_id": f"todo_{case_id}", "turn_instance_id": "turn-1"}
    refs = [RESULT_REF, RECEIPT_REF, *decision_refs]
    events.append(build_explore_finding_event(
        goal_id=GOAL, node_id=node_id, finding_id=f"{case_id}-candidate",
        title=label, summary=summary, status="confirmed", agent_id="worker", evidence_refs=refs,
        recorded_at="2026-11-21T12:00:00Z",
        execution_observation={
            "schema_version": "explore_execution_observation_v0",
            "candidate_sha256": RESULT_SHA, "result_candidate_sha256": RESULT_SHA, "result_ref": RESULT_REF,
            "input_revision": "input-v1", "planned_method_id": "verify", "executed_method_id": "verify",
            "change_reason": None, "elapsed_seconds": 1, "limit_seconds": None, "search_iterations": None,
            "stop_reason": None, "evidence_refs": refs,
            "verification_evidence": {
                "host": "local-cli", "baseline": {"ref": baseline_ref, "sha256": BASELINE_SHA},
                "proofs": {"external_verifier": {"criterion_id": "verify", "ref": RECEIPT_REF, "sha256": "c" * 64}},
            },
        },
        work_binding=binding,
        work_yardstick={
            "binding": binding,
            "decision": {
                "schema_version": "work_yardstick_decision_v0",
                "target": {"summary": "Reduce loss", "scope": "Frozen input", "acceptance_ref": "check:exact"},
                "authoritative": EXACT, "proxy": PROXY, "counterevidence_refs": decision_refs, "prior_finding_id": None,
                "next_action": {"method_id": "verify", "reason": "Compare fixed inputs", "cost_bound": "One evaluation"},
            },
            "source_snapshot": {"event_count": len(events), "event_ids_sha256": hashlib.sha256(ids.encode()).hexdigest()},
        },
        measurements=measurements(RESULT_SHA, RESULT_REF, 5, 8),
    ))
    if late:
        events.append(reference)
    if revise:
        events.append(build_explore_finding_event(
            **control, recorded_at="2026-11-21T12:10:00Z",
            measurements=measurements(BASELINE_SHA, baseline_ref, 2, 10),
        ))


def populated_history():
    events = []
    for attempt in range(75):
        add_case(events, f"Comparison {attempt}", "same-question", attempt=attempt,
                 baseline_author="other-worker" if attempt == 31 else "worker")
    return events


if __name__ == "__main__":
    events = []
    add_case(events, "Original matched measurements", "matched", revise=True)
    add_case(events, "Reference from another worker", "foreign", baseline_author="other-worker", refuted=True)
    add_case(events, "Reference received later", "late", late=True)
    print(json.dumps({"goal_id": GOAL, "display_name": "Candidate verification", "events": events}))
