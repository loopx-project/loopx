from __future__ import annotations

import copy
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, TypeVar

from ..content_digest import ENVELOPED_SHA256_PATTERN
from ...file_lock import exclusive_cross_runtime_file_lock
from ...paths import GLOBAL_REGISTRY_FILENAME


STRICT_SCHEMA_VERSION = "loopx_project_registry_envelope_v1"
SOURCE_SESSION_SCHEMA_VERSION = "loopx_project_registry_envelope_v2"
CURRENT_WRITER_PROTOCOL = "goal_instance_v1"
SOURCE_SESSION_WRITER_PROTOCOL = "goal_instance_v2"
SOURCE_SESSION_PROFILE_ID = "source_session_v1"
_STRICT_HEADER_KEYS = {
    "schema_version",
    "minimum_writer_protocol",
    "payload_sha256",
}

T = TypeVar("T")


class ProjectRegistryError(ValueError):
    """Base error for an invalid or unsafe project registry operation."""


class ProjectRegistryProtocolError(ProjectRegistryError):
    """The registry requires a writer protocol this package does not support."""


class ProjectRegistryMutationError(ProjectRegistryError):
    """A registry replacement could not be verified and was compensated."""


class ProjectRegistryRestoreError(ProjectRegistryMutationError):
    """Exact-byte compensation could not be verified."""


class _ProjectRegistryFormat(str, Enum):
    LEGACY_OBJECT = "legacy_object_v0"
    STRICT_ENVELOPE_V1 = "strict_envelope_v1"
    STRICT_ENVELOPE_V2 = "strict_envelope_v2"


@dataclass(frozen=True, slots=True)
class _ProjectRegistryDocument:
    payload: dict[str, Any]
    format: _ProjectRegistryFormat
    minimum_writer_protocol: str | None
    raw_bytes: bytes


