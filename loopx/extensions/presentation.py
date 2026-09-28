from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
import hashlib
from importlib import import_module
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from ..control_plane.runtime.public_safety import SECRET_LIKE_SURFACE_PATTERN
from urllib.parse import parse_qsl, urlparse

from ..file_lock import exclusive_file_lock
from .runtime import (
    MAX_EXTENSION_RESPONSE_BYTES,
    _resolved_active_extension,
    extension_catalog_entries,
    run_standalone_extension,
)
from .process_runtime import run_capped_process
from .readiness import (
    CORE_VIEW_VALIDATORS,
    ResolvedRuntimeEntrypoint,
    runtime_process_environment,
)
from .manifest import validate_extension_id


EXTENSION_PRESENTATION_PROJECTION_SCHEMA_VERSION = (
    "extension_presentation_projection_v0"
)
EXTENSION_PROJECTION_SURFACE_SCHEMA_VERSION = "extension_projection_surface_v0"
EXTENSION_PRESENTATION_SURFACES_SCHEMA_VERSION = (
    "extension_presentation_surfaces_v0"
)
EXTENSION_PROJECTION_PUBLISH_RECEIPT_SCHEMA_VERSION = (
    "extension_projection_publish_receipt_v0"
)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ANCHOR_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MARKUP_RE = re.compile(r"<[^>]*>|javascript:", re.IGNORECASE)
_LOCAL_PATH_RE = re.compile(
    r"(?:^|[\s(])(?:~[/\\]|/+(?:Users|home|tmp|private|var|etc|opt)/|"
    r"[A-Za-z]:[\\/])"
)
_PRIVATE_RELATIVE_PATH_RE = re.compile(
    r"(?:^|[\s:=('/\\])\.(?:codex|git|local)(?:[/\\]|$)",
    re.IGNORECASE,
)
# Local threshold policy only: the credential *shapes* are decided once by
# SECRET_LIKE_SURFACE_PATTERN, which this site consults in addition to this list.
_CREDENTIAL_RE = re.compile(
    r"(?:bearer\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"(?:api[_ -]?key|access[_ -]?token|secret|password)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)
_SENSITIVE_TEXT_RE = re.compile(
    r"\b(?:account[\s_-]*(?:id|number)|order[\s_-]*(?:id|request)|"
    r"portfolio(?:[\s_-]+holdings)?|position[\s_-]*size)\b",
    re.IGNORECASE,
)
_FORBIDDEN_KEY_TOKENS = {
    "account_id",
    "account_number",
    "access_token",
    "api_key",
    "credential",
    "cookie",
    "order_id",
    "order_request",
    "portfolio",
    "portfolio_holdings",
    "position_size",
    "raw_provider",
    "raw_request",
    "raw_response",
    "secret",
    "token",
}
_ISOLATED_VIEW_VALIDATOR = """\
import importlib
import json
import sys

request = json.load(sys.stdin)
reference = request["reference"]
module_name, attribute_name = reference.split(":", 1)
try:
    module = importlib.import_module(module_name)
except (ImportError, ModuleNotFoundError):
    response = {"ok": False, "status": "unavailable"}
else:
    validator = getattr(module, attribute_name, None)
    if not callable(validator):
        response = {"ok": False, "status": "not_callable"}
    elif request["operation"] == "resolve":
        response = {"ok": True, "status": "ready"}
    else:
        try:
            response = {"ok": True, "view": validator(request["view"])}
        except Exception as exc:
            response = {
                "ok": False,
                "status": "rejected",
                "error": str(exc)[:1000],
            }
json.dump(response, sys.stdout, ensure_ascii=False, separators=(",", ":"))
"""


def _is_forbidden_key_name(value: Any) -> bool:
    normalized = str(value).lower().replace("-", "_")
    return any(
        normalized == token
        or normalized.startswith(f"{token}_")
        or normalized.endswith(f"_{token}")
        for token in _FORBIDDEN_KEY_TOKENS
    )


