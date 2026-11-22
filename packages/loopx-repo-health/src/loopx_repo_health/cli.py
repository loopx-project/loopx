from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from .contract import validate_snapshot
from .github import GitHubRepoHealthCollector
from .render import render_markdown


EXTENSION_ID = "loopx-repo-health"
REQUEST_SCHEMA_VERSION = "loopx_repo_health_request_v0"
RESPONSE_SCHEMA_VERSION = "loopx_repo_health_response_v0"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=EXTENSION_ID)
    parser.add_argument("--doctor", action="store_true", help="run the managed-runtime health check")
    sub = parser.add_subparsers(dest="command")
    snap = sub.add_parser("snapshot", help="collect a repo-health snapshot")
    snap.add_argument("--owner", required=True)
    snap.add_argument("--repo", required=True)
    snap.add_argument("--format", choices=("json", "md"), default="json")
    return parser


def _emit(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")


def _doctor() -> int:
    _emit(
        {
            "ok": True,
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "extension_id": EXTENSION_ID,
            "doctor": "ok",
        }
    )
    return 0


def _run_request(payload: dict[str, Any]) -> int:
    if not isinstance(payload, dict):
        raise ValueError("extension input must be a JSON object")
    if payload.get("schema_version") != REQUEST_SCHEMA_VERSION:
        raise ValueError(f"extension input must use {REQUEST_SCHEMA_VERSION}")
    owner = payload.get("owner")
    repo = payload.get("repo")
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError("request requires non-empty string `owner`")
    if not isinstance(repo, str) or not repo.strip():
        raise ValueError("request requires non-empty string `repo`")
    snapshot = GitHubRepoHealthCollector(owner.strip(), repo.strip()).collect().to_dict()
    violations = validate_snapshot(snapshot)
    if violations:
        raise ValueError(f"snapshot contract violated: {violations}")
    _emit(
        {
            "ok": True,
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "extension_id": EXTENSION_ID,
            "request_schema_version": REQUEST_SCHEMA_VERSION,
            "result": snapshot,
        }
    )
    return 0


def _snapshot_command(args: argparse.Namespace) -> int:
    snapshot = GitHubRepoHealthCollector(args.owner, args.repo).collect().to_dict()
    violations = validate_snapshot(snapshot)
    if violations:
        _emit({"ok": False, "error": f"snapshot contract violated: {violations}"})
        return 1
    if args.format == "md":
        sys.stdout.write(render_markdown(snapshot))
    else:
        _emit(snapshot)
    return 0


def run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.doctor:
        return _doctor()
    try:
        if args.command == "snapshot":
            return _snapshot_command(args)
        payload = json.load(sys.stdin)
        return _run_request(payload)
    except (json.JSONDecodeError, OSError, ValueError, KeyError) as exc:
        _emit(
            {
                "ok": False,
                "schema_version": RESPONSE_SCHEMA_VERSION,
                "extension_id": EXTENSION_ID,
                "error": str(exc),
            }
        )
        return 1


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
