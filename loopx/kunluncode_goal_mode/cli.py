"""Install, bind, inspect, and run the LoopX KunlunCode adapter."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from loopx.control_plane.projects.registry_codec import (
    load_project_registry,
    mutate_project_registry,
    require_runtime_compatible_project_registry,
)
from loopx.goal_mode_context import registered_agent_ids
from loopx.kunluncode_goal_mode import DEFAULT_AGENT_ID, MCP_SERVER_NAME
from loopx.kunluncode_goal_mode.app_server import build_app_server_command
from loopx.kunluncode_goal_mode.context import goal_context, write_binding
from loopx.kunluncode_goal_mode.runtime import (
    compact_runtime_state,
    read_runtime_state,
    run_native_goal,
)


# Single source for the adapter's `mcp` pin. It is an exact pin, not a range:
# loopx/goal_mode_mcp.py imports `mcp.server.fastmcp`, which the MCP SDK 2.x
# line no longer ships, and the pin is a deliberate security pin (892faa2c9
# "fix(security): upgrade the MCP SDK pin"). It belongs to the KunlunCode
# adapter venv only; loopx/claude_goal_mode/scripts/install.py provisions its
# own venv and spells the same dependency as a range ("mcp<2"). The two are
# separate packaging boundaries, so they are not required to agree on a
# spelling. Bump MCP_SDK_VERSION alone; the probe and the user-facing message
# below derive from it.
MCP_SDK_VERSION = "1.28.1"
MCP_REQUIREMENT = f"mcp=={MCP_SDK_VERSION}"
MCP_SCRIPT = Path(__file__).with_name("server.py").resolve()
DEFAULT_MCP_VENV = (
    Path.home() / ".local" / "share" / "loopx" / "kunluncode-mcp" / ".venv"
)


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def _loopx_prefix() -> list[str]:
    executable = shutil.which("loopx")
    return [executable] if executable else [sys.executable, "-m", "loopx.cli"]


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _compatible_python(value: str | Path) -> bool:
    probe = _run(
        [
            str(value),
            "-c",
            (
                "from importlib.metadata import version; "
                "from mcp.server.fastmcp import FastMCP; "
                "import loopx.kunluncode_goal_mode.server; "
                f"assert version('mcp') == '{MCP_SDK_VERSION}'"
            ),
        ],
        timeout=30,
    )
    return probe.returncode == 0


def _source_root() -> Path | None:
    candidate = Path(__file__).resolve().parents[2]
    return candidate if (candidate / "pyproject.toml").is_file() else None


def provision_mcp_python(*, dry_run: bool = False) -> str:
    """Provision the adapter only through uv; never mutate system Python."""
    if _compatible_python(sys.executable):
        return sys.executable
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is required to provision the KunlunCode MCP environment")
    venv_python = _venv_python(DEFAULT_MCP_VENV)
    source_root = _source_root()
    loopx_requirement = (
        ["-e", str(source_root)]
        if source_root is not None
        else [f"loopx=={importlib.metadata.version('loopx')}"]
    )
    if dry_run:
        return str(venv_python)
    DEFAULT_MCP_VENV.parent.mkdir(parents=True, exist_ok=True)
    if not venv_python.is_file():
        created = _run([uv, "venv", "--python", sys.executable, str(DEFAULT_MCP_VENV)])
        if created.returncode != 0:
            raise RuntimeError(
                "uv venv failed: " + (created.stdout + created.stderr)[-800:]
            )
    installed = _run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(venv_python),
            *loopx_requirement,
            MCP_REQUIREMENT,
        ],
        timeout=300,
    )
    if installed.returncode != 0 or not _compatible_python(venv_python):
        raise RuntimeError(
            "uv pip install failed: " + (installed.stdout + installed.stderr)[-1200:]
        )
    return str(venv_python)


def _configured_mcp_servers(kunluncode: str) -> list[dict[str, Any]]:
    result = _run([kunluncode, "config", "get", "mcp_servers"])
    if result.returncode != 0:
        raise RuntimeError("cannot read KunlunCode MCP configuration")
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError("KunlunCode returned invalid mcp_servers JSON") from exc
    return [item for item in payload if isinstance(item, dict)]


def _is_managed_server(entry: dict[str, Any]) -> bool:
    args = list(entry.get("args") or [])
    return (
        entry.get("name") == MCP_SERVER_NAME
        and len(args) == 1
        and str(args[0])
        .replace("\\", "/")
        .endswith("/loopx/kunluncode_goal_mode/server.py")
    )


def _mcp_add_command(
    kunluncode: str,
    *,
    name: str,
    command: str,
    args: list[str],
) -> list[str]:
    result = [
        kunluncode,
        "mcp",
        "add",
        name,
        "--command",
        command,
    ]
    if args:
        result.extend(["--args", *args])
    return result


def _restorable_mcp_command(
    kunluncode: str, entry: dict[str, Any]
) -> list[str] | None:
    command = entry.get("command")
    args = entry.get("args", [])
    if not isinstance(command, str) or not command.strip() or not isinstance(args, list):
        return None
    if not all(isinstance(argument, str) for argument in args):
        return None
    return _mcp_add_command(
        kunluncode,
        name=str(entry.get("name") or MCP_SERVER_NAME),
        command=command,
        args=args,
    )


def install_mcp(*, python: str | None, dry_run: bool, replace: bool) -> str:
    kunluncode = shutil.which("kunluncode")
    if not kunluncode:
        raise RuntimeError("kunluncode is not on PATH")
    if python:
        selected_path = Path(python).expanduser()
        if not selected_path.is_absolute():
            selected_path = Path.cwd() / selected_path
        selected_python = str(selected_path.absolute())
    else:
        selected_python = provision_mcp_python(dry_run=dry_run)
    if not dry_run and not _compatible_python(selected_python):
        raise RuntimeError(
            f"{selected_python} must import LoopX and {MCP_REQUIREMENT}; "
            "use uv to sync the adapter environment"
        )
    existing = next(
        (
            item
            for item in _configured_mcp_servers(kunluncode)
            if item.get("name") == MCP_SERVER_NAME
        ),
        None,
    )
    if existing and not _is_managed_server(existing) and not replace:
        raise RuntimeError(
            f"KunlunCode MCP server {MCP_SERVER_NAME!r} is user-owned; pass --replace to replace it"
        )
    restore_command = (
        _restorable_mcp_command(kunluncode, existing) if existing else None
    )
    if existing and restore_command is None:
        raise RuntimeError(
            f"KunlunCode MCP server {MCP_SERVER_NAME!r} cannot be safely replaced because its "
            "registration cannot be restored with `mcp add`; back it up and remove it manually"
        )
    if dry_run:
        return selected_python
    if existing:
        removed = _run([kunluncode, "mcp", "remove", MCP_SERVER_NAME])
        if removed.returncode != 0:
            raise RuntimeError("failed to remove prior managed KunlunCode MCP entry")
    added = _run(
        _mcp_add_command(
            kunluncode,
            name=MCP_SERVER_NAME,
            command=selected_python,
            args=[str(MCP_SCRIPT)],
        )
    )
    if added.returncode != 0:
        if restore_command is not None:
            restored = _run(restore_command)
            if restored.returncode == 0:
                raise RuntimeError(
                    "KunlunCode MCP registration failed; the previous entry was restored: "
                    + (added.stdout + added.stderr)[-800:]
                )
            raise RuntimeError(
                "KunlunCode MCP registration failed and automatic restoration of the previous "
                "entry also failed; inspect `kunluncode config get mcp_servers` before retrying"
            )
        raise RuntimeError(
            "KunlunCode MCP registration failed: "
            + (added.stdout + added.stderr)[-800:]
        )
    return selected_python


def uninstall_mcp(*, dry_run: bool) -> bool:
    kunluncode = shutil.which("kunluncode")
    if not kunluncode:
        raise RuntimeError("kunluncode is not on PATH")
    existing = next(
        (
            item
            for item in _configured_mcp_servers(kunluncode)
            if item.get("name") == MCP_SERVER_NAME
        ),
        None,
    )
    if existing is None:
        return False
    if not _is_managed_server(existing):
        raise RuntimeError(
            f"refusing to remove user-owned MCP server {MCP_SERVER_NAME!r}"
        )
    if not dry_run:
        removed = _run([kunluncode, "mcp", "remove", MCP_SERVER_NAME])
        if removed.returncode != 0:
            raise RuntimeError("failed to remove KunlunCode MCP entry")
    return True


def _registry_path(project: Path) -> Path:
    current = project / ".loopx" / "registry.json"
    legacy = project / ".goal-harness" / "registry.json"
    return current if current.is_file() or not legacy.is_file() else legacy


def _annotate_registry(registry: Path, *, goal_id: str, agent_id: str) -> None:
    def reduce(payload: dict[str, Any]) -> None:
        goals = payload.get("goals") or []
        if not any(
            str(goal.get("id") or "") == goal_id
            for goal in goals
            if isinstance(goal, dict)
        ):
            raise RuntimeError(f"goal {goal_id!r} is not registered in {registry}")
        backends = payload.setdefault("agent_backends", [])
        if "kunluncode" not in backends:
            backends.append("kunluncode")

    mutate_project_registry(
        registry,
        operation="kunluncode_registry_annotate",
        reducer=reduce,
    )
    write_binding(registry.parent.parent, goal_id=goal_id, agent_id=agent_id)


def _registered_agents_for_goal(registry: Path, goal_id: str) -> list[str]:
    payload = load_project_registry(registry)
    require_runtime_compatible_project_registry(
        payload,
        operation="KunlunCode Goal adapter",
    )
    goal = next(
        (
            item
            for item in payload.get("goals") or []
            if isinstance(item, dict) and str(item.get("id") or "") == goal_id
        ),
        None,
    )
    if goal is None:
        raise RuntimeError(f"goal {goal_id!r} is not registered in {registry}")
    return list(registered_agent_ids(goal))


def _bootstrap_if_needed(
    project: Path,
    *,
    goal_id: str,
    objective: str | None,
    dry_run: bool,
) -> Path:
    registry = _registry_path(project)
    if registry.is_file():
        return registry
    if not objective:
        raise RuntimeError(
            "project has no LoopX registry; --objective is required for first connect"
        )
    command = [
        *_loopx_prefix(),
        "bootstrap",
        "--project",
        str(project),
        "--goal-id",
        goal_id,
        "--objective",
        objective,
        "--state-file",
        f".loopx/goals/{goal_id}/ACTIVE_GOAL_STATE.md",
        "--no-global-sync",
    ]
    if dry_run:
        return registry
    result = _run(command, cwd=project)
    if result.returncode != 0:
        raise RuntimeError(
            "LoopX bootstrap failed: " + (result.stdout + result.stderr)[-1200:]
        )
    return registry


def connect_project(
    *,
    project: Path,
    goal_id: str,
    agent_id: str,
    objective: str | None,
    python: str | None,
    skip_mcp: bool,
    replace: bool,
    dry_run: bool,
) -> dict[str, Any]:
    project = project.resolve()
    registry = _bootstrap_if_needed(
        project, goal_id=goal_id, objective=objective, dry_run=dry_run
    )
    if not dry_run:
        registered_agents = _registered_agents_for_goal(registry, goal_id)
        if agent_id not in registered_agents:
            registered_agents.append(agent_id)
        agent_arguments = [
            argument
            for registered_agent in registered_agents
            for argument in ("--registered-agent", registered_agent)
        ]
        configured = _run(
            [
                *_loopx_prefix(),
                "--registry",
                str(registry),
                "configure-goal",
                "--goal-id",
                goal_id,
                "--agent-model",
                "peer_v1",
                *agent_arguments,
                "--execute",
            ],
            cwd=project,
        )
        if configured.returncode != 0:
            raise RuntimeError(
                "LoopX agent registration failed: "
                + (configured.stdout + configured.stderr)[-1200:]
            )
        _annotate_registry(registry, goal_id=goal_id, agent_id=agent_id)
    selected_python = None
    if not skip_mcp:
        selected_python = install_mcp(python=python, dry_run=dry_run, replace=replace)
    return {
        "ok": True,
        "dry_run": dry_run,
        "project": str(project),
        "registry": str(registry),
        "goal_id": goal_id,
        "agent_id": agent_id,
        "binding": str(project / ".loopx" / "kunluncode.json"),
        "mcp_server": None if skip_mcp else MCP_SERVER_NAME,
        "mcp_python": selected_python,
        "global_mcp_registration": not skip_mcp,
        "native_app_server": True,
        "default_run_mode": "goal-pro",
    }


def add_todo(project: Path, text: str) -> dict[str, Any]:
    context = goal_context(project)
    if context is None:
        raise RuntimeError(
            "KunlunCode is not bound; run loopx-kunluncode connect first"
        )
    result = _run(
        [
            *_loopx_prefix(),
            "--registry",
            context["registry"],
            "--format",
            "json",
            "todo",
            "add",
            "--goal-id",
            context["goal_id"],
            "--role",
            "agent",
            "--text",
            text,
            "--claimed-by",
            context["agent_id"],
        ],
        cwd=project,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "todo add failed: " + (result.stdout + result.stderr)[-1000:]
        )
    return json.loads(result.stdout)


def worker_prompt(agent_id: str) -> str:
    return (
        f"You are the LoopX KunlunCode worker bound as agent {agent_id!r}. "
        f"Use the {MCP_SERVER_NAME} MCP lifecycle tools; do not use a Claude binding or guess ids. "
        "Call should_run first. If should_run is false, report the reason and stop. "
        "If true, claim exactly the selected todo with claim_task, perform one bounded segment, "
        "verify it with a real check, then call complete_task with the same agent_id and compact "
        "public-safe evidence. Supply either next_agent_todo or no_follow_up=true. "
        "Leave the unused field empty and never reuse the completed todo_id as next_agent_todo. "
        "When the selected todo already names a check, run that check directly instead of exploring unrelated help or files. "
        "Re-check should_run once and stop. Stay inside the projected goal boundary; writes, "
        "publishing, destructive actions, and production actions still require their existing authority."
    )


def run_worker(
    project: Path,
    *,
    permission_mode: str,
    max_duration_secs: int,
    json_output: bool,
    dry_run: bool,
    mode: str = "goal-pro",
    controller_timeout_secs: int = 3600,
    token_budget: int | None = None,
) -> int:
    context = goal_context(project)
    if context is None:
        raise RuntimeError(
            "KunlunCode is not bound; run loopx-kunluncode connect first"
        )
    kunluncode = shutil.which("kunluncode")
    if not kunluncode:
        raise RuntimeError("kunluncode is not on PATH")
    if mode != "headless":
        if dry_run:
            command = build_app_server_command(
                kunluncode,
                project=project,
                permission_mode=permission_mode,
                max_duration_secs=max_duration_secs,
            )
            payload = {
                "ok": True,
                "dry_run": True,
                "mode": mode,
                "command": command,
                "side_effects": "none",
            }
            print(
                json.dumps(payload, indent=2)
                if json_output
                else " ".join(command) + " <native-goal-controller>"
            )
            return 0
        payload = run_native_goal(
            project,
            context,
            mode=mode,
            permission_mode=permission_mode,
            max_duration_secs=max_duration_secs,
            controller_timeout_secs=controller_timeout_secs,
            token_budget=token_budget,
            kunluncode_bin=kunluncode,
        )
        if json_output:
            print(json.dumps(payload, indent=2))
        elif payload.get("status") == "skipped":
            print(
                "KunlunCode native Goal skipped: "
                + str(payload.get("reason") or "not due")
            )
        else:
            runtime = (
                payload.get("runtime")
                if isinstance(payload.get("runtime"), dict)
                else {}
            )
            print(
                f"KunlunCode {mode} committed for todo {runtime.get('todo_id')}; "
                "native verification and LoopX writeback completed."
            )
        return 0
    command = [
        kunluncode,
        "--cwd",
        str(project.resolve()),
        "--permission-mode",
        permission_mode,
        "--tool-profile",
        "full",
        "--max-duration-secs",
        str(max_duration_secs),
    ]
    if json_output:
        command.append("--json")
    command.append(worker_prompt(context["agent_id"]))
    if dry_run:
        print(" ".join(command[:-1]) + " <loopx-worker-prompt>")
        return 0
    environment = dict(os.environ)
    adapter_bin = str(Path(sys.executable).absolute().parent)
    environment["PATH"] = adapter_bin + os.pathsep + environment.get("PATH", "")
    try:
        return subprocess.run(
            command,
            cwd=str(project.resolve()),
            env=environment,
            timeout=1800,
        ).returncode
    except subprocess.TimeoutExpired:
        print("KunlunCode adapter execution timed out after 1800 seconds", file=sys.stderr)
        return 124


def status(project: Path) -> dict[str, Any]:
    context = goal_context(project)
    if context is None:
        raise RuntimeError(
            "KunlunCode is not bound; run loopx-kunluncode connect first"
        )
    result = _run(
        [
            *_loopx_prefix(),
            "--registry",
            context["registry"],
            "--format",
            "json",
            "quota",
            "should-run",
            "--goal-id",
            context["goal_id"],
            "--agent-id",
            context["agent_id"],
            "--runtime-profile",
            "kunluncode",
            "--available-capability",
            "shell",
            "--available-capability",
            "filesystem_write",
        ],
        cwd=project,
    )
    if result.returncode != 0:
        raise RuntimeError("status failed: " + (result.stdout + result.stderr)[-1000:])
    payload = json.loads(result.stdout)
    payload["kunluncode_native_goal"] = compact_runtime_state(
        read_runtime_state(project.resolve())
    )
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loopx-kunluncode")
    subparsers = parser.add_subparsers(dest="command", required=True)

    connect = subparsers.add_parser(
        "connect", help="Bind one project goal and install the MCP server."
    )
    connect.add_argument("--project", default=".")
    connect.add_argument("--goal-id", required=True)
    connect.add_argument("--agent-id", default=DEFAULT_AGENT_ID)
    connect.add_argument("--objective")
    connect.add_argument("--python")
    connect.add_argument("--skip-mcp", action="store_true")
    connect.add_argument("--replace", action="store_true")
    connect.add_argument("--dry-run", action="store_true")

    install = subparsers.add_parser(
        "install", help="Install the global KunlunCode MCP entry."
    )
    install.add_argument("--python")
    install.add_argument("--replace", action="store_true")
    install.add_argument("--dry-run", action="store_true")

    uninstall = subparsers.add_parser(
        "uninstall", help="Remove only the managed KunlunCode MCP entry."
    )
    uninstall.add_argument("--dry-run", action="store_true")

    add = subparsers.add_parser(
        "add", help="Add and assign one todo to the bound KunlunCode agent."
    )
    add.add_argument("text", nargs="+")
    add.add_argument("--project", default=".")

    run = subparsers.add_parser(
        "run",
        help="Run a recoverable native KunlunCode Goal (Goal Pro by default).",
    )
    run.add_argument("--project", default=".")
    run.add_argument(
        "--mode",
        choices=["goal-pro", "goal", "headless"],
        default="goal-pro",
        help=(
            "goal-pro uses strict independent verification; goal uses native Arrangement; "
            "headless preserves the legacy one-turn MCP worker"
        ),
    )
    run.add_argument(
        "--permission-mode",
        choices=["ask", "auto", "accept-edits", "dont-ask", "bypass", "yolo"],
        default="auto",
    )
    run.add_argument("--max-duration-secs", type=int, default=600)
    run.add_argument(
        "--controller-timeout-secs",
        type=int,
        default=3600,
        help="Overall wait budget across native Goal auto-continuations.",
    )
    run.add_argument(
        "--token-budget",
        type=int,
        help="Optional positive KunlunCode native Goal token budget.",
    )
    run.add_argument("--json", action="store_true")
    run.add_argument("--dry-run", action="store_true")

    status_parser = subparsers.add_parser(
        "status", help="Read the bound KunlunCode lane status."
    )
    status_parser.add_argument("--project", default=".")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "connect":
            payload = connect_project(
                project=Path(args.project),
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                objective=args.objective,
                python=args.python,
                skip_mcp=args.skip_mcp,
                replace=args.replace,
                dry_run=args.dry_run,
            )
            print(json.dumps(payload, indent=2))
            return 0
        if args.command == "install":
            selected = install_mcp(
                python=args.python, dry_run=args.dry_run, replace=args.replace
            )
            print(
                json.dumps(
                    {
                        "ok": True,
                        "mcp_server": MCP_SERVER_NAME,
                        "python": selected,
                        "dry_run": args.dry_run,
                    },
                    indent=2,
                )
            )
            return 0
        if args.command == "uninstall":
            removed = uninstall_mcp(dry_run=args.dry_run)
            print(
                json.dumps(
                    {"ok": True, "removed": removed, "dry_run": args.dry_run}, indent=2
                )
            )
            return 0
        if args.command == "add":
            print(
                json.dumps(add_todo(Path(args.project), " ".join(args.text)), indent=2)
            )
            return 0
        if args.command == "run":
            return run_worker(
                Path(args.project),
                permission_mode=args.permission_mode,
                max_duration_secs=args.max_duration_secs,
                json_output=args.json,
                dry_run=args.dry_run,
                mode=args.mode,
                controller_timeout_secs=args.controller_timeout_secs,
                token_budget=args.token_budget,
            )
        if args.command == "status":
            print(json.dumps(status(Path(args.project)), indent=2))
            return 0
    except (
        OSError,
        RuntimeError,
        json.JSONDecodeError,
        importlib.metadata.PackageNotFoundError,
    ) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
