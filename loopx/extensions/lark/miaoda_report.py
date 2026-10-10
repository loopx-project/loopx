from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ...capabilities.periodic_report.adapters import PeriodicReportAdapterRegistry
from ...capabilities.periodic_report.bindings import (
    DELIVERY_INTENT_SCHEMA,
    GENERATION_BUNDLE_SCHEMA,
    build_periodic_report_delivery_receipt,
    build_periodic_report_extension_readiness,
    build_periodic_report_generation_bundle,
)
from ...capabilities.periodic_report.core import _reject_raw_keys
from ...capabilities.periodic_report.profile import build_periodic_report_activation
from . import LARK_EXTENSION_ID, LARK_MIAODA_HTML_PERMISSION
from .presentation.periodic_report import periodic_report_miaoda_html_sink_adapter

MIAODA_DELIVERY_REQUEST_SCHEMA = "periodic_report_miaoda_delivery_request_v0"
MIAODA_DELIVERY_RESULT_SCHEMA = "periodic_report_miaoda_delivery_result_v0"
MIAODA_CAPABILITY_ID = "report.miaoda_html.publish"
MIAODA_PROTOCOL = "periodic_report_sink_v0"

CommandRunner = Callable[[Sequence[str], Path | None, float | None], Mapping[str, Any]]


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _text(value: object, label: str, *, maximum: int = 500) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > maximum:
        raise ValueError(f"{label} exceeds {maximum} characters")
    return text


def _reject_unknown_fields(
    value: Mapping[str, Any],
    *,
    allowed: set[str],
    label: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{label} contains unsupported fields: {', '.join(unknown)}")


def _json_payload(result: Mapping[str, Any], label: str) -> tuple[int, dict[str, Any]]:
    try:
        returncode = int(result["returncode"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} returned an invalid process receipt") from exc
    raw_payload = str(result.get("stdout") or result.get("stderr") or "")
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} returned invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise TypeError(f"{label} returned invalid JSON")
    return returncode, {str(key): value for key, value in payload.items()}


def _json_response(result: Mapping[str, Any], label: str) -> dict[str, Any]:
    returncode, payload = _json_payload(result, label)
    if returncode != 0 or payload.get("ok") is not True:
        raise ValueError(f"{label} did not return an ok receipt")
    return payload


def _api_error_code(payload: Mapping[str, Any]) -> int | None:
    error = payload.get("error")
    candidates = [payload.get("code")]
    if isinstance(error, Mapping):
        candidates.append(error.get("code"))
    for value in candidates:
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _unavailable_access_scope() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "retryable": True,
        "reason": "provider_query_failed",
    }


def _default_runner(
    args: Sequence[str],
    cwd: Path | None = None,
    timeout: float | None = None,
) -> Mapping[str, Any]:
    completed = subprocess.run(
        list(args),
        cwd=str(cwd) if cwd else None,
        timeout=timeout,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        check=False,
    )
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _cli_prefix(cli_bin: str, profile: str | None) -> list[str]:
    prefix = [cli_bin]
    if profile:
        prefix.extend(["--profile", profile])
    return prefix


