from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse
from urllib.request import urlopen

from ..control_plane.content_digest import BARE_SHA256_PATTERN
from .public_safety import (
    PUBLIC_BOUNDARY_PATTERNS,
    public_safe_boundary,
)


STATIC_SITE_MANIFEST_VERSION = "loopx_static_site_presentation_manifest_v0"
STATIC_SITE_REVISION_VERSION = "loopx_static_site_presentation_revision_v0"
STATIC_SITE_RECEIPT_VERSION = "loopx_static_site_deploy_receipt_v0"
STATIC_SITE_EVENT_VERSION = "loopx_static_site_deploy_event_v0"

MANIFEST_FILE = ".loopx-static-site-manifest.json"
REVISION_FILE = ".loopx-static-site-revision.json"
RECEIPT_FILE = ".loopx-static-site-receipt.json"
RECEIPT_LOG_FILE = "receipts.jsonl"
REVISION_DIR = "revisions"

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_TEXT_SUFFIXES = {".css", ".html", ".js", ".json", ".md", ".svg", ".txt", ".xml"}
_PUBLIC_BOUNDARY_PATTERNS = PUBLIC_BOUNDARY_PATTERNS
_RESERVED_TOP_LEVEL = {MANIFEST_FILE, RECEIPT_FILE, REVISION_FILE, REVISION_DIR}


