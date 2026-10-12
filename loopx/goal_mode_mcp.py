"""Shared MCP control plane for host-specific LoopX goal adapters."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any

try:  # pydantic ships with the FastMCP extra; base installs have no pydantic.
    from pydantic import Strict
except ImportError:  # pragma: no cover - exercised on base installs only
    class Strict:  # type: ignore[no-redef]
        """Inert stand-in so the shared annotation still evaluates without
        pydantic; FastMCP cannot run on such an install anyway."""

from .control_plane.host_adapter_settlement import (
    HostTodoSettlementRequest,
    host_vision_request,
    project_host_interaction,
    refresh_host_todo_vision,
    settle_host_todo_completion,
)


ContextResolver = Callable[[], dict[str, Any] | None]

# The one boundary type for a caller-supplied lease version. FastMCP validates
# tool arguments with pydantic, and lax pydantic coerces JSON true to 1 before
# any loopx code runs, so strictness must live in the annotation itself; the
# control-plane method and the FastMCP wrapper share this alias so the two
# signatures cannot drift.
ExpectedTaskLeaseVersion = Annotated[int, Strict()] | None


@dataclass(frozen=True)
class GoalModeMCPConfig:
    server_name: str
    runtime_profile: str
    legacy_host_surface: str
    scheduler_owner: str = "agent_cli_loop"
    execution_mode: str = "interactive"
    setup_hint: str = "connect this host adapter first"


class GoalModeMCPControlPlane:
    """Deterministic lifecycle operations exposed through FastMCP."""

    def __init__(self, config: GoalModeMCPConfig, context_resolver: ContextResolver):
        self.config = config
        self.context_resolver = context_resolver

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

    def command_prefix(self) -> list[str]:
        executable = shutil.which("loopx")
        return [executable] if executable else [sys.executable, "-m", "loopx.cli"]

    @staticmethod
    def _runtime_profile_flag_is_unsupported(
        result: subprocess.CompletedProcess[str],
    ) -> bool:
        diagnostic = str(result.stderr or "").lower()
        return (
            result.returncode == 2
            and "--runtime-profile" in diagnostic
            and (
                "unrecognized arguments" in diagnostic
                or "invalid choice" in diagnostic
            )
        )

    def run_cli(
        self,
        args: list[str],
        *,
        legacy_args: list[str] | None = None,
    ) -> str:
        _, registry = self.context()
        command = self.command_prefix()
        if registry:
            command += ["--registry", registry]
        command += ["--format", "json"]
        result = subprocess.run(
            [*command, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
        )
        if legacy_args is not None and self._runtime_profile_flag_is_unsupported(result):
            result = subprocess.run(
                [*command, *legacy_args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
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

    def should_run_args(self, goal_id: str, agent_id: str | None) -> tuple[list[str], list[str]]:
        common = ["quota", "should-run", "--goal-id", goal_id]
        if agent_id:
            common += ["--agent-id", agent_id]
        return (
            [*common, "--runtime-profile", self.config.runtime_profile],
            [
                *common,
                "--host-surface",
                self.config.legacy_host_surface,
                "--scheduler-owner",
                self.config.scheduler_owner,
                "--execution-mode",
                self.config.execution_mode,
            ],
        )

    def should_run(self) -> str:
        goal_id, _ = self.context()
        if not goal_id:
            return self.no_goal_message()
        args, legacy_args = self.should_run_args(goal_id, self.bound_agent_id())
        return project_host_interaction(self.run_cli(args, legacy_args=legacy_args))

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
        task_lease_idempotency_key: str = "",
        task_lease_expected_version: ExpectedTaskLeaseVersion = None,
        no_follow_up: bool = False,
        successor_todo_ids: list[str] | None = None,
        agent_vision: dict[str, Any] | None = None,
        vision_unchanged_reason: str = "",
        first_delivery: bool = False,
        delivery_read_context_id: str = "",
        read_context_id: str = "",
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
        if task_lease_idempotency_key:
            args += ["--task-lease-idempotency-key", task_lease_idempotency_key]
        if task_lease_expected_version is not None:
            args += [
                "--task-lease-expected-version",
                str(task_lease_expected_version),
            ]
        if no_follow_up:
            args.append("--no-follow-up")
        request = HostTodoSettlementRequest(
            goal_id=goal_id,
            agent_id=agent_id,
            todo_id=todo_id,
            runtime_profile=self.config.runtime_profile,
            legacy_host_surface=self.config.legacy_host_surface,
            scheduler_owner=self.config.scheduler_owner,
            execution_mode=self.config.execution_mode,
            completion_args=tuple(args),
            first_delivery=first_delivery,
            delivery_read_context_id=delivery_read_context_id or None,
            checkpoint_read_context_id=read_context_id or None,
            no_follow_up=no_follow_up,
            goal_ref=self.goal_ref(),
        )
        with host_vision_request(request, agent_vision, vision_unchanged_reason) as authored:
            return settle_host_todo_completion(authored, run_cli=self.run_cli)

    def review_task_vision(
        self, todo_id: str, agent_id: str, agent_vision: dict[str, Any] | None = None,
        vision_unchanged_reason: str = "",
        read_context_id: str = "",
        first_delivery: bool = False,
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
            legacy_host_surface=self.config.legacy_host_surface,
            scheduler_owner=self.config.scheduler_owner, execution_mode=self.config.execution_mode,
            completion_args=(),
            checkpoint_read_context_id=read_context_id or None,
            first_delivery=first_delivery,
            goal_ref=self.goal_ref(),
        )
        with host_vision_request(request, agent_vision, vision_unchanged_reason) as authored:
            return refresh_host_todo_vision(authored, run_cli=self.run_cli)


def create_fastmcp_server(
    config: GoalModeMCPConfig,
    context_resolver: ContextResolver,
):
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as exc:  # pragma: no cover - exercised by real adapter startup
        raise SystemExit(
            "MCP SDK v1 is required. Install the adapter with loopx-kunluncode install.\n"
            + str(exc)
        ) from exc

    control = GoalModeMCPControlPlane(config, context_resolver)
    server = FastMCP(config.server_name)

    if config.legacy_host_surface == "claude_code":
        @server.tool()
        def host_prompt() -> str:
            """Read current Claude Goal execution rules for this server's bound identity."""
            return control.host_prompt()

    @server.tool()
    def should_run() -> str:
        """Whether the bound goal and agent should run now."""
        return control.should_run()

    @server.tool()
    def list_todos() -> str:
        """List open todos visible to the bound agent."""
        return control.list_todos()

    @server.tool()
    def claim_task(todo_id: str, agent_id: str) -> str:
        """Claim one todo as the bound agent."""
        return control.claim_task(todo_id, agent_id)

    @server.tool()
    def review_task_vision(
        todo_id: str, agent_id: str, agent_vision: dict[str, Any] | None = None,
        vision_unchanged_reason: str = "",
        read_context_id: str = "",
        first_delivery: bool = False,
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
        return control.review_task_vision(todo_id, agent_id, agent_vision, vision_unchanged_reason, read_context_id, first_delivery)

    @server.tool()
    def complete_task(
        todo_id: str,
        agent_id: str,
        evidence: str,
        next_agent_todo: str = "",
        task_lease_idempotency_key: str = "",
        task_lease_expected_version: ExpectedTaskLeaseVersion = None,
        no_follow_up: bool = False,
        successor_todo_ids: list[str] | None = None,
        agent_vision: dict[str, Any] | None = None,
        vision_unchanged_reason: str = "",
        first_delivery: bool = False,
        delivery_read_context_id: str = "",
        read_context_id: str = "",
    ) -> str:
        """Complete verified work and settle once. With first_delivery=true, first
        call without Vision to read the result basis; verify it and echo its
        delivery_read_context_id. Read the returned post-result direction basis,
        then call with both original read identities and a newly judged Vision.
        An interrupted call retains the same Todo/Turn; do not repeat work.
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
            task_lease_idempotency_key=task_lease_idempotency_key,
            task_lease_expected_version=task_lease_expected_version,
            no_follow_up=no_follow_up,
            successor_todo_ids=successor_todo_ids,
            agent_vision=agent_vision,
            vision_unchanged_reason=vision_unchanged_reason,
            first_delivery=first_delivery,
            delivery_read_context_id=delivery_read_context_id,
            read_context_id=read_context_id,
        )

    return server, control
