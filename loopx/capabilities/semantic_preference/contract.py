from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from ...public_safe_text import (
    MODULE_QUALIFIED_SURFACE_PATTERN as SURFACE_RE,
    PUBLIC_SAFE_REFERENCE_PATTERN as TOKEN_RE,
)
from ...extensions.manifest import validate_extension_id
from ...extensions.runtime import (
    default_extension_state_file,
    doctor_installed_extension,
    resolve_capability_binding,
    resolve_capability_extension_id,
    resolve_extension_binding,
)


CONFIG_SCHEMA = "semantic_preference_hook_config_v0"
REQUEST_SCHEMA = "semantic_preference_provider_request_v0"
RESPONSE_SCHEMA = "semantic_preference_provider_response_v0"
RECALL_SCHEMA = "semantic_preference_recall_v0"
RECEIPT_SCHEMA = "semantic_preference_application_receipt_v0"
MAINTENANCE_GUIDANCE_SCHEMA = "semantic_preference_maintenance_guidance_v0"
MAINTENANCE_RECEIPT_SCHEMA = "semantic_preference_maintenance_receipt_v0"
DOCTOR_SCHEMA = "semantic_preference_provider_doctor_v0"
CORPUS_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
MAX_PROVIDER_OUTPUT_BYTES = 256_000
MAX_ITEM_BYTES = 8_000
MAX_RECEIPT_REFS = 20
MAX_CORPORA = 20
MAX_SETUP_HINT_LENGTH = 1_000
MAX_PROVIDER_ARGS = 64
SEMANTIC_PREFERENCE_PROTOCOL = "semantic_preference_provider_v0"
SEMANTIC_PREFERENCE_PERMISSION = "semantic_preference.read"

_READ_ROLES = {"primary", "fallback", "reference"}
_WRITE_MODES = {"read_only", "provider_managed"}
_WRITEBACK_TRIGGERS = {"explicit_feedback", "source_truth_changed"}
_CLOSURE_POLICIES = {"none", "write_wait_l2_read_scoped_recall"}


def _surface(value: object) -> str:
    result = str(value or "").strip()
    if not SURFACE_RE.fullmatch(result):
        raise ValueError(
            "surface must be a module-qualified token such as pr_review.reply"
        )
    return result


def _config(config: str | Path, project: str | Path) -> dict[str, Any]:
    root = Path(project).expanduser().resolve()
    path = Path(config).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("project directory does not exist")
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = None
    if relative is not None:
        tracked = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--error-unmatch",
                "--",
                str(relative),
            ],
            capture_output=True,
            check=False,
        )
        ignored = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "--quiet", "--", str(relative)],
            capture_output=True,
            check=False,
        )
        if tracked.returncode == 0 or ignored.returncode != 0:
            raise ValueError("config inside the project must be ignored and untracked")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("config must be a readable JSON object") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != CONFIG_SCHEMA:
        raise ValueError(f"config schema_version must be {CONFIG_SCHEMA}")
    if not isinstance(payload.get("enabled", False), bool):
        raise ValueError("config enabled must be a boolean")
    return payload


