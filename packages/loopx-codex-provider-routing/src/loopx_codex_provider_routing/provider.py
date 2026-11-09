"""Installed native provider lifecycle, separate from public-safe qualification."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from .native import NativeAccounts, policy
from .native_host import HostError

SCHEMA = "loopx_native_account_provider_v1"


def doctor(owner):
    from .relay_lifecycle import consumer_statuses, same_instance
    # Exercises the shipped TS bridge with a synthetic disabled policy; no
    # credential discovery, account switching or upstream inference is invoked.
    policy({"operation": "api-role", "consumer": "worker", "policy": {}})
    consumers = consumer_statuses(owner)
    worker, steward = consumers["worker"], consumers["steward"]
    worker_runtime, steward_runtime = worker.get("runtime"), steward.get("runtime")
    same = same_instance(worker_runtime, steward_runtime) if worker_runtime and steward_runtime else None
    ready = worker["state"] in ("not_started", "ready") and (
        steward["state"] == "not_configured" or (steward["ready"] and same is True))
    return {"ok": ready, "schema_version": SCHEMA, "provider_id": "loopx-native-account-provider",
            "installation_ready": ready, "relay": worker, "consumer_readiness": consumers,
            "same_relay_instance": same, "message_delivery_verified": False}


def main():
    parser = argparse.ArgumentParser(prog="loopx-native-account-provider")
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--runtime-root", type=Path, default=Path.home() / ".loopx")
    args = parser.parse_args()
    try:
        owner = NativeAccounts(args.runtime_root)
        if args.doctor:
            result = doctor(owner)
        else:
            request = json.load(sys.stdin)
            if (not isinstance(request, dict) or request.get("schema_version") != SCHEMA
                    or set(request) - {"schema_version", "operation", "execute", "legacy_pid"}):
                raise HostError("invalid_request")
            operation = request.get("operation")
            if operation == "reconcile" and request.get("execute") is True:
                from .openai_relay import route
                from .relay_lifecycle import reconcile
                with owner._lock(name="api-relay-state.lock", wait_seconds=5):
                    value = route(owner)
                    consumer = "steward" if "steward_token" in value else "worker"
                    reconcile(owner, value, consumer=consumer, legacy_pid=request.get("legacy_pid"))
            elif operation not in ("status", "reconcile"):
                raise HostError("invalid_request")
            result = doctor(owner)
            result["effect_performed"] = operation == "reconcile" and request.get("execute") is True
        print(json.dumps(result))
        return 0 if result["ok"] else 1
    except (HostError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"ok": False, "schema_version": SCHEMA,
                          "code": error.code if isinstance(error, HostError) else "provider_unavailable"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
