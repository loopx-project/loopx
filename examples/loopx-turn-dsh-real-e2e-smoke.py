#!/usr/bin/env python3
"""Real DeepSeek Harness end-to-end qualification for both LoopX DSH hosts.

This smoke uses the actual ``deepseek-harness-sdk`` and the bundled dsh runtime
binary. It does not call a real DeepSeek model endpoint: a local mock
OpenAI-compatible SSE server stands in for the LLM, so the test is hermetic and
does not require DEEPSEEK_API_KEY. It proves that the adapter can start a real
dsh JSON-RPC runtime, run one bounded turn, receive a typed JSON final message,
and let LoopX validate/writeback/spend through the normal Turn chain. The
smoke clears ambient DSH home variables and supplies one explicit local home,
so a passing run proves the SDK launch prerequisite is wired by the adapter.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx.cli import main as cli_main  # noqa: E402

GOAL_ID = "loopx-turn-dsh-real-e2e"
AGENT_ID = "dsh-real-e2e"
TODO_ID = "todo_dshreale2e01"
MARKER_NAME = "docs/turn-e2e-marker.txt"
MARKER_VALUE = "loopx-turn-dsh-real-e2e-step-1"
ADAPTER = REPO_ROOT / "scripts" / "dsh_turn_host_adapter.py"


def _write_fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    workspace = root / "workspace"
    runtime.mkdir(parents=True)
    workspace.mkdir(parents=True)
    (workspace / "docs").mkdir()
    state = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state.parent.mkdir(parents=True)
    state.write_text(
        "\n".join(
            [
                "---",
                "status: active",
                "updated_at: 2026-01-01T00:00:00+00:00",
                "---",
                "",
                "# LoopX Turn DeepSeek Harness Real E2E",
                "",
                "## Agent Todo",
                "",
                (
                    f"- [ ] [P0] Ask the DeepSeek Harness runtime to produce a "
                    f"typed result; the mock LLM writes `{MARKER_NAME}`."
                ),
                (
                    f"  <!-- loopx:todo todo_id={TODO_ID} status=open "
                    "task_class=advancement_task action_kind=real_dsh_e2e "
                    f"claimed_by={AGENT_ID} priority=P0 -->"
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "loopx-turn-public-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state.relative_to(project)),
                        "adapter": {
                            "kind": "fixture_v0",
                            "status": "connected-delivery",
                        },
                        "quota": {"compute": 1.0, "window_hours": 24},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
                            "agent_profiles": {
                                AGENT_ID: {
                                    "schema_version": "agent_profile_v1",
                                    "profile_role": "fixture",
                                    "scope": "public qualification",
                                }
                            },
                            "write_scope": ["docs/**"],
                        },
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return project, runtime, workspace, registry


def _write_minimal_cordis(session_root: Path) -> Path:
    """A dsh JSON-RPC composition that avoids node-pty/subprocess.

    The bundled runtime can load this on machines where the default bash/pty
    native addon is unavailable, while still exercising the real agent core,
    JSON-RPC server, persistence, and LLM adapter.
    """
    path = session_root.parent / "no-pty.cordis.yml"
    path.write_text(
        "\n".join(
            [
                "- id: sdk-jsonrpc-server",
                "  name: '@deepseek-ai/dsh-sdk-jsonrpc-server'",
                "- id: agent-core",
                "  name: '@deepseek-ai/dsh-agent-spine-demo'",
                "  config:",
                "    workspaceContext:",
                "      maxBytes: 65536",
                "- id: llm-deepseek",
                "  name: '@deepseek-ai/dsh-llm-deepseek'",
                "- id: sessions",
                "  name: '@deepseek-ai/dsh-session-persistence-jsonl'",
                "  config:",
                f"    root: {session_root}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _validator_command() -> list[str]:
    program = (
        "import json,pathlib,sys; "
        "json.load(sys.stdin); "
        f"p=pathlib.Path({MARKER_NAME!r}); "
        "raise SystemExit(0 if p.is_file() and "
        f"p.read_text(encoding='utf-8').strip() == {MARKER_VALUE!r} else 9)"
    )
    return [sys.executable, "-c", program]


def _run_cli(argv: list[str]) -> tuple[int, dict[str, Any]]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = cli_main(argv)
    payload = json.loads(output.getvalue())
    assert isinstance(payload, dict), payload
    return exit_code, payload


def _base_argv(
    *,
    registry: Path,
    runtime: Path,
    workspace: Path,
    dsh_home: Path,
    cordis: Path,
    host: str,
    timeout_seconds: float,
) -> list[str]:
    host_command = json.dumps(
        [
            sys.executable,
            str(ADAPTER),
            "--cordis",
            str(cordis),
            "--dsh-home",
            str(dsh_home),
            "--request-timeout-seconds",
            "30",
            "--workspace",
            str(workspace),
            "--model",
            "mock-model",
        ]
    )
    argv = [
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime),
        "--format",
        "json",
        "turn",
        "run-once",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        "real-dsh-qualification-turn-1",
        "--host",
        host,
        "--execution-mode",
        "isolated-headless",
        "--project",
        str(workspace),
    ]
    if host == "generic-cli":
        argv.extend(["--host-command-json", host_command])
    else:
        argv.extend(
            [
                "--dsh-home",
                str(dsh_home),
                "--dsh-cordis",
                str(cordis),
                "--dsh-model",
                "mock-model",
            ]
        )
    argv.extend(
        [
            "--validation-command-json",
            json.dumps(_validator_command()),
            "--validation-failure-kind",
            "repair_required",
            "--no-global-sync",
            "--timeout-seconds",
            str(timeout_seconds),
        ]
    )
    return argv


def _quota_spend_count(runtime: Path) -> int:
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    if not index.is_file():
        return 0
    return sum(
        1
        for line in index.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("classification") == "quota_slot_spent"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument(
        "--host",
        choices=["generic-cli", "dsh"],
        default="generic-cli",
        help="Exercise the subprocess adapter or the built-in in-process host.",
    )
    args = parser.parse_args()

    try:
        import deepseek_harness  # noqa: F401
    except ImportError:
        print("skip: deepseek-harness-sdk is not installed")
        return 0

    result_block = json.dumps(
        {
            "result_kind": "validated_progress",
            "classification": "real_dsh_mock_llm",
            "summary": "Real dsh runtime returned a typed result through the mock LLM.",
            "recommended_action": "Review the real dsh e2e marker.",
            "next_action": "Inspect the marker and replay idempotently.",
            "vision_unchanged_reason": "The objective path is unchanged.",
        }
    )

    class MockHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("content-length", "0"))
            self.rfile.read(length)
            # The mock LLM is the only "tool" this composition has. It writes
            # the same marker an independent validator later checks.
            marker_path.write_text(MARKER_VALUE, encoding="utf-8")
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            payload = (
                'data: {"choices":[{"delta":{"role":"assistant","content":'
                + json.dumps(result_block)
                + "}}]}\n\n"
            ).encode("utf-8")
            self.wfile.write(payload)
            self.wfile.write(
                b'data: {"choices":[{"delta":{"content":""},"finish_reason":"stop"}],"usage":{"prompt_tokens":2,"completion_tokens":1}}\n\n'
            )
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, _format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    thread = threading.Thread(target=server.serve_forever, name="dsh-mock-llm", daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    old_env = {
        key: os.environ.get(key)
        for key in (
            "DEEPSEEK_BASE_URL",
            "DEEPSEEK_API_KEY",
            "DSH_CWD",
            "DSH_HOME",
            "DSH_SESSION_ROOT",
        )
    }
    try:
        os.environ["DEEPSEEK_BASE_URL"] = base_url
        os.environ["DEEPSEEK_API_KEY"] = "dsh-real-e2e-mock-key"
        for key in ("DSH_CWD", "DSH_HOME", "DSH_SESSION_ROOT"):
            os.environ.pop(key, None)
        with tempfile.TemporaryDirectory(prefix="loopx-turn-dsh-real-e2e-") as directory:
            root = Path(directory)
            project, runtime, workspace, registry = _write_fixture(root)
            session_root = root / "sessions"
            session_root.mkdir(parents=True)
            dsh_home = root / "dsh-home"
            cordis = _write_minimal_cordis(session_root)
            marker_path = workspace / MARKER_NAME

            base = _base_argv(
                registry=registry,
                runtime=runtime,
                workspace=workspace,
                dsh_home=dsh_home,
                cordis=cordis,
                host=args.host,
                timeout_seconds=args.timeout_seconds,
            )
            exit_code, payload = _run_cli([*base, "--execute"])

            marker_valid = (
                marker_path.is_file()
                and marker_path.read_text(encoding="utf-8").strip() == MARKER_VALUE
            )

            turn_key = payload.get("resume_turn_key")
            replay_exit_code, replay = (None, None)
            if exit_code == 0 and isinstance(turn_key, str):
                replay_base = list(base)
                idx = replay_base.index("--turn-instance-id")
                del replay_base[idx : idx + 2]
                replay_exit_code, replay = _run_cli(
                    [*replay_base, "--resume-turn-key", turn_key, "--execute"]
                )

            summary = {
                "schema_version": "loopx_turn_dsh_real_e2e_v1",
                "real_dsh_invoked": True,
                "host": args.host,
                "ambient_dsh_home_cleared": True,
                "configured_dsh_home_created": dsh_home.is_dir(),
                "mock_llm_base_url": base_url,
                "exit_code": exit_code,
                "status": payload.get("status"),
                "reason": payload.get("reason"),
                "result_kind": payload.get("result_kind"),
                "validation": payload.get("validation"),
                "effects": payload.get("effects"),
                "marker_valid": marker_valid,
                "quota_slot_spend_count": _quota_spend_count(runtime),
                "replay_exit_code": replay_exit_code,
                "replay_effects": replay.get("effects") if isinstance(replay, dict) else None,
                "global_registry_synced": False,
            }
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        server.shutdown()
        server.server_close()

    print(json.dumps(summary, indent=2, sort_keys=True))

    effects = summary["effects"] or {}
    expected_effects = {
        "host_invoked": True,
        "state_written": True,
        "quota_spent": True,
        "scheduler_acknowledged": False,
    }
    replay_effects = summary["replay_effects"] or {}
    expected_replay_effects = {
        "host_invoked": False,
        "state_written": False,
        "quota_spent": False,
        "scheduler_acknowledged": False,
    }
    ok = (
        exit_code == 0
        and summary["status"] == "committed"
        and summary["configured_dsh_home_created"] is True
        and summary["result_kind"] == "validated_progress"
        and (summary["validation"] or {}).get("status") == "passed"
        and effects == expected_effects
        and marker_valid
        and summary["quota_slot_spend_count"] == 1
        and replay_exit_code == 0
        and replay_effects == expected_replay_effects
        and (replay or {}).get("status") == "committed"
        and summary["global_registry_synced"] is False
    )
    if not ok:
        raise SystemExit(f"DeepSeek Harness real dsh e2e failed: {summary}")
    print("DeepSeek Harness real dsh e2e passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