class LarkCliMiaodaProvider:
    """Perform bounded Miaoda HTML publication through authenticated lark-cli."""

    def __init__(
        self,
        *,
        cli_bin: str,
        profile: str | None = None,
        runner: CommandRunner = _default_runner,
    ) -> None:
        self._prefix = _cli_prefix(_text(cli_bin, "cli_bin"), profile)
        self._runner = runner

    def _call(
        self,
        args: Sequence[str],
        *,
        label: str,
        cwd: Path | None = None,
    ) -> dict[str, Any]:
        return _json_response(
            self._runner([*self._prefix, *args], cwd, 120.0),
            label,
        )

    @staticmethod
    def _data(payload: Mapping[str, Any]) -> dict[str, Any]:
        return _mapping(payload.get("data"), "lark-cli response.data")

    def _list_app(self, app_id: str) -> dict[str, Any]:
        page_token: str | None = None
        for _page in range(20):
            args = [
                "apps",
                "+list",
                "--ownership",
                "all",
                "--page-size",
                "100",
                "--format",
                "json",
            ]
            if page_token:
                args.extend(["--page-token", page_token])
            payload = self._call(args, label="Miaoda app list readback")
            data = self._data(payload)
            items = data.get("items")
            if not isinstance(items, list):
                raise TypeError("Miaoda app list readback is missing items")
            for item in items:
                if isinstance(item, Mapping) and item.get("app_id") == app_id:
                    return {str(key): value for key, value in item.items()}
            if data.get("has_more") is not True:
                break
            page_token = _text(data.get("page_token"), "Miaoda page_token")
        raise ValueError("Miaoda app readback could not find the requested app")

    def _read_access_scope(
        self,
        app_id: str,
        *,
        app_type: str,
    ) -> dict[str, Any]:
        label = "Miaoda access-scope readback"
        try:
            returncode, payload = _json_payload(
                self._runner(
                    [
                        *self._prefix,
                        "apps",
                        "+access-scope-get",
                        "--app-id",
                        app_id,
                        "--format",
                        "json",
                    ],
                    None,
                    120.0,
                ),
                label,
            )
        except (OSError, subprocess.TimeoutExpired, TypeError, ValueError):
            return _unavailable_access_scope()
        if returncode == 0 and payload.get("ok") is True:
            try:
                data = self._data(payload)
                access_scope = _text(
                    data.get("access_scope") or data.get("scope"),
                    "Miaoda access_scope",
                )
                if not isinstance(data.get("require_login"), bool):
                    raise TypeError("Miaoda require_login must be a boolean")
            except (TypeError, ValueError):
                return _unavailable_access_scope()
            return {
                "status": "verified",
                "retryable": False,
                "scope": access_scope,
                "require_login": data["require_login"],
            }
        if _api_error_code(payload) == 40002 and app_type == "html":
            return {
                "status": "unsupported_by_app_type",
                "retryable": False,
                "reason": "creative_html_scope_readback_unavailable",
            }
        return _unavailable_access_scope()

    def publish(
        self,
        artifact: Mapping[str, Any],
        app_id: str,
        _idempotency_key: str,
    ) -> dict[str, Any]:
        content = _text(artifact.get("content"), "artifact.content", maximum=10_485_760)
        with tempfile.TemporaryDirectory(prefix="loopx-miaoda-report-") as raw:
            root = Path(raw)
            (root / "index.html").write_text(content, encoding="utf-8")
            payload = self._call(
                [
                    "apps",
                    "+html-publish",
                    "--app-id",
                    app_id,
                    "--path",
                    "./index.html",
                    "--format",
                    "json",
                ],
                label="Miaoda HTML publish",
                cwd=root,
            )
        data = self._data(payload)
        release_id = _text(data.get("release_id"), "Miaoda publish release_id")
        online_url = str(data.get("online_url") or data.get("url") or "").strip()
        if not online_url:
            online_url = _text(
                self._list_app(app_id).get("online_url"),
                "Miaoda publish online_url",
            )
        return {
            "app_id": app_id,
            "online_url": online_url,
            "release_id": release_id,
        }

    def readback(self, app_id: str, release_id: str) -> dict[str, Any]:
        item = self._list_app(app_id)
        release = self._data(
            self._call(
                [
                    "apps",
                    "+release-get",
                    "--app-id",
                    app_id,
                    "--release-id",
                    release_id,
                    "--format",
                    "json",
                ],
                label="Miaoda release readback",
            )
        )
        observed_release_id = _text(
            release.get("release_id"), "Miaoda readback release_id"
        )
        release_status = _text(release.get("status"), "Miaoda readback release status")
        app_type = _text(item.get("app_type"), "Miaoda readback app_type")
        return {
            "app_id": _text(item.get("app_id"), "Miaoda readback app_id"),
            "online_url": _text(item.get("online_url"), "Miaoda readback online_url"),
            "is_published": item.get("is_published") is True,
            "release_readback": {
                "release_id": observed_release_id,
                "status": release_status,
                "verified": (
                    observed_release_id == release_id and release_status == "finished"
                ),
            },
            "access_scope_readback": self._read_access_scope(
                app_id,
                app_type=app_type,
            ),
        }


