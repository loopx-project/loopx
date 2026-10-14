#!/usr/bin/env python3
"""Smoke-test persistent context-provider service ownership receipts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loopx.capabilities.context_providers.service_ownership import (  # noqa: E402
    context_provider_service_restarted,
    load_context_provider_service_ownership,
)


def write_receipt(path: Path, *, generation: str, pid: int) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "context_provider_service_ownership_receipt_v0",
                "provider": "contract-provider",
                "service_ref": "public-index-service",
                "ownership_mode": "persistent_external",
                "generation": generation,
                "pid": pid,
                "observed_at": "2026-07-12T04:30:00Z",
            }
        ),
        encoding="utf-8",
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-provider-ownership-") as tmpdir:
        receipt = Path(tmpdir) / "service.json"

        missing = load_context_provider_service_ownership(
            receipt,
            expected_provider="contract-provider",
        )
        assert missing.verified is False
        assert missing.reason_code == "provider_service_ownership_receipt_unavailable"

        exited_provider = subprocess.Popen([sys.executable, "-c", "pass"])
        exited_provider.wait()
        write_receipt(receipt, generation="generation-1", pid=exited_provider.pid)
        exited = load_context_provider_service_ownership(
            receipt,
            expected_provider="contract-provider",
        )
        assert exited.verified is False
        assert exited.reason_code == "provider_service_process_unavailable"
        exited_public = exited.public_packet(required=True)
        assert exited_public["ok"] is False
        assert exited_public["progress_disposition"] == "not_started"

        write_receipt(receipt, generation="generation-1", pid=os.getpid())
        before = load_context_provider_service_ownership(
            receipt,
            expected_provider="contract-provider",
        )
        assert before.verified is True
        public = before.public_packet(required=True, attempt_latency_ms=37)
        assert public["status"] == "verified"
        assert public["progress_disposition"] == "fresh_attempt"
        assert public["cost_accounting"] == "append_attempt"
        assert public["attempt_latency_ms"] == 37

        encoded = json.dumps(public, sort_keys=True)
        assert str(receipt) not in encoded
        assert "pid" not in public
        assert all(value != os.getpid() for value in public.values())
        assert public["process_id_captured"] is False
        assert "public-index-service" not in encoded

        write_receipt(receipt, generation="generation-2", pid=os.getpid())
        after = load_context_provider_service_ownership(
            receipt,
            expected_provider="contract-provider",
        )
        assert context_provider_service_restarted(before, after) is True
        restarted = after.public_packet(required=True, restart_detected=True)
        assert restarted["ok"] is False
        assert restarted["status"] == "restarted"
        assert restarted["progress_disposition"] == "restart_detected_no_resume"
        assert restarted["cost_accounting"] == "append_attempt"

        malformed = json.loads(receipt.read_text(encoding="utf-8"))
        malformed.pop("observed_at")
        receipt.write_text(json.dumps(malformed), encoding="utf-8")
        invalid = load_context_provider_service_ownership(
            receipt,
            expected_provider="contract-provider",
        )
        assert invalid.verified is False
        assert invalid.reason_code == "provider_service_ownership_receipt_invalid"

    print("context-provider-service-ownership-smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
