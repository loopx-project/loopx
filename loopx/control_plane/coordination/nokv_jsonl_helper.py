"""Narrow JSON-lines bridge from LoopX to the NoKV Python SDK.

The bridge deliberately knows only byte storage.  Authority transitions,
receipts, cursor ordering, and ambiguous-outcome reconciliation stay in the
TypeScript ``NoKVAuthorityStore``.  The first JSON line configures one SDK
client; every later line invokes exactly one of ``store_identity``,
``read_blob``, or ``cas_publish_blob``.  Every publication names the workbench
incarnation it expects; the SDK refuses a stale one before any row or object
exists, and the helper admits only an SDK that can make that refusal typed.
"""

from __future__ import annotations

import base64
import binascii
import inspect
import json
import re
import sys
from collections.abc import Callable, Mapping
from typing import Any, TextIO

_HEX_128 = re.compile(r"^[0-9a-f]{32}$")
_CLIENT_AVAILABILITY_ERRORS = (RuntimeError, OSError)
QUALIFIED_NOKV_SDK_VERSION = "0.11.1"
QUALIFIED_NOKV_API_VERSION = 1
# NoKV 0.11.1 evaluates ``expected_workspace_incarnation_id`` atomically with
# the generation before any durable row or object exists and refuses a stale
# incarnation with a typed exception. Both halves are admission requirements.
_INCARNATION_FENCE_PARAMETER = "expected_workspace_incarnation_id"
_INCARNATION_MISMATCH_EXCEPTION = "WorkspaceIncarnationMismatch"

_CONFIG_KEYS = frozenset(
    {
        "root_id",
        "routing",
        "object_store",
        "max_attempts",
        "connect_timeout_ms",
        "read_timeout_ms",
        "write_timeout_ms",
        "handshake_timeout_ms",
        "workbench_root",
    }
)
_ETCD_ROUTING_KEYS = frozenset({"kind", "endpoints", "key_prefix", "lease_ttl_seconds"})
# Seed routing names one or more serving NoKV owners directly (numeric IP:port);
# the SDK rejects hostnames, empty lists and unspecified ports itself.
_SEEDS_ROUTING_KEYS = frozenset({"kind", "endpoints"})
# Every routing kind this helper can express. A kind outside this set is a
# configuration error; a kind inside it that the installed SDK cannot build is
# a capability mismatch between the wheel and the configuration.
_ROUTING_KINDS = frozenset({"etcd", "seeds", "static"})
_STATIC_ROUTING_KEYS = frozenset(
    {
        "kind",
        "endpoint",
        "logical_shard_id",
        "object_namespace_id",
        "placement_generation",
        "owner_epoch",
    }
)
_MEMORY_OBJECT_STORE_KEYS = frozenset({"kind"})
_S3_OBJECT_STORE_KEYS = frozenset(
    {
        "kind",
        "bucket",
        "region",
        "root",
        "endpoint",
        "access_key_id",
        "secret_access_key",
        "session_token",
        "virtual_host_style",
        "skip_signature",
    }
)


class RequestError(ValueError):
    """The JSON-lines caller violated the raw storage protocol."""


class SdkCapabilityMismatch(RequestError):
    """The installed NoKV SDK lacks a surface this helper requires.

    Either the routing constructor this configuration names or the fenced
    publication surface (``expected_workspace_incarnation_id`` plus the typed
    ``WorkspaceIncarnationMismatch`` refusal). Raised only after the
    configuration itself validated, so the caller can tell "wrong wheel" apart
    from "invalid config".
    """


class ProviderProtocolError(RuntimeError):
    """The NoKV SDK returned a shape that violates its reviewed contract."""


class ClientAdmissionUnavailable(RuntimeError):
    """The configured SDK could not admit a live route or object provider."""


def _request_id(value: object) -> str | None:
    if isinstance(value, str) and value.strip() == value and value:
        return value
    return None


def _response(
    request_id: str | None, status: str, **values: object
) -> dict[str, object]:
    return {"request_id": request_id, "status": status, **values}


