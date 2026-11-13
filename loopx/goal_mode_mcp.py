"""Shared MCP control plane for host-specific LoopX goal adapters."""
from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from .control_plane.host_adapter_settlement import (
    McpGuardContent,
    HostTodoSettlementRequest,
    host_adapter_turn_instance_id,
    host_vision_request,
    project_host_interaction,
    refresh_host_todo_vision,
    settle_host_todo_completion,
)


ContextResolver = Callable[[], dict[str, Any] | None]

@dataclass(frozen=True)
class GoalModeMCPConfig:
    server_name: str
    runtime_profile: str
    legacy_host_surface: str
    setup_hint: str = "connect this host adapter first"


class GoalModeMCPControlPlane:
    """Deterministic lifecycle operations exposed through FastMCP."""

    def __init__(self, config: GoalModeMCPConfig, context_resolver: ContextResolver):
        self.config = config
        self.context_resolver = context_resolver
        self.controller_owns_settlement = False

    def state(self) -> dict[str, Any]:
        return self.context_resolver() or {}

    def context(self) -> tuple[str | None, str | None]:
        state = self.state()
        return state.get("goal_id"), state.get("registry")

    def bound_agent_id(self) -> str | None:
        return self.state().get("agent_id")

    def goal_ref(self) -> dict[str, str] | None:
        value = self.state().get("goal_ref")
        if not isinstance(value, Mapping):
            return None
        goal_id = value.get("goal_id")
        goal_instance_id = value.get("goal_instance_id")
        if not isinstance(goal_id, str) or not isinstance(goal_instance_id, str):
            return None
        return {
            "goal_id": goal_id,
            "goal_instance_id": goal_instance_id,
        }

    def _allow_terminal_stride_path(self, goal_id: str, vision: dict[str, Any] | None) -> bool:
        """Use the same effective Goal cadence as formal writeback before Todo effects."""
        if not isinstance(vision, dict) or not isinstance(vision.get("path_delta"), dict):
            return False
        if vision["path_delta"].get("outcome") != "stop" or "stride_decision" not in vision:
            return False
        from .capabilities.todo_replan_cadence.machine_defaults import resolve_todo_replan_cadence_goal
        from .control_plane.projects.registry_codec import load_registry
        from .paths import (default_registry_path, global_registry_path,
                            resolve_runtime_root, select_default_runtime_root)
        from .registry import find_registry_goal

        selected = self.context()[1]
        if selected == "@host-global":
            registry_path = global_registry_path(select_default_runtime_root())
        else:
            registry_path = Path(selected).expanduser() if selected else default_registry_path()
            if not selected and not registry_path.exists():
                registry_path = global_registry_path(select_default_runtime_root())
        registry = load_registry(registry_path)
        goal = find_registry_goal(registry, goal_id)
        if goal is None:
            raise ValueError(f"{goal_id}: registry does not contain the Goal for Vision preflight")
        runtime_root = resolve_runtime_root(registry, registry_path=registry_path)
        effective = resolve_todo_replan_cadence_goal(goal, runtime_root)
        return (effective.get("execution_profile") or {}).get("stride_strategy") == "evidence_conditioned_v1"

    def command_prefix(self) -> list[str]:
        # The MCP server and its CLI exchange a private settlement contract.
        # Resolve the CLI from this server's package, even if PATH or the
        # current workspace contains a different LoopX version.
        package_root = Path(__file__).resolve().parent.parent
        launcher = (
            "import runpy,sys;"
            "sys.path.insert(0,sys.argv.pop(1));"
            "runpy.run_module('loopx.entrypoint',run_name='__main__')"
        )
        return [sys.executable, "-P", "-c", launcher, str(package_root)]

    def run_cli(self, args: list[str]) -> str:
        _, registry = self.context()
        command = self.command_prefix()
        if registry:
            command += ["--registry", registry]
        command += ["--format", "json"]
        # MCP projects the original decision itself. Pin off only for this
        # subprocess so machine-level context delivery cannot wrap the input.
        environment = {**os.environ, "LOOPX_CONTEXT_DELIVERY": "off"}
        result = subprocess.run(
            [*command, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            env=environment,
        )
        return (result.stdout or "") + (
            ("\n" + result.stderr) if result.returncode else ""
        )

    def no_goal_message(self) -> str:
        return json.dumps(
            {
                "ok": False,
                "error": "goal-mode is not active for this host in this project",
                "next_action": self.config.setup_hint,
            }
        )

    def _identity_error(self, requested_agent_id: str) -> str | None:
        bound = self.bound_agent_id()
        if bound and requested_agent_id == bound:
            return None
        return json.dumps(
            {
                "ok": False,
                "error": "agent_id does not match the host binding",
                "requested_agent_id": requested_agent_id,
                "bound_agent_id": bound,
            }
        )

    def should_run_args(self, goal_id: str, agent_id: str | None) -> list[str]:
        args = ["quota", "should-run", "--goal-id", goal_id, "--complete-turn",
                "--runtime-profile", self.config.runtime_profile]
        if agent_id:
            args += ["--agent-id", agent_id]
        return args

    def _read_guard(self, todo_id: str | None = None) -> str | McpGuardContent:
        """Read the decision, optionally admitting a chosen Todo under the host Turn."""
        goal_id, _ = self.context()
        if not goal_id:
            return self.no_goal_message()
        agent_id = self.bound_agent_id()
        args = self.should_run_args(goal_id, agent_id)
        if todo_id is not None:
            if self.controller_owns_settlement:
                return json.dumps({"ok": False, "reason": "The outer controller owns Todo admission."})
            if not agent_id:
                return json.dumps({"ok": False, "reason": "A bound Agent is required to admit a Todo."})
            goal_ref = self.goal_ref()
            turn_id = host_adapter_turn_instance_id(HostTodoSettlementRequest(
                goal_id=goal_id, agent_id=agent_id, todo_id=todo_id,
                runtime_profile=self.config.runtime_profile, completion_args=(), goal_ref=goal_ref,
            ))
            args += ["--todo-id", todo_id, "--turn-instance-id", turn_id, "--host-todo-admission"]
            if goal_ref:
                args += ["--goal-instance-id", goal_ref["goal_instance_id"]]
        return project_host_interaction(
            self.run_cli(args), controller_owns_settlement=self.controller_owns_settlement,
        )

    def should_run(self, todo_id: str | None = None) -> str:
        result = self._read_guard(todo_id)
        return result.source if isinstance(result, McpGuardContent) else result

    def list_todos(self) -> str:
        return self.should_run()

    def host_prompt(self) -> str:
        from .claude_goal_mode.scripts.goalmode_cmd import loop_execution_content
        state = self.state()
        goal_id, agent_id = state.get("goal_id"), state.get("agent_id")
        if not goal_id or not agent_id:
            return json.dumps({"ok": False, "error": "bound Goal and agent are required"})
        return json.dumps({"ok": True, "goal_id": goal_id, "agent_id": agent_id,
                           "task_body": loop_execution_content(goal_id, agent_id)})

    def claim_task(self, todo_id: str, agent_id: str) -> str:
        goal_id, _ = self.context()
        if not goal_id:
            return self.no_goal_message()
        identity_error = self._identity_error(agent_id)
        if identity_error:
            return identity_error
        return self.run_cli(
            [
                "todo",
                "claim",
                "--goal-id",
                goal_id,
                "--todo-id",
                todo_id,
                "--claimed-by",
                agent_id,
                "--agent-id",
                agent_id,
            ]
        )

    def complete_task(
        self,
        todo_id: str,
        agent_id: str,
        evidence: str,
        next_agent_todo: str = "",
        claim_operation_id: str | None = None,
        no_follow_up: bool = False,
        successor_todo_ids: list[str] | None = None,
        agent_vision: dict[str, Any] | None = None,
        vision_unchanged_reason: str = "",
    ) -> str:
        goal_id, _ = self.context()
        if not goal_id:
            return self.no_goal_message()
        identity_error = self._identity_error(agent_id)
        if identity_error:
            return identity_error
        if next_agent_todo and no_follow_up:
            return json.dumps(
                {
                    "ok": False,
                    "error": "next_agent_todo and no_follow_up are mutually exclusive",
                }
            )
        if successor_todo_ids is not None and (
            not isinstance(successor_todo_ids, list)
            or any(not isinstance(value, str) or not value.strip() for value in successor_todo_ids)
        ):
            return json.dumps({"ok": False, "error": "successor_todo_ids must be a list of nonempty ids"})
        if successor_todo_ids and (next_agent_todo or no_follow_up):
            return json.dumps({"ok": False, "error": "choose existing successors, a new successor, or no follow-up"})
        args = [
            "todo",
            "complete",
            "--goal-id",
            goal_id,
            "--todo-id",
            todo_id,
            "--claimed-by",
            agent_id,
            "--agent-id",
            agent_id,
            "--evidence",
            evidence,
        ]
        if next_agent_todo:
            args += ["--next-agent-todo", next_agent_todo]
        for successor in successor_todo_ids or []:
            args += ["--successor-todo-id", successor]
        if claim_operation_id is not None:
            args += ["--claim-operation-id", claim_operation_id]
        if no_follow_up:
            args.append("--no-follow-up")
        request = HostTodoSettlementRequest(
            goal_id=goal_id,
            agent_id=agent_id,
            todo_id=todo_id,
            runtime_profile=self.config.runtime_profile,
            completion_args=tuple(args),
            no_follow_up=no_follow_up,
            goal_ref=self.goal_ref(),
        )
        with host_vision_request(request, agent_vision, vision_unchanged_reason,
                                 allow_terminal_stride_path=self._allow_terminal_stride_path(goal_id, agent_vision)) as authored:
            return settle_host_todo_completion(authored, run_cli=self.run_cli)

    def review_task_vision(
        self, todo_id: str, agent_id: str, agent_vision: dict[str, Any] | None = None,
        vision_unchanged_reason: str = "",
        read_context_id: str = "",
    ) -> str:
        goal_id, _ = self.context()
        if not goal_id:
            return self.no_goal_message()
        identity_error = self._identity_error(agent_id)
        if identity_error:
            return identity_error
        request = HostTodoSettlementRequest(
            goal_id=goal_id, agent_id=agent_id, todo_id=todo_id,
            runtime_profile=self.config.runtime_profile,
            completion_args=(),
            checkpoint_read_context_id=read_context_id or None,
            goal_ref=self.goal_ref(),
        )
        with host_vision_request(request, agent_vision, vision_unchanged_reason,
                                 allow_terminal_stride_path=self._allow_terminal_stride_path(goal_id, agent_vision)) as authored:
            return refresh_host_todo_vision(authored, run_cli=self.run_cli)


def create_fastmcp_server(
    config: GoalModeMCPConfig,
    context_resolver: ContextResolver,
):
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.server.fastmcp.exceptions import ToolError
        from mcp.types import CallToolResult, TextContent
        from pydantic import BaseModel
    except Exception as exc:  # pragma: no cover - exercised by real adapter startup
        raise SystemExit(
            "MCP SDK v1 is required. Install the adapter with loopx-kunluncode install.\n"
            + str(exc)
        ) from exc

    control = GoalModeMCPControlPlane(config, context_resolver)
    completion_arguments = frozenset(inspect.signature(control.complete_task).parameters)

    class GoalModeServer(FastMCP):
        async def call_tool(self, name: str, arguments: dict[str, Any]):
            # FastMCP ignores unknown arguments by default. Retired execution
            # proof must reject, never disappear before a completion write.
            if name == "complete_task" and set(arguments) - completion_arguments:
                raise ToolError("Unsupported complete_task arguments; use its current schema and the claim_operation_id returned by claim_task")
            return await super().call_tool(name, arguments)

    class GuardToolOutput(BaseModel):
        result: str
        mcp_channel: dict[str, Any] | None = None

    server = GoalModeServer(config.server_name)

    def guard_content(projected: str | McpGuardContent) -> CallToolResult:
        if isinstance(projected, McpGuardContent):
            channel = dict(projected.channel)
            return CallToolResult(
                content=[
                    TextContent(type="text", text=projected.source),
                    TextContent(type="text", text=json.dumps(channel, ensure_ascii=False)),
                ],
                structuredContent={"result": projected.source, "mcp_channel": channel},
            )
        return CallToolResult(
            content=[TextContent(type="text", text=projected)],
            structuredContent={"result": projected},
        )

    if config.legacy_host_surface == "claude_code":
        @server.tool()
        def host_prompt() -> str:
            """Read current Claude Goal execution rules for this server's bound identity."""
            return control.host_prompt()

    def should_run(todo_id: str | None = None) -> Any:
        """Read the current decision. Pass the chosen ordinary todo_id to admit work.
        The bound Agent and Turn match complete_task. Read the returned admission
        before work and reuse the same todo_id on retry. Independent replans use
        the CLI actions in the original decision.
        """
        return guard_content(control._read_guard(todo_id))

    def list_todos() -> Any:
        """Read the complete Todo decision and its separate MCP guidance."""
        return guard_content(control._read_guard())

    # Supply the evaluated SDK annotation before registration. Collection
    # diagnostics have a string result and no separate MCP channel.
    for read_guard in (should_run, list_todos):
        read_guard.__annotations__["return"] = Annotated[CallToolResult, GuardToolOutput]
        server.tool()(read_guard)

    @server.tool()
    def claim_task(todo_id: str, agent_id: str) -> str:
        """Claim one Todo; retain its claim_operation_id for updates and completion."""
        return control.claim_task(todo_id, agent_id)

    @server.tool()
    def review_task_vision(
        todo_id: str, agent_id: str, agent_vision: dict[str, Any] | None = None,
        vision_unchanged_reason: str = "",
        read_context_id: str = "",
    ) -> str:
        """Supply a missing vision decision for a previously completed MCP Todo.
        First call with only todo_id and agent_id to read the current basis.
        Judge that basis, then call again with its read_context_id and one decision.
        On stale/replaced context, read and judge again; do not reuse the old decision.
        Retry a lost response with the same receipt and decision, without a new read.
        Uses its original Turn, never repeats work or spends again. agent_vision is
        a goal_vision_replan_contract_v0 packet with state and vision_patch fields.
        Compare Goal acceptance with evidence; vision_closed closes a stage and
        still requires a successor, no_followup asserts no remaining scoped work.
        An unchanged reason requires an existing valid vision. Recheck should_run;
        checkpoint success alone does not certify Goal completion or clear gates.
        """
        return control.review_task_vision(todo_id, agent_id, agent_vision, vision_unchanged_reason, read_context_id)

    @server.tool()
    def complete_task(
        todo_id: str,
        agent_id: str,
        evidence: str,
        next_agent_todo: str = "",
        claim_operation_id: str | None = None,
        no_follow_up: bool = False,
        successor_todo_ids: list[str] | None = None,
        agent_vision: dict[str, Any] | None = None,
        vision_unchanged_reason: str = "",
    ) -> str:
        """Complete verified work and settle once using claim_operation_id from claim_task.
        Reuse that reference on retries; ownership and expiry are checked internally.
        Link existing planned successors
        with successor_todo_ids; next_agent_todo creates a NEW Todo, not an id link.
        no_follow_up closes this Todo's continuation, NOT the Goal's vision.
        Do not duplicate existing work; only the fresh should_run contract can
        establish Goal terminal state, regardless of the Todo closeout receipt.
        Include an authored agent_vision (goal_vision_replan_contract_v0 with state
        and vision_patch), or an unchanged reason backed by an existing vision.
        Omission keeps a required checkpoint open; repair with review_task_vision.
        If settlement failed, correct uncommitted input and retry complete_task
        with the same completion intent; checkpoint-only recovery cannot spend.
        """
        return control.complete_task(
            todo_id,
            agent_id,
            evidence,
            next_agent_todo=next_agent_todo,
            claim_operation_id=claim_operation_id,
            no_follow_up=no_follow_up,
            successor_todo_ids=successor_todo_ids,
            agent_vision=agent_vision,
            vision_unchanged_reason=vision_unchanged_reason,
        )

    return server, control
