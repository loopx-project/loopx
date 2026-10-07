"""Read-only financial assessment of one filled position episode.

This is a financial reducer, not a monitor scheduler or venue adapter. Inputs
and outputs contain private normalized account facts and must stay private.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_CEILING, localcontext
from functools import lru_cache
from importlib.resources import files
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jsonschema import Draft202012Validator

from .replay import canonical_sha256

def canonical_digest(value: object) -> str:
    return "sha256:" + canonical_sha256(value)

EXTENSION_ID = "loopx-finance-value-discovery"
OPERATION = "evaluate_finance_position_guard"
REQUEST_SCHEMA = "position_guard_request_v0"
RESULT_SCHEMA = "position_guard_result_v0"
PARTIAL_REQUEST_SCHEMA = "position_guard_partial_request_v1"
PARTIAL_RESULT_SCHEMA = "position_guard_partial_result_v1"
KINDS = ("position", "orders", "fills", "monitor")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return parsed
    except ValueError:
        pass
    # JSON Schema format validation may lack its optional date-time checker.
    # Enforce comparable clocks here without reflecting a private source value.
    raise ValueError("position guard input/result failed schema admission")


@lru_cache(maxsize=3)
def _validator(name: str) -> Draft202012Validator:
    # Optional finance operations must not break legacy --doctor/import paths.
    from jsonschema import Draft202012Validator, FormatChecker

    path = files("loopx_finance_value_discovery").joinpath("schemas", name)
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _validate(value: object, name: str) -> None:
    # A malformed private value must never be reflected in an error message.
    if not _validator(name).is_valid(value):
        raise ValueError("position guard input/result failed schema admission")


def _number(value: str) -> Decimal:
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("position guard requires finite decimal amounts")
    return result


def _amount(value: Decimal) -> str:
    return format(value, "f")


def _cash_amount(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.00000001"), rounding=ROUND_CEILING), "f").rstrip("0").rstrip(".") or "0"


def _source_state(observed_at: str | None, decision: datetime, max_age_seconds: int) -> str:
    if observed_at is None:
        return "missing"
    observed = _time(observed_at)
    return "available_unverified" if (
        observed <= decision and decision - observed <= timedelta(seconds=max_age_seconds)
    ) else "stale_or_future"


def evaluate_finance_position_guard(request: object) -> dict[str, Any]:
    if isinstance(request, dict) and request.get("schema_version") == PARTIAL_REQUEST_SCHEMA:
        _validate(request, "position-guard-partial.schema.json")
        result = _evaluate_partial(request)
        _validate(result, "position-guard-partial.schema.json")
        return result
    _validate(request, "position-guard-request.schema.json")
    assert isinstance(request, dict)
    with localcontext() as context:
        context.prec = 80
        result = _evaluate(request)
    _validate(result, "position-guard-response.schema.json")
    return result


def _evaluate_partial(request: dict[str, Any]) -> dict[str, Any]:
    """Project a caller's unresolved episode without inventing full receipts.

    Digest binding proves supplied-input consistency, never source authenticity.
    This path cannot authenticate a new deadline, financial facts or closure.
    """
    payload = request["input"]
    episode = payload["episode"]
    position = payload["position"]
    decision = _time(payload["decision_at"])
    runtime = _utc_now()
    if decision > runtime or runtime - decision > timedelta(seconds=300):
        raise ValueError("position guard decision clock is not current")
    if episode["trade_id"] != episode["target_key"]:
        raise ValueError("position guard requires a stable episode target key")
    parts = {"episode": episode, "position": position,
             "decision": payload["decision"], "execution": payload["execution"]}
    expected = sorted(
        (kind, value["source_ref"], canonical_digest(value))
        for kind, value in parts.items() if value is not None and value["source_ref"] is not None
    )
    actual = sorted((r["kind"], r["ref"], r["digest"]) for r in request["context_refs"])
    if actual != expected:
        raise ValueError("position guard partial digest binding mismatch")
    deadline_reached = decision >= _time(episode["max_hold_until"])
    quantity = None
    source_state = "missing"
    asset_matches = None
    gaps = set(payload["evidence_gaps"])
    if position is not None:
        if position["quantity"] is not None:
            quantity = _amount(_number(position["quantity"]))
            if "." in quantity:
                quantity = quantity.rstrip("0").rstrip(".")
        source_state = _source_state(position["observed_at"], decision, payload["max_age_seconds"])
        if position["asset"] is not None:
            asset_matches = position["asset"] == episode["asset"]
        if asset_matches is False:
            gaps.add("source_conflict")
    gaps = sorted(gaps)
    reasons = ["partial_evidence_unverified"]
    if deadline_reached:
        reasons.append("maximum_hold_reached")
    if source_state != "available_unverified":
        reasons.append("position_readback_" + source_state)
    if asset_matches is False:
        reasons.append("receipt_identity_mismatch")
    # Semantic identity excludes poll/receipt clocks, invocation ids and repeated
    # capture digests. Freshness class still changes when evidence becomes stale.
    material = {
        "trade_id": episode["trade_id"], "asset": episode["asset"],
        "original_episode_digest": episode["source_digest"],
        "maximum_hold_until": episode["max_hold_until"], "deadline_reached": deadline_reached,
        "decision": payload["decision"]["disposition"],
        "execution": payload["execution"]["disposition"],
        "observed_quantity": quantity, "position_source_state": source_state,
        "observed_asset": position["asset"] if position is not None else None,
        "quantity_unit": position["quantity_unit"] if position is not None else None,
        "position_asset_matches": asset_matches,
        "evidence_gaps": gaps, "pending": True,
    }
    result = {
        "schema_version": PARTIAL_RESULT_SCHEMA, "extension_id": EXTENSION_ID,
        "operation": OPERATION, "invocation_id": request["invocation_id"],
        "trade_id": episode["trade_id"], "target_key": episode["target_key"],
        "evaluated_at": payload["decision_at"], "request_digest": canonical_digest(request),
        "state": "exit_review_required" if deadline_reached else "attention_required",
        "urgent": deadline_reached, "reasons": reasons,
        "next_action": "verify_position_for_human_exit_review" if deadline_reached else "refresh_or_repair_readback",
        "observed_quantity": quantity, "position_verified": False,
        "risk_estimate": None, "protection_coverage": None, "exit_draft": None,
        "obligation": {"maximum_hold_until": episode["max_hold_until"],
                       "deadline_reached": deadline_reached,
                       "deadline_authority_verified": False,
                       "decision": payload["decision"]["disposition"],
                       "execution": payload["execution"]["disposition"],
                       "closeout_verified": False, "pending": True},
        "source_projections": {k: dict(v) if v is not None else None for k, v in parts.items()},
        "evidence_gaps": gaps,
        "material_projection": material, "material_digest": canonical_digest(material),
        "monitor_projection": {"keep_monitor_open": True},
        "effects": {"financial_mutations": False, "monitor_mutations": False, "scheduler_created": False},
        "privacy": "private_account_material",
    }
    if "source_clocks" in payload:
        clocks = dict(payload["source_clocks"])
        states = {kind: _source_state(value, decision, payload["max_age_seconds"])
                  for kind, value in clocks.items()}
        # A source bundle still exists when its position parser returns null.
        # Retain every capture clock; selecting the newest would hide a future
        # or stale history read. Freshness never authenticates account facts.
        result["source_clock_projection"] = {
            "observed_at": clocks, "states": states,
            "capture_digest": payload.get("source_capture_digest"),
        }
        material["source_clock_states"] = states
        result["material_digest"] = canonical_digest(material)
    return result


def _evaluate(request: dict[str, Any]) -> dict[str, Any]:
    payload = request["input"]
    trade = payload["trade"]
    decision = _time(payload["decision_at"])
    runtime = _utc_now()
    if decision > runtime or runtime - decision > timedelta(seconds=300):
        raise ValueError("position guard decision clock is not current")
    entry_at = _time(trade["entry_at"])
    deadline = _time(trade["max_hold_until"])
    if entry_at > decision or deadline <= entry_at:
        raise ValueError("position guard trade timeline is invalid")
    if trade["target_key"] != trade["trade_id"]:
        raise ValueError("position guard requires a stable episode target key")
    if trade["price_currency"] != trade["cash_currency"]:
        raise ValueError("position guard requires one normalized quote-cash currency")
    entry_quantity = _number(trade["entry_quantity"])
    entry_price = _number(trade["entry_price"])
    stop_price = _number(trade["stop_price"])
    take_price = _number(trade["take_profit_price"])
    if trade["side"] == "long":
        valid_levels = stop_price < entry_price < take_price
    else:
        valid_levels = take_price < entry_price < stop_price
    if not valid_levels:
        raise ValueError("position guard trade protection levels are invalid")

    receipts = payload["receipts"]
    by_kind = {row["kind"]: row for row in receipts}
    if len(by_kind) != 4 or set(by_kind) != set(KINDS):
        raise ValueError("position guard requires four unique receipt kinds")
    refs = request["context_refs"]
    expected = sorted((row["kind"], row["receipt_id"], canonical_digest(row)) for row in receipts)
    actual = sorted((row["kind"], row["ref"], row["digest"]) for row in refs)
    if actual != expected:
        raise ValueError("position guard receipt digest binding mismatch")

    reasons: list[str] = []
    def reason(code: str) -> None:
        if code not in reasons:
            reasons.append(code)

    observation_times = []
    position_verified = True
    for row in receipts:
        identity_matches = (row["trade_id"] == trade["trade_id"] and row["asset"] == trade["asset"]
                            and row["account_scope_digest"] == trade["account_scope_digest"])
        if not identity_matches:
            reason("receipt_identity_mismatch")
        observed = _time(row["observed_at"])
        observation_times.append(observed)
        fresh = observed <= decision and decision - observed <= timedelta(seconds=payload["max_age_seconds"])
        if not fresh:
            reason("receipt_stale_or_future")
        if not row["complete"]:
            reason("receipt_incomplete")
        if row["kind"] == "position":
            position_verified = identity_matches and fresh and row["complete"]
    if max(observation_times) - min(observation_times) > timedelta(seconds=payload["max_skew_seconds"]):
        reason("receipt_snapshot_skew")
    evidence_valid = not reasons

    position = by_kind["position"]["data"]
    orders = by_kind["orders"]["data"]["orders"]
    fill_data = by_kind["fills"]["data"]
    monitor = by_kind["monitor"]["data"]
    quantity = _number(position["quantity"])
    mark = _number(position["trigger_reference_price"])
    if position["side"] != trade["side"]:
        reason("position_side_mismatch")
    if quantity > entry_quantity:
        reason("position_increased_or_episode_mismatch")
    position_verified = position_verified and position["side"] == trade["side"] and quantity <= entry_quantity
    ids: dict[str, dict[str, Any]] = {}
    for fill in fill_data["fills"]:
        prior = ids.get(fill["fill_id"])
        if prior is not None and prior != fill:
            reason("conflicting_fill_identity")
        ids[fill["fill_id"]] = fill
        at = _time(fill["filled_at"])
        if at < entry_at or at > _time(by_kind["fills"]["observed_at"]):
            reason("fill_outside_episode_timeline")
    unique_fills = list(ids.values())
    entries = [f for f in unique_fills if f["role"] == "entry"]
    exits = [f for f in unique_fills if f["role"] == "exit"]
    entered = sum((_number(f["quantity"]) for f in entries), Decimal(0))
    exited = sum((_number(f["quantity"]) for f in exits), Decimal(0))
    if not fill_data["history_complete"] or entered != entry_quantity or entered - exited != quantity:
        reason("fill_position_reconciliation_unverified")
    if set(trade["opening_fill_ids"]) != {f["fill_id"] for f in entries}:
        reason("opening_fill_identity_mismatch")
    # Closure requires complete history captured at/after the position read and
    # all orders captured at/after it. A flat, earlier snapshot is insufficient.
    if quantity == 0 and (
        _time(by_kind["fills"]["observed_at"]) < _time(by_kind["position"]["observed_at"])
        or _time(by_kind["orders"]["observed_at"]) < _time(by_kind["position"]["observed_at"])
    ):
        reason("closure_readback_order_unverified")

    expected_exit_side = "sell" if trade["side"] == "long" else "buy"
    cover = {"stop_loss": Decimal(0), "take_profit": Decimal(0)}
    order_ids: set[str] = set()
    for order in orders:
        if order["order_id"] in order_ids:
            reason("duplicate_order_identity")
        order_ids.add(order["order_id"])
        if not order["reduce_only"] or order["side"] != expected_exit_side:
            reason("unsafe_open_order")
            continue
        if order["kind"] == "other":
            reason("unexpected_open_order")
            continue
        trigger = _number(order["trigger_price"])
        wanted = stop_price if order["kind"] == "stop_loss" else take_price
        if trigger != wanted or order["trigger_basis"] != position["trigger_basis"]:
            reason("protection_plan_or_basis_mismatch")
            continue
        loss_leg = order["kind"] == "stop_loss"
        valid_trigger = ((trigger < mark) == (trade["side"] == "long")) if loss_leg else ((trigger > mark) == (trade["side"] == "long"))
        if trigger == mark or not valid_trigger:
            reason("protection_trigger_crossed")
            continue
        # M1 accepts one individually full leg. Split/alternative quantities
        # are never summed without venue-specific tranche/contingency proof.
        cover[order["kind"]] = max(cover[order["kind"]], _number(order["remaining_quantity"]))
    if quantity > 0:
        if cover["stop_loss"] < quantity:
            reason("stop_loss_undercovered")
        if cover["take_profit"] < quantity:
            reason("take_profit_undercovered")
    elif orders:
        reason("residual_orders_after_flat")

    if (monitor["task_class"] != "continuous_monitor" or monitor["status"] != "open"
        or not monitor["todo_id"] or not monitor["claimed_by"]
        or monitor["target_key"] != trade["target_key"]):
        reason("monitor_binding_unverified")
    due = _time(monitor["next_due_at"])
    expires = _time(monitor["expires_at"]) if monitor["expires_at"] else None
    if quantity > 0:
        if expires is not None and expires <= decision:
            reason("monitor_expired_with_open_exposure")
        if due > deadline or (expires is not None and expires <= due):
            reason("monitor_misses_hold_deadline")
        if due <= decision:
            reason("review_due")
        if decision >= deadline:
            reason("maximum_hold_reached")

    # Fees already include any builder component: count each deduplicated fill
    # exactly once. Funding paid is signed (negative is a credit).
    sign = Decimal(1) if trade["side"] == "long" else Decimal(-1)
    actual_entry = sum((_number(f["quantity"]) * _number(f["price"]) for f in entries), Decimal(0)) / entered if entered else entry_price
    realized = sum((sign * _number(f["quantity"]) * (_number(f["price"]) - actual_entry) for f in exits), Decimal(0))
    fees = sum((_number(f["fee_cash"]) for f in unique_fills), Decimal(0))
    funding = _number(fill_data["funding_paid_cash"])
    reserve = _number(trade["exit_cost_reserve_cash"])
    stressed_net = realized + sign * quantity * (stop_price - actual_entry) - fees - funding - reserve
    estimated_loss = max(Decimal(0), -stressed_net)
    if not fill_data["costs_complete"]:
        reason("costs_unverified")
    elif quantity > 0 and estimated_loss > _number(trade["max_loss_cash"]):
        reason("estimated_stop_loss_exceeds_budget")

    verification_errors = [r for r in reasons if r not in {"review_due", "maximum_hold_reached", "estimated_stop_loss_exceeds_budget"}]
    closable = evidence_valid and quantity == 0 and not verification_errors and not orders and exited == entered
    urgent = any(r != "review_due" for r in reasons)
    if closable:
        state, action = "closed_verified", "propose_close_monitor"
    elif quantity == 0:
        state, action = "closure_unverified", "reconcile_exit_and_residual_orders"
    elif "maximum_hold_reached" in reasons:
        # The holding obligation survives late polling and missing evidence.
        # Verification still gates estimates, draft quantity and closure.
        state, action = "exit_review_required", "prepare_human_exit_draft"
    elif not evidence_valid or verification_errors:
        state, action = "attention_required", "refresh_or_repair_readback"
    elif "estimated_stop_loss_exceeds_budget" in reasons:
        state, action = "exit_review_required", "prepare_human_exit_draft"
    elif "review_due" in reasons:
        state, action = "review_due", "review_position"
    else:
        state, action = "protected_open", "keep_core_monitor"
    # No order payload, click instruction, signature or submit authority.
    draft = None
    if state == "exit_review_required" and position_verified:
        draft = {"status": "manual_final_check_required", "asset": trade["asset"],
                 "side": expected_exit_side, "quantity": _amount(quantity), "reduce_only": True,
                 "financial_authority": "human_final_submit", "price_terms": "fresh_venue_preview_required"}
    # A financial suggestion must never postpone a review already scheduled by
    # Core. Core remains responsible for accepting and persisting any change.
    future_due = min(due, decision + timedelta(seconds=trade["review_interval_seconds"]), deadline)
    return {"schema_version": RESULT_SCHEMA, "extension_id": EXTENSION_ID, "operation": OPERATION,
            "invocation_id": request["invocation_id"], "trade_id": trade["trade_id"],
            "target_key": trade["target_key"], "evaluated_at": payload["decision_at"],
            "request_digest": canonical_digest(request), "state": state, "urgent": urgent or bool(quantity == 0 and orders),
            "reasons": reasons, "next_action": action, "open_quantity": _amount(quantity),
            "protection_coverage": {k: _amount(min(v, quantity)) for k, v in cover.items()},
            "risk_estimate": {"estimated_stop_loss_cash": _cash_amount(estimated_loss) if evidence_valid and not verification_errors else None,
                              "currency": trade["cash_currency"], "is_loss_guarantee": False},
            "monitor_projection": {"todo_id": monitor["todo_id"], "next_due_at": monitor["next_due_at"],
                                   "maximum_hold_until": trade["max_hold_until"],
                                   "suggested_next_due_at": (decision if reasons else future_due).isoformat(),
                                   "keep_monitor_open": not closable},
            "exit_draft": draft, "effects": {"financial_mutations": False, "monitor_mutations": False,
                                               "scheduler_created": False}, "privacy": "private_account_material"}
