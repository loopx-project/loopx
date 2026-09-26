"""The operator's own model-provider credential for this machine.

LoopX runs two model surfaces on an operator-supplied credential: the steward
channel a person talks to, and the managed Turn host the steward drives. Both
authenticate with the same pair -- an API key and, when the endpoint is not the
provider default, a base URL -- and both used to read that pair from the service
environment only. An operator therefore could not configure them from a product
surface: changing the key meant editing a launch file and restarting the
service.

This module owns that pair.

It is deliberately not a machine-configuration namespace. The machine
configuration document is projected to the browser, read back by ``describe``
and ``inspect``, and copied into per-transaction backups and rollback plans, so
a secret stored there would be readable from four surfaces and copied by every
unrelated settings change. The credential instead lives in its own file under
the machine runtime root, mode ``0600`` inside a ``0700`` directory, written
atomically, and no readback in this module ever returns its value.

Resolution is field by field, and the machine store outranks the process
environment:

1. this machine's stored credential -- the operator's explicit choice, made in
   a product surface and read back with its own source;
2. the process environment (``DEEPSEEK_API_KEY`` / ``DEEPSEEK_BASE_URL``),
   which stays the bootstrap for a machine whose store is not written yet and
   the escape hatch for a launch file that must override one field.

That order matches the ``steward_executor`` machine setting, where the machine
value also outranks the service environment, so the two machine-level settings
a person edits do not follow two different precedence rules.

A credential authenticates the configuration that runs; it never selects one.
Storing a key here does not move the steward off its resolved endpoint or the
managed host off its resolved execution profile.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..paths import select_default_runtime_root
from ..registry import atomic_write_json, read_json
from .operator_credential import (
    OPERATOR_CREDENTIAL_ENV_VARS,
    OPERATOR_ENDPOINT_ENV_VAR,
)

OPERATOR_PROVIDER_STORE_SCHEMA = "operator_provider_credential_v0"
OPERATOR_PROVIDER_PROJECTION_SCHEMA = "operator_provider_credential_projection_v0"
OPERATOR_PROVIDER_STORE_REF = "machine/credentials/operator_provider.json"

PROVIDER_KEY_FIELD = "provider_key"
BASE_URL_FIELD = "base_url"

SOURCE_MACHINE_STORE = "machine_store"
SOURCE_SERVICE_ENVIRONMENT = "service_environment"
SOURCE_UNSET = "unset"

STATUS_CONFIGURED = "configured"
STATUS_ABSENT = "absent"
STATUS_INVALID = "invalid"

# Bounds, not policy: a credential longer than this is a paste accident, and an
# unbounded value would land in a prompt-adjacent readback and a lock file.
_VALUE_LIMIT = 4096
_BASE_URL_LIMIT = 512
_URL_SCHEMES = ("http://", "https://")

REPAIR_WRITE_OPERATOR_CREDENTIAL = (
    "Open the machine capability settings and store the credential, or set "
    "DEEPSEEK_API_KEY (with DEEPSEEK_BASE_URL when the endpoint is not the "
    "provider default) in the service environment."
)


def machine_runtime_root(runtime_root: Path | None = None) -> Path:
    """Return the machine runtime root this credential is stored under.

    A caller that owns a root passes it. A caller that does not -- an endpoint
    catalogue that reports the machine's own capability, for example -- resolves
    the machine's canonical root, because the credential is a property of the
    machine rather than of the call site.
    """

    if runtime_root is not None:
        return Path(runtime_root).expanduser()
    return select_default_runtime_root()


def operator_provider_store_path(runtime_root: Path | None = None) -> Path:
    return machine_runtime_root(runtime_root) / OPERATOR_PROVIDER_STORE_REF


def _optional_secret(value: Any, *, field: str) -> str | None:
    """Normalize one absent-or-set credential field.

    A blank value and an absent value mean the same thing -- this machine does
    not decide that field -- so both normalize to ``None`` rather than an empty
    string that would read as a configured value. Errors never echo the value.
    """

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > _VALUE_LIMIT:
        raise ValueError(f"operator provider {field} is too long")
    if any(character.isspace() for character in text):
        raise ValueError(f"operator provider {field} must not contain whitespace")
    return text


def _optional_base_url(value: Any) -> str | None:
    text = _optional_secret(value, field=BASE_URL_FIELD)
    if text is None:
        return None
    if len(text) > _BASE_URL_LIMIT:
        raise ValueError("operator provider base_url is too long")
    if not text.startswith(_URL_SCHEMES):
        raise ValueError(
            "operator provider base_url must start with http:// or https://"
        )
    return text.rstrip("/")


def normalize_operator_provider(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one stored credential record, fail closed."""

    unknown = sorted(
        set(raw) - {"schema_version", PROVIDER_KEY_FIELD, BASE_URL_FIELD}
    )
    if unknown:
        raise ValueError(
            "operator provider credential contains unsupported fields: "
            + ", ".join(unknown)
        )
    if raw.get("schema_version") != OPERATOR_PROVIDER_STORE_SCHEMA:
        raise ValueError(
            f"operator provider credential must use {OPERATOR_PROVIDER_STORE_SCHEMA}"
        )
    return {
        "schema_version": OPERATOR_PROVIDER_STORE_SCHEMA,
        PROVIDER_KEY_FIELD: _optional_secret(raw.get(PROVIDER_KEY_FIELD), field=PROVIDER_KEY_FIELD),
        BASE_URL_FIELD: _optional_base_url(raw.get(BASE_URL_FIELD)),
    }


