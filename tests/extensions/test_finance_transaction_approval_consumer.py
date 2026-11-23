from __future__ import annotations

from datetime import UTC, datetime, timedelta
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.chat import action_normalization
from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.extensions.lark.goal_channel_operation import (
    build_goal_channel_operation_card,
)


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages" / "loopx-finance-value-discovery"
PACKAGE_SRC = PACKAGE / "src"
EXECUTION_SRC = ROOT / "packages" / "loopx-finance-execution" / "src"
GOAL_ID = "finance-approval-fixture"
AGENT_ID = "finance-fixture-agent"
OPERATOR_ID = "ou_finance_approver"


def _module():
    sys.path.insert(0, str(PACKAGE_SRC))
    try:
        return importlib.import_module(
            "loopx_finance_value_discovery.operation_request"
        )
    finally:
        sys.path.remove(str(PACKAGE_SRC))


def _execution_module():
    sys.path.insert(0, str(EXECUTION_SRC))
    try:
        return importlib.import_module("loopx_finance_execution.simulator")
    finally:
        sys.path.remove(str(EXECUTION_SRC))


def _input(*, now: datetime) -> dict[str, object]:
    return {
        "schema_version": "finance_transaction_approval_input_v0",
        "request_id": "synthetic-request-1",
        "candidate_ref": "candidate:synthetic-1",
        "asset": "SYNTH",
        "side": "buy",
        "quantity": "2.00",
        "quantity_unit": "SYNTH",
        "order_type": "limit",
        "limit_price": "10.00",
        "price_unit": "TEST",
        "time_in_force": "GTC",
        "reduce_only": False,
        "maximum_fee": "0.10",
        "fee_unit": "TEST",
        "expected_edge_bps": "80",
        "maximum_cost_bps": "20",
        "evidence_observed_at": (now - timedelta(minutes=5)).isoformat(),
        "evidence_refs": [
            {
                "label": "Synthetic filing",
                "ref": "https://example.com/evidence/synthetic-1",
            }
        ],
        "no_trade_conditions": ["Do not proceed if the evidence snapshot is stale."],
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "authorized_principals": [f"lark:{OPERATOR_ID}"],
        "executor_revision": "simulator-fixture-r1",
        "simulation": True,
    }


def _service(tmp_path: Path) -> ChatActionService:
    project = tmp_path / "project"
    project.mkdir()
    (project / "ACTIVE_GOAL_STATE.md").write_text(
        f"---\ngoal_id: {GOAL_ID}\n---\n\n## User Todo\n\n## Agent Todo\n",
        encoding="utf-8",
    )
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir()
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(project),
                        "state_file": "ACTIVE_GOAL_STATE.md",
                        "coordination": {"registered_agents": [AGENT_ID]},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return ChatActionService(
        store=ChatActionStore(tmp_path / "runtime" / "chat" / "actions"),
        registry_path=registry,
    )


