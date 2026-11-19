"""Successful refresh display shares input without changing any result fact."""
from copy import deepcopy
import json

import pytest

from loopx.control_plane.quota.cli_projection import compact_refresh_state_cli_payload
from test_explore_result_writeback import ATTACHMENT


def packet():
    attachment = {**ATTACHMENT, "future_measurement": {"value": 2**60 + 1, "stop": "Keep the original stop."}}
    return {"ok": True, "settlement_identity": {"effect_id": "original-turn"},
        "settlement_progress": {"state": "settled"}, "future_control": {"stop": True},
        "explore_result": attachment, "explore_result_delivery": {
            "ok": True, "retryable": False, "primary_committed": True, "future_delivery": [0, False],
            "hook": {"schema_version": "loopx_post_writeback_capability_hook_dispatch_v0",
                "phase": "post_writeback", "failures": [], "intent_count": 1, "intents": [{
                    "schema_version": "loopx_capability_intent_v0", "intent_kind": "explore.result_ingestion",
                    "idempotency_key": "original-intent", "source_receipt_id": "original-receipt",
                    "payload": {"attachment": deepcopy(attachment), "future_input": {"retain": True}},
                    "requested_write_scope": []}]}}}


def intent_payload(value):
    return value["explore_result_delivery"]["hook"]["intents"][0]["payload"]


@pytest.mark.parametrize("value", [2**60 + 1, 1.0, -0.0])
def test_all_result_facts_are_reconstructable_in_one_response_without_mutation(value):
    source = packet()
    source["explore_result"]["future_measurement"]["value"] = value
    intent_payload(source)["attachment"]["future_measurement"]["value"] = value
    original = json.dumps(source)
    shared = compact_refresh_state_cli_payload(source)
    assert intent_payload(shared)["attachment_ref"] == "$.explore_result"
    restored = deepcopy(shared)
    target = intent_payload(restored)
    assert target.pop("attachment_ref") == "$.explore_result"
    target["attachment"] = restored["explore_result"]
    assert restored == source
    assert json.dumps(source) == original
    assert len(json.dumps(shared).encode()) < len(original.encode())
    assert compact_refresh_state_cli_payload(shared) == shared


@pytest.mark.parametrize("mutation", ["failed", "pending", "hook-failed", "conflict", "numeric-collision",
    "unknown-hook", "unknown-intent", "unknown-attachment", "preexisting-ref", "missing-source"])
def test_failures_conflicts_unknown_schemas_and_reference_collisions_stay_complete(mutation):
    source = packet()
    delivery = source["explore_result_delivery"]
    if mutation == "failed":
        source["ok"] = False
    elif mutation == "pending":
        delivery.update(ok=False, retryable=True, error="Replay the original writeback.")
    elif mutation == "hook-failed":
        delivery["hook"]["failures"] = [{"error_code": "future_failure"}]
    elif mutation == "conflict":
        intent_payload(source)["attachment"]["interpretation"] += " Additional stop."
    elif mutation == "numeric-collision":
        intent_payload(source)["attachment"]["future_measurement"]["value"] = 2**60
    elif mutation == "unknown-hook":
        delivery["hook"]["schema_version"] = "future_hook_v1"
    elif mutation == "unknown-intent":
        delivery["hook"]["intents"][0]["intent_kind"] = "future_ingestion"
    elif mutation == "unknown-attachment":
        source["explore_result"]["schema_version"] = "future_attachment_v1"
    elif mutation == "preexisting-ref":
        intent_payload(source)["attachment_ref"] = "$.other_source"
    else:
        del source["explore_result"]
    original = json.dumps(source)
    assert json.dumps(compact_refresh_state_cli_payload(source)) == original


def test_disabled_path_needs_no_projection_and_display_failure_retains_committed_receipt(monkeypatch):
    from loopx.control_plane.quota import turn_envelope
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("display transport unavailable")

    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", unavailable)
    off = {"ok": True, "settlement_progress": {"state": "settled"}}
    assert compact_refresh_state_cli_payload(off) is off
    assert calls == []
    source = packet()
    assert compact_refresh_state_cli_payload(source) is source
    assert len(calls) == 1
