"""Content-only receive adapter. Never opens a registry or executes commands."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ..control_plane.handoff.handoff_fragments import (
    ENVELOPE_PREFIX,
    HandoffShardError,
    build_handoff_shard_manifest,
    reassemble_handoff_shards,
    restore_handoff_text,
)


def restore_handoff_input(text: str, *, input_format: str) -> str:
    if input_format == "markdown":
        if not text.strip():
            raise HandoffShardError("missing", "empty handoff input")
        if text.startswith("【LoopX Review Packet】") and ENVELOPE_PREFIX not in text:
            raise HandoffShardError(
                "input",
                "use full packet JSON or handoff-only Markdown for an unfragmented packet",
            )
        # Unframed input remains content; apparent fragments must verify first.
        return restore_handoff_text(text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HandoffShardError(
            "input", "invalid JSON; select --input-format markdown for raw Markdown"
        ) from exc
    if not isinstance(value, dict) or value.get("ok") is not True:
        raise HandoffShardError(
            "input", "expected a successful review-packet JSON object"
        )
    shards = value.get("project_agent_handoff_fragments")
    manifest = value.get("handoff_fragment_manifest")
    fields = [
        value[key] for key in ("project_agent_handoff", "handoff_text") if key in value
    ]
    if not fields or any(not isinstance(field, str) or not field for field in fields):
        raise HandoffShardError("input", "missing complete handoff text field")
    if shards is not None or manifest is not None:
        if not isinstance(shards, list) or not shards:
            raise HandoffShardError(
                "missing",
                "missing handoff fragments; obtain the complete producer output",
            )
        if any(not isinstance(shard, str) for shard in shards):
            raise HandoffShardError("input", "handoff fragments must be strings")
        restored = reassemble_handoff_shards(shards)
        if not isinstance(manifest, dict) or manifest != build_handoff_shard_manifest(
            restored, shards
        ):
            raise HandoffShardError(
                "manifest", "manifest does not match the verified fragment set"
            )
    else:
        restored = restore_handoff_text(fields[0])
    if any(field != restored for field in fields):
        raise HandoffShardError(
            "integrity", "complete handoff fields disagree with the recovered text"
        )
    return restored


def handle_handoff_restore(args: Any, *, output_format: Any, print_payload: Any) -> int:
    try:
        # Reject ownership arguments rather than implying restoration adopts work.
        ownership_fields = (
            "goal_id",
            "todo_id",
            "agent_id",
            "session_id",
            "operation_id",
            "expected_revision",
            "rationale",
            "source_ref",
            "artifact",
            "target_agent_id",
            "task_lease_idempotency_key",
            "task_lease_expected_version",
            "from_context",
        )
        if any(
            getattr(args, key, None) is not None and getattr(args, key, None) != []
            for key in ownership_fields
        ):
            raise HandoffShardError(
                "input",
                "restore accepts content only; ownership arguments belong to prepare/inspect/adopt",
            )
        if args.handoff_format == "digest":
            raise HandoffShardError(
                "input", "restore supports --format json or markdown"
            )
        if not args.input:
            raise HandoffShardError(
                "input", "restore requires --input FILE (or - for stdin)"
            )
        text = (
            sys.stdin.read()
            if args.input == "-"
            else Path(args.input).read_text(encoding="utf-8")
        )
        restored = restore_handoff_input(text, input_format=args.input_format)
        payload: dict[str, Any] = {"ok": True, "handoff_text": restored}
    except (HandoffShardError, OSError, UnicodeError) as exc:
        payload = {
            "ok": False,
            "error_code": getattr(exc, "code", "input"),
            "error": str(exc),
            "next_action": "Obtain the unchanged complete handoff and retry; no content was executed or adopted.",
        }
    fmt = args.handoff_format or output_format(args)
    if payload["ok"] and fmt == "markdown":
        # Do not add a newline to byte-exact decoded content.
        sys.stdout.write(payload["handoff_text"])
    else:
        print_payload(
            payload, fmt, lambda value: json.dumps(value, ensure_ascii=False, indent=2)
        )
    return 0 if payload["ok"] else 1
