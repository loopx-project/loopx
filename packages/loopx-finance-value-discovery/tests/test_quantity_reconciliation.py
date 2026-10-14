"""Count reconciliation does not authenticate ownership, cash or trading."""
import copy
import json
from decimal import localcontext
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from loopx_finance_value_discovery import assess_quantity_reconciliation
from test_period_dispatch import invoke

PACKAGE = Path(__file__).resolve().parents[1]


def request():
    return json.loads((PACKAGE / "examples/quantity-reconciliation-v1.json").read_text())


def test_exact_stock_plus_signed_flows_and_same_input_are_retained():
    payload = request(); before = copy.deepcopy(payload)
    result = assess_quantity_reconciliation(payload)
    assert result == assess_quantity_reconciliation(payload) and payload == before
    assert result["state"] == "consistent" and result["quantity_reconciled"]
    assert result["candidate_net_quantity"] == "17"
    assert result["reconciled_closing_quantity"] == "117"
    assert result["candidate_closing_difference"] == "0"
    assert not result["reason_codes"]
    assert result["operands"] == payload
    assert result["exact_cash"] is None
    assert not any(result[k] for k in ["financial_admission", "trading_allowed",
                                     "cash_flow_derived", "source_evidence_authenticated"])


@pytest.mark.parametrize("case", ["class", "subject", "pool", "version", "unit", "scale", "source", "duplicate", "order", "outside_window", "unknown_identity"])
def test_incompatible_rows_never_get_combined(case):
    p = request(); f = p["flows"][0]
    identity_keys = {"class":"instrument_ref", "subject":"subject_ref",
                     "pool":"ownership_scope_ref", "version":"source_version_ref"}
    if case in identity_keys: f["identity"][identity_keys[case]] = "synthetic:other"
    if case == "unit": f["unit"]["measure_ref"] = "native_contract"
    if case == "scale": f["unit"]["scale"] = 3
    if case == "source": f["source_digest"] = "sha256:" + "a" * 64
    if case == "duplicate": p["flows"][1]["evidence_ref"] = f["evidence_ref"]
    if case == "order": p["flows"].reverse()
    if case == "outside_window": f["position"] = 0
    if case == "unknown_identity": f["identity"]["ownership_scope_ref"] = None
    r = assess_quantity_reconciliation(p)
    assert not r["quantity_reconciled"] and r["state"] == "ineligible"
    assert r["candidate_expected_closing_quantity"] is None
    assert r["reconciled_closing_quantity"] is None
    assert assess_quantity_reconciliation(request())["quantity_reconciled"]


@pytest.mark.parametrize("case", ["opening", "closing", "flow", "coverage", "coverage_proof", "direction"])
def test_incomplete_evidence_never_infers_missing_stock_or_zero_flow(case):
    p = request()
    if case in {"opening", "closing", "flow"}:
        row = p[case + "_stock"] if case != "flow" else p["flows"][0]
        row.update(value=None, accuracy=None)
    if case == "coverage": p["coverage"]["complete"] = False
    if case == "coverage_proof": p["coverage"]["evidence_ref"] = None
    if case == "direction": p["flows"][0]["direction"] = None
    r = assess_quantity_reconciliation(p)
    assert r["state"] == "incomplete" and not r["quantity_reconciled"]
    assert r["candidate_expected_closing_quantity"] is None
    assert r["candidate_net_quantity"] is None


@pytest.mark.parametrize("case", ["unknown_accuracy", "rounded", "stock_as_flow", "flow_as_stock", "clock", "timezone", "source_ref"])
def test_known_lexical_sum_does_not_promote_unproven_evidence(case):
    p = request(); f = p["flows"][0]
    if case == "unknown_accuracy": f["accuracy"] = None
    if case == "rounded": f["accuracy"] = {"decimals":"0"}
    if case == "stock_as_flow": f["aggregation_kind"] = "stock"
    if case == "flow_as_stock": p["opening_stock"]["aggregation_kind"] = "flow"
    if case == "clock": f["captured_at"] = None
    if case == "timezone": f["captured_at"] = "2001-01-04T00:00:00"
    if case == "source_ref": f["evidence_ref"] = None
    r = assess_quantity_reconciliation(p)
    assert r["state"] == "ineligible" and not r["quantity_reconciled"]
    assert r["candidate_expected_closing_quantity"] == "117"
    assert r["reconciled_closing_quantity"] is None
    assert r["reason_codes"]


