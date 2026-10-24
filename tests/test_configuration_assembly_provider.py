from __future__ import annotations

import json
import sys
from pathlib import Path

from loopx.configuration_assembly_provider import (
    SCHEMA_VERSION,
    invoke_configuration_provider,
    plan_digest,
)


def _provider(tmp_path: Path, response: dict | str, *, sleep: bool = False) -> list[str]:
    body = "import sys,time; sys.stdin.read(); "
    if sleep:
        body += "time.sleep(1); "
    body += f"print({json.dumps(json.dumps(response) if isinstance(response, dict) else response)})"
    script = tmp_path / "provider.py"
    script.write_text(body, encoding="utf-8")
    return [sys.executable, str(script)]


def test_provider_is_default_off_and_read_only() -> None:
    result = invoke_configuration_provider(
        operation="probe", operation_id="op-1", argv=["unused"]
    )
    assert result["failure_kind"] == "disabled"
    assert result["status"] == "unknown"
    assert result["available"] is False


def test_plan_requires_bound_identity_and_digest(tmp_path: Path) -> None:
    plan = {"revision": "public-v1", "state": "incomplete"}
    response = {
        "schema_version": SCHEMA_VERSION,
        "operation": "plan",
        "operation_id": "op-1",
        "status": "incomplete",
        "plan_id": "plan-1",
        "plan_digest": plan_digest(plan),
        "plan": plan,
        "private": "must not escape",
    }
    result = invoke_configuration_provider(
        operation="plan",
        operation_id="op-1",
        argv=_provider(tmp_path, response),
        enabled=True,
    )
    assert result["status"] == "incomplete"
    assert result["plan"] == plan
    assert "private" not in result
    assert "validated" not in result


def test_rejects_schema_identity_and_digest_mismatch(tmp_path: Path) -> None:
    base = {
        "schema_version": SCHEMA_VERSION,
        "operation": "plan",
        "operation_id": "op-1",
        "status": "ready",
        "plan_id": "plan-1",
        "plan_digest": "wrong",
        "plan": {},
    }
    assert invoke_configuration_provider(
        operation="plan", operation_id="op-1", argv=_provider(tmp_path, base), enabled=True
    )["failure_kind"] == "plan_digest_mismatch"
    base["schema_version"] = "future_v1"
    assert invoke_configuration_provider(
        operation="plan", operation_id="op-1", argv=_provider(tmp_path, base), enabled=True
    )["failure_kind"] == "incompatible_schema"
    base["schema_version"] = SCHEMA_VERSION
    base["operation_id"] = "other"
    assert invoke_configuration_provider(
        operation="plan", operation_id="op-1", argv=_provider(tmp_path, base), enabled=True
    )["failure_kind"] == "identity_mismatch"


def test_provider_failures_are_public_safe(tmp_path: Path) -> None:
    malformed = invoke_configuration_provider(
        operation="probe", operation_id="op-1", argv=_provider(tmp_path, "{"), enabled=True
    )
    assert malformed["failure_kind"] == "malformed_response"
    timeout = invoke_configuration_provider(
        operation="probe",
        operation_id="op-1",
        argv=_provider(tmp_path, {}, sleep=True),
        enabled=True,
        timeout_seconds=0.01,
    )
    assert timeout["failure_kind"] == "timeout"
    missing = invoke_configuration_provider(
        operation="probe", operation_id="op-1", argv=[str(tmp_path / "missing")], enabled=True
    )
    assert missing["failure_kind"] == "missing_executable"