def _failure(
    request_id: str | None,
    status: str,
    reason_code: str,
    error: object,
) -> dict[str, object]:
    return _response(
        request_id,
        status,
        reason_code=reason_code,
        reason=str(error) if str(error) else reason_code,
    )


def _opaque_failure(
    request_id: str | None,
    status: str,
    reason_code: str,
    reason: str,
) -> dict[str, object]:
    return _response(
        request_id,
        status,
        reason_code=reason_code,
        reason=reason,
    )


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RequestError(f"{name} must be an object")
    return value


def _require_exact_keys(
    values: Mapping[str, Any],
    allowed: frozenset[str],
    name: str,
) -> None:
    if any(key not in allowed for key in values):
        raise RequestError(f"{name} contains unsupported fields")


def _required_string(values: Mapping[str, Any], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value or value.strip() != value:
        raise RequestError(f"{name} must be a non-empty trimmed string")
    return value


def _generation(value: object, name: str, *, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        suffix = " or null" if nullable else ""
        raise RequestError(f"{name} must be a positive integer{suffix}")
    return value


def _sdk_generation(value: object, name: str) -> int:
    try:
        generation = _generation(value, name)
    except RequestError as error:
        raise ProviderProtocolError(str(error)) from error
    assert generation is not None
    return generation


def _decode_bytes(value: object) -> bytes:
    if not isinstance(value, str):
        raise RequestError("bytes_base64 must be a string")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise RequestError("bytes_base64 is not canonical base64") from error


def _identity(client: Any, workbench: str) -> str:
    cursor: bytes | None = None
    seen_cursors: set[bytes] = set()
    identities: set[str] = set()
    while True:
        page = _mapping(
            client.find_workspaces(cursor=cursor, limit=100),
            "find_workspaces result",
        )
        workspaces = page.get("workspaces")
        if not isinstance(workspaces, list):
            raise ProviderProtocolError("find_workspaces omitted workspaces")
        for item in workspaces:
            entry = _mapping(item, "workspace entry")
            workspace = _mapping(entry.get("workspace"), "workspace summary")
            if workspace.get("workbench") != workbench:
                continue
            incarnation = workspace.get("workspace_incarnation_id")
            if not isinstance(incarnation, str) or not _HEX_128.fullmatch(incarnation):
                raise ProviderProtocolError(
                    "workspace incarnation identity must be 32 lowercase hex"
                )
            identities.add(incarnation)
        next_cursor = page.get("next_cursor")
        if next_cursor is None:
            break
        if not isinstance(next_cursor, bytes) or not next_cursor:
            raise ProviderProtocolError("find_workspaces next_cursor is invalid")
        if next_cursor in seen_cursors:
            raise ProviderProtocolError("find_workspaces cursor did not advance")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    if len(identities) != 1:
        raise ProviderProtocolError(
            "workbench did not resolve to one incarnation identity"
        )
    return f"nokv:{workbench}:{next(iter(identities))}"


def _store_identity(
    client: Any,
    request_id: str,
    values: Mapping[str, Any],
) -> dict[str, object]:
    workbench = _required_string(values, "workbench")
    try:
        identity = _identity(client, workbench)
    except (ProviderProtocolError, TypeError, ValueError) as error:
        return _failure(
            request_id,
            "failed",
            "provider_protocol_violation",
            error,
        )
    except _CLIENT_AVAILABILITY_ERRORS:
        return _opaque_failure(
            request_id,
            "unavailable",
            "nokv_identity_unavailable",
            "NoKV identity lookup is unavailable",
        )
    return _response(request_id, "available", store_identity=identity)


def _read_blob(
    client: Any,
    request_id: str,
    values: Mapping[str, Any],
) -> dict[str, object]:
    workbench = _required_string(values, "workbench")
    path = _required_string(values, "path")
    try:
        result = _mapping(client.read(workbench, path), "read result")
    except FileNotFoundError:
        return _response(request_id, "missing")
    except _CLIENT_AVAILABILITY_ERRORS:
        return _opaque_failure(
            request_id,
            "unavailable",
            "nokv_read_unavailable",
            "NoKV blob read is unavailable",
        )
    except (TypeError, ValueError) as error:
        return _failure(request_id, "failed", "provider_protocol_violation", error)
    try:
        raw = result.get("bytes")
        if not isinstance(raw, (bytes, bytearray, memoryview)):
            raise ProviderProtocolError("read result omitted bytes")
        metadata = _mapping(result.get("metadata"), "read metadata")
        if metadata.get("workbench") != workbench:
            raise ProviderProtocolError("read metadata workbench mismatch")
        if metadata.get("path") != path:
            raise ProviderProtocolError("read metadata path mismatch")
        incarnation = metadata.get("workspace_incarnation_id")
        if not isinstance(incarnation, str) or not _HEX_128.fullmatch(incarnation):
            raise ProviderProtocolError(
                "read metadata workspace incarnation identity is invalid"
            )
        current_identity = _identity(client, workbench)
        if current_identity != f"nokv:{workbench}:{incarnation}":
            raise ProviderProtocolError("read metadata workspace incarnation mismatch")
        generation = _sdk_generation(metadata.get("generation"), "read generation")
    except (ProviderProtocolError, TypeError, ValueError) as error:
        return _failure(request_id, "failed", "provider_protocol_violation", error)
    except _CLIENT_AVAILABILITY_ERRORS:
        return _opaque_failure(
            request_id,
            "unavailable",
            "nokv_read_unavailable",
            "NoKV blob read is unavailable",
        )
    return _response(
        request_id,
        "loaded",
        bytes_base64=base64.b64encode(bytes(raw)).decode("ascii"),
        generation=generation,
    )


def _cas_publish_blob(
    client: Any,
    request_id: str,
    values: Mapping[str, Any],
) -> dict[str, object]:
    workbench = _required_string(values, "workbench")
    path = _required_string(values, "path")
    expected_generation = _generation(
        values.get("expected_generation"),
        "expected_generation",
        nullable=True,
    )
    payload = _decode_bytes(values.get("bytes_base64"))
    operation_id = _required_string(values, "operation_id")
    artifact_revision_id = _required_string(values, "artifact_revision_id")
    expected_incarnation = _required_string(values, _INCARNATION_FENCE_PARAMETER)
    if not _HEX_128.fullmatch(operation_id):
        raise RequestError("operation_id must be 32 lowercase hex")
    if not _HEX_128.fullmatch(artifact_revision_id):
        raise RequestError("artifact_revision_id must be 32 lowercase hex")
    if not _HEX_128.fullmatch(expected_incarnation):
        raise RequestError(f"{_INCARNATION_FENCE_PARAMETER} must be 32 lowercase hex")
    try:
        raw_result = client.publish_bytes(
            workbench,
            path,
            payload,
            content_type="application/json",
            expected_generation=expected_generation,
            operation_id=operation_id,
            artifact_revision_id=artifact_revision_id,
            expected_workspace_incarnation_id=expected_incarnation,
        )
    except _incarnation_mismatch_errors() as error:
        # The owner evaluated the fence before claiming the path, so nothing
        # durable exists for this attempt. A refusal that names a different
        # fence than the one sent is an SDK contract violation, not evidence.
        if getattr(error, "expected", None) != expected_incarnation:
            return _opaque_failure(
                request_id,
                "ambiguous",
                "provider_protocol_violation",
                "NoKV refused a workbench incarnation fence this request did not send",
            )
        return _opaque_failure(
            request_id,
            "failed",
            "store_identity_mismatch",
            "NoKV workbench incarnation does not match the expected incarnation",
        )
    except FileExistsError:
        return _response(
            request_id,
            "conflict",
            current_generation=None,
        )
    except (RuntimeError, OSError, TypeError, ValueError):
        # RuntimeError covers both a rejected generation and a response lost
        # after commit.  Human error text cannot distinguish them, so only a
        # later authority-envelope readback may settle the outcome.
        return _opaque_failure(
            request_id,
            "ambiguous",
            "nokv_publish_outcome_unknown",
            "NoKV publish outcome is unknown",
        )
    try:
        result = _mapping(raw_result, "publish result")
    except (TypeError, ValueError):
        return _opaque_failure(
            request_id,
            "ambiguous",
            "provider_protocol_violation",
            "NoKV publish response violated the storage protocol",
        )
    try:
        generation = _sdk_generation(result.get("generation"), "publish generation")
        expected_result_generation = (expected_generation or 0) + 1
        if result.get("workbench") != workbench:
            raise ProviderProtocolError("publish result workbench mismatch")
        if result.get("path") != path:
            raise ProviderProtocolError("publish result path mismatch")
        if result.get("operation_id") != operation_id:
            raise ProviderProtocolError("publish result operation identity mismatch")
        if result.get("artifact_revision_id") != artifact_revision_id:
            raise ProviderProtocolError("publish result artifact revision mismatch")
        if generation != expected_result_generation:
            raise ProviderProtocolError("publish result generation mismatch")
    except ProviderProtocolError:
        return _opaque_failure(
            request_id,
            "ambiguous",
            "provider_protocol_violation",
            "NoKV publish response violated the storage protocol",
        )
    return _response(request_id, "applied", generation=generation)


def handle_request(client: Any, value: object) -> dict[str, object]:
    """Execute one raw storage request without interpreting stored bytes."""

    request_id = _request_id(
        value.get("request_id") if isinstance(value, Mapping) else None
    )
    try:
        values = _mapping(value, "request")
        if request_id is None:
            raise RequestError("request_id must be a non-empty trimmed string")
        operation = _required_string(values, "operation")
        handlers: dict[
            str,
            Callable[[Any, str, Mapping[str, Any]], dict[str, object]],
        ] = {
            "store_identity": _store_identity,
            "read_blob": _read_blob,
            "cas_publish_blob": _cas_publish_blob,
        }
        handler = handlers.get(operation)
        if handler is None:
            raise RequestError(f"unknown operation {operation!r}")
        return handler(client, request_id, values)
    except RequestError as error:
        return _failure(request_id, "failed", "invalid_request", error)


def serve(client: Any, incoming: TextIO, outgoing: TextIO) -> None:
    """Serve raw storage requests until EOF."""

    for line in incoming:
        try:
            request = json.loads(line)
        except json.JSONDecodeError as error:
            result = _failure(None, "failed", "invalid_json", error)
        else:
            result = handle_request(client, request)
        outgoing.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
        outgoing.flush()


def _string_list(values: Mapping[str, Any], name: str) -> list[str]:
    value = values.get(name)
    if not isinstance(value, list) or not value:
        raise RequestError(f"{name} must be a non-empty array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise RequestError(f"{name} entries must be non-empty strings")
        result.append(item)
    return result


def build_client(config_value: object) -> Any:
    """Construct one eagerly admitted NoKV client from the open handshake."""

    config = _mapping(config_value, "config")
    _require_exact_keys(config, _CONFIG_KEYS, "config")
    routing_value = _mapping(config.get("routing"), "routing")
    routing_kind = _required_string(routing_value, "kind")
    if routing_kind not in _ROUTING_KINDS:
        raise RequestError(f"unsupported routing kind {routing_kind!r}")
    if routing_kind == "etcd":
        _require_exact_keys(routing_value, _ETCD_ROUTING_KEYS, "routing")
        routing_arguments: tuple[object, ...] = (
            _string_list(routing_value, "endpoints"),
            _required_string(routing_value, "key_prefix"),
            _generation(
                routing_value.get("lease_ttl_seconds", 10),
                "lease_ttl_seconds",
            ),
        )
    elif routing_kind == "static":
        _require_exact_keys(routing_value, _STATIC_ROUTING_KEYS, "routing")
        routing_arguments = (
            _required_string(routing_value, "endpoint"),
            _required_string(routing_value, "logical_shard_id"),
            _required_string(routing_value, "object_namespace_id"),
            _generation(
                routing_value.get("placement_generation"),
                "placement_generation",
            ),
            _generation(routing_value.get("owner_epoch"), "owner_epoch"),
        )
    else:
        _require_exact_keys(routing_value, _SEEDS_ROUTING_KEYS, "routing")
        routing_arguments = (_string_list(routing_value, "endpoints"),)

    object_value = _mapping(config.get("object_store"), "object_store")
    object_kind = _required_string(object_value, "kind")
    if object_kind == "memory":
        _require_exact_keys(
            object_value,
            _MEMORY_OBJECT_STORE_KEYS,
            "object_store",
        )
        object_arguments: dict[str, object] | None = None
    elif object_kind == "s3":
        _require_exact_keys(object_value, _S3_OBJECT_STORE_KEYS, "object_store")
        object_arguments = {
            "bucket": _required_string(object_value, "bucket"),
            "region": object_value.get("region", "us-east-1"),
            "root": object_value.get("root", "/"),
            "endpoint": object_value.get("endpoint"),
            "access_key_id": object_value.get("access_key_id"),
            "secret_access_key": object_value.get("secret_access_key"),
            "session_token": object_value.get("session_token"),
            "virtual_host_style": object_value.get("virtual_host_style", False),
            "skip_signature": object_value.get("skip_signature", False),
        }
    else:
        raise RequestError(f"unsupported object store kind {object_kind!r}")

    root_id = _required_string(config, "root_id")
    if not _HEX_128.fullmatch(root_id):
        raise RequestError("root_id must be 32 lowercase hex")
    max_attempts = _generation(config.get("max_attempts", 3), "max_attempts")
    connect_timeout_ms = _generation(
        config.get("connect_timeout_ms", 5_000),
        "connect_timeout_ms",
    )
    read_timeout_ms = _generation(
        config.get("read_timeout_ms", 30_000),
        "read_timeout_ms",
    )
    write_timeout_ms = _generation(
        config.get("write_timeout_ms", 30_000),
        "write_timeout_ms",
    )
    handshake_timeout_ms = _generation(
        config.get("handshake_timeout_ms", 5_000),
        "handshake_timeout_ms",
    )
    workbench_root = config.get("workbench_root")
    if workbench_root is not None and (
        not isinstance(workbench_root, str) or not workbench_root
    ):
        raise RequestError("workbench_root must be a non-empty string or null")

    try:
        import nokv
    except ImportError as error:  # pragma: no cover - exercised by live packaging
        raise RequestError("the NoKV Python SDK is not installed") from error
    if (
        getattr(nokv, "__version__", None) != QUALIFIED_NOKV_SDK_VERSION
        or getattr(nokv, "API_VERSION", None) != QUALIFIED_NOKV_API_VERSION
    ):
        raise RequestError(
            "the NoKV Python SDK must be version "
            f"{QUALIFIED_NOKV_SDK_VERSION} with API version "
            f"{QUALIFIED_NOKV_API_VERSION}"
        )
    try:
        Client = nokv.Client
        ObjectStoreConfig = nokv.ObjectStoreConfig
        RoutingConfig = nokv.RoutingConfig
    except AttributeError as error:
        raise RequestError("the NoKV Python SDK surface is incomplete") from error
    if not publish_incarnation_fence_supported(Client):
        raise SdkCapabilityMismatch(
            "the installed NoKV Python SDK does not fence publication on the "
            f"expected workbench incarnation (Client.publish_bytes lacks "
            f"{_INCARNATION_FENCE_PARAMETER} or nokv.{_INCARNATION_MISMATCH_EXCEPTION} "
            "is missing)"
        )

    # The kind was checked against _ROUTING_KINDS above, so this attribute
    # lookup never reaches an arbitrary caller-chosen name. The 0.11.x release
    # wheels provide etcd/static; the metadata-runtimes line provides seeds.
    routing_constructor = getattr(RoutingConfig, routing_kind, None)
    if not callable(routing_constructor):
        raise SdkCapabilityMismatch(
            "the installed NoKV Python SDK does not provide "
            f"RoutingConfig.{routing_kind}"
        )
    try:
        routing = routing_constructor(*routing_arguments)
    except (TypeError, ValueError) as error:
        raise RequestError("NoKV routing configuration is invalid") from error
    try:
        object_store = (
            ObjectStoreConfig.memory()
            if object_arguments is None
            else ObjectStoreConfig.s3(**object_arguments)
        )
    except (TypeError, ValueError) as error:
        raise RequestError("NoKV object-store configuration is invalid") from error
    try:
        return Client(
            root_id=root_id,
            routing=routing,
            object_store=object_store,
            max_attempts=max_attempts,
            connect_timeout_ms=connect_timeout_ms,
            read_timeout_ms=read_timeout_ms,
            write_timeout_ms=write_timeout_ms,
            handshake_timeout_ms=handshake_timeout_ms,
            workbench_root=workbench_root,
        )
    except (RuntimeError, OSError, ValueError) as error:
        raise ClientAdmissionUnavailable from error
    except TypeError as error:
        raise RequestError("NoKV client configuration is invalid") from error


def _incarnation_mismatch_errors() -> tuple[type[BaseException], ...]:
    """The SDK's typed incarnation-fence refusal, or nothing when it has none."""
    error = getattr(sys.modules.get("nokv"), _INCARNATION_MISMATCH_EXCEPTION, None)
    if isinstance(error, type) and issubclass(error, BaseException):
        return (error,)
    return ()


def publish_incarnation_fence_supported(client_type: object) -> bool:
    """True only when publishing names the fence and refusing it is typed.

    Both halves are required: a wheel that accepted the keyword without a typed
    refusal would collapse a stale-incarnation rejection into the ambiguous
    ``RuntimeError`` path, which is exactly the outcome the fence exists to
    remove. The 0.11.0 release wheel has neither half.
    """
    if not _incarnation_mismatch_errors():
        return False
    try:
        signature = inspect.signature(getattr(client_type, "publish_bytes"))
    except (AttributeError, TypeError, ValueError):
        return False
    parameter = signature.parameters.get(_INCARNATION_FENCE_PARAMETER)
    return parameter is not None and parameter.kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    )


def _sdk_protocol_schema() -> str | None:
    """Return the wire schema the imported SDK declares, if it declares one.

    The 0.11.0 release wheel has no such attribute; newer wheels export
    ``WORKSPACE_PROTOCOL_SCHEMA`` so a deployment can compare it against the
    server before trusting a nominally equal ``__version__``.
    """
    schema = getattr(sys.modules.get("nokv"), "WORKSPACE_PROTOCOL_SCHEMA", None)
    if isinstance(schema, str) and schema and schema.strip() == schema:
        return schema
    return None


def main() -> int:
    first = sys.stdin.readline()
    try:
        value = json.loads(first)
        values = _mapping(value, "open request")
        request_id = _request_id(values.get("request_id"))
        if request_id is None or values.get("operation") != "open":
            raise RequestError("first request must be an open handshake")
        client = build_client(values.get("config"))
    except json.JSONDecodeError as error:
        result = _failure(None, "failed", "invalid_json", error)
    except SdkCapabilityMismatch as error:
        result = _failure(
            _request_id(value.get("request_id"))
            if isinstance(value, Mapping)
            else None,
            "failed",
            "nokv_sdk_capability_mismatch",
            error,
        )
    except RequestError as error:
        result = _failure(
            _request_id(value.get("request_id"))
            if isinstance(value, Mapping)
            else None,
            "failed",
            "invalid_config",
            error,
        )
    except _CLIENT_AVAILABILITY_ERRORS:
        result = _opaque_failure(
            request_id,
            "unavailable",
            "nokv_open_unavailable",
            "NoKV client admission is unavailable",
        )
    except (TypeError, ValueError):
        result = _opaque_failure(
            request_id,
            "failed",
            "invalid_config",
            "NoKV helper configuration is invalid",
        )
    else:
        sys.stdout.write(
            json.dumps(
                _response(
                    request_id,
                    "ready",
                    nokv_sdk_version=QUALIFIED_NOKV_SDK_VERSION,
                    nokv_api_version=QUALIFIED_NOKV_API_VERSION,
                    nokv_protocol_schema=_sdk_protocol_schema(),
                ),
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        sys.stdout.flush()
        serve(client, sys.stdin, sys.stdout)
        return 0
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 2


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess E2E
    raise SystemExit(main())