class StaticSiteContractError(ValueError):
    """Raised when a static presentation input violates the publish contract."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_identifier(value: str, *, label: str) -> str:
    normalized = str(value or "").strip()
    if not _SAFE_ID.fullmatch(normalized) or ".." in normalized:
        raise StaticSiteContractError(
            f"{label} must be 1-64 public-safe characters: letters, digits, dot, underscore, or hyphen"
        )
    return normalized


def _validate_entry_path(value: str) -> str:
    path = Path(str(value or "").strip())
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise StaticSiteContractError(
            "entry_path must be a relative path inside the site bundle"
        )
    return path.as_posix()


def _paths_overlap(left: Path, right: Path) -> bool:
    left_resolved = left.resolve()
    right_resolved = right.resolve()
    return (
        left_resolved == right_resolved
        or left_resolved in right_resolved.parents
        or right_resolved in left_resolved.parents
    )


def _scan_public_text(path: Path, relative_path: str) -> None:
    if path.suffix.lower() not in _TEXT_SUFFIXES or path.stat().st_size > 2_000_000:
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    for label, pattern in _PUBLIC_BOUNDARY_PATTERNS:
        if pattern.search(text):
            raise StaticSiteContractError(
                f"public boundary scan failed for {relative_path}: {label}"
            )


def _collect_site_files(site_dir: Path, *, entry_path: str) -> list[dict[str, object]]:
    if not site_dir.is_dir():
        raise StaticSiteContractError(
            f"site_dir does not exist or is not a directory: {site_dir}"
        )
    if not (site_dir / entry_path).is_file():
        raise StaticSiteContractError(f"site entry is missing: {entry_path}")

    files: list[dict[str, object]] = []
    for path in sorted(site_dir.rglob("*")):
        if path.is_symlink():
            raise StaticSiteContractError(
                f"site bundle must not contain symlinks: {path.relative_to(site_dir)}"
            )
        if not path.is_file():
            continue
        relative_path = path.relative_to(site_dir).as_posix()
        if relative_path.split("/", 1)[0] in _RESERVED_TOP_LEVEL:
            raise StaticSiteContractError(
                f"site bundle uses reserved path: {relative_path}"
            )
        _scan_public_text(path, relative_path)
        content = path.read_bytes()
        files.append(
            {
                "path": relative_path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        )
    if not files:
        raise StaticSiteContractError("site bundle must contain at least one file")
    return files


def _semantic_digest(files: list[dict[str, object]]) -> str:
    encoded = json.dumps(
        files, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _normalize_base_url(base_url: str | None) -> str:
    value = str(base_url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise StaticSiteContractError(
            "github-pages publisher requires a clean https --base-url"
        )
    return value if value.endswith("/") else f"{value}/"


def _publisher_projection(
    *,
    publisher_kind: str,
    base_url: str | None,
    revision: str,
) -> dict[str, object]:
    if publisher_kind == "local":
        if base_url:
            raise StaticSiteContractError("local publisher does not accept --base-url")
        return {
            "kind": "local",
            "base_url": None,
            "latest_url": "./",
            "revision_url": f"./{REVISION_DIR}/{quote(revision, safe='._-')}/",
            "http_readback_required": False,
        }
    if publisher_kind == "github-pages":
        normalized = _normalize_base_url(base_url)
        return {
            "kind": "github-pages",
            "base_url": normalized,
            "latest_url": normalized,
            "revision_url": urljoin(
                normalized, f"{REVISION_DIR}/{quote(revision, safe='._-')}/"
            ),
            "http_readback_required": True,
            "deployment_contract": {
                "artifact_root": ".",
                "provider_owns_deploy_receipt": True,
                "loopx_receipt_readback": RECEIPT_FILE,
            },
        }
    raise StaticSiteContractError(f"unsupported publisher kind: {publisher_kind}")


def _validation_receipt(
    *,
    desktop_visual_check: str,
    mobile_visual_check: str,
    link_check: str,
) -> dict[str, str]:
    receipt = {
        "desktop_visual_check": str(desktop_visual_check or "").strip(),
        "mobile_visual_check": str(mobile_visual_check or "").strip(),
        "link_check": str(link_check or "").strip(),
    }
    failed = [name for name, value in receipt.items() if value != "passed"]
    if failed:
        raise StaticSiteContractError(
            "static presentation packaging requires passed validation receipts: "
            + ", ".join(failed)
        )
    return receipt


def _receipt(
    *,
    site_id: str,
    revision: str,
    semantic_digest: str,
    entry_path: str,
    publisher: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schema_version": STATIC_SITE_RECEIPT_VERSION,
        "site_id": site_id,
        "revision": revision,
        "semantic_digest": semantic_digest,
        "entry_path": entry_path,
        "publication_state": "prepared",
        "publisher_kind": publisher["kind"],
        "latest_url": publisher["latest_url"],
        "revision_url": publisher["revision_url"],
        "readback": {
            "required": publisher["http_readback_required"],
            "receipt_path": RECEIPT_FILE,
        },
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StaticSiteContractError(
            f"cannot read static presentation state {path.name}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise StaticSiteContractError(
            f"static presentation state must be a JSON object: {path.name}"
        )
    return payload


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _copy_declared_files(
    source: Path, target: Path, files: list[dict[str, object]]
) -> None:
    for item in files:
        relative_path = str(item["path"])
        destination = target / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative_path, destination)


def _validate_retained_revision_files(
    revision_dir: Path,
    value: object,
) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise StaticSiteContractError(
            "rollback revision does not contain a file manifest"
        )
    if revision_dir.parent.is_symlink() or revision_dir.is_symlink():
        raise StaticSiteContractError(
            "rollback revision must not use symlinked storage"
        )

    files: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise StaticSiteContractError(
                f"rollback revision file entry {index} must be an object"
            )
        raw_path = item.get("path")
        if not isinstance(raw_path, str):
            raise StaticSiteContractError(
                f"rollback revision file path must be a string at index {index}"
            )
        relative = Path(raw_path)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise StaticSiteContractError(
                f"revision file path must stay inside the retained revision: {raw_path!r}"
            )
        relative_path = relative.as_posix()
        if (
            relative_path.split("/", 1)[0] in _RESERVED_TOP_LEVEL
            or relative_path in seen
        ):
            raise StaticSiteContractError(
                f"revision file path is reserved or duplicated: {relative_path}"
            )

        expected_digest = item.get("sha256")
        expected_size = item.get("size")
        if (
            not isinstance(expected_digest, str)
            or not BARE_SHA256_PATTERN.fullmatch(expected_digest)
            or (
                not isinstance(expected_size, int)
                or isinstance(expected_size, bool)
                or expected_size < 0
            )
        ):
            raise StaticSiteContractError(
                f"rollback revision file metadata is invalid: {relative_path}"
            )

        source = revision_dir
        for part in relative.parts:
            source /= part
            if source.is_symlink():
                raise StaticSiteContractError(
                    f"rollback revision file must not use symlinks: {relative_path}"
                )
        if not source.is_file():
            raise StaticSiteContractError(
                f"rollback revision file is missing: {relative_path}"
            )
        try:
            content = source.read_bytes()
        except OSError as exc:
            raise StaticSiteContractError(
                f"cannot read rollback revision file {relative_path}: {exc}"
            ) from exc
        if (
            len(content) != expected_size
            or hashlib.sha256(content).hexdigest() != expected_digest
        ):
            raise StaticSiteContractError(
                f"rollback revision file integrity mismatch: {relative_path}"
            )
        seen.add(relative_path)
        files.append(
            {
                "path": relative_path,
                "sha256": expected_digest,
                "size": expected_size,
            }
        )
    return files


def _append_state_event(state_dir: Path, payload: Mapping[str, object]) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / RECEIPT_LOG_FILE
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return log_path


def _default_state_dir(output_dir: Path) -> Path:
    return output_dir.parent / f".{output_dir.name}.loopx-state"


def _replace_output(output_dir: Path, build: Callable[[Path], None]) -> None:
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    backup = output_dir.parent / f".{output_dir.name}.loopx-backup"
    if backup.exists():
        raise StaticSiteContractError(
            f"stale static presentation backup requires inspection: {backup}"
        )
    with tempfile.TemporaryDirectory(
        prefix=f".{output_dir.name}.loopx-stage-", dir=output_dir.parent
    ) as temp_dir:
        staged = Path(temp_dir) / "publish"
        staged.mkdir()
        build(staged)
        had_output = output_dir.exists()
        if had_output:
            os.replace(output_dir, backup)
        try:
            os.replace(staged, output_dir)
        except Exception:
            if had_output and backup.exists() and not output_dir.exists():
                os.replace(backup, output_dir)
            raise
        if backup.exists():
            shutil.rmtree(backup)


def package_static_site(
    *,
    site_dir: Path,
    output_dir: Path,
    site_id: str,
    entry_path: str = "index.html",
    revision: str | None = None,
    publisher_kind: str = "local",
    base_url: str | None = None,
    state_dir: Path | None = None,
    desktop_visual_check: str,
    mobile_visual_check: str,
    link_check: str,
    execute: bool = False,
) -> dict[str, object]:
    site_dir = site_dir.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    if _paths_overlap(site_dir, output_dir):
        raise StaticSiteContractError(
            "site_dir and output_dir must be independent directories"
        )

    site_id = _validate_identifier(site_id, label="site_id")
    entry_path = _validate_entry_path(entry_path)
    files = _collect_site_files(site_dir, entry_path=entry_path)
    semantic_digest = _semantic_digest(files)
    requested_revision = _validate_identifier(
        revision or semantic_digest.removeprefix("sha256:")[:16],
        label="revision",
    )
    validation = _validation_receipt(
        desktop_visual_check=desktop_visual_check,
        mobile_visual_check=mobile_visual_check,
        link_check=link_check,
    )
    existing = _read_json(output_dir / MANIFEST_FILE)

    exact_semantic_reuse = bool(
        existing
        and existing.get("site_id") == site_id
        and existing.get("semantic_digest") == semantic_digest
        and existing.get("entry_path") == entry_path
        and existing.get("files") == files
        and existing.get("validation") == validation
    )
    active_revision = (
        _validate_identifier(str(existing["active_revision"]), label="active_revision")
        if exact_semantic_reuse and existing
        else requested_revision
    )
    publisher = _publisher_projection(
        publisher_kind=publisher_kind,
        base_url=base_url,
        revision=active_revision,
    )
    previous_revision = None
    if existing:
        if exact_semantic_reuse:
            previous_revision = existing.get("previous_revision")
        elif existing.get("active_revision") != active_revision:
            previous_revision = existing.get("active_revision")
        else:
            previous_revision = existing.get("previous_revision")

    manifest: dict[str, object] = {
        "schema_version": STATIC_SITE_MANIFEST_VERSION,
        "site_id": site_id,
        "semantic_digest": semantic_digest,
        "active_revision": active_revision,
        "previous_revision": previous_revision,
        "entry_path": entry_path,
        "latest_entry_path": entry_path,
        "revision_entry_path": f"{REVISION_DIR}/{active_revision}/{entry_path}",
        "publisher": publisher,
        "validation": validation,
        "files": files,
        "public_boundary": public_safe_boundary(),
    }
    deploy_receipt = _receipt(
        site_id=site_id,
        revision=active_revision,
        semantic_digest=semantic_digest,
        entry_path=entry_path,
        publisher=publisher,
    )
    revision_metadata: dict[str, object] = {
        "schema_version": STATIC_SITE_REVISION_VERSION,
        "site_id": site_id,
        "revision": active_revision,
        "semantic_digest": semantic_digest,
        "entry_path": entry_path,
        "validation": validation,
        "files": files,
    }

    existing_revision = _read_json(
        output_dir / REVISION_DIR / active_revision / REVISION_FILE
    )
    if (
        existing_revision
        and existing_revision.get("semantic_digest") != semantic_digest
    ):
        raise StaticSiteContractError(
            f"revision {active_revision} already exists with a different semantic digest"
        )
    write_required = not (
        existing == manifest
        and existing_revision == revision_metadata
        and _read_json(output_dir / RECEIPT_FILE) == deploy_receipt
    )
    state_path = (
        state_dir.expanduser().resolve()
        if state_dir
        else _default_state_dir(output_dir)
    )

    if execute and write_required:

        def build(staged: Path) -> None:
            existing_revisions = output_dir / REVISION_DIR
            staged_revisions = staged / REVISION_DIR
            if existing_revisions.is_dir():
                shutil.copytree(existing_revisions, staged_revisions)
            else:
                staged_revisions.mkdir()

            revision_dir = staged_revisions / active_revision
            if not revision_dir.exists():
                revision_dir.mkdir()
                _copy_declared_files(site_dir, revision_dir, files)
            _write_json(revision_dir / REVISION_FILE, revision_metadata)
            _write_json(revision_dir / RECEIPT_FILE, deploy_receipt)
            _copy_declared_files(site_dir, staged, files)
            _write_json(staged / MANIFEST_FILE, manifest)
            _write_json(staged / RECEIPT_FILE, deploy_receipt)

        _replace_output(output_dir, build)
        _append_state_event(
            state_path,
            {
                "schema_version": STATIC_SITE_EVENT_VERSION,
                "event": "package_prepared",
                "recorded_at": _utc_now(),
                "site_id": site_id,
                "revision": active_revision,
                "previous_revision": previous_revision,
                "semantic_digest": semantic_digest,
                "publisher_kind": publisher_kind,
                "latest_url": publisher["latest_url"],
                "revision_url": publisher["revision_url"],
            },
        )

    return {
        "ok": True,
        "schema_version": STATIC_SITE_MANIFEST_VERSION,
        "mode": "package",
        "dry_run": not execute,
        "write_required": write_required,
        "write_performed": bool(execute and write_required),
        "semantic_noop": not write_required,
        "requested_revision": requested_revision,
        "active_revision": active_revision,
        "previous_revision": previous_revision,
        "semantic_digest": semantic_digest,
        "publisher": publisher,
        "output_dir": str(output_dir),
        "manifest_path": str(output_dir / MANIFEST_FILE),
        "receipt_path": str(output_dir / RECEIPT_FILE),
        "state_receipt_log": str(state_path / RECEIPT_LOG_FILE),
        "validation": validation,
    }


def rollback_static_site(
    *,
    output_dir: Path,
    revision: str | None = None,
    state_dir: Path | None = None,
    execute: bool = False,
) -> dict[str, object]:
    output_dir = output_dir.expanduser().resolve()
    manifest = _read_json(output_dir / MANIFEST_FILE)
    if not manifest or manifest.get("schema_version") != STATIC_SITE_MANIFEST_VERSION:
        raise StaticSiteContractError(
            "rollback requires an existing static presentation manifest"
        )
    current_revision = _validate_identifier(
        str(manifest.get("active_revision") or ""), label="active_revision"
    )
    target_revision = _validate_identifier(
        revision or str(manifest.get("previous_revision") or ""),
        label="rollback revision",
    )
    if target_revision == current_revision:
        raise StaticSiteContractError("rollback target is already active")
    target_dir = output_dir / REVISION_DIR / target_revision
    metadata = _read_json(target_dir / REVISION_FILE)
    if not metadata or metadata.get("schema_version") != STATIC_SITE_REVISION_VERSION:
        raise StaticSiteContractError(
            f"rollback revision is missing or invalid: {target_revision}"
        )
    if (
        metadata.get("site_id") != manifest.get("site_id")
        or metadata.get("revision") != target_revision
    ):
        raise StaticSiteContractError(
            f"rollback revision metadata binding is invalid: {target_revision}"
        )
    files = _validate_retained_revision_files(target_dir, metadata.get("files"))
    semantic_digest = str(metadata.get("semantic_digest") or "")
    if semantic_digest != _semantic_digest(files):
        raise StaticSiteContractError(
            f"rollback revision semantic integrity mismatch: {target_revision}"
        )
    entry_path = _validate_entry_path(str(metadata.get("entry_path") or ""))
    if entry_path not in {str(item["path"]) for item in files}:
        raise StaticSiteContractError(
            f"rollback revision entry is missing from the file manifest: {entry_path}"
        )
    validation_state = metadata.get("validation")
    if not isinstance(validation_state, Mapping):
        raise StaticSiteContractError("rollback revision validation receipt is missing")
    validation = _validation_receipt(
        desktop_visual_check=str(validation_state.get("desktop_visual_check") or ""),
        mobile_visual_check=str(validation_state.get("mobile_visual_check") or ""),
        link_check=str(validation_state.get("link_check") or ""),
    )

    publisher_state = (
        manifest.get("publisher") if isinstance(manifest.get("publisher"), dict) else {}
    )
    publisher = _publisher_projection(
        publisher_kind=str(publisher_state.get("kind") or "local"),
        base_url=publisher_state.get("base_url")
        if isinstance(publisher_state.get("base_url"), str)
        else None,
        revision=target_revision,
    )
    new_manifest = {
        **manifest,
        "semantic_digest": semantic_digest,
        "active_revision": target_revision,
        "previous_revision": current_revision,
        "entry_path": entry_path,
        "latest_entry_path": entry_path,
        "revision_entry_path": f"{REVISION_DIR}/{target_revision}/{entry_path}",
        "publisher": publisher,
        "validation": validation,
        "files": files,
    }
    deploy_receipt = _receipt(
        site_id=str(manifest["site_id"]),
        revision=target_revision,
        semantic_digest=semantic_digest,
        entry_path=entry_path,
        publisher=publisher,
    )
    write_required = (
        manifest != new_manifest
        or _read_json(output_dir / RECEIPT_FILE) != deploy_receipt
    )
    state_path = (
        state_dir.expanduser().resolve()
        if state_dir
        else _default_state_dir(output_dir)
    )

    if execute and write_required:

        def build(staged: Path) -> None:
            shutil.copytree(output_dir / REVISION_DIR, staged / REVISION_DIR)
            _copy_declared_files(target_dir, staged, files)
            _write_json(staged / MANIFEST_FILE, new_manifest)
            _write_json(staged / RECEIPT_FILE, deploy_receipt)

        _replace_output(output_dir, build)
        _append_state_event(
            state_path,
            {
                "schema_version": STATIC_SITE_EVENT_VERSION,
                "event": "rollback_prepared",
                "recorded_at": _utc_now(),
                "site_id": manifest["site_id"],
                "revision": target_revision,
                "previous_revision": current_revision,
                "semantic_digest": semantic_digest,
                "publisher_kind": publisher["kind"],
                "latest_url": publisher["latest_url"],
                "revision_url": publisher["revision_url"],
            },
        )

    return {
        "ok": True,
        "schema_version": STATIC_SITE_MANIFEST_VERSION,
        "mode": "rollback",
        "dry_run": not execute,
        "write_required": write_required,
        "write_performed": bool(execute and write_required),
        "from_revision": current_revision,
        "active_revision": target_revision,
        "semantic_digest": semantic_digest,
        "publisher": publisher,
        "output_dir": str(output_dir),
        "manifest_path": str(output_dir / MANIFEST_FILE),
        "receipt_path": str(output_dir / RECEIPT_FILE),
        "state_receipt_log": str(state_path / RECEIPT_LOG_FILE),
    }


def verify_static_site_readback(
    *,
    output_dir: Path,
    receipt_url: str | None = None,
    state_dir: Path | None = None,
    retries: int = 3,
    retry_delay_seconds: float = 1.0,
    execute: bool = False,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, object]:
    output_dir = output_dir.expanduser().resolve()
    expected = _read_json(output_dir / RECEIPT_FILE)
    manifest = _read_json(output_dir / MANIFEST_FILE)
    if not expected or not manifest:
        raise StaticSiteContractError(
            "readback verification requires a packaged static presentation"
        )
    if retries < 1 or retries > 10:
        raise StaticSiteContractError("retries must be between 1 and 10")
    if retry_delay_seconds < 0 or retry_delay_seconds > 30:
        raise StaticSiteContractError("retry_delay_seconds must be between 0 and 30")

    publisher = (
        manifest.get("publisher") if isinstance(manifest.get("publisher"), dict) else {}
    )
    target_url = str(receipt_url or "").strip()
    if not target_url:
        latest_url = str(publisher.get("latest_url") or "")
        if not latest_url.startswith("https://"):
            raise StaticSiteContractError(
                "remote readback requires --receipt-url for a non-HTTP publisher"
            )
        target_url = urljoin(latest_url, RECEIPT_FILE)
    parsed = urlparse(target_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise StaticSiteContractError("receipt_url must be an absolute HTTP(S) URL")

    last_error: Exception | None = None
    observed: dict[str, Any] | None = None
    attempts = 0
    for attempts in range(1, retries + 1):
        try:
            with opener(target_url, timeout=10) as response:
                body = response.read(262_145)
            if len(body) > 262_144:
                raise StaticSiteContractError("remote receipt exceeds 256 KiB")
            candidate = json.loads(body.decode("utf-8"))
            if not isinstance(candidate, dict):
                raise StaticSiteContractError("remote receipt must be a JSON object")
            observed = candidate
            for field in ("schema_version", "site_id", "revision", "semantic_digest"):
                if observed.get(field) != expected.get(field):
                    raise StaticSiteContractError(
                        f"remote receipt {field} mismatch: expected {expected.get(field)!r}, observed {observed.get(field)!r}"
                    )
            break
        except Exception as exc:  # bounded retry preserves the exact last cause
            last_error = exc
            observed = None
            if attempts < retries:
                time.sleep(retry_delay_seconds)
    if observed is None:
        raise StaticSiteContractError(
            f"remote receipt readback failed after {attempts} attempt(s): {last_error}"
        )

    state_path = (
        state_dir.expanduser().resolve()
        if state_dir
        else _default_state_dir(output_dir)
    )
    if execute:
        _append_state_event(
            state_path,
            {
                "schema_version": STATIC_SITE_EVENT_VERSION,
                "event": "http_readback_verified",
                "recorded_at": _utc_now(),
                "site_id": expected["site_id"],
                "revision": expected["revision"],
                "semantic_digest": expected["semantic_digest"],
                "publisher_kind": expected["publisher_kind"],
                "receipt_url": target_url,
                "attempts": attempts,
            },
        )

    return {
        "ok": True,
        "schema_version": STATIC_SITE_RECEIPT_VERSION,
        "mode": "verify-readback",
        "dry_run": not execute,
        "verified": True,
        "receipt_url": target_url,
        "attempts": attempts,
        "site_id": expected["site_id"],
        "revision": expected["revision"],
        "semantic_digest": expected["semantic_digest"],
        "state_receipt_log": str(state_path / RECEIPT_LOG_FILE),
    }
