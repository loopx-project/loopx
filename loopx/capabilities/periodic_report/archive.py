"""Compatibility archive packet for existing OpenViking-style resource sinks.

New integrations should declare a provider-neutral archive sink binding and
keep provider-specific transport in an extension. These helpers remain public
so existing profiles can migrate without changing their receipt truth.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import unquote, urlsplit

from ...control_plane.content_digest import ENVELOPED_SHA256_PATTERN
from ...control_plane.digest_envelope import sha256_envelope
from .adapters import ARTIFACT_SCHEMA, DOCUMENT_SCHEMA
from .core import _normalize_trigger_receipt, _reject_raw_keys


ARCHIVE_BUNDLE_SCHEMA = "periodic_report_archive_bundle_v0"
ARCHIVE_MANIFEST_SCHEMA = "periodic_report_archive_manifest_v0"
ARCHIVE_RECEIPT_SCHEMA = "periodic_report_archive_receipt_v0"
MEMORY_REFERENCE_SCHEMA = "periodic_report_memory_reference_v0"

_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")

ArchiveReadback = Callable[[str], Mapping[str, Any]]


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _sequence(value: object, label: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{label} must be a list")
    return list(value)


def _text(value: object, label: str, *, maximum: int = 1000) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > maximum:
        raise ValueError(f"{label} exceeds {maximum} characters")
    return text


def _content(value: object, label: str, *, maximum: int) -> str:
    content = str(value or "")
    if not content.strip():
        raise ValueError(f"{label} is required")
    if len(content) > maximum:
        raise ValueError(f"{label} exceeds {maximum} characters")
    return content


def _token(value: object, label: str) -> str:
    token = _text(value, label, maximum=128).lower()
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError(f"{label} must be a lower-snake-like public token")
    return token


def _sha256(value: object, label: str) -> str:
    digest = _text(value, label, maximum=80)
    if not ENVELOPED_SHA256_PATTERN.fullmatch(digest):
        raise ValueError(f"{label} must use sha256")
    return digest


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _content_digest(content: str) -> str:
    return sha256_envelope(content.encode("utf-8"))


def _resource_root(value: object) -> str:
    raw_root = _text(value, "archive_root_uri", maximum=500)
    _reject_raw_keys({"archive_root_uri": raw_root}, "archive_root_uri")
    if "?" in raw_root or "#" in raw_root:
        raise ValueError("archive_root_uri must stay under viking://resources/")
    root = raw_root.rstrip("/")
    parsed = urlsplit(root)
    path_segments = [segment for segment in parsed.path.split("/") if segment]
    decoded_segments = [unquote(segment) for segment in path_segments]
    if (
        parsed.scheme != "viking"
        or parsed.netloc != "resources"
        or not path_segments
        or parsed.query
        or parsed.fragment
        or any(
            decoded in {".", ".."}
            or any(character in decoded for character in "/\\?#")
            or unquote(decoded) != decoded
            for decoded in decoded_segments
        )
    ):
        raise ValueError("archive_root_uri must stay under viking://resources/")
    return root


def _source_snapshots(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for index, raw in enumerate(
        _sequence(document.get("source_snapshots"), "document.source_snapshots")
    ):
        item = _mapping(raw, f"document.source_snapshots[{index}]")
        snapshot: dict[str, Any] = {
            key: item[key]
            for key in (
                "source_id",
                "source_kind",
                "status",
                "observed_at",
                "snapshot_digest",
                "snapshot_ref",
                "item_count",
                "retryable",
            )
            if key in item
        }
        snapshot["source_id"] = _token(
            snapshot.get("source_id"), f"source_snapshots[{index}].source_id"
        )
        snapshot["source_kind"] = _token(
            snapshot.get("source_kind"), f"source_snapshots[{index}].source_kind"
        )
        snapshots.append(snapshot)
    if not snapshots:
        raise ValueError("document.source_snapshots must not be empty")
    return sorted(snapshots, key=lambda item: str(item["source_id"]))


def _delivery_receipts(value: object) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for index, raw in enumerate(_sequence(value, "delivery_receipts")):
        item = _mapping(raw, f"delivery_receipts[{index}]")
        receipt: dict[str, Any] = {
            "sink_id": _token(
                item.get("sink_id"), f"delivery_receipts[{index}].sink_id"
            ),
            "sink_kind": _token(
                item.get("sink_kind"), f"delivery_receipts[{index}].sink_kind"
            ),
            "sink_role": _token(
                item.get("sink_role"), f"delivery_receipts[{index}].sink_role"
            ),
            "status": _token(item.get("status"), f"delivery_receipts[{index}].status"),
            "readback_verified": item.get("readback_verified") is True,
        }
        for key in ("receipt_ref", "result_id", "delivered_at"):
            if item.get(key):
                receipt[key] = _text(
                    item.get(key), f"delivery_receipts[{index}].{key}", maximum=500
                )
        receipts.append(receipt)
    return sorted(receipts, key=lambda item: (item["sink_role"], item["sink_id"]))


def _semantic_tags(value: object) -> list[str]:
    tags = {
        _token(item, "semantic_tags[]") for item in _sequence(value, "semantic_tags")
    }
    tags.add("periodic_report")
    return sorted(tags)


def _memory_conclusions(value: object) -> list[str]:
    conclusions = [
        _text(item, "memory_conclusions[]", maximum=300)
        for item in _sequence(value, "memory_conclusions")
    ]
    if len(conclusions) > 8:
        raise ValueError("memory_conclusions supports at most 8 items")
    return conclusions


def build_periodic_report_archive_bundle(
    *,
    artifact: Mapping[str, Any],
    document: Mapping[str, Any],
    archive_root_uri: str,
    delivery_receipts: Sequence[Mapping[str, Any]] = (),
    semantic_tags: Sequence[str] = (),
    memory_conclusions: Sequence[str] = (),
) -> dict[str, Any]:
    """Build the immutable body/manifest pair for one report archive.

    The project Resource is the report-history source of truth.  The memory
    projection deliberately carries only distilled conclusions and the
    original report URI; it never copies the report body.
    """

    normalized_artifact = _mapping(artifact, "artifact")
    normalized_document = _mapping(document, "document")
    _reject_raw_keys(normalized_artifact, "artifact")
    _reject_raw_keys(normalized_document, "document")
    _reject_raw_keys(list(delivery_receipts), "delivery_receipts")
    _reject_raw_keys(list(semantic_tags), "semantic_tags")
    _reject_raw_keys(list(memory_conclusions), "memory_conclusions")
    if normalized_artifact.get("schema_version") != ARTIFACT_SCHEMA:
        raise ValueError(f"artifact must use {ARTIFACT_SCHEMA}")
    if normalized_document.get("schema_version") != DOCUMENT_SCHEMA:
        raise ValueError(f"document must use {DOCUMENT_SCHEMA}")
    content = _content(
        normalized_artifact.get("content"), "artifact.content", maximum=1000000
    )
    content_digest = _sha256(
        normalized_artifact.get("content_digest"), "artifact.content_digest"
    )
    if content_digest != _content_digest(content):
        raise ValueError("artifact.content_digest does not match content")
    document_digest = _sha256(
        normalized_artifact.get("document_digest"), "artifact.document_digest"
    )
    expected_document_digest = _content_digest(_canonical_json(normalized_document))
    if document_digest != expected_document_digest:
        raise ValueError("artifact.document_digest does not match document")
    profile = _mapping(normalized_document.get("profile"), "document.profile")
    period_window = _mapping(
        normalized_document.get("period_window"), "document.period_window"
    )
    trigger_receipt = _normalize_trigger_receipt(
        normalized_document.get("trigger_receipt")
    )
    identity = {
        "profile_id": _token(profile.get("profile_id"), "document.profile.profile_id"),
        "profile_version": _token(
            profile.get("profile_version"), "document.profile.profile_version"
        ),
        "period_window": period_window,
        "document_digest": document_digest,
        "content_digest": content_digest,
    }
    if trigger_receipt is not None and any(
        trigger_receipt["profile"].get(key) != identity[key]
        for key in ("profile_id", "profile_version")
    ):
        raise ValueError("trigger_receipt.profile must match the document profile")
    archive_id = (
        f"report_{hashlib.sha256(_canonical_json(identity).encode()).hexdigest()[:24]}"
    )
    root_uri = (
        f"{_resource_root(archive_root_uri)}/{identity['profile_id']}/{archive_id}"
    )
    report_uri = f"{root_uri}/report.md"
    manifest_uri = f"{root_uri}/manifest.json"
    tags = _semantic_tags(list(semantic_tags))
    conclusions = _memory_conclusions(list(memory_conclusions))
    snapshots = _source_snapshots(normalized_document)
    receipts = _delivery_receipts(list(delivery_receipts))
    manifest = {
        "schema_version": ARCHIVE_MANIFEST_SCHEMA,
        "manifest_version": "v0",
        "archive_id": archive_id,
        "title": _text(normalized_document.get("title"), "document.title", maximum=200),
        "generated_at": _text(
            normalized_document.get("generated_at"), "document.generated_at", maximum=80
        ),
        "period_window": period_window,
        "profile": {
            "profile_id": identity["profile_id"],
            "profile_version": identity["profile_version"],
        },
        "artifact_receipt": {
            "artifact_id": _token(
                normalized_artifact.get("artifact_id"), "artifact.artifact_id"
            ),
            "renderer_id": _token(
                normalized_artifact.get("renderer_id"), "artifact.renderer_id"
            ),
            "renderer_kind": _token(
                normalized_artifact.get("renderer_kind"), "artifact.renderer_kind"
            ),
            "artifact_ref": _text(
                normalized_artifact.get("artifact_ref"),
                "artifact.artifact_ref",
                maximum=500,
            ),
            "content_digest": content_digest,
            "document_digest": document_digest,
        },
        "source_snapshots": snapshots,
        "delivery_receipts": receipts,
        "semantic_tags": tags,
        "resources": {
            "report_uri": report_uri,
            "manifest_uri": manifest_uri,
        },
        "memory_policy": {
            "distillation_required": True,
            "full_report_copied": False,
            "report_uri_required": True,
        },
    }
    if trigger_receipt:
        manifest["trigger_receipt"] = trigger_receipt
    manifest_content = _canonical_json(manifest) + "\n"
    memory_reference = {
        "schema_version": MEMORY_REFERENCE_SCHEMA,
        "archive_id": archive_id,
        "report_uri": report_uri,
        "manifest_uri": manifest_uri,
        "semantic_tags": tags,
        "conclusions": conclusions,
        "distillation_required": True,
        "full_report_copied": False,
    }
    return {
        "schema_version": ARCHIVE_BUNDLE_SCHEMA,
        "archive_id": archive_id,
        "root_uri": root_uri,
        "resources": [
            {
                "resource_kind": "report_body",
                "resource_uri": report_uri,
                "media_type": "text/markdown",
                "content": content,
                "content_digest": content_digest,
            },
            {
                "resource_kind": "manifest",
                "resource_uri": manifest_uri,
                "media_type": "application/json",
                "content": manifest_content,
                "content_digest": _content_digest(manifest_content),
            },
        ],
        "manifest": manifest,
        "memory_reference": memory_reference,
        "boundary": {
            "project_resource_is_history_source_of_truth": True,
            "memory_contains_full_report": False,
            "schedule_policy_applied": False,
            "business_evidence_judged": False,
            "external_writes_performed": False,
        },
    }


def verify_periodic_report_archive_receipts(
    *,
    bundle: Mapping[str, Any],
    written: Mapping[str, Any],
    readback: ArchiveReadback,
) -> dict[str, Any]:
    """Require exact URI, result-id, and digest readback for every resource."""

    expected_resources = {
        str(item["resource_kind"]): dict(item)
        for item in _sequence(bundle.get("resources"), "bundle.resources")
        if isinstance(item, Mapping)
    }
    written_rows = _sequence(written.get("resources"), "written.resources")
    if len(written_rows) != len(expected_resources):
        raise ValueError("archive write must return one receipt per resource")
    receipts: list[dict[str, Any]] = []
    seen_kinds: set[str] = set()
    seen_result_ids: set[str] = set()
    all_verified = True
    for index, raw in enumerate(written_rows):
        row = _mapping(raw, f"written.resources[{index}]")
        kind = _token(
            row.get("resource_kind"), f"written.resources[{index}].resource_kind"
        )
        if kind in seen_kinds or kind not in expected_resources:
            raise ValueError(
                "archive write returned duplicate or unknown resource_kind"
            )
        seen_kinds.add(kind)
        expected = expected_resources[kind]
        resource_uri = _text(
            row.get("resource_uri"),
            f"written.resources[{index}].resource_uri",
            maximum=500,
        )
        result_id = _text(
            row.get("result_id"), f"written.resources[{index}].result_id", maximum=500
        )
        if resource_uri != expected["resource_uri"]:
            raise ValueError("archive write returned a different resource_uri")
        if result_id in seen_result_ids:
            raise ValueError("archive write returned duplicate result_id values")
        seen_result_ids.add(result_id)
        observed = _mapping(readback(resource_uri), f"readback[{kind}]")
        verified = (
            observed.get("verified") is True
            and str(observed.get("resource_uri") or "").strip() == resource_uri
            and str(observed.get("result_id") or "").strip() == result_id
            and str(observed.get("content_digest") or "").strip()
            == expected["content_digest"]
        )
        all_verified = all_verified and verified
        receipts.append(
            {
                "schema_version": ARCHIVE_RECEIPT_SCHEMA,
                "resource_kind": kind,
                "resource_uri": resource_uri,
                "result_id": result_id,
                "content_digest": expected["content_digest"],
                "readback_verified": verified,
            }
        )
    return {
        "verified": all_verified and seen_kinds == set(expected_resources),
        "resource_receipts": sorted(
            receipts, key=lambda item: str(item["resource_kind"])
        ),
    }