def _normalized_generation_bundle(raw: object) -> dict[str, Any]:
    supplied = _mapping(raw, "generation_bundle")
    if supplied.get("schema_version") != GENERATION_BUNDLE_SCHEMA:
        raise ValueError(f"generation_bundle must use {GENERATION_BUNDLE_SCHEMA}")
    normalized = build_periodic_report_generation_bundle(
        document=_mapping(supplied.get("document"), "generation_bundle.document"),
        artifacts=[
            _mapping(item, "generation_bundle.artifacts[]")
            for item in supplied.get("artifacts") or []
        ],
    )
    if supplied != normalized:
        raise ValueError("generation_bundle does not match its normalized receipts")
    return normalized


def _selected_binding(
    profile: Mapping[str, Any],
    *,
    sink_id: str,
) -> dict[str, Any]:
    matches = [
        item
        for item in profile.get("sink_bindings") or []
        if isinstance(item, Mapping) and item.get("sink_id") == sink_id
    ]
    if len(matches) != 1:
        raise ValueError("delivery intent must select exactly one profile sink")
    binding = {str(key): value for key, value in matches[0].items()}
    if (
        binding.get("sink_kind") != "miaoda_html"
        or binding.get("sink_role") != "delivery"
        or binding.get("dependency_policy") == "disabled"
    ):
        raise ValueError(
            "delivery intent requires an enabled miaoda_html delivery sink"
        )
    capability = _mapping(binding.get("capability"), "sink.capability")
    extension = _mapping(binding.get("extension"), "sink.extension")
    if (
        capability.get("capability_id") != MIAODA_CAPABILITY_ID
        or capability.get("capability_version") != "v0"
    ):
        raise ValueError("Miaoda sink binding uses the wrong capability")
    if (
        extension.get("extension_id") != LARK_EXTENSION_ID
        or extension.get("protocol") != MIAODA_PROTOCOL
    ):
        raise ValueError("Miaoda sink binding uses the wrong extension protocol")
    return binding