def read_operator_provider(runtime_root: Path | None = None) -> dict[str, Any] | None:
    """Read the stored credential, or ``None`` when this machine has none.

    A malformed record raises instead of resolving to "unconfigured": an
    operator who wrote a credential and then sees the channel fall back to the
    individual executor is owed the difference between "not set" and "set and
    unreadable".
    """

    path = operator_provider_store_path(runtime_root)
    if not path.is_file():
        return None
    return normalize_operator_provider(read_json(path))


def operator_provider_fingerprint(api_key: str | None) -> str | None:
    """Return a stable, non-reversible identifier for a stored key.

    An operator has to be able to tell one key from another -- "is the key I
    just set the one that is running?" -- without any surface being able to read
    the key back. A truncated digest answers that and nothing else.
    """

    if not api_key:
        return None
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


def operator_provider_revision(record: Mapping[str, Any] | None) -> str:
    """Return the revision of one stored record, or ``absent``.

    The revision is derived from the non-secret fields plus the key
    fingerprint, so changing the key changes the revision without the revision
    being a second encoding of the key.
    """

    if record is None:
        return "absent"
    normalized = normalize_operator_provider(record)
    payload = {
        "schema_version": normalized["schema_version"],
        PROVIDER_KEY_FIELD: operator_provider_fingerprint(normalized[PROVIDER_KEY_FIELD]),
        BASE_URL_FIELD: normalized[BASE_URL_FIELD],
    }
    digest = hashlib.sha256(
        repr(sorted(payload.items())).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def _secure_write(path: Path, payload: dict[str, Any]) -> None:
    """Write a credential so only its owner can read it, or not at all."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    atomic_write_json(path, payload)
    path.chmod(0o600)


def write_operator_provider(
    *,
    runtime_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
    api_key: Any = None,
    base_url: Any = None,
    clear_api_key: bool = False,
    clear_base_url: bool = False,
) -> dict[str, Any]:
    """Store one credential update and return its redacted readback.

    The update is a merge, not a replacement: a form that submits only the base
    URL keeps the stored key, and a form that submits only a key keeps the
    stored base URL. Clearing is explicit, so an empty field can never delete a
    credential the operator did not mean to touch.
    """

    stored = read_operator_provider(runtime_root)
    current: dict[str, Any] = dict(stored) if stored is not None else {
        "schema_version": OPERATOR_PROVIDER_STORE_SCHEMA,
        PROVIDER_KEY_FIELD: None,
        BASE_URL_FIELD: None,
    }
    if clear_api_key:
        current[PROVIDER_KEY_FIELD] = None
    elif api_key is not None:
        current[PROVIDER_KEY_FIELD] = api_key
    if clear_base_url:
        current[BASE_URL_FIELD] = None
    elif base_url is not None:
        current[BASE_URL_FIELD] = base_url
    normalized = normalize_operator_provider(current)
    if normalized[PROVIDER_KEY_FIELD] is None and normalized[BASE_URL_FIELD] is None:
        # An empty record configures nothing, and leaving it behind would make
        # "this machine has a credential" true for a file that carries no
        # credential at all.
        return clear_operator_provider(runtime_root, environ=environ)
    _secure_write(operator_provider_store_path(runtime_root), normalized)
    return operator_provider_projection(runtime_root, environ=environ)


def clear_operator_provider(
    runtime_root: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Remove this machine's stored credential and return the readback."""

    path = operator_provider_store_path(runtime_root)
    if path.is_file():
        path.unlink()
    return operator_provider_projection(runtime_root, environ=environ)


def _process_environ(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _environment_value(
    names: tuple[str, ...], environ: Mapping[str, str] | None
) -> tuple[str | None, str]:
    source = _process_environ(environ)
    for name in names:
        value = str(source.get(name, "") or "").strip()
        if value:
            return value, name
    return None, ""


def _field_readback(
    *,
    stored: str | None,
    stored_present: bool,
    env_value: str | None,
    env_name: str,
    secret: bool,
    blocked: bool = False,
) -> dict[str, Any]:
    if blocked:
        # A credential this machine cannot read configures nothing: falling
        # back to the environment here would run a surface on a credential the
        # operator can no longer see in the product surface that owns it.
        value, source = None, SOURCE_UNSET
    elif stored_present:
        value, source = stored, SOURCE_MACHINE_STORE
    elif env_value is not None:
        value, source = env_value, SOURCE_SERVICE_ENVIRONMENT
    else:
        value, source = None, SOURCE_UNSET
    readback: dict[str, Any] = {
        "source": source,
        "configured": value is not None,
    }
    if blocked:
        readback["blocked_by"] = STATUS_INVALID
    if source == SOURCE_SERVICE_ENVIRONMENT:
        readback["env_var"] = env_name
    if secret:
        # The value never leaves this module, whatever its source.
        readback["fingerprint"] = operator_provider_fingerprint(value)
    else:
        readback["value"] = value
    return readback


def operator_provider_projection(
    runtime_root: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return the only readback of the operator credential: redacted.

    This is what a Dashboard, ``loopx machine-config credential status`` and the
    channel readback quote, so it reports where each field came from and whether
    a key is configured -- and never the key itself.
    """

    record: dict[str, Any] | None = None
    unreadable = False
    try:
        record = read_operator_provider(runtime_root)
    except (OSError, TypeError, ValueError):
        unreadable = True
    env_api_key, env_api_key_name = _environment_value(
        OPERATOR_CREDENTIAL_ENV_VARS, environ
    )
    env_base_url, env_base_url_name = _environment_value(
        (OPERATOR_ENDPOINT_ENV_VAR,), environ
    )
    stored_api_key = (record or {}).get(PROVIDER_KEY_FIELD)
    stored_base_url = (record or {}).get(BASE_URL_FIELD)
    projection: dict[str, Any] = {
        "schema_version": OPERATOR_PROVIDER_PROJECTION_SCHEMA,
        "store_ref": OPERATOR_PROVIDER_STORE_REF,
        "store_revision": operator_provider_revision(record),
        "record_present": record is not None,
        PROVIDER_KEY_FIELD: _field_readback(
            stored=str(stored_api_key) if stored_api_key else None,
            stored_present=bool(stored_api_key),
            env_value=env_api_key,
            env_name=env_api_key_name or OPERATOR_CREDENTIAL_ENV_VARS[0],
            secret=True,
            blocked=unreadable,
        ),
        BASE_URL_FIELD: _field_readback(
            stored=str(stored_base_url) if stored_base_url else None,
            stored_present=bool(stored_base_url),
            env_value=env_base_url,
            env_name=env_base_url_name or OPERATOR_ENDPOINT_ENV_VAR,
            secret=False,
            blocked=unreadable,
        ),
    }
    if unreadable:
        status = STATUS_INVALID
    elif projection[PROVIDER_KEY_FIELD]["configured"] or projection[BASE_URL_FIELD][
        "configured"
    ]:
        status = STATUS_CONFIGURED
    else:
        status = STATUS_ABSENT
    projection["status"] = status
    projection["repair"] = (
        REPAIR_WRITE_OPERATOR_CREDENTIAL if status != STATUS_CONFIGURED else ""
    )
    return projection


def operator_credential_source(
    runtime_root: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return where the effective credential came from, as a typed value."""

    projection = operator_provider_projection(runtime_root, environ=environ)
    return str(projection[PROVIDER_KEY_FIELD]["source"])


def operator_provider_environ(
    runtime_root: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return an environment mapping with the stored credential resolved in.

    Every surface that resolves a credential already reads a mapping rather than
    ``os.environ`` directly, so resolving the machine store here is what lets
    the steward channel and the managed host pick up a key an operator stored
    without editing a launch file or restarting a service. The input mapping is
    never mutated.
    """

    resolved = dict(_process_environ(environ))
    try:
        record = read_operator_provider(runtime_root)
    except (OSError, TypeError, ValueError):
        # A damaged record resolves to "no credential" rather than falling back
        # to the environment: authenticating a surface with a credential the
        # operator can no longer see in the product surface that owns it is
        # worse than refusing, and the readback names the fault and its repair.
        resolved.pop(OPERATOR_CREDENTIAL_ENV_VARS[0], None)
        resolved.pop(OPERATOR_ENDPOINT_ENV_VAR, None)
        return resolved
    if record is None:
        return resolved
    api_key = record.get(PROVIDER_KEY_FIELD)
    if api_key:
        resolved[OPERATOR_CREDENTIAL_ENV_VARS[0]] = str(api_key)
    base_url = record.get(BASE_URL_FIELD)
    if base_url:
        resolved[OPERATOR_ENDPOINT_ENV_VAR] = str(base_url)
    return resolved


def operator_provider_host_credential(
    runtime_root: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return only the resolved credential pair, for handing to a child host.

    A managed host runs as its own process and its adapter reads the credential
    from its own environment, so the pair has to travel with the launch. Only
    these names travel: passing on the whole resolved environment would hand a
    child the service's unrelated settings as a side effect of a credential
    change.
    """

    resolved = operator_provider_environ(runtime_root, environ=environ)
    return {
        name: resolved[name]
        for name in (OPERATOR_CREDENTIAL_ENV_VARS[0], OPERATOR_ENDPOINT_ENV_VAR)
        if resolved.get(name)
    }


__all__ = [
    "PROVIDER_KEY_FIELD",
    "BASE_URL_FIELD",
    "OPERATOR_PROVIDER_PROJECTION_SCHEMA",
    "OPERATOR_PROVIDER_STORE_REF",
    "OPERATOR_PROVIDER_STORE_SCHEMA",
    "SOURCE_MACHINE_STORE",
    "SOURCE_SERVICE_ENVIRONMENT",
    "SOURCE_UNSET",
    "STATUS_ABSENT",
    "STATUS_CONFIGURED",
    "STATUS_INVALID",
    "clear_operator_provider",
    "machine_runtime_root",
    "normalize_operator_provider",
    "operator_credential_source",
    "operator_provider_environ",
    "operator_provider_fingerprint",
    "operator_provider_host_credential",
    "operator_provider_projection",
    "operator_provider_revision",
    "operator_provider_store_path",
    "read_operator_provider",
    "write_operator_provider",
]
