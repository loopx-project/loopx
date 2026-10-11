from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from ...capabilities.periodic_report.adapters import (
    ARTIFACT_SCHEMA,
    SINK_RESULT_SCHEMA,
)
from ...capabilities.periodic_report.archive import (
    build_periodic_report_archive_bundle,
)
from ...capabilities.periodic_report.extension_envelope import (
    validate_openviking_archive_execution_envelope,
)
from ...entrypoint import pin_utf8_stdio
from .activation import (
    OPENVIKING_PERIODIC_REPORT_EXTENSION_ID,
    OPENVIKING_PERIODIC_REPORT_EXTENSION_VERSION,
    PERIODIC_REPORT_SINK_PROTOCOL,
    validate_openviking_periodic_report_profile_activation,
)


REQUEST_SCHEMA = "openviking_periodic_report_archive_request_v0"
COMMIT_SCHEMA = "openviking_periodic_report_archive_commit_v0"


class OpenVikingResourceClient(Protocol):
    def read(self, uri: str, offset: int = 0, limit: int = -1) -> str: ...

    def write(
        self,
        uri: str,
        content: str,
        mode: str = "replace",
        wait: bool = False,
        timeout: float | None = None,
    ) -> Mapping[str, Any]: ...


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _text(value: object, label: str, *, maximum: int = 1000) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > maximum:
        raise ValueError(f"{label} exceeds {maximum} characters")
    return text


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _result_id(manifest_uri: str, bundle_digest: str) -> str:
    digest = hashlib.sha256(f"{manifest_uri}\n{bundle_digest}".encode()).hexdigest()
    return f"ov_report_{digest[:24]}"


def _committed_resources(
    bundle: Mapping[str, Any],
    *,
    idempotency_key: str,
) -> tuple[list[dict[str, Any]], str]:
    resources = [dict(item) for item in bundle.get("resources", [])]
    if [item.get("resource_kind") for item in resources] != [
        "report_body",
        "manifest",
    ]:
        raise ValueError("archive bundle must contain report body and manifest")
    report = resources[0]
    manifest_resource = resources[1]
    manifest = _mapping(bundle.get("manifest"), "bundle.manifest")
    commit_core = {
        "schema_version": COMMIT_SCHEMA,
        "extension_id": OPENVIKING_PERIODIC_REPORT_EXTENSION_ID,
        "extension_version": OPENVIKING_PERIODIC_REPORT_EXTENSION_VERSION,
        "protocol": PERIODIC_REPORT_SINK_PROTOCOL,
        "report_uri": report["resource_uri"],
        "report_digest": report["content_digest"],
        "manifest_uri": manifest_resource["resource_uri"],
        "idempotency_key": idempotency_key,
    }
    bundle_digest = _digest(
        _canonical_json({"manifest": manifest, "commit": commit_core})
    )
    result_id = _result_id(str(manifest_resource["resource_uri"]), bundle_digest)
    manifest["archive_commit"] = {
        **commit_core,
        "bundle_digest": bundle_digest,
        "result_id": result_id,
        "write_order": ["report.md", "manifest.json"],
        "manifest_written_last": True,
    }
    manifest_content = _canonical_json(manifest) + "\n"
    manifest_resource["content"] = manifest_content
    manifest_resource["content_digest"] = _digest(manifest_content)
    return [report, manifest_resource], result_id


def _read_optional(client: OpenVikingResourceClient, uri: str) -> str | None:
    try:
        return client.read(uri)
    except FileNotFoundError:
        return None
    except Exception as exc:
        if exc.__class__.__name__ in {"NotFoundError", "ResourceNotFoundError"}:
            return None
        raise


def _ensure_exact(
    client: OpenVikingResourceClient,
    resource: Mapping[str, Any],
) -> bool:
    uri = str(resource["resource_uri"])
    expected = str(resource["content"])
    existing = _read_optional(client, uri)
    if existing is not None:
        if existing != expected:
            raise ValueError(f"OpenViking Resource already differs: {uri}")
        return False
    try:
        write_result = client.write(uri, expected, mode="create", wait=False)
    except Exception as exc:
        raced = _read_optional(client, uri)
        if raced == expected:
            return False
        raise exc
    if isinstance(write_result, Mapping) and write_result.get("uri") not in {
        None,
        uri,
    }:
        raise ValueError("OpenViking write returned a different Resource URI")
    if client.read(uri) != expected:
        raise ValueError(f"OpenViking exact readback failed: {uri}")
    return True