def deliver_periodic_report_to_miaoda(
    request: Mapping[str, Any],
    *,
    extension_activation: Mapping[str, Any],
    execute: bool = False,
    cli_bin: str = "lark-cli",
    lark_profile: str | None = None,
    runner: CommandRunner = _default_runner,
) -> dict[str, Any]:
    """Execute one typed hosted-delivery intent; local generation is not success."""

    payload = _mapping(request, "request")
    _reject_raw_keys(payload, "request")
    _reject_unknown_fields(
        payload,
        allowed={
            "schema_version",
            "profile",
            "generation_bundle",
            "delivery_intent",
        },
        label="request",
    )
    if payload.get("schema_version") != MIAODA_DELIVERY_REQUEST_SCHEMA:
        raise ValueError(f"request must use {MIAODA_DELIVERY_REQUEST_SCHEMA}")
    activation = build_periodic_report_activation(
        _mapping(payload.get("profile"), "request.profile")
    )
    if activation.get("active") is not True:
        raise ValueError("Miaoda delivery requires an enabled periodic report profile")
    generation = _normalized_generation_bundle(payload.get("generation_bundle"))
    if generation["document"].get("profile") != {
        "profile_id": activation["profile"].get("profile_id"),
        "profile_version": activation["profile"].get("profile_version"),
    }:
        raise ValueError("generation bundle belongs to a different report profile")

    intent = _mapping(payload.get("delivery_intent"), "request.delivery_intent")
    _reject_unknown_fields(
        intent,
        allowed={
            "schema_version",
            "kind",
            "sink_id",
            "sink_kind",
            "app_id",
            "idempotency_key",
            "artifact_id",
        },
        label="delivery_intent",
    )
    if intent.get("schema_version") != DELIVERY_INTENT_SCHEMA:
        raise ValueError(f"delivery_intent must use {DELIVERY_INTENT_SCHEMA}")
    if intent.get("kind") != "hosted" or intent.get("sink_kind") != "miaoda_html":
        raise ValueError("delivery_intent must be hosted miaoda_html")
    sink_id = _text(intent.get("sink_id"), "delivery_intent.sink_id", maximum=128)
    app_id = _text(intent.get("app_id"), "delivery_intent.app_id", maximum=128)
    idempotency_key = _text(
        intent.get("idempotency_key"),
        "delivery_intent.idempotency_key",
        maximum=128,
    )
    binding = _selected_binding(activation["profile"], sink_id=sink_id)

    extension = _mapping(binding.get("extension"), "sink.extension")
    observed_activation = _mapping(extension_activation, "extension_activation")
    if (
        observed_activation.get("schema_version") != "loopx_extension_activation_v0"
        or observed_activation.get("extension_id") != LARK_EXTENSION_ID
        or observed_activation.get("provider_version")
        != extension.get("extension_version")
        or observed_activation.get("enabled") is not True
        or observed_activation.get("doctor_verified") is not True
        or not isinstance(observed_activation.get("required_permissions"), list)
        or LARK_MIAODA_HTML_PERMISSION
        not in observed_activation["required_permissions"]
    ):
        raise ValueError("active Lark extension does not match the Miaoda sink binding")

    artifacts = [
        artifact
        for artifact in generation["artifacts"]
        if artifact.get("renderer_kind") == "html"
    ]
    artifact_id = str(intent.get("artifact_id") or "").strip()
    if artifact_id:
        artifacts = [
            artifact
            for artifact in artifacts
            if artifact.get("artifact_id") == artifact_id
        ]
    if len(artifacts) != 1:
        raise ValueError("delivery intent must resolve exactly one HTML artifact")

    provider = LarkCliMiaodaProvider(
        cli_bin=cli_bin,
        profile=lark_profile,
        runner=runner,
    )
    registry = PeriodicReportAdapterRegistry()
    registry.register_sink(
        periodic_report_miaoda_html_sink_adapter(
            publish=provider.publish,
            readback=provider.readback,
            sink_id=sink_id,
        )
    )
    extension_receipt = {
        "extension_id": LARK_EXTENSION_ID,
        "extension_version": extension["extension_version"],
        "protocol": MIAODA_PROTOCOL,
        "status": "ready",
        "readback_verified": True,
        "capabilities": [binding["capability"]],
    }
    readiness = build_periodic_report_extension_readiness(
        generation_receipt=generation["generation_receipt"],
        sink_bindings=[binding],
        extension_receipts=[extension_receipt],
    )
    sink_result = registry.deliver(
        sink_id,
        artifacts[0],
        {
            "execute": bool(execute),
            "idempotency_key": idempotency_key,
            "app_id": app_id,
        },
    )
    delivery = build_periodic_report_delivery_receipt(
        generation_receipt=generation["generation_receipt"],
        readiness_receipt=readiness,
        sink_results=[sink_result],
    )
    intent_satisfied = (
        sink_result.get("status") == "sent"
        and sink_result.get("readback_verified") is True
    )
    return {
        "ok": bool(intent_satisfied or not execute),
        "schema_version": MIAODA_DELIVERY_RESULT_SCHEMA,
        "status": (
            "satisfied"
            if intent_satisfied
            else "pending_execution"
            if not execute
            else "readback_unverified"
        ),
        "intent_satisfied": intent_satisfied,
        "generation_usable": True,
        "sink_result": sink_result,
        "delivery_receipt": delivery,
        "extension_activation": observed_activation,
        "boundary": {
            "hosted_delivery_requires_typed_sink": True,
            "local_generation_satisfies_intent": False,
            "external_writes_performed": sink_result.get("external_writes_performed")
            is True,
            "exact_readback_required": True,
        },
    }


__all__ = [
    "DELIVERY_INTENT_SCHEMA",
    "MIAODA_DELIVERY_REQUEST_SCHEMA",
    "MIAODA_DELIVERY_RESULT_SCHEMA",
    "LarkCliMiaodaProvider",
    "deliver_periodic_report_to_miaoda",
]