def _record(
    value: Any,
    *,
    context: str,
    allowed: set[str],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be an object")
    unsupported = sorted(set(value) - allowed)
    if unsupported:
        raise ValueError(f"{context} contains unsupported keys {unsupported}")
    return value


def _plain_text(
    value: Any,
    *,
    context: str,
    max_length: int = 600,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{context} must be plain text")
    text = value.strip()
    if not text and not allow_empty:
        raise ValueError(f"{context} must be non-empty plain text")
    if len(text) > max_length:
        raise ValueError(f"{context} must contain at most {max_length} characters")
    if "\x00" in text or _MARKUP_RE.search(text):
        raise ValueError(f"{context} must be plain text without markup")
    if _LOCAL_PATH_RE.search(text) or _PRIVATE_RELATIVE_PATH_RE.search(text):
        raise ValueError(f"{context} must not contain a local path")
    if SECRET_LIKE_SURFACE_PATTERN.search(text) or _CREDENTIAL_RE.search(text):
        raise ValueError(f"{context} must not contain credential material")
    if _SENSITIVE_TEXT_RE.search(text):
        raise ValueError(f"{context} must not contain sensitive material")
    return text


def _required_text(
    record: Mapping[str, Any],
    key: str,
    *,
    context: str,
    max_length: int = 600,
) -> str:
    return _plain_text(
        record.get(key),
        context=f"{context}.{key}",
        max_length=max_length,
    )


def _identifier(value: Any, *, context: str) -> str:
    text = _plain_text(value, context=context, max_length=128)
    if not _ID_RE.fullmatch(text):
        raise ValueError(f"{context} must be a stable identifier")
    return text


def _enum(value: Any, allowed: set[str], *, context: str) -> str:
    text = _plain_text(value, context=context, max_length=64)
    if text not in allowed:
        raise ValueError(f"{context} must be one of {sorted(allowed)}")
    return text


def _boolean(value: Any, *, context: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{context} must be a boolean")
    return value


def _number(value: Any, *, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{context} must be finite")
    return number


def _iso_value(value: Any, *, context: str, date_only_allowed: bool = True) -> str:
    text = _plain_text(value, context=context, max_length=40)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        if "T" in normalized:
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                raise ValueError
        elif date_only_allowed:
            date.fromisoformat(normalized)
        else:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"{context} must be a valid ISO-8601 value") from exc
    return text


def _bounded_list(
    value: Any,
    *,
    context: str,
    minimum: int = 0,
    maximum: int,
) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    if len(value) < minimum:
        raise ValueError(f"{context} requires at least {minimum} items")
    if len(value) > maximum:
        raise ValueError(f"{context} must contain at most {maximum} items")
    return value


def _text_list(
    value: Any,
    *,
    context: str,
    minimum: int = 0,
    maximum: int,
    item_limit: int = 600,
) -> list[str]:
    return [
        _plain_text(
            item,
            context=f"{context}[{index}]",
            max_length=item_limit,
        )
        for index, item in enumerate(
            _bounded_list(
                value,
                context=context,
                minimum=minimum,
                maximum=maximum,
            )
        )
    ]


def _assert_allowed_keys_recursively(value: Any, *, context: str = "view") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if _is_forbidden_key_name(normalized):
                if normalized not in {
                    "raw_provider_payload_recorded",
                    "private_source_content_read",
                }:
                    raise ValueError(f"{context} contains forbidden key `{key}`")
            _assert_allowed_keys_recursively(child, context=f"{context}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_allowed_keys_recursively(child, context=f"{context}[{index}]")
    elif isinstance(value, str):
        _plain_text(value, context=context, max_length=2_000, allow_empty=True)


def _evidence_reference(value: Any, *, context: str) -> str:
    reference = _plain_text(value, context=context, max_length=500)
    if "://" not in reference:
        if not _ID_RE.fullmatch(reference):
            raise ValueError(f"{context} must be a compact evidence reference")
        return reference
    parsed = urlparse(reference)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not hostname
        or hostname in {"localhost", "127.0.0.1", "::1"}
        or hostname.endswith((".internal", ".local"))
        or hostname.startswith(("private.", "internal."))
        or parsed.username
        or parsed.password
    ):
        raise ValueError(f"{context} contains an unsafe evidence reference")
    sensitive_url_fields = {
        key.lower().replace("-", "_")
        for component in (parsed.query, parsed.fragment)
        for key, _value in parse_qsl(component, keep_blank_values=True)
    }
    if any(_is_forbidden_key_name(field) for field in sensitive_url_fields):
        raise ValueError(f"{context} contains an unsafe evidence reference")
    return reference


def _sha256(value: Any, *, context: str) -> str:
    text = _plain_text(value, context=context, max_length=64)
    if not _SHA256_RE.fullmatch(text):
        raise ValueError(f"{context} must be a lowercase SHA-256")
    return text


def validate_opaque_presentation_view(value: Any) -> Any:
    """Validate a provider-neutral object view with public-safe structure only."""
    if not isinstance(value, Mapping):
        raise ValueError("presentation_projection.view must be an object")
    _assert_allowed_keys_recursively(value, context="presentation_projection.view")
    return dict(value)


def load_presentation_view_validator(
    declared_surface: Mapping[str, Any],
    *,
    runtime_entrypoint: ResolvedRuntimeEntrypoint | None = None,
    timeout_seconds: int = 30,
) -> Callable[[Any], Any]:
    """Load the exact validator declared by one active presentation surface."""

    reference = declared_surface.get("view_validator")
    if not isinstance(reference, str) or ":" not in reference:
        raise ValueError("presentation surface has no declared view_validator")
    module_name, attribute_name = reference.split(":", 1)
    if runtime_entrypoint is not None and reference not in CORE_VIEW_VALIDATORS:
        python_executable = runtime_entrypoint.python_executable
        if python_executable is None:
            raise ValueError(
                f"presentation surface view_validator `{reference}` is unavailable"
            )

        def invoke_runtime_validator(
            operation: str, *, value: Any = None
        ) -> Mapping[str, Any]:
            try:
                request = json.dumps(
                    {"operation": operation, "reference": reference, "view": value},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "presentation surface view is not JSON serializable"
                ) from exc
            environment = dict(
                runtime_process_environment(runtime_entrypoint.path_prefix) or os.environ
            )
            environment.pop("PYTHONPATH", None)
            try:
                completed = run_capped_process(
                    [python_executable, "-I", "-c", _ISOLATED_VIEW_VALIDATOR],
                    stdin=request,
                    timeout_seconds=timeout_seconds,
                    output_limit_bytes=MAX_EXTENSION_RESPONSE_BYTES,
                    env=environment,
                )
            except OSError as exc:
                raise ValueError(
                    f"presentation surface view_validator `{reference}` is unavailable"
                ) from exc
            if completed.returncode != 0 or completed.failure_kind is not None:
                raise ValueError(
                    f"presentation surface view_validator `{reference}` is unavailable"
                )
            try:
                response = json.loads(completed.stdout.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"presentation surface view_validator `{reference}` returned invalid output"
                ) from exc
            if not isinstance(response, Mapping):
                raise ValueError(
                    f"presentation surface view_validator `{reference}` returned invalid output"
                )
            return response

        resolved = invoke_runtime_validator("resolve")
        if resolved.get("status") == "not_callable":
            raise ValueError(
                f"presentation surface view_validator `{reference}` is not callable"
            )
        if resolved.get("ok") is not True:
            raise ValueError(
                f"presentation surface view_validator `{reference}` is unavailable"
            )

        def validate_in_runtime(value: Any) -> Any:
            response = invoke_runtime_validator("validate", value=value)
            if response.get("ok") is True:
                return response.get("view")
            if response.get("status") == "not_callable":
                raise ValueError(
                    f"presentation surface view_validator `{reference}` is not callable"
                )
            if response.get("status") == "rejected":
                error = response.get("error")
                raise ValueError(
                    error
                    if isinstance(error, str) and error.strip()
                    else "presentation surface view was rejected"
                )
            raise ValueError(
                f"presentation surface view_validator `{reference}` is unavailable"
            )

        return validate_in_runtime

    try:
        module = import_module(module_name)
    except (ImportError, ModuleNotFoundError) as exc:
        raise ValueError(
            f"presentation surface view_validator `{reference}` is unavailable"
        ) from exc
    validator = getattr(module, attribute_name, None)
    if not callable(validator):
        raise ValueError(
            f"presentation surface view_validator `{reference}` is not callable"
        )
    return validator


def _runtime_presentation_view_validator(
    declared_surface: Mapping[str, Any],
    *,
    runtime_entrypoint: ResolvedRuntimeEntrypoint,
    manifest: Mapping[str, Any],
) -> Callable[[Any], Any]:
    runtime = manifest.get("runtime")
    if not isinstance(runtime, Mapping):
        raise ValueError("extension active manifest is incomplete")
    return load_presentation_view_validator(
        declared_surface,
        runtime_entrypoint=runtime_entrypoint,
        timeout_seconds=int(runtime["timeout_seconds"]),
    )


def validate_provider_presentation_projection(
    value: Mapping[str, Any],
    *,
    declared_surface: Mapping[str, Any],
    view_validator: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    """Validate provider-owned fields before Core binds lifecycle identity."""

    record = _record(
        value,
        context="presentation_projection",
        allowed={
            "schema_version",
            "surface_id",
            "goal_id",
            "generated_at",
            "review_due_at",
            "lineage",
            "view_schema",
            "view",
        },
    )
    if (
        record.get("schema_version")
        != EXTENSION_PRESENTATION_PROJECTION_SCHEMA_VERSION
    ):
        raise ValueError(
            "presentation_projection has unsupported schema_version"
        )
    surface_id = _identifier(
        record.get("surface_id"),
        context="presentation_projection.surface_id",
    )
    if surface_id != declared_surface.get("id"):
        raise ValueError("presentation_projection surface_id is not declared")
    view_schema = _plain_text(
        record.get("view_schema"),
        context="presentation_projection.view_schema",
        max_length=80,
    )
    if view_schema != declared_surface.get("view_schema"):
        raise ValueError("presentation_projection view_schema does not match manifest")
    review_due_raw = record.get("review_due_at")
    review_due_at = (
        None
        if review_due_raw is None
        else _iso_value(
            review_due_raw,
            context="presentation_projection.review_due_at",
            date_only_allowed=False,
        )
    )
    lineage = _record(
        record.get("lineage"),
        context="presentation_projection.lineage",
        allowed={
            "source_id",
            "version",
            "row_lifecycle",
            "supersedes",
            "superseded_by",
        },
    )
    version = lineage.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("presentation_projection.lineage.version must be positive")
    row_lifecycle = _enum(
        lineage.get("row_lifecycle"),
        {"active"},
        context="presentation_projection.lineage.row_lifecycle",
    )
    supersedes = [
        _identifier(
            item,
            context=f"presentation_projection.lineage.supersedes[{index}]",
        )
        for index, item in enumerate(
            _bounded_list(
                lineage.get("supersedes"),
                context="presentation_projection.lineage.supersedes",
                maximum=20,
            )
        )
    ]
    superseded_by_raw = lineage.get("superseded_by")
    superseded_by = (
        None
        if superseded_by_raw is None
        else _identifier(
            superseded_by_raw,
            context="presentation_projection.lineage.superseded_by",
        )
    )
    return {
        "schema_version": EXTENSION_PRESENTATION_PROJECTION_SCHEMA_VERSION,
        "surface_id": surface_id,
        "goal_id": _identifier(
            record.get("goal_id"),
            context="presentation_projection.goal_id",
        ),
        "generated_at": _iso_value(
            record.get("generated_at"),
            context="presentation_projection.generated_at",
            date_only_allowed=False,
        ),
        "review_due_at": review_due_at,
        "lineage": {
            "source_id": _identifier(
                lineage.get("source_id"),
                context="presentation_projection.lineage.source_id",
            ),
            "version": version,
            "row_lifecycle": row_lifecycle,
            "supersedes": supersedes,
            "superseded_by": superseded_by,
        },
        "view_schema": view_schema,
        "view": (view_validator or load_presentation_view_validator(declared_surface))(
            record.get("view")
        ),
    }


def validate_projection_hash(value: Any, *, context: str) -> str:
    return _sha256(value, context=context)


def default_extension_projection_root(state_file: str | Path) -> Path:
    return Path(state_file).expanduser().parent / "projections"


def _extension_projection_path(
    state_file: str | Path,
    *,
    extension_id: str,
    surface_id: str,
) -> Path:
    root = default_extension_projection_root(state_file).resolve()
    candidate = (root / extension_id / f"{surface_id}.json").resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "extension projection path must remain within the canonical projection root"
        ) from exc
    return candidate


def _declared_surface(
    manifest: Mapping[str, Any],
    *,
    extension_id: str,
    surface_id: str,
) -> Mapping[str, Any]:
    surfaces = manifest.get("presentation_surfaces")
    if not isinstance(surfaces, list):
        raise ValueError("extension active manifest has invalid presentation surfaces")
    matching = [
        item
        for item in surfaces
        if isinstance(item, Mapping) and item.get("id") == surface_id
    ]
    if not matching:
        raise ValueError(
            f"extension `{extension_id}` does not declare presentation surface "
            f"`{surface_id}`"
        )
    if len(matching) != 1:
        raise ValueError("extension active manifest has duplicate presentation surfaces")
    return matching[0]


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value),
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _envelope_hash(value: Mapping[str, Any]) -> str:
    payload = {key: child for key, child in value.items() if key != "payload_sha256"}
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _validate_persisted_envelope(
    value: Any,
    *,
    extension_id: str,
    revision: str,
    declared_surface: Mapping[str, Any],
    view_validator: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    record = _record(
        value,
        context="projection_envelope",
        allowed={
            "schema_version",
            "extension_id",
            "extension_revision",
            "surface_id",
            "surface_kind",
            "view_schema",
            "visibility",
            "goal_id",
            "generated_at",
            "review_due_at",
            "payload_sha256",
            "lineage",
            "view",
        },
    )
    if record.get("schema_version") != EXTENSION_PROJECTION_SURFACE_SCHEMA_VERSION:
        raise ValueError("projection_envelope has unsupported schema_version")
    expected = {
        "extension_id": extension_id,
        "extension_revision": revision,
        "surface_id": declared_surface.get("id"),
        "surface_kind": declared_surface.get("kind"),
        "view_schema": declared_surface.get("view_schema"),
        "visibility": declared_surface.get("visibility"),
    }
    for key, expected_value in expected.items():
        if record.get(key) != expected_value:
            raise ValueError(f"projection_envelope.{key} does not match active state")
    provider_projection = validate_provider_presentation_projection(
        {
            "schema_version": EXTENSION_PRESENTATION_PROJECTION_SCHEMA_VERSION,
            "surface_id": record.get("surface_id"),
            "goal_id": record.get("goal_id"),
            "generated_at": record.get("generated_at"),
            "review_due_at": record.get("review_due_at"),
            "lineage": record.get("lineage"),
            "view_schema": record.get("view_schema"),
            "view": record.get("view"),
        },
        declared_surface=declared_surface,
        view_validator=view_validator,
    )
    envelope = {
        "schema_version": EXTENSION_PROJECTION_SURFACE_SCHEMA_VERSION,
        **expected,
        "goal_id": provider_projection["goal_id"],
        "generated_at": provider_projection["generated_at"],
        "review_due_at": provider_projection["review_due_at"],
        "lineage": provider_projection["lineage"],
        "view": provider_projection["view"],
    }
    payload_sha256 = validate_projection_hash(
        record.get("payload_sha256"),
        context="projection_envelope.payload_sha256",
    )
    if _envelope_hash(envelope) != payload_sha256:
        raise ValueError("projection_envelope payload_sha256 does not match content")
    envelope["payload_sha256"] = payload_sha256
    return envelope


def _atomic_write_projection(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                dict(payload),
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        if os.name == "posix":
            directory_fd = os.open(
                path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def publish_extension_projection(
    extension_id: str,
    surface_id: str,
    *,
    state_file: str | Path,
    request: Mapping[str, Any],
    execute: bool = False,
) -> dict[str, Any]:
    """Run one ready standalone provider and atomically publish its declared view."""

    state_path = Path(state_file).expanduser()
    safe_extension_id = validate_extension_id(extension_id)
    active_revision, verified_entrypoint, manifest = _resolved_active_extension(
        safe_extension_id,
        state_file=state_path,
    )
    surface = _declared_surface(
        manifest,
        extension_id=safe_extension_id,
        surface_id=surface_id,
    )
    receipt: dict[str, Any] = {
        "ok": True,
        "schema_version": EXTENSION_PROJECTION_PUBLISH_RECEIPT_SCHEMA_VERSION,
        "operation": "publish_projection",
        "extension_id": safe_extension_id,
        "surface_id": surface_id,
        "revision": active_revision,
        "dry_run": not execute,
        "executed": False,
        "status": "ready" if not execute else "running",
    }
    if not execute:
        return receipt

    view_validator = _runtime_presentation_view_validator(
        surface,
        runtime_entrypoint=verified_entrypoint,
        manifest=manifest,
    )
    runtime_receipt = run_standalone_extension(
        safe_extension_id,
        state_file=state_path,
        request=request,
        execute=True,
    )
    if not runtime_receipt.get("ok") or runtime_receipt.get("status") != "succeeded":
        raise ValueError(
            f"extension `{safe_extension_id}` provider did not produce a successful result"
        )
    provider_result = runtime_receipt.get("provider_result")
    if not isinstance(provider_result, Mapping):
        raise ValueError("extension provider result must be an object")
    provider_projection = validate_provider_presentation_projection(
        provider_result.get("presentation_projection"),
        declared_surface=surface,
        view_validator=view_validator,
    )

    confirmed_revision, confirmed_entrypoint, confirmed_manifest = (
        _resolved_active_extension(safe_extension_id, state_file=state_path)
    )
    if (
        confirmed_revision != active_revision
        or confirmed_entrypoint.identity != verified_entrypoint.identity
    ):
        raise ValueError(
            f"extension `{safe_extension_id}` lifecycle changed during projection publication"
        )
    confirmed_surface = _declared_surface(
        confirmed_manifest,
        extension_id=safe_extension_id,
        surface_id=surface_id,
    )
    if dict(confirmed_surface) != dict(surface):
        raise ValueError(
            f"extension `{safe_extension_id}` surface changed during projection publication"
        )

    envelope: dict[str, Any] = {
        "schema_version": EXTENSION_PROJECTION_SURFACE_SCHEMA_VERSION,
        "extension_id": safe_extension_id,
        "extension_revision": active_revision,
        "surface_id": surface_id,
        "surface_kind": surface["kind"],
        "view_schema": surface["view_schema"],
        "visibility": surface["visibility"],
        "goal_id": provider_projection["goal_id"],
        "generated_at": provider_projection["generated_at"],
        "review_due_at": provider_projection["review_due_at"],
        "lineage": provider_projection["lineage"],
        "view": provider_projection["view"],
    }
    envelope["payload_sha256"] = _envelope_hash(envelope)
    projection_path = _extension_projection_path(
        state_path,
        extension_id=safe_extension_id,
        surface_id=surface_id,
    )
    with exclusive_file_lock(projection_path):
        final_revision, final_entrypoint, final_manifest = _resolved_active_extension(
            safe_extension_id,
            state_file=state_path,
        )
        if (
            final_revision != active_revision
            or final_entrypoint.identity != verified_entrypoint.identity
            or dict(
                _declared_surface(
                    final_manifest,
                    extension_id=safe_extension_id,
                    surface_id=surface_id,
                )
            )
            != dict(surface)
        ):
            raise ValueError(
                f"extension `{safe_extension_id}` lifecycle changed before projection write"
            )
        _atomic_write_projection(projection_path, envelope)
        try:
            readback_raw = json.loads(projection_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("published projection readback is unreadable") from exc
        readback = _validate_persisted_envelope(
            readback_raw,
            extension_id=safe_extension_id,
            revision=active_revision,
            declared_surface=surface,
            view_validator=view_validator,
        )
        if readback != envelope:
            raise ValueError("published projection readback does not match exact payload")

    return {
        **receipt,
        "executed": True,
        "status": "published",
        "payload_sha256": envelope["payload_sha256"],
        "readback_verified": True,
    }


def _surface_status_item(
    *,
    extension_id: str,
    revision: str,
    surface: Mapping[str, Any],
    state: str,
    diagnostic: str | None = None,
    envelope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "extension_id": extension_id,
        "extension_revision": revision,
        "surface_id": surface["id"],
        "surface_kind": surface["kind"],
        "title": surface["title"],
        "view_schema": surface["view_schema"],
        "visibility": surface["visibility"],
        "state": state,
        "goal_id": envelope.get("goal_id") if envelope else None,
        "generated_at": envelope.get("generated_at") if envelope else None,
        "review_due_at": envelope.get("review_due_at") if envelope else None,
        "diagnostic": diagnostic,
        "empty_state_title": surface["empty_state_title"],
        "empty_state_detail": surface["empty_state_detail"],
    }
    if envelope is not None and state in {"ready", "review_due"}:
        # Status is a compact index, not a data channel: reference the published
        # projection by content-addressed pointer instead of inlining the full
        # (potentially large, provider-owned) view. Callers that need the view
        # read the referenced projection payload directly.
        item["detail_ref"] = {
            "extension_id": extension_id,
            "surface_id": surface["id"],
            "extension_revision": revision,
            "payload_sha256": envelope["payload_sha256"],
        }
    return item


def _review_due(
    review_due_at: str | None,
    *,
    now: datetime,
) -> bool:
    if review_due_at is None:
        return False
    normalized = (
        review_due_at[:-1] + "+00:00"
        if review_due_at.endswith("Z")
        else review_due_at
    )
    deadline = datetime.fromisoformat(normalized)
    return deadline <= now


def _projection_diagnostic(exc: ValueError) -> str:
    message = str(exc)
    if "payload_sha256" in message:
        return "projection_hash_invalid"
    if "schema_version" in message:
        return "projection_schema_invalid"
    if "local path" in message or "credential" in message or "forbidden key" in message:
        return "projection_boundary_invalid"
    return "projection_content_invalid"


def collect_active_extension_presentation_surfaces(
    *,
    state_file: str | Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read active, doctor-ready surface declarations and revision-bound data."""

    state_path = Path(state_file).expanduser()
    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        raise ValueError("presentation surface collection `now` must be timezone-aware")
    items: list[dict[str, Any]] = []
    for manifest in extension_catalog_entries(state_file=state_path):
        provider = manifest.get("provider")
        if not isinstance(provider, Mapping) or provider.get("ready") is not True:
            continue
        extension_id = str(provider.get("id") or "")
        revision = str(provider.get("active_revision") or "")
        try:
            resolved_revision, runtime_entrypoint, active_manifest = (
                _resolved_active_extension(extension_id, state_file=state_path)
            )
        except ValueError:
            continue
        if resolved_revision != revision:
            continue
        surfaces = manifest.get("presentation_surfaces")
        if not isinstance(surfaces, list):
            continue
        for surface in surfaces:
            if not isinstance(surface, Mapping):
                continue
            projection_path = _extension_projection_path(
                state_path,
                extension_id=extension_id,
                surface_id=str(surface["id"]),
            )
            if not projection_path.exists():
                items.append(
                    _surface_status_item(
                        extension_id=extension_id,
                        revision=revision,
                        surface=surface,
                        state="empty",
                    )
                )
                continue
            try:
                raw = json.loads(projection_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                items.append(
                    _surface_status_item(
                        extension_id=extension_id,
                        revision=revision,
                        surface=surface,
                        state="invalid",
                        diagnostic="projection_unreadable",
                    )
                )
                continue
            if (
                isinstance(raw, Mapping)
                and raw.get("extension_revision") != revision
            ):
                items.append(
                    _surface_status_item(
                        extension_id=extension_id,
                        revision=revision,
                        surface=surface,
                        state="empty",
                    )
                )
                continue
            try:
                view_validator = _runtime_presentation_view_validator(
                    surface,
                    runtime_entrypoint=runtime_entrypoint,
                    manifest=active_manifest,
                )
                envelope = _validate_persisted_envelope(
                    raw,
                    extension_id=extension_id,
                    revision=revision,
                    declared_surface=surface,
                    view_validator=view_validator,
                )
            except ValueError as exc:
                items.append(
                    _surface_status_item(
                        extension_id=extension_id,
                        revision=revision,
                        surface=surface,
                        state="invalid",
                        diagnostic=_projection_diagnostic(exc),
                    )
                )
                continue
            state = (
                "review_due"
                if _review_due(envelope["review_due_at"], now=observed_at)
                else "ready"
            )
            items.append(
                _surface_status_item(
                    extension_id=extension_id,
                    revision=revision,
                    surface=surface,
                    state=state,
                    envelope=envelope,
                )
            )

    counts = {
        state: sum(item["state"] == state for item in items)
        for state in ("ready", "review_due", "empty", "invalid")
    }
    return {
        "schema_version": EXTENSION_PRESENTATION_SURFACES_SCHEMA_VERSION,
        "count": len(items),
        "ready_count": counts["ready"],
        "review_due_count": counts["review_due"],
        "empty_count": counts["empty"],
        "invalid_count": counts["invalid"],
        "items": items,
    }


def read_extension_projection(
    *,
    state_file: str | Path,
    extension_id: str,
    surface_id: str,
    extension_revision: str,
    payload_sha256: str,
) -> dict[str, Any]:
    """Resolve one published projection by its compact status detail reference."""

    state_path = Path(state_file).expanduser()
    safe_extension_id = validate_extension_id(extension_id)
    safe_surface_id = _identifier(surface_id, context="surface_id")
    requested_revision = _identifier(
        extension_revision,
        context="extension_revision",
    )
    requested_hash = validate_projection_hash(
        payload_sha256,
        context="payload_sha256",
    )
    active_revision, runtime_entrypoint, manifest = _resolved_active_extension(
        safe_extension_id,
        state_file=state_path,
    )
    if active_revision != requested_revision:
        raise ValueError(
            "extension_revision does not match the active extension revision"
        )
    declared_surface = _declared_surface(
        manifest,
        extension_id=safe_extension_id,
        surface_id=safe_surface_id,
    )
    if declared_surface.get("visibility") != "public-safe":
        raise ValueError(
            "owner-only projection reads require an authenticated audience"
        )
    projection_path = _extension_projection_path(
        state_path,
        extension_id=safe_extension_id,
        surface_id=safe_surface_id,
    )
    if not projection_path.exists():
        raise ValueError("no published projection for the requested surface")
    try:
        raw = json.loads(projection_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("published projection is unreadable") from exc
    if isinstance(raw, Mapping) and raw.get("extension_revision") != active_revision:
        raise ValueError("published projection belongs to another revision")
    envelope = _validate_persisted_envelope(
        raw,
        extension_id=safe_extension_id,
        revision=active_revision,
        declared_surface=declared_surface,
        view_validator=_runtime_presentation_view_validator(
            declared_surface,
            runtime_entrypoint=runtime_entrypoint,
            manifest=manifest,
        ),
    )
    if envelope["payload_sha256"] != requested_hash:
        raise ValueError("payload_sha256 does not match the published projection")
    return envelope
