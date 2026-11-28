from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from .extensions.process_runtime import run_capped_process

SCHEMA_VERSION = "loopx_configuration_assembly_provider_v0"
MAX_RESPONSE_BYTES = 64 * 1024
ALLOWED_OPERATIONS = {"probe", "plan"}
ALLOWED_STATUSES = {"ready", "unknown", "incomplete"}


def _failure(operation: str, operation_id: str, kind: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "operation": operation,
        "operation_id": operation_id,
        "status": "unknown",
        "available": False,
        "failure_kind": kind,
    }


def plan_digest(plan: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(plan), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def invoke_configuration_provider(
    *,
    operation: str,
    operation_id: str,
    argv: Sequence[str] | None,
    enabled: bool = False,
    timeout_seconds: float = 5,
    request: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Invoke the default-off read-only provider without granting lifecycle authority."""
    if operation not in ALLOWED_OPERATIONS:
        raise ValueError("configuration provider operation must be probe or plan")
    if not enabled:
        return _failure(operation, operation_id, "disabled")
    if not argv:
        return _failure(operation, operation_id, "missing_executable")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "operation": operation,
        "operation_id": operation_id,
        "request": dict(request or {}),
    }
    try:
        result = run_capped_process(
            argv,
            stdin=json.dumps(payload, separators=(",", ":")).encode(),
            timeout_seconds=timeout_seconds,
            output_limit_bytes=MAX_RESPONSE_BYTES,
        )
    except OSError:
        return _failure(operation, operation_id, "missing_executable")
    if result.failure_kind:
        return _failure(operation, operation_id, result.failure_kind)
    if result.returncode != 0:
        return _failure(operation, operation_id, "provider_failed")
    try:
        response = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _failure(operation, operation_id, "malformed_response")
    if not isinstance(response, dict):
        return _failure(operation, operation_id, "malformed_response")
    if response.get("schema_version") != SCHEMA_VERSION:
        return _failure(operation, operation_id, "incompatible_schema")
    if response.get("operation") != operation or response.get("operation_id") != operation_id:
        return _failure(operation, operation_id, "identity_mismatch")
    status = response.get("status")
    if status not in ALLOWED_STATUSES:
        return _failure(operation, operation_id, "invalid_status")
    public = {
        "schema_version": SCHEMA_VERSION,
        "operation": operation,
        "operation_id": operation_id,
        "status": status,
        "available": True,
    }
    if operation == "plan":
        plan = response.get("plan")
        if not isinstance(plan, dict) or not isinstance(response.get("plan_id"), str):
            return _failure(operation, operation_id, "invalid_plan")
        if response.get("plan_digest") != plan_digest(plan):
            return _failure(operation, operation_id, "plan_digest_mismatch")
        public.update(
            plan_id=response["plan_id"],
            plan_digest=response["plan_digest"],
            plan=plan,
        )
    return public


def main() -> int:
    parser = argparse.ArgumentParser(description="Invoke a read-only configuration provider")
    parser.add_argument("operation", choices=sorted(ALLOWED_OPERATIONS))
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--provider", nargs="+", required=True)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--timeout", type=float, default=5)
    args = parser.parse_args()
    result = invoke_configuration_provider(
        operation=args.operation,
        operation_id=args.operation_id,
        argv=args.provider,
        enabled=args.enable,
        timeout_seconds=args.timeout,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["available"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
