from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from collections.abc import Callable, Iterable

from ..opencode2_goal_mode import worker_source

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]


def _materialized_worker_path() -> Path:
    cache_root = Path(tempfile.gettempdir()) / "loopx" / "opencode2-goal-worker"
    cache_root.mkdir(parents=True, exist_ok=True)
    target = cache_root / "opencode2-goal-worker.mjs"
    content = worker_source()
    try:
        if target.read_text(encoding="utf-8") == content:
            return target
    except OSError:
        pass
    temporary = cache_root / f"opencode2-goal-worker.mjs.{__import__('os').getpid()}.tmp"
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(target)
    return target


def register_opencode2_goal_worker_command(
    subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "opencode2-goal-worker",
        help=(
            "Drive an OpenCode 2 session as a persistent LoopX goal loop: "
            "quota-gated continuation, backoff waits, and visible stop/pause notices."
        ),
    )
    parser.add_argument("--goal-id", required=True, help="LoopX goal id.")
    parser.add_argument(
        "--directory",
        required=True,
        help="Project directory for the OpenCode 2 session and quota probes.",
    )
    parser.add_argument("--agent-id", help="LoopX agent id to scope quota decisions.")
    parser.add_argument(
        "--capability",
        action="append",
        default=[],
        help="Declared host capability (repeatable).",
    )
    parser.add_argument(
        "--session-id",
        help="Attach to an existing OpenCode 2 session instead of creating one.",
    )
    parser.add_argument(
        "--task-body",
        help="Initial task text; only used for the first prompt of a new session.",
    )
    parser.add_argument("--max-turns", type=int, help="Turn budget (default 10000).")
    parser.add_argument(
        "--max-duration-minutes",
        type=float,
        help="Duration budget in minutes (default 43200 = 30 days).",
    )
    parser.add_argument(
        "--force-resume",
        action="store_true",
        help="Restart the loop even when worker state is paused.",
    )
    parser.add_argument(
        "--state-dir",
        help="Override the worker state directory.",
    )
    return parser


def handle_opencode2_goal_worker_command(
    args: argparse.Namespace, print_payload: PrintPayload
) -> int:
    worker_path = _materialized_worker_path()
    node_args = [
        "node",
        str(worker_path),
        "--goal-id",
        args.goal_id,
        "--directory",
        args.directory,
    ]
    if args.agent_id:
        node_args += ["--agent-id", args.agent_id]
    if args.registry:
        node_args += ["--registry", args.registry]
    for capability in args.capability or []:
        node_args += ["--capability", capability]
    if args.session_id:
        node_args += ["--session-id", args.session_id]
    if args.task_body:
        node_args += ["--task-body", args.task_body]
    if args.max_turns:
        node_args += ["--max-turns", str(args.max_turns)]
    if args.max_duration_minutes:
        node_args += ["--max-duration-minutes", str(args.max_duration_minutes)]
    if args.force_resume:
        node_args.append("--force-resume")
    if args.state_dir:
        node_args += ["--state-dir", args.state_dir]

    try:
        process = subprocess.Popen(
            node_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        payload = {
            "ok": False,
            "schema_version": "loopx_opencode2_worker_error_v0",
            "error_kind": "node_unavailable",
            "reason": "node is required to run the OpenCode 2 goal worker.",
        }
        print_payload(payload, args.format, _render_worker_markdown)
        return 1

    fd, raw_log_path = tempfile.mkstemp(prefix="loopx-oc2-worker-", suffix=".log")
    os.close(fd)
    log_path = Path(raw_log_path)
    worker_log_tail = ""

    def _drain(stream: Iterable[str]) -> None:
        nonlocal worker_log_tail
        with log_path.open("a", encoding="utf-8") as log_handle:
            for line in stream:
                print(line, end="", flush=True)
                log_handle.write(line)
                log_handle.flush()
        tail = log_path.read_text(encoding="utf-8").splitlines()[-120:]
        worker_log_tail = "\n".join(tail)

    stderr_thread = threading.Thread(
        target=_drain, args=(process.stderr,), daemon=True
    )
    stderr_thread.start()
    try:
        stdout, _ = process.communicate()
    except KeyboardInterrupt:
        process.kill()
        process.wait()
        interrupt_payload: dict[str, object] = {
            "ok": False,
            "schema_version": "loopx_opencode2_worker_result_v0",
            "operation": "opencode2_goal_worker",
            "exit_code": 130,
            "reason": "interrupted by the operator",
        }
        log_path.unlink(missing_ok=True)
        print_payload(interrupt_payload, args.format, _render_worker_markdown)
        return 130
    stderr_thread.join(timeout=5)
    stdout_text = (stdout or "").strip()
    try:
        parsed = json.loads(stdout_text) if stdout_text else None
    except json.JSONDecodeError:
        parsed = None
    if not isinstance(parsed, dict) or parsed.get("operation") != "opencode2_goal_worker":
        payload: dict[str, object] = {
            "ok": False,
            "schema_version": "loopx_opencode2_worker_result_v0",
            "operation": "opencode2_goal_worker",
            "exit_code": process.returncode,
            "error_kind": "worker_result_missing",
            "reason": (
                "the worker exited without a result payload; the driver did "
                "not start or was interrupted, do not treat this as success"
            ),
        }
        if stdout_text:
            payload["raw_stdout"] = stdout_text[-800:]
        if worker_log_tail:
            payload["worker_log_tail"] = worker_log_tail[-1200:]
        log_path.unlink(missing_ok=True)
        print_payload(payload, args.format, _render_worker_markdown)
        return 1
    payload: dict[str, object] = {
        "ok": process.returncode == 0 and parsed.get("ok", True) is not False,
        "schema_version": "loopx_opencode2_worker_result_v0",
        "operation": "opencode2_goal_worker",
        "exit_code": process.returncode,
    }
    payload.update(parsed)
    if worker_log_tail:
        payload["worker_log_tail"] = worker_log_tail[-1200:]
    log_path.unlink(missing_ok=True)
    print_payload(payload, args.format, _render_worker_markdown)
    return process.returncode


def _render_worker_markdown(payload: dict[str, object]) -> str:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    kind = result.get("kind", "error")
    lines = [
        "# LoopX OpenCode 2 goal worker",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- exit_code: `{payload.get('exit_code')}`",
        f"- outcome: `{kind}`",
    ]
    if payload.get("worker_log_tail"):
        lines += ["", "## Worker log tail", "```", str(payload["worker_log_tail"]), "```"]
    return "\n".join(lines)
