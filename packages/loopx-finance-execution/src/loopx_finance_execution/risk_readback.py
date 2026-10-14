"""Compare caller budgets with retained venue loss bounds, without execution.

The venue producer owns these facts and their authentication. A bound declared
here is never inferred from notional, leverage, a stop price or available funds.
Core still owns approval/consumption, and account capacity remains independent.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any

from .order_readback import TERMS_SCHEMA, _exact_sum, _number, _object, _order, _time
from .simulator import _digest, _token


_AMOUNTS = {
    "maximum_cash_committed",
    "maximum_additional_liability",
    "maximum_total_cost",
}
_BOUND_FIELDS = _AMOUNTS | {
    "schema_version",
    "terms_digest",
    "source_ref",
    "cash_unit",
    "liability_scope",
    "automatic_margin_addition",
}


def _opaque_id(value: object, name: str) -> None:
    if not isinstance(value, str) or _token(value, field=name) != value:
        raise ValueError(f"{name} requires an exact opaque string")


def check_risk_bound(
    terms: object,
    budget: object,
    exposure_bound: object,
    *,
    observed_at: str,
    now: str,
    maximum_age_seconds: int,
) -> dict[str, Any]:
    """Read-only check of declared maximum cash and loss, including all costs.

    Null amounts mean unknown, including a missing extra-liability or total-cost
    bound. Cross margin and automatic collateral addition are unsupported.
    Passing does not authenticate the venue facts, reserve money or grant an
    execution permit. Preserve this result beside the original source receipt.
    """
    order = _order(terms, TERMS_SCHEMA)
    for name in (
        "account_ref",
        "venue",
        "product",
        "instrument",
        "side",
        "quantity_unit",
        "order_type",
        "price_unit",
        "time_in_force",
        "margin_mode",
        "fee_unit",
    ):
        _opaque_id(order[name], name)
    limits = _object(
        budget,
        {"schema_version", "cash_unit", "maximum_input", "maximum_loss"},
        "budget",
    )
    if limits["schema_version"] != "finance_risk_budget_v0":
        raise ValueError("unsupported risk budget")
    _opaque_id(limits["cash_unit"], "cash_unit")
    maximum_input = _number(limits["maximum_input"], "maximum_input")
    maximum_loss = _number(limits["maximum_loss"], "maximum_loss")
    if type(maximum_age_seconds) is not int or not 0 < maximum_age_seconds <= 300:
        raise ValueError("maximum_age_seconds must be an explicit 1..300 bound")
    current = _time(now, "now")
    observed = _time(observed_at, "observed_at")
    digest = _digest(terms)
    reasons = []
    quantity_supported = order["quantity_unit"] == order["instrument"] and (
        (order["margin_mode"] == "cash" and order["product"] == "spot")
        or (
            order["margin_mode"] in {"isolated", "cross"}
            and order["product"] == "linear_perpetual"
        )
    )
    if not quantity_supported:
        reasons.append("product_or_quantity_basis_unsupported")
    if current >= _time(order["expires_at"], "expires_at"):
        reasons.append("expired")
    if observed > current or (current - observed).total_seconds() > maximum_age_seconds:
        reasons.append("stale_or_future_bound")
    if order["margin_mode"] == "cross":
        reasons.append("cross_margin_unsupported")
    if order["margin_mode"] == "cash" and (
        _number(order["leverage"], "leverage") != 1 or order["side"] != "buy"
    ):
        reasons.append("leveraged_or_short_cash_unsupported")
    bound = None
    amounts = {}
    if exposure_bound is None:
        reasons.append("exposure_bound_missing")
    else:
        bound = _object(exposure_bound, _BOUND_FIELDS, "exposure_bound")
        if bound["schema_version"] != "finance_exposure_bound_v0":
            raise ValueError("unsupported exposure bound")
        _opaque_id(bound["source_ref"], "source_ref")
        _opaque_id(bound["cash_unit"], "cash_unit")
        if bound["terms_digest"] != digest:
            reasons.append("terms_digest_mismatch")
        if bound["liability_scope"] not in {"capped", "uncapped", "unknown"}:
            raise ValueError("unsupported liability_scope")
        if bound["liability_scope"] != "capped":
            reasons.append("liability_not_capped")
        if (
            bound["automatic_margin_addition"] is not None
            and type(bound["automatic_margin_addition"]) is not bool
        ):
            raise ValueError("automatic_margin_addition must be boolean or null")
        if bound["automatic_margin_addition"] is not False:
            reasons.append("automatic_margin_addition_or_unknown")
        for name in sorted(_AMOUNTS):
            value = bound[name]
            if value is None:
                reasons.append(f"unknown:{name}")
            else:
                amounts[name] = _number(value, name, zero=True)
    units = {order["price_unit"], order["fee_unit"], limits["cash_unit"]}
    if bound is not None:
        units.add(bound["cash_unit"])
    if len(units) != 1:
        reasons.append("cash_unit_mismatch")
    upper_bounds = None
    if (
        len(amounts) == len(_AMOUNTS)
        and len(units) == 1
        and bound is not None
        and bound["terms_digest"] == digest
        and bound["liability_scope"] == "capped"
        and bound["automatic_margin_addition"] is False
        and order["margin_mode"] != "cross"
        and quantity_supported
    ):
        committed = amounts["maximum_cash_committed"]
        liability = amounts["maximum_additional_liability"]
        cost = amounts["maximum_total_cost"]
        notional = Fraction(_number(order["quantity"], "quantity")) * Fraction(
            _number(order["limit_price"], "limit_price")
        )
        leverage = Fraction(_number(order["leverage"], "leverage"))
        collateral = Fraction(committed)
        if order["margin_mode"] == "cash" and collateral < notional:
            reasons.append("cash_bound_below_order_notional")
        if order["margin_mode"] == "isolated" and collateral * leverage < notional:
            reasons.append("cash_bound_below_initial_margin")
        # Additional liability can demand further cash as well as cause loss.
        # Conservatively risk all committed cash: no stop-fill guarantee or
        # directional hypothesis may reduce it. Arithmetic ignores ambient
        # Decimal precision/traps, including the initial-margin comparison.
        cash = _exact_sum([committed, liability, cost])
        upper_bounds = {"maximum_input": str(cash), "maximum_loss": str(cash)}
        if cash > maximum_input:
            reasons.append("input_budget_exceeded")
        if cash > maximum_loss:
            reasons.append("loss_budget_exceeded")
    return {
        "schema_version": "finance_risk_bound_check_v0",
        "ok": not reasons,
        "status": "within_declared_bound" if not reasons else "held",
        "terms_digest": digest,
        "budget_digest": _digest(budget),
        "source_ref": bound["source_ref"] if bound is not None else None,
        "upper_bounds": upper_bounds,
        "reasons": reasons,
        "read_only": True,
        "venue_bound_authenticated": False,
        "execution_allowed": False,
        "external_write_performed": False,
    }
