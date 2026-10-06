from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from .adapters import (
    DOCUMENT_SCHEMA,
    SINK_RESULT_SCHEMA,
    _normalize_artifact_result,
)
from .core import (
    _reject_raw_keys,
    _SINK_ROLES,
    _SINK_STATUSES,
)
from ...control_plane.content_digest import ENVELOPED_SHA256_PATTERN
from ...control_plane.digest_envelope import sha256_envelope

GENERATION_BUNDLE_SCHEMA = "periodic_report_generation_bundle_v0"
GENERATION_RECEIPT_SCHEMA = "periodic_report_generation_receipt_v0"
SINK_BINDING_SCHEMA = "periodic_report_sink_binding_v0"
EXTENSION_READINESS_SCHEMA = "periodic_report_extension_readiness_v0"
DELIVERY_INTENT_SCHEMA = "periodic_report_delivery_intent_v0"
DELIVERY_RECEIPT_SCHEMA = "periodic_report_delivery_receipt_v0"

_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_VERSION_RE = re.compile(r"^[0-9a-z][a-z0-9_.+-]{0,127}$")
_DEPENDENCY_POLICIES = {"required", "optional", "disabled"}
_PROVIDER_STATUSES = {"ready", "unavailable", "unknown"}
_READINESS_ITEM_STATUSES = {
    "disabled",
    "incompatible",
    "missing",
    "ready",
    "unavailable",
    "unknown",
    "unverified",
}
_ACCESS_SCOPE_READBACK_STATUSES = {
    "unavailable",
    "unsupported_by_app_type",
    "verified",
}
_CONTENT_READBACK_STATUSES = {"unavailable"}


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _sequence(value: object, label: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{label} must be a list")
    return list(value)


def _text(value: object, label: str, *, maximum: int = 500) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > maximum:
        raise ValueError(f"{label} exceeds {maximum} characters")
    return text


def _token(value: object, label: str) -> str:
    token = _text(value, label, maximum=128).lower()
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError(f"{label} must be a lower-snake-like public token")
    return token


def _version(value: object, label: str) -> str:
    version = _text(value, label, maximum=128).lower()
    if not _VERSION_RE.fullmatch(version):
        raise ValueError(f"{label} must be a public version token")
    return version


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _normalize_periodic_report_release_readback(
    value: object,
    label: str,
    *,
    expected_release_id: object,
) -> dict[str, Any]:
    receipt = _mapping(value, label)
    release_id = _text(receipt.get("release_id"), f"{label}.release_id")
    status = _token(receipt.get("status"), f"{label}.status")
    expected = _text(expected_release_id, f"{label}.expected_release_id")
    verified = release_id == expected and status == "finished"
    if _boolean(receipt.get("verified"), f"{label}.verified") is not verified:
        raise ValueError(f"{label}.verified does not match exact release readback")
    normalized = {
        "release_id": release_id,
        "status": status,
        "verified": verified,
    }
    if set(receipt) != set(normalized):
        raise ValueError(f"{label} contains unsupported fields")
    return normalized


def _normalize_periodic_report_access_scope_readback(
    value: object, label: str
) -> dict[str, Any]:
    receipt = _mapping(value, label)
    status = _token(receipt.get("status"), f"{label}.status")
    if status not in _ACCESS_SCOPE_READBACK_STATUSES:
        raise ValueError(f"{label}.status is invalid")
    retryable = _boolean(receipt.get("retryable"), f"{label}.retryable")
    if retryable is not (status == "unavailable"):
        raise ValueError(f"{label}.retryable does not match status")
    normalized: dict[str, Any] = {"status": status, "retryable": retryable}
    if status == "verified":
        normalized["scope"] = _text(receipt.get("scope"), f"{label}.scope")
        normalized["require_login"] = _boolean(
            receipt.get("require_login"), f"{label}.require_login"
        )
    else:
        normalized["reason"] = _token(receipt.get("reason"), f"{label}.reason")
    if set(receipt) != set(normalized):
        raise ValueError(f"{label} contains unsupported fields")
    return normalized


def _normalize_periodic_report_content_readback(
    value: object, label: str
) -> dict[str, Any]:
    receipt = _mapping(value, label)
    status = _token(receipt.get("status"), f"{label}.status")
    if status not in _CONTENT_READBACK_STATUSES:
        raise ValueError(f"{label}.status is invalid")
    digest_verified = _boolean(
        receipt.get("digest_verified"), f"{label}.digest_verified"
    )
    if digest_verified is not False:
        raise ValueError(f"{label}.digest_verified does not match status")
    normalized: dict[str, Any] = {
        "status": status,
        "digest_verified": digest_verified,
    }
    normalized["reason"] = _token(receipt.get("reason"), f"{label}.reason")
    if set(receipt) != set(normalized):
        raise ValueError(f"{label} contains unsupported fields")
    return normalized


def _canonical_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
    )


