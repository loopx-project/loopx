from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ...file_lock import process_is_alive


CONTEXT_PROVIDER_SERVICE_RECEIPT_SCHEMA_VERSION = (
    "context_provider_service_ownership_receipt_v0"
)
_RECEIPT_FIELDS = {
    "schema_version",
    "provider",
    "service_ref",
    "ownership_mode",
    "generation",
    "pid",
    "observed_at",
}
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")


def _opaque_ref(kind: str, value: str) -> str:
    digest = hashlib.sha256(f"{kind}\n{value}".encode("utf-8")).hexdigest()[:20]
    return f"{kind}-{digest}"


@dataclass(frozen=True)
class ContextProviderServiceOwnership:
    provider: str
    status: str
    reason_code: str
    ownership_mode: str = "unknown"
    service_ref: str = ""
    generation: str = ""
    pid: int = 0
    process_alive: bool = False

    @property
    def verified(self) -> bool:
        return self.status == "verified"

    def public_packet(
        self,
        *,
        required: bool,
        restart_detected: bool = False,
        attempt_latency_ms: int = 0,
    ) -> dict[str, object]:
        if restart_detected:
            status = "restarted"
            reason_code = "provider_service_restarted"
            progress_disposition = "restart_detected_no_resume"
        elif self.verified:
            status = "verified"
            reason_code = None
            progress_disposition = "fresh_attempt"
        else:
            status = self.status
            reason_code = self.reason_code
            progress_disposition = "not_started"
        return {
            "schema_version": "context_provider_service_ownership_v0",
            "ok": status == "verified",
            "required": required,
            "status": status,
            "reason_code": reason_code,
            "provider": self.provider,
            "ownership_mode": self.ownership_mode,
            "service_ref": (
                _opaque_ref("service", self.service_ref) if self.service_ref else None
            ),
            "generation_ref": (
                _opaque_ref("generation", self.generation)
                if self.generation
                else None
            ),
            "process_alive": self.process_alive,
            "restart_detected": restart_detected,
            "progress_disposition": progress_disposition,
            "cost_accounting": "append_attempt",
            "attempt_latency_ms": max(0, int(attempt_latency_ms)),
            "raw_receipt_captured": False,
            "process_id_captured": False,
            "local_path_captured": False,
        }


def load_context_provider_service_ownership(
    receipt_path: str | Path | None,
    *,
    expected_provider: str,
) -> ContextProviderServiceOwnership:
    if not receipt_path:
        return ContextProviderServiceOwnership(
            provider=expected_provider,
            status="missing",
            reason_code="provider_service_ownership_receipt_required",
        )
    try:
        payload = json.loads(Path(receipt_path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ContextProviderServiceOwnership(
            provider=expected_provider,
            status="unavailable",
            reason_code="provider_service_ownership_receipt_unavailable",
        )
    if not isinstance(payload, Mapping):
        return ContextProviderServiceOwnership(
            provider=expected_provider,
            status="invalid",
            reason_code="provider_service_ownership_receipt_invalid",
        )
    if payload.get("schema_version") != CONTEXT_PROVIDER_SERVICE_RECEIPT_SCHEMA_VERSION:
        return ContextProviderServiceOwnership(
            provider=expected_provider,
            status="invalid",
            reason_code="provider_service_ownership_receipt_invalid",
        )
    if set(payload) != _RECEIPT_FIELDS:
        return ContextProviderServiceOwnership(
            provider=expected_provider,
            status="invalid",
            reason_code="provider_service_ownership_receipt_invalid",
        )
    provider = str(payload.get("provider") or "")
    service_ref = str(payload.get("service_ref") or "")
    generation = str(payload.get("generation") or "")
    ownership_mode = str(payload.get("ownership_mode") or "")
    pid = payload.get("pid")
    if (
        provider != expected_provider
        or not _LABEL.fullmatch(provider)
        or not _LABEL.fullmatch(service_ref)
        or not _LABEL.fullmatch(generation)
        or ownership_mode != "persistent_external"
        or not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
    ):
        return ContextProviderServiceOwnership(
            provider=expected_provider,
            status="invalid",
            reason_code="provider_service_ownership_receipt_invalid",
            ownership_mode=ownership_mode or "unknown",
        )
    alive = process_is_alive(pid)
    return ContextProviderServiceOwnership(
        provider=provider,
        status="verified" if alive else "unavailable",
        reason_code=("" if alive else "provider_service_process_unavailable"),
        ownership_mode=ownership_mode,
        service_ref=service_ref,
        generation=generation,
        pid=pid,
        process_alive=alive,
    )


def context_provider_service_restarted(
    before: ContextProviderServiceOwnership,
    after: ContextProviderServiceOwnership,
) -> bool:
    return bool(
        before.verified
        and after.verified
        and (
            before.generation != after.generation
            or before.pid != after.pid
        )
    )
