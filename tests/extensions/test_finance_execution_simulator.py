from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tomllib

import pytest

from loopx.capabilities.catalog import build_capability_detail_packet
from loopx.extensions.manifest import load_extension_manifest


ROOT = Path(__file__).resolve().parents[2]
EXTENSION_ROOT = ROOT / "packages" / "loopx-finance-execution"
sys.path.insert(0, str(EXTENSION_ROOT / "src"))

from loopx_finance_execution.simulator import (  # noqa: E402
    execute_simulated_finance_operation,
)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _request() -> dict[str, object]:
    payload = {
        "schema_version": "finance_order_intent_v0",
        "asset": "SYNTH",
        "side": "buy",
        "quantity": "1.25",
        "quantity_unit": "SYNTH",
        "order_type": "limit",
        "limit_price": "8.00",
        "price_unit": "TEST",
        "time_in_force": "GTC",
        "reduce_only": False,
        "maximum_fee": "0.10",
        "fee_unit": "TEST",
    }
    return {
        "schema_version": "finance_operation_execute_request_v0",
        "protocol": "finance_operation_executor_v0",
        "permission": "finance.operation.simulate",
        "operation_id": "proposal-fixture",
        "operation_kind": "finance.order.simulate",
        "operation_schema": "finance_order_intent_v0",
        "payload": payload,
        "payload_digest": _digest(payload),
        "confirmation_digest": "a" * 64,
        "claim_id": "claim-fixture",
        "executor_revision": "simulator-v0",
        "destination_account_ref": "account:simulation",
    }


def test_simulator_returns_explicit_non_effectful_fill() -> None:
    result = execute_simulated_finance_operation(_request())

    assert result["outcome"] == "simulated_filled"
    assert result["simulation"] is True
    assert result["external_write_performed"] is False
    assert result["details"]["notional"] == "10.0000"
    assert result["payload_digest"] == _request()["payload_digest"]


def test_simulator_rejects_digest_drift_and_non_simulation_account() -> None:
    drifted = _request()
    drifted["payload"]["quantity"] = "2.00"
    with pytest.raises(ValueError, match="payload_digest"):
        execute_simulated_finance_operation(drifted)

    real_account = _request()
    real_account["destination_account_ref"] = "account:venue"
    with pytest.raises(ValueError, match="account:simulation"):
        execute_simulated_finance_operation(real_account)


def test_manifest_requires_only_the_simulation_permission() -> None:
    manifest = tomllib.loads(
        (EXTENSION_ROOT / "extension.toml").read_text(encoding="utf-8")
    )
    pyproject = tomllib.loads(
        (EXTENSION_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert manifest["permissions"] == ["finance.operation.simulate"]
    assert manifest["runtime"]["required_permissions"] == ["finance.operation.simulate"]
    assert manifest["runtime"]["protocol"] == "finance_operation_executor_v0"
    normalized = load_extension_manifest(EXTENSION_ROOT / "extension.toml")
    assert normalized["provider"]["version"] == pyproject["project"]["version"]
    assert normalized["implementations"] == [
        {
            "capability_id": "human-confirmed-operation-executor",
            "protocol": "finance_operation_executor_v0",
            "provider_id": "loopx-finance-execution",
            "provider_version": "0.1.1",
        }
    ]


def test_manifest_registers_the_capability_it_implements() -> None:
    manifest_path = EXTENSION_ROOT / "extension.toml"

    capability = build_capability_detail_packet(
        "human-confirmed-operation-executor",
        [manifest_path],
    )["capability"]

    assert capability["provider_id"] == "loopx-finance-execution"
    assert capability["entry_command"] == (
        "loopx goal-channel prepare-operation --help"
    )
    assert capability["provider_state"] == {
        "declared": True,
        "installed": False,
        "enabled": False,
        "ready": False,
    }
    assert capability["implementation_providers"] == [
        {
            "capability_id": "human-confirmed-operation-executor",
            "protocol": "finance_operation_executor_v0",
            "provider_id": "loopx-finance-execution",
            "provider_version": "0.1.1",
            "provider_state": {
                "declared": True,
                "installed": False,
                "enabled": False,
                "ready": False,
            },
        }
    ]
