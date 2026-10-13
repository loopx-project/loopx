"""Financial readback gates for an already approved operation.

This provider owns financial terms and venue facts, never human authentication,
confirmation, consumption, quota or dispatch. These pure, read-only functions
do not issue an execution permit or submit/retry an order. The caller must keep
the original Core operation and consumption receipt and the venue attempt.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .simulator import _digest, _token


TERMS_SCHEMA = "finance_execution_terms_v0"
SUMMARY_SCHEMA = "finance_order_summary_v0"
OBSERVATION_SCHEMA = "finance_execution_observation_v0"
_DECIMALS = {"quantity", "limit_price", "leverage", "maximum_fee"}
_TERMS = {
    "account_ref",
    "venue",
    "product",
    "instrument",
    "side",
    "quantity",
    "quantity_unit",
    "order_type",
    "limit_price",
    "price_unit",
    "time_in_force",
    "reduce_only",
    "margin_mode",
    "leverage",
    "maximum_fee",
    "fee_unit",
    "protective_orders",
    "expires_at",
}


def _object(value: object, fields: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"{name} has missing or unsupported fields")
    return dict(value)


def _number(value: object, name: str, *, zero: bool = False) -> Decimal:
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError(f"{name} requires a finite decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{name} requires a finite decimal string") from exc
    if not result.is_finite() or result < 0 or (not zero and result == 0):
        raise ValueError(f"{name} is outside its supported range")
    return result


def _time(value: object, name: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError(f"{name} requires an aware timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{name} requires an aware timestamp")
    return result.astimezone(timezone.utc)


def _order(value: object, schema: str) -> dict[str, Any]:
    row = _object(value, _TERMS | {"schema_version"}, "order")
    if row.pop("schema_version") != schema:
        raise ValueError("order schema is unsupported")
    for name in _TERMS - _DECIMALS - {"reduce_only", "protective_orders", "expires_at"}:
        _token(row[name], field=name)
    for name in _DECIMALS:
        _number(row[name], name, zero=name == "maximum_fee")
    if row["side"] not in {"buy", "sell"} or row["order_type"] != "limit":
        raise ValueError("only explicit buy/sell limit orders are supported")
    if row["time_in_force"] not in {"GTC", "IOC"}:
        raise ValueError("unsupported time_in_force")
    if row["margin_mode"] not in {"isolated", "cross", "cash"}:
        raise ValueError("unsupported margin_mode")
    if type(row["reduce_only"]) is not bool:
        raise ValueError("reduce_only must be boolean")
    _time(row["expires_at"], "expires_at")
    protections = row["protective_orders"]
    if not isinstance(protections, list) or len(protections) > 2:
        raise ValueError("protective_orders requires a bounded explicit list")
    kinds = set()
    for protection in protections:
        p = _object(
            protection,
            {
                "kind",
                "trigger_price",
                "price_unit",
                "quantity",
                "quantity_unit",
                "reduce_only",
            },
            "protection",
        )
        if p["kind"] not in {"stop_loss", "take_profit"} or p["kind"] in kinds:
            raise ValueError("protection kind is unsupported or duplicated")
        kinds.add(p["kind"])
        _number(p["trigger_price"], "trigger_price")
        if _number(p["quantity"], "protection.quantity") > _number(
            row["quantity"], "quantity"
        ):
            raise ValueError("protection quantity exceeds order quantity")
        if (
            p["price_unit"] != row["price_unit"]
            or p["quantity_unit"] != row["quantity_unit"]
            or p["reduce_only"] is not True
        ):
            raise ValueError("protection units or reduce_only drifted")
    return row


def check_order_summary(
    terms: object,
    summary: object,
    *,
    observed_at: str,
    now: str,
    maximum_age_seconds: int,
) -> dict[str, Any]:
    """Compare exact final visible terms; approval is independently Core-owned.

    The original payload digest binds the original strings, even when decimal
    readback uses another equivalent formatting. No rounding or inferred fields.
    An order must still be unexpired at the caller's final pre-submit boundary.
    """
    if type(maximum_age_seconds) is not int or not 0 < maximum_age_seconds <= 300:
        raise ValueError("maximum_age_seconds must be an explicit 1..300 bound")
    expected = _order(terms, TERMS_SCHEMA)
    current = _time(now, "now")
    observed = _time(observed_at, "observed_at")
    reasons = []
    if current >= _time(expected["expires_at"], "expires_at"):
        reasons.append("expired")
    if observed > current or (current - observed).total_seconds() > maximum_age_seconds:
        reasons.append("stale_or_future_summary")
    try:
        actual = _order(summary, SUMMARY_SCHEMA)
    except ValueError:
        actual = None
        reasons.append("summary_missing_or_unsupported")
    if actual is not None:
        for name in sorted(_TERMS):
            left, right = expected[name], actual[name]
            matches = (
                (
                    _number(left, name, zero=name == "maximum_fee")
                    == _number(right, name, zero=name == "maximum_fee")
                )
                if name in _DECIMALS
                else left == right
            )
            if not matches:
                reasons.append(f"summary_mismatch:{name}")
    return {
        "ok": not reasons,
        "terms_digest": _digest(terms),
        "reasons": reasons,
        "execution_allowed": False,
        "external_write_performed": False,
        "next_step": "independent_authority_and_capacity_check"
        if not reasons
        else "stop",
    }


def reconcile_execution(
    terms: object, observation: object, *, operation_id: str, attempt_id: str
) -> dict[str, Any]:
    """Reduce retained venue facts without allowing another submission.

    Expiry does not erase an attempted order. Incomplete order/fill coverage,
    response loss and unverified absence remain unknown. Fees stay unknown when
    absent; entry and protection have independent statuses. Result delivery must
    consume this retained result, never invoke submission again.
    """
    order = _order(terms, TERMS_SCHEMA)
    fields = {
        "schema_version",
        "operation_id",
        "attempt_id",
        "terms_digest",
        "account_ref",
        "venue",
        "instrument",
        "observed_at",
        "coverage_complete",
        "submission_attempted",
        "order_id",
        "order_state",
        "fills",
        "protection_state",
    }
    row = _object(observation, fields, "observation")
    if row["schema_version"] != OBSERVATION_SCHEMA:
        raise ValueError("observation schema is unsupported")
    _token(operation_id, field="operation_id")
    _token(attempt_id, field="attempt_id")
    if (row["operation_id"], row["attempt_id"], row["terms_digest"]) != (
        operation_id,
        attempt_id,
        _digest(terms),
    ):
        raise ValueError("observation operation, attempt or terms drifted")
    for name in ["account_ref", "venue", "instrument"]:
        if row[name] != order[name]:
            raise ValueError("observation identity drifted")
    _time(row["observed_at"], "observed_at")
    if (
        type(row["coverage_complete"]) is not bool
        or type(row["submission_attempted"]) is not bool
    ):
        raise ValueError("observation flags must be boolean")
    if row["order_state"] not in {"unknown", "open", "filled", "cancelled", "rejected"}:
        raise ValueError("unsupported order_state")
    if row["protection_state"] not in {"unknown", "active", "failed", "not_required"}:
        raise ValueError("unsupported protection_state")
    if row["order_id"] is not None:
        _token(row["order_id"], field="order_id")
    fills = row["fills"]
    if not isinstance(fills, list) or len(fills) > 1000:
        raise ValueError("fills requires a bounded list")
    quantity = Decimal(0)
    fees = Decimal(0)
    fee_known = bool(fills)
    seen = set()
    for fill in fills:
        f = _object(
            fill,
            {
                "fill_id",
                "order_id",
                "side",
                "quantity",
                "quantity_unit",
                "price",
                "price_unit",
                "fee",
                "fee_unit",
            },
            "fill",
        )
        _token(f["fill_id"], field="fill_id")
        if f["fill_id"] in seen:
            raise ValueError("duplicate fill identity")
        seen.add(f["fill_id"])
        if row["order_id"] is None or f["order_id"] != row["order_id"]:
            raise ValueError("fill order identity drifted")
        for name in ["side", "quantity_unit", "price_unit"]:
            if f[name] != order[name]:
                raise ValueError("fill side or units drifted")
        price = _number(f["price"], "fill.price")
        limit = _number(order["limit_price"], "limit_price")
        if (order["side"] == "buy" and price > limit) or (
            order["side"] == "sell" and price < limit
        ):
            raise ValueError("fill is outside the confirmed price bound")
        quantity += _number(f["quantity"], "fill.quantity")
        if f["fee"] is None:
            fee_known = False
        elif f["fee_unit"] != order["fee_unit"]:
            raise ValueError("fill fee unit drifted")
        else:
            fees += _number(f["fee"], "fill.fee", zero=True)
    target = _number(order["quantity"], "quantity")
    if quantity > target:
        raise ValueError("fills exceed confirmed quantity")
    if not row["submission_attempted"] and (
        row["order_id"] is not None or fills or row["order_state"] != "unknown"
    ):
        raise ValueError("unattempted observation contains execution facts")
    if row["order_state"] == "rejected" and quantity:
        raise ValueError("rejected order has fills")
    if not row["submission_attempted"]:
        state = "not_submitted"
    elif (
        not row["coverage_complete"]
        or row["order_id"] is None
        or row["order_state"] == "unknown"
    ):
        state = "submission_unknown"
    elif quantity == target:
        state = "filled" if row["order_state"] == "filled" else "submission_unknown"
    elif row["order_state"] == "filled":
        state = "submission_unknown"
    elif quantity:
        state = "partially_filled"
    elif row["order_state"] in {"rejected", "cancelled"}:
        state = row["order_state"]
    else:
        state = "submitted"
    protection_complete = (
        not order["protective_orders"] or row["protection_state"] == "active"
    )
    fee_exceeded = fee_known and fees > _number(
        order["maximum_fee"], "maximum_fee", zero=True
    )
    return {
        "ok": True,
        "operation_id": operation_id,
        "attempt_id": attempt_id,
        "terms_digest": _digest(terms),
        "state": state,
        "filled_quantity": str(quantity),
        "quantity_unit": order["quantity_unit"],
        "fee": str(fees) if fee_known else None,
        "fee_unit": order["fee_unit"],
        "fee_cap_exceeded": fee_exceeded,
        "protection_state": row["protection_state"],
        "protection_complete": protection_complete,
        "financial_readback_complete": state == "filled"
        and protection_complete
        and fee_known
        and not fee_exceeded,
        "resubmit_allowed": False,
        "execution_allowed": False,
        "external_write_performed": False,
        "observed_at": row["observed_at"],
    }
