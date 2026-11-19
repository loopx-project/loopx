#!/usr/bin/env python3
"""Qualify the credential-resolved default flow for one bounded managed Turn.

The shipped operator rule is *explicit selection first*: an explicit ``--host``
or ``LOOPX_TURN_HOST`` is honoured, and the shipped default is resolved from the
operator's own credential facts -- a configured operator credential runs the
managed ``dsh`` host on that credential, and its absence runs the individual
``codex-cli`` host instead of a managed host nothing can authenticate. That rule
is only usable if the *default* command (no explicit ``--host``) actually starts
the resolved host and reports what ran.

This smoke is hermetic: a local mock OpenAI-compatible SSE server stands in for
the model endpoint, so no operator key and no individual CLI subscription is
consumed. It proves, through the public CLI only:

1. no credential: the default host is the individual ``codex-cli`` executor, so
   the default flow still runs here and claims no managed credential;
2. credential: the default host resolves to the managed ``dsh`` executor and
   reports its credential environment, its billing boundary, and its
   launchability before any work runs;
3. credential: ``turn run-once`` without ``--host`` starts the real dsh runtime,
   commits one validated Turn, and reports the mode/executor/status readback;
4. credential but an unavailable managed runtime: the same default flow fails
   closed with a typed reason and writes nothing;
5. an explicit ``--host codex-cli`` stays selected even while a credential is
   configured, and an explicit ``--host dsh`` without one is unbound.
"""

from __future__ import annotations

import contextlib
import importlib.abc
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx.cli import main as cli_main  # noqa: E402
from loopx.control_plane.turn_driver.host_binding import (  # noqa: E402
    DSH_RUNTIME_UNAVAILABLE,
    EXECUTOR_KIND_INDIVIDUAL,
    EXECUTOR_KIND_MANAGED,
    OPERATOR_CREDENTIAL_UNCONFIGURED,
)

GOAL_ID = "loopx-turn-managed-default-flow"
AGENT_ID = "codex-managed-default-flow"
TODO_ID = "todo_manageddefault01"
CREDENTIAL_ENV = "DEEPSEEK_API_KEY"
ENDPOINT_ENV = "DEEPSEEK_BASE_URL"
MARKER_NAME = "docs/managed-default-flow-marker.txt"
MARKER_VALUE = "loopx-turn-managed-default-flow-step-1"


def _write_fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    workspace = root / "workspace"
    runtime.mkdir(parents=True)
    (workspace / "docs").mkdir(parents=True)

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
                "# LoopX Managed Default Flow Fixture",
                "",
                "## Next Action",
                "",
                "Run one bounded managed Turn on the credential-resolved executor.",
                "",
                "## Agent Todo",
                "",
                "- [ ] [P1] Write the managed default flow marker.",
                "  <!-- loopx:todo "
                f"todo_id={TODO_ID} status=open task_class=advancement_task "
                "action_kind=implement claimed_by=codex-managed-default-flow -->",
                "",
                "## User Todo",
                "",
            ]
        )
        + "\n",
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
                                },
                            },
                            "write_scope": ["docs/**"],
                        },
                    },
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
    """A dsh JSON-RPC composition that avoids node-pty/subprocess."""

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
        "import pathlib,sys,json; "
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


def _plan_argv(
    registry: Path,
    runtime: Path,
    project: Path,
    *,
    host: str | None = None,
    execution_mode: str | None = None,
) -> list[str]:
    argv = [
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime),
        "--format",
        "json",
        "turn",
        "plan",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
    ]
    if host:
        argv.extend(["--host", host])
    if execution_mode:
        argv.extend(["--execution-mode", execution_mode])
    return argv


def _run_once_argv(
    *,
    registry: Path,
    runtime: Path,
    project: Path,
    workspace: Path,
    dsh_home: Path,
    cordis: Path,
    turn_instance_id: str,
    runner_binding: bool = True,
) -> list[str]:
    # No --host: the default must come from the explicit product selection, and
    # the operator credential only authenticates it. That is the surface this
    # smoke qualifies.
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
        turn_instance_id,
        "--project",
        str(workspace),
    ]
    if runner_binding:
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
            "--execute",
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


