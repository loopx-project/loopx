"""Atomically export sanitized LoopX run conclusions as public-safe documents.

This extension-owned command converts LoopX run-history conclusions into
compact public-safe Markdown documents. It deliberately stops at a verified
local corpus: OpenViking ingest and recall need a separate caller-owned
capability and effect lifecycle.

Design boundary: run conclusions are *documents/knowledge*, not provider-managed
*peer preference memory*. This exporter therefore does not extend or bypass the
semantic-preference provider contract in ``provider.py``.

Reuses:
- ``loopx.history.collect_history`` to read run records (no bespoke JSON parsing).
- ``loopx.control_plane.runtime.public_safety.public_safe_compact_text`` to drop
  any field carrying local paths or secret-like tokens before it leaves the repo.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ...control_plane.content_digest import BARE_SHA256_PATTERN
from ...control_plane.runtime.public_safety import public_safe_compact_text
from ...history import collect_history, validate_goal_id_path_segment

HISTORY_EXPORT_SCHEMA_VERSION = "loopx_history_conclusion_export_v0"

_CONCLUSION_LIMIT = 400
_MAX_PROGRESS_BULLETS = 3
_CORPUS_DIR = "history-conclusions"
_MANIFEST_FILE = ".loopx-history-conclusion-export.json"
_MANIFEST_SCHEMA_VERSION = "loopx_history_conclusion_export_manifest_v0"
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")
# Bounded ISO-8601-ish timestamp: digits, T/space, colon, dot, +/- and Z only.
_TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9:.+\-]{1,20}Z?$")
_PUBLIC_GOAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,120}$")
_SECRET_SHAPED_GOAL_ID_RE = re.compile(
    r"(?i)^(?:"
    r"gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"AIza[A-Za-z0-9_-]{20,}|"
    r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{12,}|"
    r"npm_[A-Za-z0-9]{20,}|"
    r"pypi-[A-Za-z0-9_-]{20,}"
    r")$"
)


def _public_goal_id(value: Any) -> str:
    raw = str(value or "")
    goal_id = validate_goal_id_path_segment(raw)
    if (
        raw != goal_id
        or not _PUBLIC_GOAL_ID_RE.fullmatch(goal_id)
        or _SECRET_SHAPED_GOAL_ID_RE.fullmatch(goal_id)
        or public_safe_compact_text(goal_id, limit=121) != goal_id
    ):
        raise ValueError(
            "goal_id must be a public-safe token using letters, digits, dot, "
            "colon, dash, or underscore"
        )
    return goal_id


def _safe_generated_at(value: Any) -> str:
    """Return a bounded timestamp string, or empty string if not safe.

    Never trust the raw run field as content: only echo it when it matches a
    tight timestamp shape, otherwise drop it entirely.
    """
    text = str(value or "").strip()
    if text and _TIMESTAMP_RE.match(text):
        return text
    return ""


def _slug(value: str) -> str:
    slug = _SLUG_RE.sub("-", value).strip("-")
    return slug[:80] or "run"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _document_name(
    run: Mapping[str, Any],
    markdown: str,
    *,
    digest_occurrences: dict[str, int],
) -> str:
    digest = _sha256(markdown.encode("utf-8"))
    occurrence = digest_occurrences.get(digest, 0) + 1
    digest_occurrences[digest] = occurrence
    generated_at = _safe_generated_at(run.get("generated_at"))
    classification = (
        public_safe_compact_text(run.get("classification"), limit=80) or "run"
    )
    prefix = _slug(f"{generated_at}-{classification}")[:56]
    duplicate_suffix = f"-{occurrence}" if occurrence > 1 else ""
    return f"{prefix}-{digest[:16]}{duplicate_suffix}.md"


def _manifest_payload(
    *,
    goal_id: str,
    documents: Mapping[str, bytes],
) -> dict[str, Any]:
    return {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "goal_id": goal_id,
        "files": [
            {"name": name, "sha256": _sha256(data)}
            for name, data in sorted(documents.items())
        ],
    }


def _write_manifest(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_owned_manifest(path: Path, *, goal_id: str) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("history export manifest is unreadable") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError("history export manifest must be an object")
    if payload.get("schema_version") != _MANIFEST_SCHEMA_VERSION:
        raise RuntimeError("history export manifest schema is unsupported")
    if payload.get("goal_id") != goal_id:
        raise RuntimeError("history export manifest goal does not match output")
    files = payload.get("files")
    if not isinstance(files, list):
        raise RuntimeError("history export manifest files must be a list")

    owned: dict[str, str] = {}
    for item in files:
        if not isinstance(item, Mapping):
            raise RuntimeError("history export manifest file entry must be an object")
        name = str(item.get("name") or "")
        digest = str(item.get("sha256") or "")
        if (
            not name
            or Path(name).name != name
            or not name.endswith(".md")
            or not BARE_SHA256_PATTERN.fullmatch(digest)
            or name in owned
        ):
            raise RuntimeError("history export manifest contains an invalid file entry")
        owned[name] = digest
    return owned


def _validated_owned_generation(path: Path, *, goal_id: str) -> dict[str, str]:
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError("history export generation must be a directory")
    manifest_path = path / _MANIFEST_FILE
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError(
            "existing history corpus lacks an exporter ownership manifest"
        )
    owned = _read_owned_manifest(manifest_path, goal_id=goal_id)
    expected_names = {_MANIFEST_FILE, *owned}
    actual_names = {item.name for item in path.iterdir()}
    unowned = sorted(actual_names - expected_names)
    if unowned:
        raise RuntimeError(
            f"history export generation contains unowned entries: {unowned}"
        )
    missing = sorted(expected_names - actual_names)
    if missing:
        raise RuntimeError(
            f"history export generation is missing owned entries: {missing}"
        )
    for name, digest in owned.items():
        document = path / name
        if document.is_symlink() or not document.is_file():
            raise RuntimeError(
                f"manifest-owned history document is not a regular file: {name}"
            )
        if _sha256(document.read_bytes()) != digest:
            raise RuntimeError(
                f"manifest-owned history document digest does not match: {name}"
            )
    return owned


def _validate_staged_generation(
    staged: Path,
    *,
    documents: Mapping[str, bytes],
    manifest: Mapping[str, Any],
) -> None:
    for name, expected in documents.items():
        if (staged / name).read_bytes() != expected:
            raise RuntimeError(f"staged history document failed readback: {name}")
    staged_manifest = json.loads((staged / _MANIFEST_FILE).read_text(encoding="utf-8"))
    if staged_manifest != manifest:
        raise RuntimeError("staged history export manifest failed readback")


def _remove_verified_backup(backup: Path) -> None:
    shutil.rmtree(backup, ignore_errors=True)
    if backup.exists():
        raise RuntimeError(
            "history export backup cleanup is incomplete; retry publication "
            "after the verified backup can be removed"
        )


def _publish_generation(
    *,
    corpus_out: Path,
    documents: Mapping[str, bytes],
    manifest: Mapping[str, Any],
) -> int:
    corpus_out.parent.mkdir(parents=True, exist_ok=True)
    backup = corpus_out.parent / f".{corpus_out.name}.loopx-backup"
    if backup.exists() and corpus_out.exists():
        try:
            _validated_owned_generation(
                backup,
                goal_id=str(manifest["goal_id"]),
            )
        except Exception:
            raise RuntimeError(
                "history export has both live and backup generations "
                "and includes an unverified backup; manual inspection is required"
            ) from None
        try:
            _validated_owned_generation(
                corpus_out,
                goal_id=str(manifest["goal_id"]),
            )
        except Exception:
            unverified_live = (
                corpus_out.parent / f".{corpus_out.name}.loopx-unverified-live"
            )
            if unverified_live.exists():
                raise RuntimeError(
                    "history export has an unverified live generation and "
                    "its recovery quarantine already exists; manual inspection is required"
                ) from None
            os.replace(corpus_out, unverified_live)
            try:
                os.replace(backup, corpus_out)
            except Exception:
                try:
                    os.replace(unverified_live, corpus_out)
                except Exception:
                    raise RuntimeError(
                        "history export could not restore the unverified live "
                        "generation after backup recovery failed; manual inspection is required"
                    ) from None
                raise
        else:
            _remove_verified_backup(backup)
    elif backup.exists():
        _validated_owned_generation(
            backup,
            goal_id=str(manifest["goal_id"]),
        )
        os.replace(backup, corpus_out)
    prior_owned = _validated_owned_generation(
        corpus_out,
        goal_id=str(manifest["goal_id"]),
    )

    with tempfile.TemporaryDirectory(
        prefix=f".{corpus_out.name}.loopx-stage-",
        dir=corpus_out.parent,
    ) as temp_dir:
        staged = Path(temp_dir) / "publish"
        staged.mkdir()
        for name, data in sorted(documents.items()):
            (staged / name).write_text(data.decode("utf-8"), encoding="utf-8")
        _write_manifest(staged / _MANIFEST_FILE, manifest)
        _validate_staged_generation(
            staged,
            documents=documents,
            manifest=manifest,
        )

        had_output = corpus_out.exists()
        if had_output:
            os.replace(corpus_out, backup)
        try:
            os.replace(staged, corpus_out)
        except Exception:
            if had_output and backup.exists() and not corpus_out.exists():
                os.replace(backup, corpus_out)
            raise
        if backup.exists():
            _remove_verified_backup(backup)
    return len(prior_owned)


def conclusion_fields(run: dict[str, Any]) -> list[tuple[str, str]]:
    """Return public-safe (label, text) conclusion pairs for one run.

    Every value is passed through ``public_safe_compact_text`` so any row with a
    local path or secret-like token is dropped rather than exported.
    """
    fields: list[tuple[str, str]] = []

    def _add(label: str, raw: Any) -> None:
        if not raw:
            return
        safe = public_safe_compact_text(raw, limit=_CONCLUSION_LIMIT)
        if safe:
            fields.append((label, safe))

    _add("classification", run.get("classification"))
    _add("recommended_action", run.get("recommended_action"))
    _add("delivery_outcome", run.get("delivery_outcome"))

    vision_patch = (run.get("agent_vision") or {}).get("vision_patch") or {}
    _add("last_patch_summary", vision_patch.get("last_patch_summary"))
    _add("vision_summary", vision_patch.get("vision_summary"))

    progress = (run.get("state") or {}).get("progress") or []
    for bullet in progress[-_MAX_PROGRESS_BULLETS:]:
        _add("progress", bullet)

    return fields


def render_conclusion_markdown(goal_id: str, run: dict[str, Any]) -> str | None:
    """Render one run's conclusions as public-safe markdown, or None if noise.

    A run with no substantive conclusion beyond its classification (for example a
    bare ``quota_slot_spent`` slot) is treated as noise and skipped. ``goal_id``
    is validated against the canonical single-path-segment contract and
    ``generated_at`` is echoed only when it matches a bounded timestamp shape, so
    neither can inject unsafe content into the document.
    """
    safe_goal_id = _public_goal_id(goal_id)
    fields = conclusion_fields(run)
    substantive = [pair for pair in fields if pair[0] != "classification"]
    if not substantive:
        return None

    generated_at = _safe_generated_at(run.get("generated_at"))
    lines = [
        "# LoopX run conclusion",
        "",
        f"- goal: `{safe_goal_id}`",
        f"- generated_at: `{generated_at}`",
        "",
    ]
    for label, text in fields:
        lines.extend([f"## {label}", text, ""])
    return "\n".join(lines)


def export_goal_conclusions(
    *,
    goal_id: str,
    registry_path: Path,
    runtime_root: Path,
    out_dir: Path,
    limit: int = 50,
) -> dict[str, Any]:
    """Export the most recent ``limit`` run conclusions for one goal.

    Writes one public-safe markdown per substantive run under the exporter-owned
    ``out_dir/<goal_id>/history-conclusions/`` corpus and returns a summary split
    into a public-safe projection and a local-only publication receipt.

    Boundary contract:
    - ``goal_id`` is validated as a single path segment before it is used as a
      directory component, so an unsafe id (``../escaped``) cannot escape
      ``out_dir``.
    - The per-goal output directory cannot be a symlink, so an existing link
      cannot redirect this goal's corpus outside its selected output directory.
    - A private manifest identifies only this exporter's documents. The owned
      corpus directory is fully staged and read back before one atomic directory
      replacement, while unrelated files under ``out_dir/<goal_id>/`` are never
      moved or deleted.
    - The local filesystem output path lives only in ``local_receipt`` and is
      never part of the ``public_projection``.
    """
    safe_goal_id = _public_goal_id(goal_id)
    goal_root = out_dir / safe_goal_id
    if goal_root.is_symlink():
        raise RuntimeError("goal output directory must not be a symlink")

    history = collect_history(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=safe_goal_id,
        limit=limit,
    )
    runs = history.get("runs") or []
    corpus_out = goal_root / _CORPUS_DIR
    manifest_path = corpus_out / _MANIFEST_FILE

    documents: dict[str, bytes] = {}
    digest_occurrences: dict[str, int] = {}
    skipped_noise = 0
    for run in runs:
        markdown = render_conclusion_markdown(safe_goal_id, run)
        if markdown is None:
            skipped_noise += 1
            continue
        name = _document_name(
            run,
            markdown,
            digest_occurrences=digest_occurrences,
        )
        if name in documents:
            raise RuntimeError("history export generated a duplicate document name")
        documents[name] = markdown.encode("utf-8")

    manifest = _manifest_payload(goal_id=safe_goal_id, documents=documents)
    retired = _publish_generation(
        corpus_out=corpus_out,
        documents=documents,
        manifest=manifest,
    )

    return {
        "public_projection": {
            "schema_version": HISTORY_EXPORT_SCHEMA_VERSION,
            "goal_id": safe_goal_id,
            "runs_considered": len(runs),
            "written": len(documents),
            "skipped_noise": skipped_noise,
            "retired_prior_generation": retired,
            "target_scope_hint": (
                "openviking resources scope (documents), not peer memories"
            ),
        },
        "local_receipt": {
            # Local filesystem path is intentionally kept out of the public
            # projection above.
            "out_dir": str(corpus_out),
            "manifest_path": str(manifest_path),
            "owned_files": sorted(documents),
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--registry-path", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=50)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = export_goal_conclusions(
        goal_id=args.goal_id,
        registry_path=args.registry_path,
        runtime_root=args.runtime_root,
        out_dir=args.out_dir,
        limit=args.limit,
    )
    json.dump(payload, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