def _sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_envelope(encoded)


def _identity(value: object, *, prefix: str) -> str:
    return f"{prefix}_{_sha256(value).split(':', 1)[1][:24]}"


def _generation_receipt(raw: object) -> dict[str, Any]:
    receipt = _mapping(raw, "generation_receipt")
    _reject_raw_keys(receipt, "generation_receipt")
    if receipt.get("schema_version") != GENERATION_RECEIPT_SCHEMA:
        raise ValueError(f"generation_receipt must use {GENERATION_RECEIPT_SCHEMA}")
    if receipt.get("status") != "succeeded":
        raise ValueError("generation_receipt must be succeeded before delivery")
    generation_id = _token(receipt.get("generation_id"), "generation_id")
    document_digest = _text(
        receipt.get("document_digest"), "document_digest", maximum=80
    )
    if not ENVELOPED_SHA256_PATTERN.fullmatch(document_digest):
        raise ValueError("generation_receipt.document_digest must use sha256")
    artifacts = _sequence(receipt.get("artifact_receipts"), "artifact_receipts")
    if not artifacts:
        raise ValueError("generation_receipt.artifact_receipts must not be empty")
    normalized_artifacts: list[dict[str, str]] = []
    for index, raw_artifact in enumerate(artifacts):
        label = f"generation_receipt.artifact_receipts[{index}]"
        artifact = _mapping(raw_artifact, label)
        artifact_document_digest = _text(
            artifact.get("document_digest"), f"{label}.document_digest", maximum=80
        )
        if artifact_document_digest != document_digest:
            raise ValueError(f"{label} belongs to a different document")
        content_digest = _text(
            artifact.get("content_digest"), f"{label}.content_digest", maximum=80
        )
        if not ENVELOPED_SHA256_PATTERN.fullmatch(content_digest):
            raise ValueError(f"{label}.content_digest must use sha256")
        normalized_artifacts.append(
            {
                "artifact_id": _token(
                    artifact.get("artifact_id"), f"{label}.artifact_id"
                ),
                "renderer_id": _token(
                    artifact.get("renderer_id"), f"{label}.renderer_id"
                ),
                "renderer_kind": _token(
                    artifact.get("renderer_kind"), f"{label}.renderer_kind"
                ),
                "artifact_ref": _text(
                    artifact.get("artifact_ref"), f"{label}.artifact_ref", maximum=500
                ),
                "content_digest": content_digest,
                "document_digest": artifact_document_digest,
            }
        )
    normalized_artifacts.sort(key=lambda item: item["artifact_id"])
    artifact_ids = [item["artifact_id"] for item in normalized_artifacts]
    renderer_ids = [item["renderer_id"] for item in normalized_artifacts]
    if len(artifact_ids) != len(set(artifact_ids)):
        raise ValueError("generation_receipt contains duplicate artifact_id")
    if len(renderer_ids) != len(set(renderer_ids)):
        raise ValueError("generation_receipt contains duplicate renderer_id")
    if receipt.get("artifact_count") != len(normalized_artifacts):
        raise ValueError("generation_receipt.artifact_count is invalid")
    if receipt.get("provider_required") is not False:
        raise ValueError("generation_receipt.provider_required must be false")
    if receipt.get("external_writes_performed") is not False:
        raise ValueError("generation_receipt.external_writes_performed must be false")
    identity = {
        "document_digest": document_digest,
        "artifacts": normalized_artifacts,
    }
    if generation_id != _identity(identity, prefix="report_generation"):
        raise ValueError("generation_receipt.generation_id does not match contents")
    return {
        **receipt,
        "generation_id": generation_id,
        "artifact_receipts": normalized_artifacts,
    }


