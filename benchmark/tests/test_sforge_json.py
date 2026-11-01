"""Native result numbers survive JSON without interpreting literal task data."""
import copy
import json
import math

import pytest

from benchmark.runtime import sforge_json


def test_finite_legacy_representation_and_literal_tags_are_unchanged():
    value = {"score": -2.5, "zero": 0.0, "missing": None, "metrics": {
        "nonfinite_values": [{"path": ["score"], "kind": "nan"}],
        "sforge_nonfinite_json": 1, "value": {"score": None},
        "text": ["NaN", "Infinity", "-Infinity"], "negative_zero": -0.0}}
    assert sforge_json.dumps(value) == json.dumps(value, allow_nan=False)
    assert sforge_json.loads(sforge_json.dumps(value)) == value
    assert math.copysign(1, sforge_json.loads(sforge_json.dumps(value))["metrics"]["negative_zero"]) == -1


def test_nonfinite_values_and_literal_envelopes_are_lossless_without_source_mutation():
    literal = {"sforge_nonfinite_json": 1, "value": {"score": None},
               "nonfinite_values": [{"path": ["score"], "kind": "nan"}]}
    value = {"score": float("inf"), "metrics": {"nested": [float("-inf"), float("nan"), None],
             "literal": literal, 7: float("inf")}}
    restored = sforge_json.loads(sforge_json.dumps(value))
    assert restored["score"] == float("inf")
    assert restored["metrics"]["nested"][0] == float("-inf")
    assert math.isnan(restored["metrics"]["nested"][1])
    assert restored["metrics"]["nested"][2] is None
    assert restored["metrics"]["7"] == float("inf")
    assert restored["metrics"]["literal"] == literal
    assert sforge_json.loads(sforge_json.dumps(literal)) == literal
    assert sforge_json.loads(sforge_json.dumps({"sforge_nonfinite_json": "literal"})) == {
        "sforge_nonfinite_json": "literal"}
    assert value["score"] == float("inf") and 7 in value["metrics"]
    assert math.isnan(value["metrics"]["nested"][1])


def test_nonfinite_binding_is_independent_of_object_key_order():
    first = {"released_results": {"b": float("inf"), "a": float("nan")}, "next_agent_round": 3}
    second = {"next_agent_round": 3, "released_results": {"a": float("nan"), "b": float("inf")}}
    assert sforge_json.dumps(first, sort_keys=True) == sforge_json.dumps(second, sort_keys=True)


@pytest.mark.parametrize("fault", [
    "version", "boolean_version", "extra_envelope_field", "missing_values", "empty_values",
    "wrong_value", "entry_field", "unknown_kind", "boolean_index", "negative_index",
    "missing_path", "empty_path", "non_null", "duplicate", "outside_results",
])
def test_malformed_nonfinite_transport_is_rejected(fault):
    packet = sforge_json.encode_document({"released_results": {"nested": [float("inf")]},
                                          "next_agent_round": None})
    entry = packet["nonfinite_values"][0]
    if fault == "version":
        packet["sforge_nonfinite_json"] = 2
    elif fault == "boolean_version":
        packet["sforge_nonfinite_json"] = True
    elif fault == "extra_envelope_field":
        packet["unknown"] = 1
    elif fault == "missing_values":
        del packet["nonfinite_values"]
    elif fault == "empty_values":
        packet["nonfinite_values"] = []
    elif fault == "wrong_value":
        packet["value"] = []
    elif fault == "entry_field":
        entry["unknown"] = 1
    elif fault == "unknown_kind":
        entry["kind"] = "infinity"
    elif fault == "boolean_index":
        entry["path"][-1] = False
    elif fault == "negative_index":
        entry["path"][-1] = -1
    elif fault == "missing_path":
        entry["path"] = ["released_results", "missing"]
    elif fault == "empty_path":
        entry["path"] = []
    elif fault == "non_null":
        packet["value"]["released_results"]["nested"][0] = 0
    elif fault == "duplicate":
        packet["nonfinite_values"].append(copy.deepcopy(entry))
    else:
        entry["path"] = ["next_agent_round"]
    with pytest.raises(ValueError):
        sforge_json.loads(json.dumps(packet), fields=("released_results",))


@pytest.mark.parametrize("encoded", [
    '{"score":NaN}', '{"score":Infinity}', '{"score":-Infinity}', '{"score":1e999}',
    '{"score":1,"score":2}', '{"metrics":{"same":1,"same":2}}', '[]',
])
def test_unmarked_numbers_duplicate_keys_and_nonobjects_are_rejected(encoded):
    with pytest.raises(ValueError):
        sforge_json.loads(encoded)


def test_encoding_cannot_hide_invalid_opportunity_or_colliding_json_keys():
    with pytest.raises(ValueError, match="outside released"):
        sforge_json.dumps({"next_agent_round": float("inf")}, fields=("released_results",))
    with pytest.raises(ValueError, match="collide"):
        sforge_json.dumps({"metrics": {1: "first", "1": float("inf")}})
