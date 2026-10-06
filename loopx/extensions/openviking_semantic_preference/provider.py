from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ...capabilities.semantic_preference.contract import REQUEST_SCHEMA, RESPONSE_SCHEMA
from .project_peer import ProjectPeerScope, resolve_project_peer_scope


SCOPE_SCHEMA = "openviking_project_peer_scope_v0"
PREFERENCE_PATH_MARKER = "/memories/preferences/"
MAX_FIND_CALLS = 3


def _environment(cli_config: str | None) -> dict[str, str]:
    environment = dict(os.environ)
    if cli_config:
        environment["OPENVIKING_CLI_CONFIG_FILE"] = cli_config
    return environment


def _json_result(raw: str) -> Any:
    lines = raw.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.lstrip().startswith("{")),
        None,
    )
    if start is None:
        raise RuntimeError("OpenViking returned no JSON object")
    try:
        payload = json.loads("\n".join(lines[start:]))
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenViking returned invalid JSON") from exc
    if not isinstance(payload, Mapping) or payload.get("ok") is not True:
        raise RuntimeError("OpenViking returned an unsuccessful response")
    return payload.get("result")


def _run_ov(
    ov_bin: str,
    args: Sequence[str],
    *,
    cli_config: str | None,
    timeout_seconds: int,
) -> Any:
    try:
        completed = subprocess.run(
            [ov_bin, *args],
            capture_output=True,
            check=False,
            env=_environment(cli_config),
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("OpenViking command execution failed") from exc
    if completed.returncode != 0:
        raise RuntimeError("OpenViking command returned a non-zero exit")
    return _json_result(completed.stdout)


def _request() -> tuple[str, int]:
    payload = json.load(sys.stdin)
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") != REQUEST_SCHEMA
    ):
        raise ValueError(f"provider request must use {REQUEST_SCHEMA}")
    query = str(payload.get("query") or "").strip()
    if not 1 <= len(query) <= 500:
        raise ValueError("provider query must contain 1 to 500 characters")
    try:
        limit = int(payload.get("limit") or 5)
    except (TypeError, ValueError) as exc:
        raise ValueError("provider limit must be an integer") from exc
    if not 1 <= limit <= 20:
        raise ValueError("provider limit must be between 1 and 20")
    context = payload.get("context")
    if isinstance(context, Mapping) and context:
        scoped = " ".join(f"{key}={value}" for key, value in sorted(context.items()))
        query = f"{query}\nScope: {scoped}"
    return query, limit


def _find(
    *,
    ov_bin: str,
    cli_config: str | None,
    actor_peer_id: str,
    timeout_seconds: int,
    query: str,
    target_uri: str,
    limit: int,
) -> list[dict[str, str]]:
    result = _run_ov(
        ov_bin,
        [
            "--actor-peer-id",
            actor_peer_id,
            "find",
            "-o",
            "json",
            "-n",
            str(min(limit * 3, 20)),
            "-u",
            target_uri,
            query,
        ],
        cli_config=cli_config,
        timeout_seconds=timeout_seconds,
    )
    memories = result.get("memories") if isinstance(result, Mapping) else []
    target_prefix = f"{target_uri.rstrip('/')}/"
    items: list[dict[str, str]] = []
    for memory in memories if isinstance(memories, list) else []:
        if not isinstance(memory, Mapping):
            continue
        uri = str(memory.get("uri") or "").strip()
        summary = str(memory.get("abstract") or memory.get("overview") or "").strip()
        if (
            not uri.startswith(target_prefix)
            or PREFERENCE_PATH_MARKER not in uri
            or not summary
        ):
            continue
        items.append({"preference_ref": uri, "summary": summary[:2_000]})
        if len(items) >= limit:
            break
    return items


def _scope(args: argparse.Namespace) -> ProjectPeerScope:
    return resolve_project_peer_scope(
        args.project,
        user_space=args.user_space,
        loopx_project_id=args.loopx_project_id,
        remote_url=args.remote_url,
    )