def build_periodic_report_generation_bundle(
    *,
    document: Mapping[str, Any],
    artifacts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Freeze one provider-free normalized document and rendered artifact bundle."""

    normalized_document = _canonical_copy(_mapping(document, "document"))
    _reject_raw_keys(normalized_document, "document")
    if normalized_document.get("schema_version") != DOCUMENT_SCHEMA:
        raise ValueError(f"document must use {DOCUMENT_SCHEMA}")
    if not 1 <= len(artifacts) <= 8:
        raise ValueError("artifacts must contain between 1 and 8 items")
    normalized_artifacts = [
        _normalize_artifact_result(artifact, expected_document=normalized_document)
        for artifact in artifacts
    ]
    artifact_ids = [str(item["artifact_id"]) for item in normalized_artifacts]
    renderer_ids = [str(item["renderer_id"]) for item in normalized_artifacts]
    if len(artifact_ids) != len(set(artifact_ids)):
        raise ValueError("artifacts contains duplicate artifact_id")
    if len(renderer_ids) != len(set(renderer_ids)):
        raise ValueError("artifacts contains duplicate renderer_id")
    normalized_artifacts.sort(key=lambda item: str(item["artifact_id"]))
    document_digest = _sha256(normalized_document)
    artifact_receipts = [
        {
            key: artifact[key]
            for key in (
                "artifact_id",
                "renderer_id",
                "renderer_kind",
                "artifact_ref",
                "content_digest",
                "document_digest",
            )
        }
        for artifact in normalized_artifacts
    ]
    identity = {
        "document_digest": document_digest,
        "artifacts": artifact_receipts,
    }
    receipt = {
        "schema_version": GENERATION_RECEIPT_SCHEMA,
        "generation_id": _identity(identity, prefix="report_generation"),
        "status": "succeeded",
        "document_digest": document_digest,
        "artifact_count": len(artifact_receipts),
        "artifact_receipts": artifact_receipts,
        "provider_required": False,
        "external_writes_performed": False,
    }
    return {
        "ok": True,
        "schema_version": GENERATION_BUNDLE_SCHEMA,
        "document": normalized_document,
        "artifacts": normalized_artifacts,
        "generation_receipt": receipt,
        "boundary": {
            "provider_neutral": True,
            "generation_precedes_delivery": True,
            "sink_readiness_required_for_generation": False,
            "external_writes_performed": False,
        },
    }


def normalize_periodic_report_sink_bindings(
    raw: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize profile-owned sink dependency and extension bindings."""

    bindings: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw_item in enumerate(raw):
        label = f"sink_bindings[{index}]"
        item = _mapping(raw_item, label)
        _reject_raw_keys(item, label)
        if item.get("schema_version", SINK_BINDING_SCHEMA) != SINK_BINDING_SCHEMA:
            raise ValueError(f"{label} must use {SINK_BINDING_SCHEMA}")
        sink_id = _token(item.get("sink_id"), f"{label}.sink_id")
        sink_role = _token(item.get("sink_role"), f"{label}.sink_role")
        if sink_role not in _SINK_ROLES:
            raise ValueError(f"{label}.sink_role must be archive or delivery")
        identity = (sink_role, sink_id)
        if identity in seen:
            raise ValueError(f"duplicate sink binding {sink_role}:{sink_id}")
        seen.add(identity)
        policy = _token(item.get("dependency_policy"), f"{label}.dependency_policy")
        if policy not in _DEPENDENCY_POLICIES:
            raise ValueError(
                f"{label}.dependency_policy must be required, optional, or disabled"
            )
        capability = _mapping(item.get("capability"), f"{label}.capability")
        extension = _mapping(item.get("extension"), f"{label}.extension")
        bindings.append(
            {
                "schema_version": SINK_BINDING_SCHEMA,
                "sink_id": sink_id,
                "sink_kind": _token(item.get("sink_kind"), f"{label}.sink_kind"),
                "sink_role": sink_role,
                "dependency_policy": policy,
                "capability": {
                    "capability_id": _token(
                        capability.get("capability_id"),
                        f"{label}.capability.capability_id",
                    ),
                    "capability_version": _version(
                        capability.get("capability_version"),
                        f"{label}.capability.capability_version",
                    ),
                },
                "extension": {
                    "extension_id": _token(
                        extension.get("extension_id"),
                        f"{label}.extension.extension_id",
                    ),
                    "extension_version": _version(
                        extension.get("extension_version"),
                        f"{label}.extension.extension_version",
                    ),
                    "protocol": _token(
                        extension.get("protocol"),
                        f"{label}.extension.protocol",
                    ),
                },
            }
        )
    return sorted(
        bindings, key=lambda item: (str(item["sink_role"]), str(item["sink_id"]))
    )


def _extension_receipts(raw: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    receipts: dict[str, dict[str, Any]] = {}
    for index, raw_item in enumerate(raw):
        label = f"extension_receipts[{index}]"
        item = _mapping(raw_item, label)
        _reject_raw_keys(item, label)
        extension_id = _token(item.get("extension_id"), f"{label}.extension_id")
        if extension_id in receipts:
            raise ValueError(f"duplicate extension receipt {extension_id!r}")
        status = _token(item.get("status"), f"{label}.status")
        if status not in _PROVIDER_STATUSES:
            raise ValueError(f"{label}.status is invalid")
        capabilities: list[dict[str, str]] = []
        for capability_index, raw_capability in enumerate(
            _sequence(item.get("capabilities", []), f"{label}.capabilities")
        ):
            capability = _mapping(
                raw_capability, f"{label}.capabilities[{capability_index}]"
            )
            capabilities.append(
                {
                    "capability_id": _token(
                        capability.get("capability_id"),
                        f"{label}.capabilities[{capability_index}].capability_id",
                    ),
                    "capability_version": _version(
                        capability.get("capability_version"),
                        f"{label}.capabilities[{capability_index}].capability_version",
                    ),
                }
            )
        receipts[extension_id] = {
            "extension_id": extension_id,
            "extension_version": _version(
                item.get("extension_version"), f"{label}.extension_version"
            ),
            "protocol": _token(item.get("protocol"), f"{label}.protocol"),
            "status": status,
            "readback_verified": _boolean(
                item.get("readback_verified"), f"{label}.readback_verified"
            ),
            "capabilities": sorted(
                capabilities,
                key=lambda value: (
                    value["capability_id"],
                    value["capability_version"],
                ),
            ),
        }
    return receipts


def _binding_provider_status(
    binding: Mapping[str, Any],
    provider: Mapping[str, Any] | None,
) -> str:
    policy = str(binding["dependency_policy"])
    expected_extension = _mapping(binding["extension"], "binding.extension")
    expected_capability = _mapping(binding["capability"], "binding.capability")
    if policy == "disabled":
        return "disabled"
    if provider is None:
        return "missing"
    if (
        provider["extension_id"] != expected_extension["extension_id"]
        or provider["extension_version"] != expected_extension["extension_version"]
        or provider["protocol"] != expected_extension["protocol"]
        or expected_capability not in provider["capabilities"]
    ):
        return "incompatible"
    if provider["status"] != "ready":
        return str(provider["status"])
    if provider["readback_verified"] is not True:
        return "unverified"
    return "ready"


def build_periodic_report_extension_readiness(
    *,
    generation_receipt: Mapping[str, Any],
    sink_bindings: Sequence[Mapping[str, Any]],
    extension_receipts: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Assess formal-delivery readiness without invoking a provider."""

    generation = _generation_receipt(generation_receipt)
    bindings = normalize_periodic_report_sink_bindings(sink_bindings)
    providers = _extension_receipts(extension_receipts)
    results: list[dict[str, Any]] = []
    for binding in bindings:
        policy = str(binding["dependency_policy"])
        expected_extension = binding["extension"]
        provider = providers.get(str(expected_extension["extension_id"]))
        status = _binding_provider_status(binding, provider)
        blocks_delivery = policy == "required" and status != "ready"
        results.append(
            {
                **binding,
                "status": status,
                "ready": status == "ready",
                "blocks_delivery": blocks_delivery,
                "provider_receipt": provider,
            }
        )
    enabled = [
        result for result in results if result["dependency_policy"] != "disabled"
    ]
    required = [
        result for result in enabled if result["dependency_policy"] == "required"
    ]
    optional_degraded = any(
        result["dependency_policy"] == "optional" and not result["ready"]
        for result in enabled
    )
    blocked = any(result["blocks_delivery"] for result in required)
    ready_count = sum(bool(result["ready"]) for result in enabled)
    mode = "portable" if not enabled else ("durable" if required else "enhanced")
    if not enabled:
        readiness_status = "not_required"
    elif blocked:
        readiness_status = "blocked"
    elif optional_degraded:
        readiness_status = "degraded"
    else:
        readiness_status = "ready"
    identity = {
        "generation_id": generation["generation_id"],
        "mode": mode,
        "bindings": results,
    }
    return {
        "ok": True,
        "schema_version": EXTENSION_READINESS_SCHEMA,
        "readiness_id": _identity(identity, prefix="report_readiness"),
        "generation_id": generation["generation_id"],
        "delivery_mode": mode,
        "status": readiness_status,
        "generation_usable": True,
        "formal_delivery_required": bool(required),
        "delivery_allowed": bool(ready_count and not blocked),
        "degraded": optional_degraded,
        "sink_readiness": results,
        "readback_verified": all(
            result["status"] in {"ready", "disabled"} for result in results
        ),
        "external_writes_performed": False,
    }


def _readiness_receipt(
    raw: object,
    *,
    generation: Mapping[str, Any],
) -> dict[str, Any]:
    receipt = _mapping(raw, "readiness_receipt")
    _reject_raw_keys(receipt, "readiness_receipt")
    if receipt.get("schema_version") != EXTENSION_READINESS_SCHEMA:
        raise ValueError(f"readiness_receipt must use {EXTENSION_READINESS_SCHEMA}")
    if receipt.get("generation_id") != generation["generation_id"]:
        raise ValueError("readiness_receipt belongs to a different generation")
    readiness_id = _token(receipt.get("readiness_id"), "readiness_id")
    raw_items = _sequence(
        receipt.get("sink_readiness"), "readiness_receipt.sink_readiness"
    )
    items: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    for index, raw_item in enumerate(raw_items):
        label = f"readiness_receipt.sink_readiness[{index}]"
        item = _mapping(raw_item, label)
        binding = normalize_periodic_report_sink_bindings([item])[0]
        identity = (str(binding["sink_role"]), str(binding["sink_id"]))
        if identity in identities:
            raise ValueError(f"duplicate readiness sink {identity[0]}:{identity[1]}")
        identities.add(identity)
        status = _token(item.get("status"), f"{label}.status")
        if status not in _READINESS_ITEM_STATUSES:
            raise ValueError(f"{label}.status is invalid")
        ready = status == "ready"
        blocks_delivery = binding["dependency_policy"] == "required" and not ready
        if item.get("ready") is not ready:
            raise ValueError(f"{label}.ready does not match status")
        if item.get("blocks_delivery") is not blocks_delivery:
            raise ValueError(f"{label}.blocks_delivery does not match policy")
        provider_receipt = item.get("provider_receipt")
        normalized_provider: dict[str, Any] | None = None
        if provider_receipt is not None:
            provider_map = _mapping(provider_receipt, f"{label}.provider_receipt")
            normalized_provider = next(
                iter(_extension_receipts([provider_map]).values())
            )
        expected_status = _binding_provider_status(binding, normalized_provider)
        if status != expected_status:
            raise ValueError(f"{label}.status does not match provider receipt")
        items.append(
            {
                **binding,
                "status": status,
                "ready": ready,
                "blocks_delivery": blocks_delivery,
                "provider_receipt": normalized_provider,
            }
        )
    items.sort(key=lambda item: (str(item["sink_role"]), str(item["sink_id"])))
    enabled = [item for item in items if item["dependency_policy"] != "disabled"]
    required = [item for item in enabled if item["dependency_policy"] == "required"]
    optional_degraded = any(
        item["dependency_policy"] == "optional" and not item["ready"]
        for item in enabled
    )
    blocked = any(item["blocks_delivery"] for item in required)
    ready_count = sum(bool(item["ready"]) for item in enabled)
    mode = "portable" if not enabled else ("durable" if required else "enhanced")
    if not enabled:
        status = "not_required"
    elif blocked:
        status = "blocked"
    elif optional_degraded:
        status = "degraded"
    else:
        status = "ready"
    expected = {
        "delivery_mode": mode,
        "status": status,
        "generation_usable": True,
        "formal_delivery_required": bool(required),
        "delivery_allowed": bool(ready_count and not blocked),
        "degraded": optional_degraded,
        "readback_verified": all(
            item["status"] in {"ready", "disabled"} for item in items
        ),
        "external_writes_performed": False,
    }
    for key, value in expected.items():
        if receipt.get(key) is not value and receipt.get(key) != value:
            raise ValueError(f"readiness_receipt.{key} does not match sink readiness")
    readiness_identity = {
        "generation_id": generation["generation_id"],
        "mode": mode,
        "bindings": items,
    }
    if readiness_id != _identity(readiness_identity, prefix="report_readiness"):
        raise ValueError("readiness_receipt.readiness_id does not match contents")
    return {
        **receipt,
        **expected,
        "readiness_id": readiness_id,
        "sink_readiness": items,
    }


def build_periodic_report_delivery_receipt(
    *,
    generation_receipt: Mapping[str, Any],
    readiness_receipt: Mapping[str, Any],
    sink_results: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Normalize provider results against a version-pinned readiness receipt."""

    generation = _generation_receipt(generation_receipt)
    readiness = _readiness_receipt(readiness_receipt, generation=generation)
    readiness_items = readiness["sink_readiness"]
    results_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    for index, raw_result in enumerate(sink_results):
        label = f"sink_results[{index}]"
        result = _mapping(raw_result, label)
        _reject_raw_keys(result, label)
        if result.get("schema_version") != SINK_RESULT_SCHEMA:
            raise ValueError(f"{label} must use {SINK_RESULT_SCHEMA}")
        sink_identity = (
            _token(result.get("sink_role"), f"{label}.sink_role"),
            _token(result.get("sink_id"), f"{label}.sink_id"),
        )
        if sink_identity in results_by_identity:
            raise ValueError(
                f"duplicate sink result {sink_identity[0]}:{sink_identity[1]}"
            )
        status = _token(result.get("status"), f"{label}.status")
        if status not in _SINK_STATUSES:
            raise ValueError(f"{label}.status is invalid")
        if status == "sent" and not (
            result.get("receipt_ref")
            and result.get("readback_verified") is True
            and result.get("idempotency_key")
        ):
            raise ValueError(f"{label} sent result requires exact readback")
        if result.get("sink_kind") == "miaoda_html":
            evidence_keys = (
                "access_scope_readback",
                "content_readback",
                "release_readback",
            )
            if status == "sent":
                missing_evidence = sorted(
                    key for key in evidence_keys if result.get(key) is None
                )
                if missing_evidence:
                    raise ValueError(
                        f"{label} sent Miaoda result is missing structured readback: "
                        + ", ".join(missing_evidence)
                    )
            if result.get("release_readback") is not None:
                result["release_readback"] = (
                    _normalize_periodic_report_release_readback(
                        result["release_readback"],
                        f"{label}.release_readback",
                        expected_release_id=result.get("release_id"),
                    )
                )
            if result.get("access_scope_readback") is not None:
                result["access_scope_readback"] = (
                    _normalize_periodic_report_access_scope_readback(
                        result["access_scope_readback"],
                        f"{label}.access_scope_readback",
                    )
                )
            if result.get("content_readback") is not None:
                result["content_readback"] = (
                    _normalize_periodic_report_content_readback(
                        result["content_readback"],
                        f"{label}.content_readback",
                    )
                )
            if status == "sent" and result["release_readback"]["verified"] is not True:
                raise ValueError(
                    f"{label} sent Miaoda result requires exact release readback"
                )
        results_by_identity[sink_identity] = result

    sink_receipts: list[dict[str, Any]] = []
    expected_identities: set[tuple[str, str]] = set()
    for index, raw_item in enumerate(readiness_items):
        item = _mapping(raw_item, f"sink_readiness[{index}]")
        sink_identity = (str(item.get("sink_role")), str(item.get("sink_id")))
        expected_identities.add(sink_identity)
        policy = str(item.get("dependency_policy"))
        readiness_status = str(item.get("status"))
        sink_result = results_by_identity.get(sink_identity)
        if policy == "disabled":
            status = "skipped"
            retryable = False
            reason = "dependency_disabled"
        elif readiness_status != "ready":
            status = "failed" if policy == "required" else "skipped"
            retryable = True
            reason = f"extension_{readiness_status}"
        elif sink_result is None:
            status = "pending"
            retryable = True
            reason = "sink_result_pending"
        else:
            if sink_result.get("sink_kind") != item.get("sink_kind"):
                raise ValueError("sink result kind does not match readiness binding")
            status = str(sink_result["status"])
            retryable = bool(sink_result.get("retryable", status != "sent"))
            reason = "provider_result"
        receipt = {
            "sink_id": sink_identity[1],
            "sink_kind": item.get("sink_kind"),
            "sink_role": sink_identity[0],
            "dependency_policy": policy,
            "status": status,
            "retryable": retryable,
            "reason": reason,
            "extension": item.get("extension"),
            "capability": item.get("capability"),
            "readback_verified": bool(
                sink_result is not None
                and sink_result.get("status") == "sent"
                and sink_result.get("readback_verified") is True
            ),
        }
        if sink_result is not None:
            for key in ("idempotency_key", "receipt_ref", "result_id"):
                if sink_result.get(key):
                    receipt[key] = sink_result[key]
            for key in (
                "release_readback",
                "access_scope_readback",
                "content_readback",
            ):
                if sink_result.get(key) is not None:
                    receipt[key] = sink_result[key]
        sink_receipts.append(receipt)
    unknown = sorted(set(results_by_identity) - expected_identities)
    if unknown:
        raise ValueError(
            f"sink_results contains unbound sink {unknown[0][0]}:{unknown[0][1]}"
        )

    enabled = [
        receipt
        for receipt in sink_receipts
        if receipt["dependency_policy"] != "disabled"
    ]
    if not enabled:
        status = "not_required"
    elif any(
        receipt["dependency_policy"] == "required" and receipt["status"] != "sent"
        for receipt in enabled
    ):
        if any(receipt["status"] == "pending" for receipt in enabled):
            status = "pending"
        elif any(receipt["status"] == "unknown" for receipt in enabled):
            status = "unknown"
        else:
            status = "failed"
    elif any(receipt["status"] == "pending" for receipt in enabled):
        status = "pending"
    elif any(receipt["status"] == "unknown" for receipt in enabled):
        status = "unknown"
    elif any(receipt["status"] != "sent" for receipt in enabled):
        status = "partial"
    else:
        status = "succeeded"
    delivery_identity = {
        "generation_id": generation["generation_id"],
        "readiness_id": readiness.get("readiness_id"),
        "sinks": sink_receipts,
    }
    return {
        "ok": status in {"not_required", "succeeded", "partial"},
        "schema_version": DELIVERY_RECEIPT_SCHEMA,
        "delivery_id": _identity(delivery_identity, prefix="report_delivery"),
        "generation_id": generation["generation_id"],
        "readiness_id": readiness.get("readiness_id"),
        "delivery_mode": readiness.get("delivery_mode"),
        "status": status,
        "terminal": status not in {"pending", "unknown"},
        "generation_usable": True,
        "sink_receipts": sink_receipts,
        "retryable_sinks": sorted(
            receipt["sink_id"]
            for receipt in sink_receipts
            if receipt["retryable"] and receipt["status"] != "sent"
        ),
        "external_writes_performed": False,
    }