def _context(values: Sequence[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values or []:
        key, separator, item = value.partition("=")
        if not separator or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key):
            raise ValueError("context must use lower-snake key=value syntax")
        if len(item) > 500:
            raise ValueError(f"context value for {key} exceeds 500 characters")
        result[key] = item
    return result


def _provider(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    provider = payload.get("provider")
    if not isinstance(provider, Mapping):
        raise ValueError("enabled config requires a provider object")
    return provider


def _string_argv(value: object, *, label: str, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (
        not allow_empty and not value
    ) or len(value) > MAX_PROVIDER_ARGS:
        raise ValueError(f"{label} must be a bounded string list")
    if not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{label} must be a bounded string list")
    return list(value)


def _extension_id(provider: Mapping[str, Any]) -> str | None:
    raw = provider.get("extension_id")
    if raw is None:
        return None
    extension_id = validate_extension_id(str(raw))
    if provider.get("argv") is not None:
        raise ValueError("provider argv and extension_id are mutually exclusive")
    return extension_id


def _extension_state_file(
    provider: Mapping[str, Any],
    runtime_root: str | Path | None = None,
) -> Path:
    value = provider.get("extension_state_file")
    if value is None:
        return default_extension_state_file(runtime_root)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("provider extension_state_file must be a non-empty path")
    return Path(value).expanduser()


def _uses_extension_provider(provider: Mapping[str, Any]) -> bool:
    return _extension_id(provider) is not None or provider.get("argv") is None


def _resolved_extension_id(
    provider: Mapping[str, Any],
    *,
    runtime_root: str | Path | None = None,
) -> str:
    extension_id = _extension_id(provider)
    if extension_id is not None:
        return extension_id
    return resolve_capability_extension_id(
        state_file=_extension_state_file(provider, runtime_root),
        capability_id="semantic-preference",
        protocol=SEMANTIC_PREFERENCE_PROTOCOL,
    )


def _provider_binding(
    provider: Mapping[str, Any],
    *,
    runtime_root: str | Path | None = None,
) -> tuple[list[str], int]:
    if _uses_extension_provider(provider):
        if provider.get("probe_argv") is not None:
            raise ValueError("extension providers use the extension doctor")
        if provider.get("timeout_seconds") is not None:
            raise ValueError("extension providers use the manifest timeout_seconds")
        args = _string_argv(
            provider.get("args", []),
            label="provider args",
            allow_empty=True,
        )
        extension_id = _extension_id(provider)
        binding = (
            resolve_extension_binding(
                extension_id,
                state_file=_extension_state_file(provider, runtime_root),
                capability_id="semantic-preference",
                protocol=SEMANTIC_PREFERENCE_PROTOCOL,
                permission=SEMANTIC_PREFERENCE_PERMISSION,
            )
            if extension_id is not None
            else resolve_capability_binding(
                state_file=_extension_state_file(provider, runtime_root),
                capability_id="semantic-preference",
                protocol=SEMANTIC_PREFERENCE_PROTOCOL,
                permission=SEMANTIC_PREFERENCE_PERMISSION,
            )
        )
        return [*[str(value) for value in binding["argv"]], *args], int(
            binding["timeout_seconds"]
        )

    argv = _string_argv(provider.get("argv"), label="provider argv")
    try:
        timeout = int(provider.get("timeout_seconds", 30))
    except (TypeError, ValueError) as exc:
        raise ValueError("provider timeout_seconds must be an integer") from exc
    if not 1 <= timeout <= 120:
        raise ValueError("provider timeout_seconds must be between 1 and 120")
    return argv, timeout


def _command_available(command: str) -> bool:
    if "/" in command or "\\" in command:
        path = Path(command).expanduser()
        return path.is_file() and path.stat().st_mode & 0o111 != 0
    return shutil.which(command) is not None


def _setup_hints(provider: Mapping[str, Any]) -> dict[str, str]:
    raw = provider.get("setup_hints")
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("provider setup_hints must be an object")
    result: dict[str, str] = {}
    for key in ("install", "configure"):
        value = str(raw.get(key) or "").strip()
        if len(value) > MAX_SETUP_HINT_LENGTH:
            raise ValueError(f"provider setup_hints.{key} exceeds 1000 characters")
        if value:
            result[key] = value
    return result


def provider_doctor(
    config: str | Path,
    *,
    project: str | Path,
    runtime_root: str | Path | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    """Inspect an optional provider without installing or configuring it."""

    payload = _config(config, project)
    if not payload.get("enabled", False):
        return {
            "ok": True,
            "schema_version": DOCTOR_SCHEMA,
            "status": "disabled",
            "available": False,
            "verified": False,
            "external_writes_performed": False,
        }
    provider = _provider(payload)
    provider_id = str(provider.get("id") or "configured_provider").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", provider_id):
        raise ValueError("provider id must use lower-snake token syntax")
    hints = _setup_hints(provider)
    if _uses_extension_provider(provider):
        if provider.get("probe_argv") is not None:
            raise ValueError("extension providers use the extension doctor")
        doctor = doctor_installed_extension(
            _resolved_extension_id(provider, runtime_root=runtime_root),
            state_file=_extension_state_file(provider, runtime_root),
            execute=execute,
        )
        return {
            "ok": True,
            "schema_version": DOCTOR_SCHEMA,
            "status": doctor["status"],
            "provider_id": provider_id,
            "available": doctor["available"],
            "verified": doctor["verified"],
            "probe_configured": True,
            "failure_kind": doctor.get("failure_kind"),
            "setup_hints": hints,
            "automatic_setup_performed": False,
            "credential_writes_performed": False,
            "service_writes_performed": False,
            "external_writes_performed": False,
        }

    argv, _ = _provider_binding(provider, runtime_root=runtime_root)
    command_available = _command_available(argv[0])
    probe_argv = provider.get("probe_argv")
    if probe_argv is not None and (
        not isinstance(probe_argv, list)
        or not probe_argv
        or not all(isinstance(value, str) and value for value in probe_argv)
    ):
        raise ValueError("provider probe_argv must be a non-empty string list")
    status = "ready" if command_available else "provider_missing"
    available = command_available
    verified = False
    failure_kind = None
    if command_available and probe_argv and not execute:
        status = "probe_required"
    elif command_available and probe_argv and execute:
        try:
            completed = subprocess.run(
                probe_argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            completed = None
            failure_kind = "probe_execution_failed"
        if completed is None or completed.returncode != 0:
            status = "provider_unavailable"
            available = False
            failure_kind = failure_kind or "probe_nonzero_exit"
        else:
            status = "ready"
            verified = True
    return {
        "ok": True,
        "schema_version": DOCTOR_SCHEMA,
        "status": status,
        "provider_id": provider_id,
        "available": available,
        "verified": verified,
        "probe_configured": bool(probe_argv),
        "failure_kind": failure_kind,
        "setup_hints": hints,
        "automatic_setup_performed": False,
        "credential_writes_performed": False,
        "service_writes_performed": False,
        "external_writes_performed": False,
    }


def _unavailable(surface: str, policy: str, kind: str) -> dict[str, Any]:
    if policy == "fail_closed":
        raise ValueError(f"semantic preference provider unavailable: {kind}")
    return {
        "ok": True,
        "schema_version": RECALL_SCHEMA,
        "status": "provider_unavailable",
        "surface": surface,
        "items": [],
        "failure_kind": kind,
    }


def _corpus_inventory(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = response.get("corpus_inventory")
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_CORPORA:
        raise ValueError("provider corpus_inventory must be a bounded list")
    inventory: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("provider corpus_inventory items must be objects")
        corpus_id = str(item.get("corpus_id") or "").strip()
        scope_ref = str(item.get("scope_ref") or "").strip()
        read_role = str(item.get("read_role") or "").strip()
        write_mode = str(item.get("write_mode") or "").strip()
        source_of_truth = str(item.get("source_of_truth") or "").strip()
        closure_policy = str(item.get("closure_policy") or "none").strip()
        write_actor_ref = str(item.get("write_actor_ref") or "").strip()
        triggers = item.get("writeback_triggers") or []
        if not CORPUS_ID_RE.fullmatch(corpus_id) or corpus_id in seen:
            raise ValueError("provider corpus_id must be unique lower-snake tokens")
        if not TOKEN_RE.fullmatch(scope_ref):
            raise ValueError("provider corpus scope_ref must be a compact token")
        if read_role not in _READ_ROLES or write_mode not in _WRITE_MODES:
            raise ValueError("provider corpus read_role or write_mode is invalid")
        if not TOKEN_RE.fullmatch(source_of_truth):
            raise ValueError("provider corpus source_of_truth must be a compact token")
        if closure_policy not in _CLOSURE_POLICIES:
            raise ValueError("provider corpus closure_policy is invalid")
        if write_actor_ref and not TOKEN_RE.fullmatch(write_actor_ref):
            raise ValueError("provider corpus write_actor_ref must be a compact token")
        if (
            not isinstance(triggers, list)
            or len(triggers) > len(_WRITEBACK_TRIGGERS)
            or any(trigger not in _WRITEBACK_TRIGGERS for trigger in triggers)
        ):
            raise ValueError("provider corpus writeback_triggers are invalid")
        seen.add(corpus_id)
        inventory.append(
            {
                "corpus_id": corpus_id,
                "scope_ref": scope_ref,
                "read_role": read_role,
                "write_mode": write_mode,
                "write_actor_ref": write_actor_ref or None,
                "source_of_truth": source_of_truth,
                "writeback_triggers": sorted(set(triggers)),
                "closure_policy": closure_policy,
            }
        )
    return inventory


def _maintenance_guidance(
    inventory: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    writable = [item for item in inventory if item.get("write_mode") != "read_only"]
    if not writable:
        return None
    return {
        "schema_version": MAINTENANCE_GUIDANCE_SCHEMA,
        "corpus_ids": [str(item["corpus_id"]) for item in writable],
        "writeback_triggers": sorted(
            {
                str(trigger)
                for item in writable
                for trigger in item.get("writeback_triggers") or []
            }
        ),
        "closure_outcomes": ["verified", "no_write_rationale"],
        "completion": "provider_write_then_wait_l2_read_and_scoped_recall",
    }


def recall(
    config: str | Path,
    *,
    project: str | Path,
    surface: str,
    context: Sequence[str] | None = None,
    runtime_root: str | Path | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    payload = _config(config, project)
    surface_id = _surface(surface)
    if not payload.get("enabled", False):
        return {
            "ok": True,
            "schema_version": RECALL_SCHEMA,
            "status": "disabled",
            "surface": surface_id,
            "items": [],
        }
    surfaces = payload.get("surfaces")
    if not isinstance(surfaces, Mapping) or not isinstance(
        surfaces.get(surface_id), Mapping
    ):
        raise ValueError(f"surface is not configured: {surface_id}")
    surface_config = surfaces[surface_id]
    query = str(surface_config.get("query") or "").strip()
    policy = str(surface_config.get("failure_policy") or "fail_open")
    try:
        limit = int(surface_config.get("limit", 5))
    except (TypeError, ValueError) as exc:
        raise ValueError("surface limit must be an integer") from exc
    if not 1 <= len(query) <= 500 or not 1 <= limit <= 20:
        raise ValueError("surface query or limit is outside the bounded contract")
    if policy not in {"fail_open", "fail_closed"}:
        raise ValueError("failure_policy must be fail_open or fail_closed")
    provider = _provider(payload)
    try:
        argv, timeout = _provider_binding(provider, runtime_root=runtime_root)
    except ValueError:
        if execute and _uses_extension_provider(provider):
            return _unavailable(surface_id, policy, "extension_binding_unavailable")
        raise
    request = {
        "schema_version": REQUEST_SCHEMA,
        "surface": surface_id,
        "query": query,
        "limit": limit,
        "context": _context(context),
    }
    if not execute:
        return {
            "ok": True,
            "schema_version": RECALL_SCHEMA,
            "status": "preview_ready",
            "surface": surface_id,
            "request": request,
        }
    try:
        completed = subprocess.run(
            argv,
            input=json.dumps(request, ensure_ascii=False),
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _unavailable(surface_id, policy, "execution_failed")
    if completed.returncode != 0:
        return _unavailable(surface_id, policy, "nonzero_exit")
    if len(completed.stdout.encode("utf-8")) > MAX_PROVIDER_OUTPUT_BYTES:
        return _unavailable(surface_id, policy, "response_too_large")
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return _unavailable(surface_id, policy, "invalid_json")
    items = response.get("items") if isinstance(response, Mapping) else None
    if (
        not isinstance(response, Mapping)
        or response.get("schema_version") != RESPONSE_SCHEMA
        or not isinstance(items, list)
        or not all(isinstance(item, Mapping) for item in items)
    ):
        return _unavailable(surface_id, policy, "invalid_response")
    bounded = [dict(item) for item in items[:limit]]
    if any(
        len(json.dumps(item, ensure_ascii=False).encode("utf-8")) > MAX_ITEM_BYTES
        for item in bounded
    ):
        return _unavailable(surface_id, policy, "item_too_large")
    try:
        inventory = _corpus_inventory(response)
    except ValueError:
        return _unavailable(surface_id, policy, "invalid_corpus_inventory")
    return {
        "ok": True,
        "schema_version": RECALL_SCHEMA,
        "status": "completed",
        "surface": surface_id,
        "items": bounded,
        "truncated": len(items) > len(bounded),
        "corpus_inventory": inventory,
        "maintenance_guidance": _maintenance_guidance(inventory),
    }


def _token(value: object, label: str) -> str:
    result = str(value or "").strip()
    if not TOKEN_RE.fullmatch(result):
        raise ValueError(f"{label} must be a compact public-safe token")
    return result


def application_receipt(
    *,
    surface: str,
    application_id: str,
    outcome: str,
    preference_refs: Sequence[str] | None = None,
    artifact_ref: str | None = None,
) -> dict[str, Any]:
    if outcome not in {"applied", "ignored", "failed"}:
        raise ValueError("outcome must be applied, ignored, or failed")
    refs = [str(ref) for ref in preference_refs or [] if str(ref).strip()]
    if len(refs) > MAX_RECEIPT_REFS:
        raise ValueError(f"preference_refs supports at most {MAX_RECEIPT_REFS} items")
    return {
        "schema_version": RECEIPT_SCHEMA,
        "surface": _surface(surface),
        "application_id": _token(application_id, "application_id"),
        "outcome": outcome,
        "preference_ref_digests": sorted(
            {hashlib.sha256(str(ref).encode()).hexdigest()[:16] for ref in refs}
        ),
        "artifact_ref": _token(artifact_ref, "artifact_ref") if artifact_ref else None,
    }


def maintenance_receipt(
    *,
    trigger: str,
    outcome: str,
    corpus_ids: Sequence[str],
    scope_refs: Sequence[str] | None = None,
    evidence_ref: str | None = None,
) -> dict[str, Any]:
    if trigger not in _WRITEBACK_TRIGGERS:
        raise ValueError("trigger must be explicit_feedback or source_truth_changed")
    if outcome not in {"verified", "no_write_rationale", "failed"}:
        raise ValueError("outcome must be verified, no_write_rationale, or failed")
    ids = sorted({str(value or "").strip() for value in corpus_ids})
    if not ids or len(ids) > MAX_CORPORA or any(
        not CORPUS_ID_RE.fullmatch(value) for value in ids
    ):
        raise ValueError("corpus_ids must contain bounded lower-snake tokens")
    refs = [str(ref).strip() for ref in scope_refs or [] if str(ref).strip()]
    if len(refs) > MAX_CORPORA:
        raise ValueError(f"scope_refs supports at most {MAX_CORPORA} items")
    if any(not TOKEN_RE.fullmatch(ref) for ref in refs):
        raise ValueError("scope_refs must contain compact public-safe tokens")
    return {
        "schema_version": MAINTENANCE_RECEIPT_SCHEMA,
        "trigger": trigger,
        "outcome": outcome,
        "corpus_ids": ids,
        "scope_ref_digests": sorted(
            {hashlib.sha256(ref.encode()).hexdigest()[:16] for ref in refs}
        ),
        "evidence_ref": _token(evidence_ref, "evidence_ref") if evidence_ref else None,
    }