def test_builder_feeds_one_canonical_core_dashboard_and_lark_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    now = datetime(2026, 9, 14, 1, 0, tzinfo=UTC)
    monkeypatch.setattr(action_normalization, "now_utc", lambda: now)
    packet = module.build_finance_transaction_approval_packet(_input(now=now), now=now)

    assert packet["boundary"] == {
        "simulation": True,
        "human_confirmation_required": True,
        "real_order_allowed": False,
        "signature_allowed": False,
        "transfer_allowed": False,
        "external_write_performed": False,
    }
    request = packet["operation_request"]
    service = _service(tmp_path)
    proposal = service.preview(
        {
            "action_kind": "operation.execute",
            "summary": packet["summary"],
            "idempotency_key": packet["idempotency_key"],
            "context": {"kind": "goal", "goal_id": GOAL_ID},
            "normalized_parameters": {
                **request,
                "goal_id": GOAL_ID,
                "agent_id": AGENT_ID,
            },
        }
    )

    projection = proposal["normalized_parameters"]["projection"]
    assert proposal["operation"]["lifecycle_state"] == "awaiting_confirmation"
    assert projection == request["projection"]
    assert [field["label"] for field in projection["fields"]][:6] == [
        "Candidate",
        "Action",
        "Maximum notional",
        "Economics",
        "Expires",
        "Evidence observed",
    ]
    assert any(field["label"].startswith("Evidence") for field in projection["fields"])
    assert any(field["label"].startswith("No-trade") for field in projection["fields"])
    card = build_goal_channel_operation_card(proposal)
    card_text = json.dumps(card, ensure_ascii=False)
    assert "确认模拟执行" in card_text
    assert "Synthetic filing" in card_text
    assert "Do not proceed if the evidence snapshot is stale." in card_text
    assert OPERATOR_ID not in card_text
    assert '"schema_version": "finance_order_intent_v0"' not in card_text

    outcome = _execution_module().execute_simulated_finance_operation(
        {
            "schema_version": "finance_operation_execute_request_v0",
            "protocol": request["executor"]["protocol"],
            "permission": request["executor"]["permission"],
            "operation_id": proposal["proposal_id"],
            "operation_kind": request["operation_kind"],
            "operation_schema": request["operation_schema"],
            "payload": request["payload"],
            "payload_digest": request["payload_digest"],
            "confirmation_digest": proposal["operation"]["confirmation_digest"],
            "claim_id": "claim-fixture-r1",
            "executor_revision": request["executor"]["revision"],
            "destination_account_ref": request["destination_account_ref"],
        }
    )
    assert outcome["outcome"] == "simulated_filled"
    assert outcome["simulation"] is True
    assert outcome["external_write_performed"] is False


def test_builder_rejects_real_execution_and_unprofitable_or_private_requests() -> None:
    module = _module()
    now = datetime(2026, 9, 14, 1, 0, tzinfo=UTC)

    real = _input(now=now)
    real["simulation"] = False
    with pytest.raises(ValueError, match="simulation must be true"):
        module.build_finance_transaction_approval_packet(real, now=now)

    uneconomic = _input(now=now)
    uneconomic["expected_edge_bps"] = "20"
    with pytest.raises(ValueError, match="must exceed"):
        module.build_finance_transaction_approval_packet(uneconomic, now=now)

    private = _input(now=now)
    private["evidence_refs"] = [{"label": "local", "ref": "https://localhost/private"}]
    with pytest.raises(ValueError, match="unsafe evidence reference"):
        module.build_finance_transaction_approval_packet(private, now=now)

    credential_query = _input(now=now)
    credential_query["evidence_refs"] = [
        {
            "label": "unsafe query",
            "ref": "https://example.com/evidence?access_token=secret",
        }
    ]
    with pytest.raises(ValueError, match="sensitive material"):
        module.build_finance_transaction_approval_packet(credential_query, now=now)

    future_evidence = _input(now=now)
    future_evidence["evidence_observed_at"] = (
        now + timedelta(seconds=1)
    ).isoformat()
    with pytest.raises(ValueError, match="must not be in the future"):
        module.build_finance_transaction_approval_packet(future_evidence, now=now)


def test_managed_protocol_and_direct_cli_emit_the_same_request(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    input_path = tmp_path / "transaction.json"
    input_path.write_text(json.dumps(_input(now=now)), encoding="utf-8")
    env = {"PYTHONPATH": str(PACKAGE_SRC)}

    managed = subprocess.run(
        [sys.executable, "-m", "loopx_finance_value_discovery.cli"],
        cwd=ROOT,
        env=env,
        input=input_path.read_text(encoding="utf-8"),
        text=True,
        capture_output=True,
        check=False,
    )
    direct = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx_finance_value_discovery.cli",
            "build-operation-request",
            "--input-json",
            str(input_path),
            "--request-only",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert managed.returncode == 0, managed.stderr
    assert direct.returncode == 0, direct.stderr
    managed_packet = json.loads(managed.stdout)
    direct_request = json.loads(direct.stdout)
    assert managed_packet["operation_request"] == direct_request
    assert managed_packet["operation_request_digest"] == _module()._digest(
        direct_request
    )
