"""Typed receipts leave the private study state and reach the goal runtime."""

from __future__ import annotations

import json

import pytest

from loopx.capabilities.progress_review.receipt import (
    load_progress_review_receipts,
    normalize_progress_review_receipt,
)
from loopx.control_plane.status.autonomous_replan_projection import (
    build_autonomous_replan_obligation,
)
from loopx.control_plane.work_items.autonomous_replan_ack import (
    autonomous_replan_ack_recorded,
)
from loopx.control_plane.work_items.external_progress_review import (
    external_progress_review_obligation,
)
from loopx.control_plane.work_items.progress_review_policy import progress_review_goal_policy
from loopx_jev import drift
from loopx_jev.progress import choice_drift_signal, noul_drift_signal
from loopx_jev.protocol import validate_noul
from loopx_jev.store import atomic_json
from drift_fixtures import DRIFT_NOULS, response
from test_drift import change_and_queue, git


@pytest.fixture
def runtime_study(tmp_path):
    repo = tmp_path / "work"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    (repo / "code.py").write_text("TIMEOUT = 1\n")
    git(repo, "add", "code.py")
    git(repo, "commit", "-qm", "baseline")
    basis = tmp_path / "basis.json"
    atomic_json(
        basis,
        {
            "goal_id": "drift-test",
            "objective": "Retry transient failures",
            "acceptance": ["A transient error is retried once"],
            "evidence": [],
        },
    )
    config = tmp_path / "config.json"
    atomic_json(
        config,
        {
            "schema_version": "loopx_jev_drift_config_v0",
            "mode": "shadow",
            "scenarios": ["progress_review"],
            "model": "fixture-v1",
            "allow_egress": True,
        },
    )
    root = tmp_path / "observer"
    runtime = tmp_path / "runtime"
    created = drift.initialize(
        root, repo, basis, config, ["code.py", "new.txt"], runtime_root=runtime
    )
    assert created["receipts"] == "goal_runtime"
    return root, repo, basis, config, runtime


def test_noul_validation_and_drift_signal_derivation() -> None:
    assert validate_noul({"type": "noul", "noul": 0.25}) == 0.25
    for bad in ({"type": "choice"}, {"type": "noul", "noul": 1.5}, {"type": "noul", "noul": True}, {"type": "noul"}):
        with pytest.raises(ValueError):
            validate_noul(bad)
    # Behaviour change is recorded only; it does not protect unrelated work.
    assert noul_drift_signal(0.05, 0.1, 0.6) is True
    assert noul_drift_signal(0.9, 0.1, 0.6) is False
    assert noul_drift_signal(0.05, 0.7, 0.6) is False
    assert noul_drift_signal(0.5, 0.5, 0.6) is None
    assert noul_drift_signal(None, 0.1, 0.6) is None
    assert choice_drift_signal("off_goal", "no_new_evidence") is True
    assert choice_drift_signal("on_goal", "unknown") is False
    assert choice_drift_signal("unknown", "new_evidence") is False
    assert choice_drift_signal("unknown", "unknown") is None
    assert choice_drift_signal("off_goal", "unknown") is None


def test_completed_drift_evaluation_writes_a_normalized_goal_receipt(runtime_study):
    root, repo, basis, config, runtime = runtime_study
    change_and_queue((root, repo, basis, config), 1)

    def send(request, config, key):
        return {"response": response(request, ["off_goal", "no_new_evidence"], nouls=DRIFT_NOULS)}

    drained = drift.drain(root, config, transport=send, credential=lambda: "fixture")
    assert drained["processed"][0]["status"] == "completed"
    receipts, rejected = load_progress_review_receipts(runtime, "drift-test")
    assert rejected == 0 and len(receipts) == 1
    receipt = receipts[0]
    assert receipt["status"] == "completed"
    assert receipt["drift_signal"] == {"noul": True, "choice": True}
    assert receipt["judgments"]["choice"] == {"relation": "off_goal", "increment": "no_new_evidence"}
    assert receipt["judgments"]["noul"]["behavior_change"] == 0.05
    assert receipt["run"]["turn_instance_id"] == "turn-1"
    assert receipt["run"]["agent_id"] == "worker"
    assert receipt["question_version"] == "scoped-progress-sentinel-v2"
    assert receipt["signal_rule_version"] == "progress_review_signal_rule_v2"
    assert receipt["reason"] is None
    assert receipt["model"] == "fixture-v1"
    assert receipt["timing_ns"]["evaluation"] >= 0
    raw = (runtime / "goals" / "drift-test" / "progress-review" / "receipts").glob("*.json")
    text = json.dumps([json.loads(path.read_text()) for path in raw])
    assert "RENAMED_TIMEOUT" not in text and "delta" not in text.lower().replace("drift", "")
    view = drift.status(root)
    assert view["receipts_written"] == 1
    assert view["events"][0]["receipt"]["status"] == "written"
    assert view["events"][0]["drift_signal"] == {"noul": True, "choice": True}