def test_mismatch_direction_and_missing_flow_are_real_conflicts():
    p = request(); p["closing_stock"]["value"] = "118"
    r = assess_quantity_reconciliation(p)
    assert r["state"] == "conflict" and r["candidate_closing_difference"] == "1"
    assert not r["quantity_reconciled"] and r["reconciled_closing_quantity"] is None
    p = request(); p["flows"][1]["direction"] = "increase"
    assert assess_quantity_reconciliation(p)["candidate_expected_closing_quantity"] == "133"
    assert assess_quantity_reconciliation(p)["state"] == "conflict"
    p = request(); p["flows"].pop()
    assert assess_quantity_reconciliation(p)["candidate_closing_difference"] == "-8"
    assert assess_quantity_reconciliation(p)["state"] == "conflict"


def test_legitimate_zero_without_flows_requires_explicit_complete_coverage():
    p = request(); p["flows"] = []
    p["opening_stock"]["value"] = p["closing_stock"]["value"] = "0"
    r = assess_quantity_reconciliation(p)
    assert r["quantity_reconciled"] and r["candidate_net_quantity"] == "0"
    p["coverage"]["complete"] = False
    assert not assess_quantity_reconciliation(p)["quantity_reconciled"]


def test_decimal_context_cannot_change_quantity_math():
    p = request(); p["opening_stock"]["value"] = "100.000000000000000001"
    p["closing_stock"]["value"] = "117.000000000000000001"
    with localcontext() as c:
        c.prec = 3; small = assess_quantity_reconciliation(p)
    with localcontext() as c:
        c.prec = 80; large = assess_quantity_reconciliation(p)
    assert small == large and small["quantity_reconciled"]


@pytest.mark.parametrize("case", ["currency_shape", "money_measure", "negative", "bool_position", "bad_direction", "extra_permission", "overflow", "missing_value_accuracy", "flow_limit"])
def test_malformed_inputs_fail_and_correct_input_recovers(case):
    p = request(); f = p["flows"][0]
    if case == "currency_shape": f["unit"] = {"currency":"USD", "scale":0}
    if case == "money_measure": f["unit"]["measure_ref"] = "USD"
    if case == "negative": f["value"] = "-25"
    if case == "bool_position": f["position"] = True
    if case == "bad_direction": f["direction"] = "buy"
    if case == "extra_permission": p["trading_allowed"] = True
    if case == "overflow": f["value"] = "1e1000"
    if case == "missing_value_accuracy": f["value"] = None
    if case == "flow_limit": p["flows"] = [copy.deepcopy(f) for _ in range(257)]
    with pytest.raises(ValueError): assess_quantity_reconciliation(p)
    assert assess_quantity_reconciliation(request())["quantity_reconciled"]


def test_direct_and_stdin_use_one_owner_and_fail_with_same_error():
    p = request()
    assert invoke(["assess-quantity", "--input-json", "-"], p) == invoke([], p) == (0, assess_quantity_reconciliation(p))
    p["flows"][0]["unit"] = {"currency":"USD", "scale":0}
    assert invoke(["assess-quantity", "--input-json", "-"], p) == invoke([], p)
    assert invoke([], p)[0] == 1


def test_packaged_schemas_validate_positive_missing_and_conflict_results():
    schemas = PACKAGE / "src/loopx_finance_value_discovery/schemas"
    source = json.loads((schemas / "quantity-reconciliation-input.schema.json").read_text())
    target = json.loads((schemas / "quantity-reconciliation-response.schema.json").read_text())
    registry = Registry().with_resource(source["$id"], Resource.from_contents(source))
    for schema in (source, target): Draft202012Validator.check_schema(schema)
    for p in [request(), dict(request(), flows=[])]:
        Draft202012Validator(source).validate(p)
        Draft202012Validator(target, registry=registry).validate(assess_quantity_reconciliation(p))


def test_expanded_exponents_fit_the_response_contract():
    p = request(); p["flows"] = [p["flows"][0]]
    p["opening_stock"]["value"] = p["closing_stock"]["value"] = "1e64"
    p["flows"][0]["value"] = "1e-64"
    result = assess_quantity_reconciliation(p)
    assert result["state"] == "conflict"
    assert len(result["candidate_expected_closing_quantity"]) > 128
    schemas = PACKAGE / "src/loopx_finance_value_discovery/schemas"
    source = json.loads((schemas / "quantity-reconciliation-input.schema.json").read_text())
    target = json.loads((schemas / "quantity-reconciliation-response.schema.json").read_text())
    registry = Registry().with_resource(source["$id"], Resource.from_contents(source))
    Draft202012Validator(source).validate(p)
    Draft202012Validator(target, registry=registry).validate(result)
