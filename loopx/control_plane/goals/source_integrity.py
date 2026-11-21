"""Read-only content verification for explicitly registered local sources.

This filesystem adapter measures bytes and uses existing Goal health diagnostics.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .contract_health import contract_error_diagnostic


def compute_file_sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def verify_authority_source_digests(
    goal: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Verify every local digest-bearing authority, independent of its domain.

    Legacy entries without a digest retain their previous behavior. This is a
    read-only check: prose in active state cannot approve or replace a lock.
    """
    diagnostics: list[dict[str, Any]] = []
    checks: list[str] = []
    project = Path(str(goal.get("repo") or "")).expanduser()
    goal_id = str(goal.get("id") or "")
    for source in goal.get("authority_sources") or []:
        if not isinstance(source, dict) or not source.get("sha256"):
            continue
        relative = str(source.get("path") or "")
        path = project / relative
        code = ""
        if not relative or not path.is_file():
            code = "authority_source_missing"
        else:
            try:
                actual = compute_file_sha256(path)
            except OSError:
                code = "authority_source_unreadable"
            else:
                if actual != source["sha256"]:
                    code = "authority_source_digest_mismatch"
        if code:
            diagnostics.append(contract_error_diagnostic(
                code=code,
                message=f"{goal_id}: authority source `{relative}` failed verification ({code})",
                goal_id=goal_id,
            ))
        else:
            checks.append(f"{goal_id}: authority source `{relative}` digest verified")
    return diagnostics, checks