def archive_request(
    request: Mapping[str, Any],
    *,
    client: OpenVikingResourceClient,
    extension_revision: str | None = None,
) -> dict[str, Any]:
    payload = _mapping(request, "request")
    if payload.get("schema_version") != REQUEST_SCHEMA:
        raise ValueError(f"request must use {REQUEST_SCHEMA}")
    artifact = _mapping(payload.get("artifact"), "request.artifact")
    if artifact.get("schema_version") != ARTIFACT_SCHEMA:
        raise ValueError(f"request.artifact must use {ARTIFACT_SCHEMA}")
    if artifact.get("renderer_kind") != "markdown":
        raise ValueError("OpenViking v0 archive requires the Markdown artifact")
    document = _mapping(payload.get("document"), "request.document")
    context = _mapping(payload.get("context"), "request.context")
    sink_id = _text(context.get("sink_id"), "request.context.sink_id", maximum=128)
    idempotency_key = _text(
        context.get("idempotency_key"),
        "request.context.idempotency_key",
        maximum=128,
    )
    activation = validate_openviking_periodic_report_profile_activation(
        _mapping(payload.get("activation_receipt"), "request.activation_receipt"),
        sink_id=sink_id,
    )
    if payload.get("execute") is True:
        revision = str(extension_revision or "").strip()
        if not revision:
            raise ValueError("effectful extension execution requires a bound revision")
        validate_openviking_archive_execution_envelope(
            _mapping(
                payload.get("execution_envelope"),
                "request.execution_envelope",
            ),
            request=payload,
            extension_revision=revision,
        )
    document_profile = _mapping(document.get("profile"), "request.document.profile")
    active_profile = activation["activation"]["profile"]
    if any(
        document_profile.get(key) != active_profile.get(key)
        for key in ("profile_id", "profile_version")
    ):
        raise ValueError(
            "report document and activation receipt use different profiles"
        )

    bundle = build_periodic_report_archive_bundle(
        artifact=artifact,
        document=document,
        archive_root_uri=_text(
            context.get("archive_root_uri"),
            "request.context.archive_root_uri",
            maximum=500,
        ),
        delivery_receipts=list(context.get("delivery_receipts") or []),
        semantic_tags=list(context.get("semantic_tags") or []),
        memory_conclusions=list(context.get("memory_conclusions") or []),
    )
    resources, result_id = _committed_resources(
        bundle,
        idempotency_key=idempotency_key,
    )
    base = {
        "ok": True,
        "schema_version": SINK_RESULT_SCHEMA,
        "sink_id": sink_id,
        "sink_kind": "project_resource",
        "sink_role": "archive",
        "idempotency_key": idempotency_key,
        "archive_id": bundle["archive_id"],
        "result_id": result_id,
        "receipt_ref": resources[-1]["resource_uri"],
        "schedule_policy_applied": False,
        "business_evidence_judged": False,
    }
    if payload.get("execute") is not True:
        return {
            **base,
            "status": "pending",
            "retryable": False,
            "readback_verified": False,
            "desired_resource_uris": [item["resource_uri"] for item in resources],
            "external_writes_performed": False,
        }

    write_count = 0
    receipts: list[dict[str, Any]] = []
    for resource in resources:
        write_count += int(_ensure_exact(client, resource))
        observed = client.read(str(resource["resource_uri"]))
        content_digest = _digest(observed)
        verified = content_digest == resource["content_digest"]
        receipts.append(
            {
                "resource_kind": resource["resource_kind"],
                "resource_uri": resource["resource_uri"],
                "content_digest": content_digest,
                "result_id": _result_id(str(resource["resource_uri"]), content_digest),
                "readback_verified": verified,
            }
        )
    verified = all(item["readback_verified"] for item in receipts)
    status = (
        "already_present"
        if write_count == 0
        else "created"
        if write_count == len(resources)
        else "recovered"
    )
    return {
        **base,
        "status": "sent" if verified else "unknown",
        "write_status": status,
        "retryable": not verified,
        "readback_verified": verified,
        "resource_receipts": receipts,
        "memory_reference": bundle["memory_reference"],
        "external_writes_performed": bool(write_count),
    }


def _sdk_module() -> Any:
    return importlib.import_module("openviking")


def _client(args: argparse.Namespace) -> OpenVikingResourceClient:
    if args.config:
        os.environ["OPENVIKING_CONFIG_FILE"] = args.config
    sdk = _sdk_module()
    if args.url:
        api_key = os.environ.get(args.api_key_env) if args.api_key_env else None
        return sdk.SyncHTTPClient(
            url=args.url,
            api_key=api_key,
            actor_peer_id=args.actor_peer_id,
        )
    return sdk.OpenViking(path=args.path, actor_peer_id=args.actor_peer_id)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LoopX OpenViking periodic-report archive provider."
    )
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--url")
    parser.add_argument("--path")
    parser.add_argument("--config")
    parser.add_argument("--api-key-env", default="OPENVIKING_API_KEY")
    parser.add_argument("--actor-peer-id")
    return parser


def _bound_extension_revision(request: Mapping[str, Any]) -> str | None:
    if request.get("execute") is not True:
        return None
    if os.environ.get("LOOPX_EXTENSION_ID") != OPENVIKING_PERIODIC_REPORT_EXTENSION_ID:
        raise ValueError("effectful extension execution requires its bound id")
    if os.environ.get("LOOPX_EXTENSION_PROTOCOL") != PERIODIC_REPORT_SINK_PROTOCOL:
        raise ValueError("effectful extension execution requires its bound protocol")
    revision = str(os.environ.get("LOOPX_EXTENSION_REVISION") or "").strip()
    if not revision:
        raise ValueError("effectful extension execution requires its bound revision")
    return revision


def main(argv: Sequence[str] | None = None) -> int:
    pin_utf8_stdio()
    args = _parser().parse_args(argv)
    if args.doctor:
        sdk = _sdk_module()
        client_type = getattr(sdk, "SyncHTTPClient", None) or getattr(
            sdk, "OpenViking", None
        )
        if client_type is None:
            raise RuntimeError("OpenViking public SDK client is unavailable")
        return 0
    request = json.load(sys.stdin)
    extension_revision = _bound_extension_revision(request)
    client = _client(args)
    try:
        result = archive_request(
            request,
            client=client,
            extension_revision=extension_revision,
        )
        json.dump(result, sys.stdout, ensure_ascii=False)
        return 0
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    raise SystemExit(main())
