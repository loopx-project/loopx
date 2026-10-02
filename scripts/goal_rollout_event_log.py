#!/usr/bin/env python3
"""Append or summarize public-safe LoopX rollout events."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.paths import select_default_runtime_root  # noqa: E402
from loopx.rollout_event_log import (  # noqa: E402
    build_rollout_event,
    append_rollout_event,
    load_rollout_events,
    rollout_event_log_path,
    summarize_rollout_events,
)


def _json_dump(payload: dict[str, Any], *, pretty: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))


def _details(values: list[str] | None) -> dict[str, str]:
    details: dict[str, str] = {}
    for value in values or []:
        if "=" not in value:
            raise SystemExit(f"--detail expects KEY=VALUE, got {value!r}")
        key, raw = value.split("=", 1)
        details[key] = raw
    return details


def _log_path(args: argparse.Namespace) -> Path:
    if args.log_path:
        return Path(args.log_path).expanduser()
    try:
        root = (
            Path(args.runtime_root).expanduser()
            if args.runtime_root is not None
            else select_default_runtime_root()
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    return rollout_event_log_path(root, args.goal_id)


def handle_append(args: argparse.Namespace) -> int:
    event = build_rollout_event(
        goal_id=args.goal_id,
        event_kind=args.event_kind,
        agent_id=args.agent_id,
        todo_id=args.todo_id,
        case_id=args.case_id,
        run_id=args.run_id,
        lane_id=args.lane_id,
        agent_role=args.agent_role,
        gate_id=args.gate_id,
        decision_id=args.decision_id,
        from_state=args.from_state,
        to_state=args.to_state,
        caused_by=args.caused_by,
        source_event_id=args.source_event_id,
        blocks=args.blocks,
        unblocks=args.unblocks,
        handoff_to=args.handoff_to,
        commit_ref=args.commit_ref,
        pr_ref=args.pr_ref,
        revert_of=args.revert_of,
        status=args.status,
        classification=args.classification,
        delivery_outcome=args.delivery_outcome,
        labels=args.label,
        summary=args.summary,
        artifact_refs=args.artifact_ref,
        details=_details(args.detail),
        private_source_kind=args.private_source_kind,
        private_source_count=args.private_source_count,
    )
    appended = append_rollout_event(_log_path(args), event)
    _json_dump(appended, pretty=args.pretty)
    return 0


def handle_summarize(args: argparse.Namespace) -> int:
    events = load_rollout_events(_log_path(args), limit=args.read_limit)
    payload = summarize_rollout_events(events, limit=args.limit)
    _json_dump(payload, pretty=args.pretty)
    return 0


def handle_observe_codex_sessions(args: argparse.Namespace) -> int:
    session_root = Path(args.session_root).expanduser()
    count = len(list(session_root.glob("**/*.jsonl"))) if session_root.exists() else 0
    event = build_rollout_event(
        goal_id=args.goal_id,
        event_kind="codex_session_observed",
        agent_id=args.agent_id,
        todo_id=args.todo_id,
        status="observed" if count else "missing",
        summary=(
            "Codex session directory shape observed as private source metadata; "
            "raw session transcripts and file paths were not recorded."
        ),
        private_source_kind="codex_sessions_jsonl",
        private_source_count=count,
    )
    appended = append_rollout_event(_log_path(args), event)
    _json_dump(appended, pretty=args.pretty)
    return 0


def _add_common_path_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--runtime-root", help="Defaults to the selected current or legacy runtime.")
    parser.add_argument("--log-path")
    parser.add_argument("--pretty", action="store_true")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record public-safe rollout events without raw sessions or logs."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    append_parser = subparsers.add_parser("append")
    _add_common_path_args(append_parser)
    append_parser.add_argument("--event-kind", required=True)
    append_parser.add_argument("--agent-id")
    append_parser.add_argument("--todo-id")
    append_parser.add_argument("--case-id")
    append_parser.add_argument("--run-id")
    append_parser.add_argument("--lane-id")
    append_parser.add_argument("--agent-role")
    append_parser.add_argument("--gate-id")
    append_parser.add_argument("--decision-id")
    append_parser.add_argument("--from-state")
    append_parser.add_argument("--to-state")
    append_parser.add_argument("--caused-by")
    append_parser.add_argument("--source-event-id")
    append_parser.add_argument("--blocks", action="append")
    append_parser.add_argument("--unblocks", action="append")
    append_parser.add_argument("--handoff-to")
    append_parser.add_argument("--commit-ref")
    append_parser.add_argument("--pr-ref")
    append_parser.add_argument("--revert-of")
    append_parser.add_argument("--status")
    append_parser.add_argument("--classification")
    append_parser.add_argument("--delivery-outcome")
    append_parser.add_argument("--summary")
    append_parser.add_argument("--artifact-ref", action="append")
    append_parser.add_argument("--label", action="append")
    append_parser.add_argument("--detail", action="append")
    append_parser.add_argument("--private-source-kind")
    append_parser.add_argument("--private-source-count", type=int)
    append_parser.set_defaults(func=handle_append)

    summarize_parser = subparsers.add_parser("summarize")
    _add_common_path_args(summarize_parser)
    summarize_parser.add_argument("--limit", type=int, default=12)
    summarize_parser.add_argument("--read-limit", type=int)
    summarize_parser.set_defaults(func=handle_summarize)

    sessions_parser = subparsers.add_parser("observe-codex-sessions")
    _add_common_path_args(sessions_parser)
    sessions_parser.add_argument("--agent-id")
    sessions_parser.add_argument("--todo-id")
    sessions_parser.add_argument("--session-root", default="~/.codex/sessions")
    sessions_parser.set_defaults(func=handle_observe_codex_sessions)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
