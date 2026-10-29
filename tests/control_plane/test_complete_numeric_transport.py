"""Complete consumer costs and the explicit experimental hash transition."""
from copy import deepcopy
import json

import pytest

from loopx.control_plane.quota import turn_envelope as turn
from loopx.control_plane.turn_driver.host_candidate import complete_host_input
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_complete_turn_decision import _complete_digest
from tests.control_plane.test_replan_host_context_projection import _quota_payload


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def test_prior_complete_numeric_binding_needs_its_pinned_reader():
    source = _quota_payload()
    source["observations"] = [0.5, 1.0, -0.0, 2**60 + 1, 1e-7]
    contract = turn.compose_turn_decision(source)
    assert contract["contract_binding"]["hash_codec"] == "context_source_numbers_v2"
    legacy = deepcopy(contract)
    binding = legacy["contract_binding"]
    binding["hash_codec"] = "context_source_numbers_v1"
    binding.pop("content_hash")
    original_source = {key: value for key, value in legacy.items()
                       if key not in {"schema_version", "contract_binding"}}
    binding["source_decision_hash"] = _complete_digest(original_source, include_fractional_tokens=True)
    binding["content_hash"] = _complete_digest(legacy, include_fractional_tokens=True)
    before = _json(legacy)
    envelope = turn.build_turn_envelope(contract)
    # This is a correctly signed earlier experimental binding, not corruption
    # that happens to reject after reinterpretation under the new hash rules.
    for read in (
        lambda: turn.assert_complete_turn_decision(legacy),
        lambda: turn.compose_turn_decision(legacy),
        lambda: turn.build_turn_envelope(legacy),
        lambda: complete_host_input({"work_contract": legacy, "turn_envelope": envelope}),
        lambda: turn._turn_envelope_result("driver_lineage", payload=legacy, envelope=envelope),
        lambda: turn._turn_envelope_result("replan_view", payload=legacy,
            source_hash=binding["content_hash"], stage="review"),
        lambda: turn.bind_turn_decision_capture(legacy, envelope,
            captured_decision_path="/capture/original.json", turn_instance_id="original-turn",
            turn_key="sha256:" + "a" * 64),
    ):
        with pytest.raises(RuntimeError, match="unsupported complete Turn decision hash codec"):
            read()
    assert _json(legacy) == before
    turn.assert_complete_turn_decision(contract)


def test_complete_numeric_observations_keep_full_transport_cost_proportional(tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    original_rpc, costs = turn.effect_runtime_result, []

    def measured_rpc(method, params, **kwargs):
        result = original_rpc(method, params, **kwargs)
        costs.append(len(_json(params).encode()) + len(_json(result).encode()))
        return result

    monkeypatch.setattr(turn, "effect_runtime_result", measured_rpc)
    totals, source_sizes = [], []
    template = _quota_payload()
    for fraction in (False, True):
        costs.clear()
        before = deepcopy(template)
        before["observations"] = [i + 0.5 if fraction else i for i in range(1000)]
        after = deepcopy(before)
        after["observations"][-1] += 1
        first, current = turn.compose_turn_decision(before), turn.compose_turn_decision(after)
        envelope = turn.build_turn_envelope(current)
        turn.assert_complete_turn_decision(current)
        model_input = complete_host_input({"work_contract": current, "turn_envelope": envelope})
        assert model_input is not None and _json(model_input["work_contract"]) == _json(current)
        assert _json(current["observations"]) == _json(after["observations"])
        scope = dict(goal_id="fixture", agent_id="reader", session_id="native", context_epoch="epoch")
        cold = turn.prepare_context_delivery(first, scope=scope, capture_id="before")
        full = turn.prepare_context_delivery(current, scope=scope, capture_id="after")
        delta = turn.prepare_context_delivery(current, scope=scope, capture_id="after", baseline=cold,
            continuity={"basis": "captured_file", "capture_bytes_verified": True, "target": cold["target"]})
        assert delta["mode"] == "delta"
        for baseline in (cold, None):
            restored = turn.restore_context_delivery(delta, baseline=baseline, current=full)
            assert _json(restored["context"]) == _json(current)
            turn.assert_complete_turn_decision(restored["context"])
        assert len(costs) == 12
        totals.append(sum(costs))
        source_sizes.append(len(_json(before).encode()) + len(_json(after).encode()))
    # The matched source differs only in numeric representation. Count both
    # RPC directions through composition, Host input, cold/delta and recovery;
    # larger fractional spellings do not justify per-value provenance growth.
    assert totals[1] / totals[0] <= 1.25 * source_sizes[1] / source_sizes[0]