class _UnavailableHarnessRuntime(importlib.abc.MetaPathFinder):
    """Make the DeepSeek Harness runtime unimportable for one bounded call."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "deepseek_harness" or fullname.startswith("deepseek_harness."):
            raise ModuleNotFoundError(fullname)
        return None


@contextlib.contextmanager
def _harness_runtime_unavailable() -> Iterator[None]:
    """Hide an already-imported runtime for one bounded call.

    ``importlib.util.find_spec`` answers from ``sys.modules`` before consulting
    ``sys.meta_path``, so a loader-only blocker cannot simulate an absent
    runtime in a process that already imported the SDK for the skip check.
    """

    finder = _UnavailableHarnessRuntime()
    hidden = {
        name: module
        for name, module in sys.modules.items()
        if name == "deepseek_harness" or name.startswith("deepseek_harness.")
    }
    for name in hidden:
        del sys.modules[name]
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        sys.modules.update(hidden)


@contextlib.contextmanager
def _operator_credential(value: str | None) -> Iterator[None]:
    previous = os.environ.get(CREDENTIAL_ENV)
    if value is None:
        os.environ.pop(CREDENTIAL_ENV, None)
    else:
        os.environ[CREDENTIAL_ENV] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(CREDENTIAL_ENV, None)
        else:
            os.environ[CREDENTIAL_ENV] = previous


def main() -> int:
    try:
        harness_spec = importlib.util.find_spec("deepseek_harness")
    except (ImportError, ValueError):
        harness_spec = None
    if harness_spec is None:
        print("skip: deepseek-harness-sdk is not installed")
        return 0

    result_block = json.dumps(
        {
            "result_kind": "validated_progress",
            "classification": "managed_default_flow_mock_llm",
            "summary": "The default managed flow returned a typed result.",
            "recommended_action": "Review the managed default flow marker.",
            "next_action": "Inspect the marker and replay idempotently.",
            "vision_unchanged_reason": "The objective path is unchanged.",
        }
    )

    with tempfile.TemporaryDirectory(prefix="loopx-managed-default-flow-") as directory:
        root = Path(directory)
        project, runtime, workspace, registry = _write_fixture(root)
        session_root = root / "sessions"
        session_root.mkdir(parents=True)
        dsh_home = root / "dsh-home"
        cordis = _write_minimal_cordis(session_root)
        marker_path = workspace / MARKER_NAME

        class MockHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("content-length", "0"))
                self.rfile.read(length)
                # The mock model is the only tool in this composition: it
                # writes the marker that independent validation then checks.
                marker_path.write_text(MARKER_VALUE, encoding="utf-8")
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                self.wfile.write(
                    (
                        'data: {"choices":[{"delta":{"role":"assistant","content":'
                        + json.dumps(result_block)
                        + "}}]}\n\n"
                    ).encode("utf-8")
                )
                self.wfile.write(
                    b'data: {"choices":[{"delta":{"content":""},'
                    b'"finish_reason":"stop"}],"usage":{"prompt_tokens":2,'
                    b'"completion_tokens":1}}\n\n'
                )
                self.wfile.write(b"data: [DONE]\n\n")

            def log_message(self, _format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
        thread = threading.Thread(
            target=server.serve_forever, name="managed-default-mock-llm", daemon=True
        )
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        ambient = {
            key: os.environ.get(key)
            for key in (
                ENDPOINT_ENV,
                CREDENTIAL_ENV,
                "DSH_CWD",
                "DSH_HOME",
                "DSH_SESSION_ROOT",
            )
        }
        try:
            os.environ[ENDPOINT_ENV] = base_url
            for key in ("DSH_CWD", "DSH_HOME", "DSH_SESSION_ROOT"):
                os.environ.pop(key, None)

            with _operator_credential(None):
                unbound_default_exit, unbound_default_plan = _run_cli(
                    _plan_argv(registry, runtime, project)
                )
                unbound_exit, unbound_plan = _run_cli(
                    _plan_argv(
                        registry,
                        runtime,
                        project,
                        host="dsh",
                        execution_mode="isolated-headless",
                    )
                )
            with _operator_credential("managed-default-flow-mock-key"):
                managed_plan_exit, managed_plan = _run_cli(
                    _plan_argv(registry, runtime, project)
                )
                individual_exit, individual_payload = _run_cli(
                    _plan_argv(registry, runtime, project, host="codex-cli")
                )
                run_exit, run_payload = _run_cli(
                    _run_once_argv(
                        registry=registry,
                        runtime=runtime,
                        project=project,
                        workspace=workspace,
                        dsh_home=dsh_home,
                        cordis=cordis,
                        turn_instance_id="managed-default-flow-turn-1",
                    )
                )
                spend_count = _quota_spend_count(runtime)
                with _harness_runtime_unavailable():
                    unavailable_exit, unavailable_payload = _run_cli(
                        _run_once_argv(
                            registry=registry,
                            runtime=runtime,
                            project=project,
                            workspace=workspace,
                            dsh_home=dsh_home,
                            cordis=cordis,
                            turn_instance_id="managed-default-flow-turn-2",
                            runner_binding=False,
                        )
                    )
                spend_after_refusal = _quota_spend_count(runtime)
            marker_ok = (
                marker_path.is_file()
                and marker_path.read_text(encoding="utf-8").strip() == MARKER_VALUE
            )
            managed = managed_plan["managed_executor"]
            unbound = unbound_plan.get("managed_executor") or {}
            unbound_default = unbound_default_plan.get("managed_executor") or {}
            individual = individual_payload["managed_executor"]
            run_executor = run_payload.get("managed_executor") or {}
            summary = {
                "schema_version": "loopx_turn_managed_default_flow_v1",
                "mock_llm_base_url": base_url,
                "default_without_credential": {
                    "exit_code": unbound_default_exit,
                    "host_kind": unbound_default_plan.get("host", {}).get("kind"),
                    "execution_mode": unbound_default_plan.get("host", {}).get(
                        "execution_mode"
                    ),
                    "executor": unbound_default.get("executor"),
                    "executor_kind": unbound_default.get("executor_kind"),
                    "credential_env": unbound_default.get("credential_env"),
                    "operator_credential_bound": unbound_default.get(
                        "operator_credential_bound"
                    ),
                    "available": unbound_default.get("available"),
                    "unavailable_reason": unbound_default.get("unavailable_reason"),
                },
                "explicit_individual_host": {
                    "exit_code": individual_exit,
                    "host_kind": individual_payload.get("host", {}).get("kind"),
                    "executor": individual.get("executor"),
                    "executor_kind": individual.get("executor_kind"),
                    "operator_credential_bound": individual.get(
                        "operator_credential_bound"
                    ),
                    "available": individual.get("available"),
                },
                "managed_default_plan": {
                    "exit_code": managed_plan_exit,
                    "host_kind": managed_plan.get("host", {}).get("kind"),
                    "execution_mode": managed_plan.get("host", {}).get(
                        "execution_mode"
                    ),
                    "executor": managed.get("executor"),
                    "executor_kind": managed.get("executor_kind"),
                    "credential_env": managed.get("credential_env"),
                    "operator_credential_bound": managed.get(
                        "operator_credential_bound"
                    ),
                    "available": managed.get("available"),
                    "unavailable_reason": managed.get("unavailable_reason"),
                },
                "explicit_host_without_credential": {
                    "exit_code": unbound_exit,
                    "host_kind": unbound_plan.get("host", {}).get("kind"),
                    "executor_kind": unbound.get("executor_kind"),
                    "credential_env": unbound.get("credential_env"),
                    "operator_credential_bound": unbound.get(
                        "operator_credential_bound"
                    ),
                    "available": unbound.get("available"),
                    "unavailable_reason": unbound.get("unavailable_reason"),
                },
                "managed_default_run": {
                    "exit_code": run_exit,
                    "status": run_payload.get("status"),
                    "mode": run_payload.get("mode"),
                    "execution_mode": run_payload.get("execution_mode"),
                    "host_kind": run_payload.get("host", {}).get("kind"),
                    "executor": run_executor.get("executor"),
                    "executor_kind": run_executor.get("executor_kind"),
                    "operator_credential_bound": run_executor.get(
                        "operator_credential_bound"
                    ),
                    "result_kind": run_payload.get("result_kind"),
                    "validation_status": (run_payload.get("validation") or {}).get(
                        "status"
                    ),
                    "quota_slot_spend_count": run_payload.get("quota_slot_spend_count"),
                    "effects": run_payload.get("effects"),
                    "marker_valid": marker_ok,
                },
                "quota_slot_spend_count": spend_count,
                "quota_slot_spend_count_after_refusal": spend_after_refusal,
                "managed_runtime_unavailable": {
                    "exit_code": unavailable_exit,
                    "status": unavailable_payload.get("status"),
                    "reason": unavailable_payload.get("reason"),
                    "effects": unavailable_payload.get("effects"),
                    "executor_kind": unavailable_payload.get(
                        "managed_executor", {}
                    ).get("executor_kind"),
                },
                "global_registry_synced": False,
            }
        finally:
            for key, value in ambient.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            server.shutdown()
            server.server_close()

    print(json.dumps(summary, indent=2, sort_keys=True))

    effects = summary["managed_default_run"]["effects"] or {}
    ok = (
        unbound_default_exit == 0
        and summary["default_without_credential"]["host_kind"] == "codex-cli"
        and summary["default_without_credential"]["execution_mode"]
        == "interactive-visible"
        and summary["default_without_credential"]["executor_kind"]
        == EXECUTOR_KIND_INDIVIDUAL
        and summary["default_without_credential"]["credential_env"] is None
        and summary["default_without_credential"]["operator_credential_bound"] is False
        and summary["default_without_credential"]["available"] is None
        and summary["default_without_credential"]["unavailable_reason"] is None
        and individual_exit == 0
        and summary["explicit_individual_host"]["host_kind"] == "codex-cli"
        and summary["explicit_individual_host"]["executor_kind"]
        == EXECUTOR_KIND_INDIVIDUAL
        and summary["explicit_individual_host"]["operator_credential_bound"] is False
        and summary["explicit_individual_host"]["available"] is None
        and managed_plan_exit == 0
        and summary["managed_default_plan"]["host_kind"] == "dsh"
        and summary["managed_default_plan"]["execution_mode"] == "isolated-headless"
        and summary["managed_default_plan"]["executor_kind"] == EXECUTOR_KIND_MANAGED
        and summary["managed_default_plan"]["credential_env"] == CREDENTIAL_ENV
        and summary["managed_default_plan"]["operator_credential_bound"] is True
        and summary["explicit_host_without_credential"]["exit_code"] == 0
        and summary["explicit_host_without_credential"]["host_kind"] == "dsh"
        and summary["explicit_host_without_credential"]["credential_env"] is None
        and summary["explicit_host_without_credential"]["operator_credential_bound"]
        is False
        and summary["explicit_host_without_credential"]["available"] is False
        and summary["explicit_host_without_credential"]["unavailable_reason"]
        == OPERATOR_CREDENTIAL_UNCONFIGURED
        and run_exit == 0
        and summary["managed_default_run"]["status"] == "committed"
        and summary["managed_default_run"]["host_kind"] == "dsh"
        and summary["managed_default_run"]["execution_mode"] == "isolated-headless"
        and summary["managed_default_run"]["executor_kind"] == EXECUTOR_KIND_MANAGED
        and summary["managed_default_run"]["operator_credential_bound"] is True
        and summary["managed_default_run"]["quota_slot_spend_count"] == 1
        and summary["managed_default_run"]["validation_status"] == "passed"
        and effects
        == {
            "host_invoked": True,
            "state_written": True,
            "quota_spent": True,
            "scheduler_acknowledged": False,
        }
        and summary["managed_default_run"]["marker_valid"] is True
        and spend_count == 1
        and unavailable_exit != 0
        and summary["managed_runtime_unavailable"]["status"] == "unavailable"
        and summary["managed_runtime_unavailable"]["reason"] == DSH_RUNTIME_UNAVAILABLE
        and summary["managed_runtime_unavailable"]["executor_kind"]
        == EXECUTOR_KIND_MANAGED
        and (summary["managed_runtime_unavailable"]["effects"] or {})
        == {
            "host_invoked": False,
            "state_written": False,
            "quota_spent": False,
            "scheduler_acknowledged": False,
        }
        and spend_after_refusal == 1
    )
    if not ok:
        print("managed default flow smoke failed")
        return 1
    print("managed default flow smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