def _corpus_inventory(
    scope: ProjectPeerScope, *, include_global_fallback: bool
) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = [
        {
            "corpus_id": "project_peer_preferences",
            "scope_ref": scope.preferences_uri,
            "read_role": "primary",
            "write_mode": "provider_managed",
            "write_actor_ref": scope.peer_id,
            "source_of_truth": "repository_revision_and_explicit_feedback",
            "writeback_triggers": ["explicit_feedback", "source_truth_changed"],
            "closure_policy": "write_wait_l2_read_scoped_recall",
        }
    ]
    if include_global_fallback:
        inventory.append(
            {
                "corpus_id": "user_global_preferences",
                "scope_ref": f"{scope.global_memory_uri}/preferences",
                "read_role": "fallback",
                "write_mode": "provider_managed",
                "write_actor_ref": None,
                "source_of_truth": "explicit_user_feedback",
                "writeback_triggers": ["explicit_feedback"],
                "closure_policy": "write_wait_l2_read_scoped_recall",
            }
        )
    return inventory


def _describe_scope(
    scope: ProjectPeerScope, *, include_global_fallback: bool
) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": SCOPE_SCHEMA,
        "project_identity_digest": hashlib.sha256(
            scope.project_identity.encode("utf-8")
        ).hexdigest()[:16],
        "peer_id": scope.peer_id,
        "memory_uri": scope.memory_uri,
        "preferences_uri": scope.preferences_uri,
        "global_memory_uri": scope.global_memory_uri,
        "global_fallback_default": False,
        "corpus_inventory": _corpus_inventory(
            scope, include_global_fallback=include_global_fallback
        ),
    }


def register_openviking_provider_arguments(parser: argparse.ArgumentParser) -> None:
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--doctor", action="store_true")
    mode.add_argument("--describe-scope", action="store_true")
    parser.add_argument("--ov-bin", default="ov")
    parser.add_argument("--cli-config")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--user-space", default="default")
    parser.add_argument("--loopx-project-id")
    parser.add_argument("--remote-url")
    parser.add_argument("--include-global-fallback", action="store_true")
    parser.add_argument("--max-find-calls", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=25)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OpenViking project-as-peer semantic preference provider."
    )
    register_openviking_provider_arguments(parser)
    return parser


def run_openviking_provider(args: argparse.Namespace) -> int:
    if not 1 <= args.timeout_seconds <= 120:
        raise ValueError("timeout-seconds must be between 1 and 120")

    if args.doctor:
        _run_ov(
            args.ov_bin,
            ["status", "-o", "json"],
            cli_config=args.cli_config,
            timeout_seconds=args.timeout_seconds,
        )
        return 0

    scope = _scope(args)
    if args.describe_scope:
        json.dump(
            _describe_scope(
                scope, include_global_fallback=args.include_global_fallback
            ),
            sys.stdout,
            ensure_ascii=False,
        )
        return 0

    if not 1 <= args.max_find_calls <= MAX_FIND_CALLS:
        raise ValueError(f"max-find-calls must be between 1 and {MAX_FIND_CALLS}")
    if args.include_global_fallback and args.max_find_calls < 2:
        raise ValueError("global fallback requires max-find-calls >= 2")

    query, limit = _request()
    items: list[dict[str, str]] = []
    find_calls = 0
    for target_uri in scope.recall_targets(
        include_global_fallback=args.include_global_fallback
    ):
        if find_calls >= args.max_find_calls:
            break
        find_calls += 1
        items = _find(
            ov_bin=args.ov_bin,
            cli_config=args.cli_config,
            actor_peer_id=scope.peer_id,
            timeout_seconds=args.timeout_seconds,
            query=query,
            target_uri=target_uri,
            limit=limit,
        )
        if items:
            break
    json.dump(
        {
            "schema_version": RESPONSE_SCHEMA,
            "items": items,
            "corpus_inventory": _corpus_inventory(
                scope, include_global_fallback=args.include_global_fallback
            ),
        },
        sys.stdout,
    )
    return 0


def run(argv: Sequence[str] | None = None) -> int:
    return run_openviking_provider(_parser().parse_args(argv))


def handle_openviking_provider(args: argparse.Namespace) -> int:
    try:
        return run_openviking_provider(args)
    except Exception as exc:  # Provider stderr is reduced by the outer hook.
        print(
            f"OpenViking project peer provider failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 2


def main() -> int:
    try:
        return run()
    except Exception as exc:  # Provider stderr is reduced by the outer hook.
        print(
            f"OpenViking project peer provider failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