def _duplicate_rejecting_object(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProjectRegistryError(
                f"strict project registry contains duplicate key: {key}"
            )
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ProjectRegistryError(
        f"strict project registry contains non-finite number: {value}"
    )


def _payload_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _decode_document(raw_bytes: bytes) -> _ProjectRegistryDocument:
    text = raw_bytes.decode("utf-8")
    stripped = text.lstrip()
    if not stripped:
        raise ProjectRegistryError("project registry is empty")

    if stripped.startswith("{"):
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ProjectRegistryError(
                "legacy project registry root must be a JSON object"
            )
        return _ProjectRegistryDocument(
            payload=payload,
            format=_ProjectRegistryFormat.LEGACY_OBJECT,
            minimum_writer_protocol=None,
            raw_bytes=raw_bytes,
        )

    if not stripped.startswith("["):
        raise ProjectRegistryError(
            "project registry root must be a JSON object or strict envelope"
        )

    root = json.loads(
        text,
        object_pairs_hook=_duplicate_rejecting_object,
        parse_constant=_reject_non_finite,
    )
    if not isinstance(root, list) or len(root) != 2:
        raise ProjectRegistryError(
            "strict project registry envelope must contain exactly two elements"
        )
    header, payload = root
    if not isinstance(header, dict) or set(header) != _STRICT_HEADER_KEYS:
        raise ProjectRegistryError(
            "strict project registry header must contain exactly "
            "schema_version, minimum_writer_protocol, and payload_sha256"
        )
    schema_version = header["schema_version"]
    strict_formats = {
        STRICT_SCHEMA_VERSION: _ProjectRegistryFormat.STRICT_ENVELOPE_V1,
        SOURCE_SESSION_SCHEMA_VERSION: _ProjectRegistryFormat.STRICT_ENVELOPE_V2,
    }
    if schema_version not in strict_formats:
        raise ProjectRegistryError(
            "strict project registry schema_version is unsupported"
        )
    protocol = header["minimum_writer_protocol"]
    if not isinstance(protocol, str) or not protocol:
        raise ProjectRegistryError(
            "strict project registry minimum_writer_protocol must be nonempty"
        )
    digest = header["payload_sha256"]
    if not isinstance(digest, str) or not ENVELOPED_SHA256_PATTERN.fullmatch(digest):
        raise ProjectRegistryError(
            "strict project registry payload_sha256 is malformed"
        )
    if not isinstance(payload, dict):
        raise ProjectRegistryError(
            "strict project registry payload must be a JSON object"
        )
    try:
        actual_digest = _payload_digest(payload)
    except (TypeError, ValueError) as exc:
        raise ProjectRegistryError(
            "strict project registry payload is not canonical JSON"
        ) from exc
    if digest != actual_digest:
        raise ProjectRegistryError(
            "strict project registry payload digest does not match"
        )
    document_format = strict_formats[schema_version]
    if (
        document_format is _ProjectRegistryFormat.STRICT_ENVELOPE_V2
        and payload.get("profile_id") != SOURCE_SESSION_PROFILE_ID
    ):
        raise ProjectRegistryError(
            "strict v2 project registry profile_id is unsupported"
        )
    return _ProjectRegistryDocument(
        payload=payload,
        format=document_format,
        minimum_writer_protocol=protocol,
        raw_bytes=raw_bytes,
    )


def _read_document(path: Path) -> _ProjectRegistryDocument:
    return _decode_document(path.read_bytes())


def load_project_registry(path: Path) -> dict[str, Any]:
    """Load either supported project-registry wire format."""

    return _read_document(path.expanduser()).payload


def decode_project_registry(raw_bytes: bytes) -> dict[str, Any]:
    """Decode one project-registry snapshot without rereading its path."""

    return _decode_document(raw_bytes).payload


def decode_registry_snapshot(path: Path, raw_bytes: bytes) -> dict[str, Any]:
    """Decode one registry snapshot while keeping global registries object-only."""

    if path.expanduser().name == GLOBAL_REGISTRY_FILENAME:
        payload = json.loads(raw_bytes)
        if not isinstance(payload, dict):
            raise ProjectRegistryError("global registry root must be a JSON object")
        return payload
    payload = decode_project_registry(raw_bytes)
    require_runtime_compatible_project_registry(
        payload,
        operation="generic registry read",
    )
    return payload


def load_registry(path: Path) -> dict[str, Any]:
    """Load a project registry or an object-only global registry."""

    expanded = path.expanduser()
    if not expanded.exists():
        return {}
    return decode_registry_snapshot(expanded, expanded.read_bytes())


def require_runtime_compatible_project_registry(
    payload: dict[str, Any],
    *,
    operation: str,
) -> None:
    """Reject the lifecycle-only M2 profile before host or business effects."""

    if payload.get("profile_id") == SOURCE_SESSION_PROFILE_ID:
        raise ProjectRegistryProtocolError(
            f"{operation} rejects lifecycle-only profile "
            f"{SOURCE_SESSION_PROFILE_ID}; use project lifecycle commands"
        )


def _encode_document(
    payload: dict[str, Any],
    *,
    format: _ProjectRegistryFormat,
    minimum_writer_protocol: str | None,
) -> bytes:
    if not isinstance(payload, dict):
        raise TypeError("project registry payload must be a JSON object")
    if format is _ProjectRegistryFormat.LEGACY_OBJECT:
        root: object = payload
        allow_nan = True
    else:
        schema_version = (
            STRICT_SCHEMA_VERSION
            if format is _ProjectRegistryFormat.STRICT_ENVELOPE_V1
            else SOURCE_SESSION_SCHEMA_VERSION
        )
        root = [
            {
                "schema_version": schema_version,
                "minimum_writer_protocol": minimum_writer_protocol,
                "payload_sha256": _payload_digest(payload),
            },
            payload,
        ]
        allow_nan = False
    return (
        json.dumps(
            root,
            ensure_ascii=False,
            allow_nan=allow_nan,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _atomic_write_bytes(path: Path, payload: bytes, *, mode: int | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _require_supported_writer(document: _ProjectRegistryDocument) -> None:
    protocol = document.minimum_writer_protocol
    if (
        document.format is not _ProjectRegistryFormat.LEGACY_OBJECT
        and protocol != CURRENT_WRITER_PROTOCOL
    ):
        raise ProjectRegistryProtocolError(
            "project registry requires unsupported writer protocol: "
            f"{protocol}"
        )


def _require_source_session_writer(document: _ProjectRegistryDocument) -> None:
    if (
        document.format is not _ProjectRegistryFormat.STRICT_ENVELOPE_V2
        or document.minimum_writer_protocol != SOURCE_SESSION_WRITER_PROTOCOL
        or document.payload.get("profile_id") != SOURCE_SESSION_PROFILE_ID
    ):
        raise ProjectRegistryProtocolError(
            "source-session transaction requires "
            f"{SOURCE_SESSION_SCHEMA_VERSION}, "
            f"{SOURCE_SESSION_WRITER_PROTOCOL}, and "
            f"profile_id={SOURCE_SESSION_PROFILE_ID}"
        )


class ProjectRegistryTransaction:
    def __init__(
        self,
        path: Path,
        *,
        document: _ProjectRegistryDocument,
        existed: bool,
        mode: int | None,
    ) -> None:
        self._path = path
        self._document = document
        self._existed = existed
        self._mode = mode
        self._finished = False

    def payload_copy(self) -> dict[str, Any]:
        return copy.deepcopy(self._document.payload)

    def commit(self, payload: dict[str, Any]) -> bool:
        if self._finished:
            raise RuntimeError("project registry transaction is already finished")
        if not isinstance(payload, dict):
            raise TypeError("project registry payload must be a JSON object")
        wrote = not self._existed or payload != self._document.payload
        if not wrote:
            self._finished = True
            return False

        encoded = _encode_document(
            payload,
            format=self._document.format,
            minimum_writer_protocol=self._document.minimum_writer_protocol,
        )
        try:
            _atomic_write_bytes(self._path, encoded, mode=self._mode)
            readback = _read_document(self._path)
            if (
                readback.format is not self._document.format
                or readback.minimum_writer_protocol
                != self._document.minimum_writer_protocol
                or readback.payload != payload
            ):
                raise ProjectRegistryMutationError(
                    "project registry readback did not match committed payload"
                )
        except Exception as exc:
            try:
                self.restore()
            except Exception as restore_exc:
                raise ProjectRegistryRestoreError(
                    "project registry readback failed and exact-byte restore failed"
                ) from restore_exc
            raise ProjectRegistryMutationError(
                "project registry write readback failed; exact preimage restored"
            ) from exc
        self._finished = True
        return True

    def remove(self) -> bool:
        if self._finished:
            raise RuntimeError("project registry transaction is already finished")
        if not self._path.exists():
            self._finished = True
            return False
        self._path.unlink()
        if self._path.exists():
            raise ProjectRegistryMutationError(
                "project registry removal readback failed"
            )
        self._finished = True
        return True

    def restore(self) -> None:
        if self._existed:
            _atomic_write_bytes(
                self._path,
                self._document.raw_bytes,
                mode=self._mode,
            )
            if self._path.read_bytes() != self._document.raw_bytes:
                raise ProjectRegistryRestoreError(
                    "project registry exact-byte restore did not verify"
                )
        else:
            self._path.unlink(missing_ok=True)
            if self._path.exists():
                raise ProjectRegistryRestoreError(
                    "new project registry could not be removed during restore"
                )
        self._finished = True


@contextmanager
def _registry_transaction(
    path: Path,
    *,
    operation: str,
    create_document: Callable[[], _ProjectRegistryDocument] | None,
    require_writer: Callable[[_ProjectRegistryDocument], None],
    agent_id: str | None = None,
) -> Iterator[ProjectRegistryTransaction]:
    expanded = path.expanduser()
    with exclusive_cross_runtime_file_lock(
        expanded,
        agent_id=agent_id,
        operation=operation,
    ):
        existed = expanded.exists()
        if existed:
            document = _read_document(expanded)
            mode = expanded.stat().st_mode & 0o777
        else:
            if create_document is None:
                raise FileNotFoundError(
                    f"registry file does not exist: {expanded}"
                )
            document = create_document()
            mode = None
        require_writer(document)
        yield ProjectRegistryTransaction(
            expanded,
            document=document,
            existed=existed,
            mode=mode,
        )


def _created_document(
    create: Callable[[], dict[str, Any]],
    *,
    format: _ProjectRegistryFormat,
    minimum_writer_protocol: str | None,
) -> _ProjectRegistryDocument:
    payload = create()
    if not isinstance(payload, dict):
        raise TypeError("project registry initializer must return a JSON object")
    return _ProjectRegistryDocument(
        payload=copy.deepcopy(payload),
        format=format,
        minimum_writer_protocol=minimum_writer_protocol,
        raw_bytes=b"",
    )


def _document_factory(
    create: Callable[[], dict[str, Any]] | None,
    *,
    format: _ProjectRegistryFormat,
    minimum_writer_protocol: str | None,
) -> Callable[[], _ProjectRegistryDocument] | None:
    if create is None:
        return None

    def build() -> _ProjectRegistryDocument:
        return _created_document(
            create,
            format=format,
            minimum_writer_protocol=minimum_writer_protocol,
        )

    return build


@contextmanager
def project_registry_transaction(
    path: Path,
    *,
    operation: str,
    create: Callable[[], dict[str, Any]] | None = None,
    agent_id: str | None = None,
) -> Iterator[ProjectRegistryTransaction]:
    """Hold one legacy/v1 project-registry transaction."""

    create_document = _document_factory(
        create,
        format=_ProjectRegistryFormat.LEGACY_OBJECT,
        minimum_writer_protocol=None,
    )
    with _registry_transaction(
        path,
        operation=operation,
        create_document=create_document,
        require_writer=_require_supported_writer,
        agent_id=agent_id,
    ) as transaction:
        yield transaction


@contextmanager
def source_session_registry_transaction(
    path: Path,
    *,
    operation: str,
    create: Callable[[], dict[str, Any]] | None = None,
    agent_id: str | None = None,
) -> Iterator[ProjectRegistryTransaction]:
    """Hold one source-session v2 project-registry transaction."""

    create_document = _document_factory(
        create,
        format=_ProjectRegistryFormat.STRICT_ENVELOPE_V2,
        minimum_writer_protocol=SOURCE_SESSION_WRITER_PROTOCOL,
    )
    with _registry_transaction(
        path,
        operation=operation,
        create_document=create_document,
        require_writer=_require_source_session_writer,
        agent_id=agent_id,
    ) as transaction:
        yield transaction


def mutate_project_registry(
    path: Path,
    *,
    operation: str,
    reducer: Callable[[dict[str, Any]], T],
    create: Callable[[], dict[str, Any]] | None = None,
    agent_id: str | None = None,
) -> T:
    """Apply one locked, format-preserving project-registry reduction."""

    with project_registry_transaction(
        path,
        operation=operation,
        create=create,
        agent_id=agent_id,
    ) as transaction:
        payload = transaction.payload_copy()
        result = reducer(payload)
        transaction.commit(payload)
        return result


def add_project_registry_backend(path: Path, backend: str) -> bool:
    """Add one backend marker without exposing the registry wire format."""

    normalized = str(backend or "").strip()
    if not normalized:
        raise ValueError("project registry backend is required")

    def reduce(payload: dict[str, Any]) -> bool:
        backends = payload.setdefault("agent_backends", [])
        if not isinstance(backends, list):
            raise ValueError("project registry agent_backends must be a list")
        if normalized in backends:
            return False
        backends.append(normalized)
        return True

    return mutate_project_registry(
        path,
        operation=f"add_project_registry_backend_{normalized}",
        reducer=reduce,
    )