@pytest.mark.parametrize(
    ("threshold", "serves", "evidence", "expected"),
    [
        (0.5, 0.5, 0.5, None),
        (0.5, 0.5, 0.49, None),
        (0.5, 0.49, 0.5, None),
        (0.5, 0.49, 0.49, True),
        (0.5, 0.51, 0.49, False),
        (0.6, 0.5, 0.5, None),
        (0.6, 0.2, 0.2, True),
    ],
)
def test_observer_only_forms_noul_obligation_from_decisive_drift(
    runtime_study, threshold, serves, evidence, expected
):
    root, repo, basis, config, runtime = runtime_study
    configured = json.loads(config.read_text())
    configured["minimum_label_probability"] = threshold
    atomic_json(config, configured)

    def send(request, config, key):
        return {"response": response(request, ["off_goal", "no_new_evidence"], nouls={
            "behavior_change": 0.5, "serves_acceptance": serves, "evidence_increment": evidence,
        })}

    for sequence in (1, 2):
        change_and_queue((root, repo, basis, config), sequence)
        drained = drift.drain(root, config, transport=send, credential=lambda: "fixture")
        assert drained["processed"][0]["status"] == "completed"
    receipts, rejected = load_progress_review_receipts(runtime, "drift-test")
    assert rejected == 0 and len(receipts) == 2
    assert all(row["signal_rule_version"] == "progress_review_signal_rule_v2" for row in receipts)
    assert all(row["drift_signal"] == {"noul": expected, "choice": True} for row in receipts)
    runs = [{
        **row["run"], "classification": "bounded_delivery",
        "progress_observation": {
            "schema_version": "typed_progress_observation_v0", "result_class": "advanced",
            "hypothesis_id": f"probe-{row['sequence']}",
        },
    } for row in receipts]
    formed = {}
    for mode in ("off", "shadow", "assist"):
        policy = progress_review_goal_policy({"control_plane": {"progress_review": {
            "mode": mode, "contract_revision": receipts[0]["contract_revision"],
        }}})
        formed[mode] = external_progress_review_obligation(
            runs, external_progress_review={"policy": policy, "receipts": receipts},
            agent_id="worker", ack_recorded=autonomous_replan_ack_recorded,
            build_obligation=build_autonomous_replan_obligation, agent_todos=None,
        ) is not None
    assert formed == {"off": False, "shadow": False, "assist": expected is True}


def test_on_goal_and_abstained_evaluations_never_carry_a_drift_flag(runtime_study):
    root, repo, basis, config, runtime = runtime_study
    change_and_queue((root, repo, basis, config), 1)

    def on_goal(request, config, key):
        return {"response": response(request, ["on_goal", "new_evidence"])}

    drift.drain(root, config, transport=on_goal, credential=lambda: "fixture")
    change_and_queue((root, repo, basis, config), 2)

    def abstain(request, config, key):
        return {"response": response(request, ["unknown", "unknown"], nouls={name: 0.5 for name in DRIFT_NOULS})}

    drift.drain(root, config, transport=abstain, credential=lambda: "fixture")
    receipts, _ = load_progress_review_receipts(runtime, "drift-test")
    by_sequence = {receipt["sequence"]: receipt for receipt in receipts}
    assert by_sequence[0]["status"] == "completed"
    assert by_sequence[0]["drift_signal"] == {"noul": False, "choice": False}
    assert by_sequence[1]["status"] == "abstained"
    assert by_sequence[1]["drift_signal"] == {"noul": None, "choice": None}
    for receipt in receipts:
        normalize_progress_review_receipt(receipt)


