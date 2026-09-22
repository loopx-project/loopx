"""Example host composition: both coordinator and members use ordinary Turns."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent


def host_arguments(root: Path, actor: str, revision: str, *, host: str, attempt: int = 0) -> list[str]:
    settings = json.loads((root / "settings.json").read_text())
    coordinator = actor == "lead"
    workspace = root / "lead" if coordinator else root / actor / revision
    if host == "codex":
        # Delegation injects its existing native MCP tools. Model/effort are an
        # independent Turn binding; no user profile or credential is copied.
        return ["--host", "codex-cli", "--codex-model", "gpt-5.6-luna",
                "--codex-reasoning-effort", "max", "--codex-sandbox", "workspace-write"]
    if host == "dsh":
        args = ["--host", "dsh", "--dsh-model", settings["dsh_model"], "--dsh-reasoning-effort", "high",
                "--dsh-home", str(root / "homes" / (actor + "-" + revision + "-" + str(attempt)))]
        patch = root / (actor + "-" + revision + "-mcp.yml")
        forwarded = ["DEEPSEEK_API_KEY", "ARK_API_KEY"] if coordinator else []
        forwarded += [name for name in ("DEEPSEEK_BASE_URL", "ARK_BASE_URL") if coordinator and os.environ.get(name)]
        document = json.dumps([{"insert": [{
            "id": "research-team", "name": "@deepseek-ai/dsh-mcp-client",
            "config": {"transport": "stdio", "serverName": "research_team",
                       "command": sys.executable,
                       "args": [str(HERE / "server.py"), "--local-root", str(root),
                                *([] if coordinator else ["--worker", actor, "--revision", revision])],
                       "env": {name: "__environment_" + name + "__" for name in forwarded},
                       "toolCallTimeoutMs": 60_000,
                       "cwd": str(workspace), "failOnStartupError": True},
        }]}])
        for name in forwarded:
            document = document.replace(json.dumps("__environment_" + name + "__"), "!!js process.env." + name)
        patch.write_text(document)
        args.extend(["--dsh-cordis", str(patch)])
        return args
    if host != "ark":
        raise ValueError("unqualified_example_host")
    selected_tools = (["read_assignment", "read_accepted_evidence", "write_report"] if coordinator
                      else ["read_input", "write_output", "read_context", "assess_request"])
    selected_tools += ["list_execution_bindings", "start_delegation", "wait_delegation", "resume_delegation"]
    profile = root / (actor + "-" + revision + "-ark.json")
    profile.write_text(json.dumps({
        "model": settings["ark_model"], "environment_id": settings["environment_id"],
        "workspace": str(workspace), "state_dir": str(root / "provider-receipts"),
        "timeout_seconds": 1100 if coordinator or actor == "cloud-analyst" else 220,
        "tool_timeout_seconds": 30, "max_tool_calls": 40 if coordinator or actor == "cloud-analyst" else 12,
        "mcp_command": [sys.executable, str(HERE / "server.py"),
                        *([] if coordinator else ["--worker", actor, "--revision", revision])],
        "mcp_env": ["LOOPX_RESEARCH_DEMO_ROOT", *(["DEEPSEEK_API_KEY"] if coordinator or actor == "cloud-analyst" else [])],
        "tool_names": selected_tools,
    }))
    return ["--host", "generic-cli", "--iteration-context", "fresh", "--host-command-json",
            json.dumps([sys.executable, "-m", "loopx_ark_turn.cli", "--config", str(profile)])]


def configure_delegations(root: Path) -> Path:
    """The example supplies policy/config only; core owns execution and return."""
    from scenario import assignments
    rows = []
    for member in assignments(root):
        actor, revision = member["worker"], member["revision"]
        rows.append({"id": actor + "/" + revision, "agent_id": actor,
                     "todo_id": "todo_" + actor + "-" + revision,
                     "requesters": [member.get("requester", "lead")],
                     "workspace": str(root / actor / revision),
                     "host_args": host_arguments(root, actor, revision, host=member["host"]),
                     "timeout_seconds": 1200 if actor == "cloud-analyst" else 300,
                     "output_refs": ["output.json"]})
    path = root / "delegation-config.json"
    path.write_text(json.dumps({"schema_version": "loopx_local_delegation_v0", "bindings": rows}))
    return path
