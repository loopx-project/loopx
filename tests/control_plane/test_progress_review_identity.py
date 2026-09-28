"""An external judgment must refer to the same accountable work transition."""
from copy import deepcopy

import pytest

from tests.control_plane.test_external_progress_review import receipt, run, trigger


@pytest.mark.parametrize("field", ["agent_id", "todo_id"])
@pytest.mark.parametrize("missing_side", ["receipt", "run"])
def test_absent_identity_is_not_a_wildcard(field, missing_side):
    runs = [dict(run(2, turn="t2"), todo_id="todo-a"), run(1, turn="t1")]
    receipts = [receipt(2, turn="t2", todo="todo-a"), receipt(1, turn="t1")]
    target = receipts[0]["run"] if missing_side == "receipt" else runs[0]
    target.pop(field)
    assert trigger(runs, receipts) is None


def test_matching_unbound_todo_is_legal_but_unattributed_agent_is_not():
    runs = [run(2, turn="t2"), run(1, turn="t1")]
    receipts = [receipt(2, turn="t2"), receipt(1, turn="t1")]
    assert trigger(runs, receipts)
    for row in runs:
        row.pop("agent_id")
    for item in receipts:
        item["run"]["agent_id"] = None
    assert trigger(runs, receipts, agent_id=None) is None


def test_peer_or_unattributed_ack_does_not_clear_the_lane():
    runs = [run(2, turn="t2"), run(1, turn="t1")]
    receipts = [receipt(2, turn="t2"), receipt(1, turn="t1")]
    for agent in ("peer", ""):
        assert trigger([run(3, turn="ack", agent=agent, ack=True), *runs], receipts)
    assert trigger([run(3, turn="ack", ack=True), *runs], receipts) is None


@pytest.mark.parametrize("bad_turn", ["bad id", "x" * 300])
def test_malformed_turn_cannot_borrow_a_legacy_receipt(bad_turn):
    assert trigger([run(2, turn=bad_turn), run(1)], [receipt(2), receipt(1)]) is None


def test_conflicting_direct_and_settlement_turn_cannot_fall_back():
    bad = dict(run(2, turn="t2"), settlement_identity={"turn_instance_id": "other"})
    assert trigger([bad, run(1)], [receipt(2), receipt(1)]) is None
    valid = dict(run(2), settlement_identity={"turn_instance_id": "t2"})
    assert trigger([valid, run(1)], [receipt(2, turn="t2"), receipt(1)])


def test_timestamp_fallback_must_be_unique_on_the_run_side():
    same_second = run(2)
    same_second["progress_observation"]["hypothesis_id"] = "another-work-transition"
    assert trigger([same_second, run(2), run(1)], [receipt(2), receipt(1)]) is None
    # Different Todo identity disambiguates two independent transitions.
    runs = [dict(run(2), todo_id="todo-a"), dict(run(2), todo_id="todo-b")]
    receipts = [receipt(2, todo="todo-a"), receipt(3, todo="todo-b")]
    receipts[1]["run"]["generated_at"] = runs[1]["generated_at"]
    assert trigger(runs, receipts)


@pytest.mark.parametrize("field,other", [("agent_id", "peer"), ("todo_id", "todo-b")])
def test_conflicting_turn_receipts_cannot_be_resolved_by_clock(field, other):
    runs = [run(2, turn="t2"), run(1, turn="t1")]
    good = receipt(2, turn="t2")
    conflicting = deepcopy(good)
    conflicting["run"][field] = other
    for timestamp in (0.0, 999.0):
        conflicting["recorded_at"] = timestamp
        for pair in ([good, conflicting], [conflicting, good]):
            assert trigger(runs, [*pair, receipt(1, turn="t1")]) is None


def test_retry_with_different_work_identity_is_not_the_same_transition():
    runs = [dict(run(3, turn="t2"), todo_id="todo-b"), run(2, turn="t2"), run(1, turn="t1")]
    assert trigger(runs, [receipt(3, turn="t2", todo="todo-b"), receipt(1, turn="t1")]) is None


def test_misattributed_positive_verdict_cannot_erase_a_formed_window():
    runs = [dict(run(3, turn="t3"), todo_id="todo-a"), run(2, turn="t2"), run(1, turn="t1")]
    result = trigger(runs, [receipt(3, turn="t3", noul=False), receipt(2, turn="t2"), receipt(1, turn="t1")])
    assert result
    assert result["unevaluated_transitions"]["by_reason"] == {"identity_conflict": 1}


def test_neutral_row_preserves_an_attributable_accepted_ack():
    ack = dict(run(3, ack=True), classification="quota_slot_spent")
    assert trigger([ack, run(2, turn="t2"), run(1, turn="t1")],
                   [receipt(2, turn="t2"), receipt(1, turn="t1")],
                   neutral_classifications={"quota_slot_spent"}) is None


def test_anonymous_positive_judgment_cannot_clear_worker_obligation():
    runs = [run(3, turn="t3", agent=""), run(2, turn="t2"), run(1, turn="t1")]
    result = trigger(runs, [receipt(3, turn="t3", agent="", noul=False), receipt(2, turn="t2"), receipt(1, turn="t1")])
    assert result
    assert all(claim["hypothesis_id"] != "hypothesis-3" for claim in result["progress_window"])
