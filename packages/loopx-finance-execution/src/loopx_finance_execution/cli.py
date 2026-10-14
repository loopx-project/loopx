from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .simulator import execute_simulated_finance_operation
from .order_readback import check_order_summary, reconcile_execution
from .risk_readback import check_risk_bound


def run(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="loopx-finance-execution")
    parser.add_argument("--doctor", action="store_true")
    readback = parser.add_mutually_exclusive_group()
    readback.add_argument(
        "--check-order-summary",
        action="store_true",
        help="Read-only final order-summary comparison; grants no execution authority.",
    )
    readback.add_argument(
        "--reconcile-execution",
        action="store_true",
        help="Read-only retained order/fill reduction; never resubmits.",
    )
    readback.add_argument(
        "--check-risk-bound",
        action="store_true",
        help="Read-only cash/loss budget check of declared venue bounds; grants no authority.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.doctor:
        return 0
    try:
        request = json.load(sys.stdin)
        if args.check_risk_bound:
            if not isinstance(request, dict) or set(request) != {
                "terms",
                "budget",
                "exposure_bound",
                "observed_at",
                "now",
                "maximum_age_seconds",
            }:
                raise ValueError("unsupported risk-bound request")
            result = check_risk_bound(**request)
        elif args.check_order_summary:
            if not isinstance(request, dict) or set(request) != {
                "terms",
                "summary",
                "observed_at",
                "now",
                "maximum_age_seconds",
            }:
                raise ValueError("unsupported summary-check request")
            result = check_order_summary(**request)
        elif args.reconcile_execution:
            if not isinstance(request, dict) or set(request) != {
                "terms",
                "observation",
                "operation_id",
                "attempt_id",
            }:
                raise ValueError("unsupported reconciliation request")
            result = reconcile_execution(**request)
        else:
            result = execute_simulated_finance_operation(request)
    except Exception as exc:  # noqa: BLE001 - redact the process boundary
        print(
            json.dumps(
                {
                    "ok": False,
                    "schema_version": "finance_operation_error_v0",
                    "error": type(exc).__name__,
                    **(
                        {"read_only": True}
                        if args.check_order_summary
                        or args.reconcile_execution
                        or args.check_risk_bound
                        else {"simulation": True}
                    ),
                    "external_write_performed": False,
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
