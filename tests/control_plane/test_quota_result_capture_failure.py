import json
import os
from pathlib import Path

import pytest

from loopx.cli_commands.quota_capture import settlement_capture_failure_view
from loopx.control_plane.quota.error_codes import QuotaDecisionTransportError

@pytest.mark.parametrize("replayed", [False, True])
def test_failed_capture_omits_large_durable_event_and_preserves_receipt(tmp_path, replayed):
    event = {"before": "漢字" * 2000, "after": {"spent_slots": 1}}
    artifact = tmp_path / "spend.json"
    artifact.write_text(json.dumps({"quota_event": event}), encoding="utf-8")
    identity = {"turn_instance_id": "original-turn"}
    receipts = {"ok": True, "receipts": [{"step_kind": "quota_spend"}]}
    payload = {"ok": True, "appended": not replayed, "idempotent_replay": replayed, "quota_event": event,
        "before": event["before"], "after": event["after"], "json_path": str(artifact),
        "settlement_identity": identity, "settlement_result": receipts, "future_control": {"keep": True}}
    error = QuotaDecisionTransportError("Private capture directory failed validation")
    view = settlement_capture_failure_view(payload, output_bytes=8192, error=error)
    assert len(json.dumps(view, ensure_ascii=False, separators=(",", ":")).encode()) + len(os.linesep) <= 8192
    assert view["settlement_result"] == receipts and view["settlement_identity"] == identity
    assert view["idempotent_replay"] is replayed and view["appended"] is not replayed
    assert view["future_control"] == payload["future_control"]
    assert "quota_event" not in view
    assert view["result_delivery_error"]["omitted_diagnostics"] == ["before", "after", "quota_event"]
    assert json.loads(Path(view["json_path"]).read_text(encoding="utf-8"))["quota_event"] == payload["quota_event"]
