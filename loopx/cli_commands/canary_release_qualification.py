from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from ..control_plane.runtime.public_safety import public_safe_compact_text
from ..control_plane.testing.release_commit_qualification import (
    EXACT_RELEASE_COMMIT_RECEIPT_SCHEMA_VERSION,
    build_exact_release_commit_qualification,
    collect_release_source_identity,
)


def register_canary_release_qualification_command(
    canary_subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = canary_subparsers.add_parser(
        "release-qualification",
        help=(
            "Validate exact-commit deterministic, canary, full-public, install, "
            "boundary, and live-model receipts without executing or publishing."
        ),
    )
    add_subcommand_format(parser)
    parser.add_argument(
        "--manifest-json",
        required=True,
        type=Path,
        help="Path to an exact_release_commit_qualification_manifest_v0 JSON object.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help=(
            "Clean Git checkout whose HEAD, tree, package version, and tag must match "
            "every receipt. Defaults to the current directory."
        ),
    )
    parser.add_argument(
        "--evaluations-dir",
        type=Path,
        default=None,
        help=(
            "Optional release evaluation ledger directory (e.g. docs/product/releases/evaluations) "
            "to run the deterministic a/b/c evaluation gate alongside receipt checks."
        ),
    )
    parser.add_argument(
        "--prior-stable-version",
        type=str,
        default=None,
        help="Optional prior stable SemVer (e.g. 2.3.0) for Major/Minor/Patch transition gating.",
    )
    parser.add_argument(
        "--evaluation-json",
        type=Path,
        default=None,
        help="Optional explicit candidate release evaluation JSON overriding evaluations-dir lookup.",
    )
    parser.add_argument(
        "--verify-git-tags",
        action="store_true",
        help="Verify evaluation tag commit/tree bindings against the Git repository.",
    )


def build_canary_release_qualification_payload(args: argparse.Namespace) -> dict[str, object]:
    try:
        raw = json.loads(args.manifest_json.expanduser().read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("--manifest-json must contain a JSON object")
        evaluations_dir = (
            args.evaluations_dir.expanduser()
            if getattr(args, "evaluations_dir", None) is not None
            else None
        )
        evaluation_json = (
            args.evaluation_json.expanduser()
            if getattr(args, "evaluation_json", None) is not None
            else None
        )
        payload = build_exact_release_commit_qualification(
            raw,
            observed_source=collect_release_source_identity(args.repo_root),
            evaluations_dir=evaluations_dir,
            prior_stable_version=getattr(args, "prior_stable_version", None),
            evaluation_json=evaluation_json,
            repo_root=args.repo_root if evaluations_dir is not None else None,
            verify_git_tags=bool(getattr(args, "verify_git_tags", False)),
        )
        payload["ok"] = payload.get("ready_for_release") is True
        return payload
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        if isinstance(exc, json.JSONDecodeError):
            error = "manifest_invalid_json"
        elif isinstance(exc, OSError):
            error = "manifest_unreadable"
        else:
            error = public_safe_compact_text(str(exc), limit=220) or "manifest_invalid"
        return {
            "ok": False,
            "schema_version": EXACT_RELEASE_COMMIT_RECEIPT_SCHEMA_VERSION,
            "ready_for_release": False,
            "decision": "invalid_input",
            "automatic_release_promotion_allowed": False,
            "error": error,
            "read_boundary": {
                "manifest_only": True,
                "checks_executed": False,
                "model_api_invoked": False,
                "release_mutation_invoked": False,
                "raw_logs_retained": False,
                "raw_model_material_retained": False,
                "local_paths_recorded": False,
            },
        }