def test_queued_event_writes_a_pending_receipt_that_evaluation_replaces(runtime_study):
    root, repo, basis, config, runtime = runtime_study
    created = drift.state(root)
    assert created["runtime_root"] == str(runtime.resolve())
    change_and_queue((root, repo, basis, config), 1)
    receipts, rejected = load_progress_review_receipts(runtime, "drift-test")
    assert rejected == 0 and len(receipts) == 1
    assert receipts[0]["status"] == "not_evaluated"
    assert receipts[0]["reason"] == "pending_evaluation"
    assert receipts[0]["drift_signal"] == {"noul": None, "choice": None}
    pending_id = receipts[0]["event_id"]

    def send(request, config, key):
        return {"response": response(request, ["off_goal", "no_new_evidence"], nouls=DRIFT_NOULS)}

    drift.drain(root, config, transport=send, credential=lambda: "fixture")
    receipts, rejected = load_progress_review_receipts(runtime, "drift-test")
    assert rejected == 0 and len(receipts) == 1
    assert receipts[0]["event_id"] == pending_id and receipts[0]["status"] == "completed"


def test_initialize_reports_the_contract_revision_to_pin(runtime_study):
    root, repo, basis, config, runtime = runtime_study
    import hashlib

    expected = hashlib.sha256(basis.read_bytes()).hexdigest()
    assert drift.state(root)["contract_revision"] == expected
    assert drift.status(root)["contract_revision"] == expected


def test_failed_evaluation_writes_a_failed_receipt_without_judgments(runtime_study):
    root, repo, basis, config, runtime = runtime_study
    change_and_queue((root, repo, basis, config), 1)
    from loopx_jev.transport import TransportFailure

    def boom(request, config, key):
        raise TransportFailure("deadline_exceeded")

    drift.drain(root, config, transport=boom, credential=lambda: "fixture")
    receipts, _ = load_progress_review_receipts(runtime, "drift-test")
    assert receipts[0]["status"] == "failed"
    assert receipts[0]["reason"] == "deadline_exceeded"
    assert receipts[0]["judgments"] == {"choice": None, "noul": None}
    assert receipts[0]["drift_signal"] == {"noul": None, "choice": None}


def test_private_only_observer_writes_no_receipt(tmp_path, runtime_study):
    root, repo, basis, config, runtime = runtime_study
    private_root = tmp_path / "private"
    drift.initialize(private_root, repo, basis, config, ["code.py"])
    assert drift.state(private_root)["runtime_root"] is None
    (repo / "code.py").write_text("RENAMED = 9\n")
    record = root.parent / "run-private.json"
    atomic_json(record, {"goal_id": "drift-test", "generated_at": "2026-01-01T00:00:09Z", "turn_instance_id": "turn-p", "agent_id": "worker"})
    drift.enqueue(private_root, drift.prepare(private_root, config), record)

    def send(request, config, key):
        return {"response": response(request, ["off_goal", "no_new_evidence"], nouls=DRIFT_NOULS)}

    drift.drain(private_root, config, transport=send, credential=lambda: "fixture")
    assert drift.status(private_root)["receipts_written"] == 0
    assert load_progress_review_receipts(runtime, "drift-test") == ([], 0)


def test_labels_are_private_and_summarize_agreement(runtime_study):
    root, repo, basis, config, runtime = runtime_study
    change_and_queue((root, repo, basis, config), 1)

    def send(request, config, key):
        return {"response": response(request, ["off_goal", "no_new_evidence"], nouls=DRIFT_NOULS)}

    drift.drain(root, config, transport=send, credential=lambda: "fixture")
    event_id = drift.status(root)["events"][0]["event_id"]
    with pytest.raises(ValueError):
        drift.label(root, event_id, "steer")
    with pytest.raises(ValueError):
        drift.label(root, "0" * 64, "drift")
    with pytest.raises(ValueError):
        drift.label(root, event_id, "drift", note="bad\x00note")
    view = drift.label(root, event_id, "on_goal", note="renamed constant only; reviewer disagrees")
    assert view["label_counts"] == {"on_goal": 1}
    assert view["label_agreement"]["noul"]["false_positive"] == 1
    assert view["events"][0]["label"]["truth"] == "on_goal"
    view = drift.label(root, event_id, "drift")
    assert view["label_agreement"]["noul"] == {
        "true_positive": 1,
        "false_positive": 0,
        "false_negative": 0,
        "true_negative": 0,
        "undecided": 0,
    }
    receipts, _ = load_progress_review_receipts(runtime, "drift-test")
    dumped = json.dumps(receipts)
    assert '"truth"' not in dumped and "reviewer disagrees" not in dumped
