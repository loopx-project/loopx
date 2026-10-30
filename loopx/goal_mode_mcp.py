"""Shared MCP control plane for host-specific LoopX goal adapters."""

from __future__ import annotations

import inspect
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .control_plane.host_adapter_settlement import (
    HostTodoSettlementRequest,
    host_vision_request,
    project_host_interaction,
    refresh_host_todo_vision,
    settle_host_todo_completion,
    settle_host_todo_wait,
)


ContextResolver = Callable[[], dict[str, Any] | None]

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

    def _runtime_root_override(self) -> Path | None:
        """Only an explicit transport root may suppress registry source routing."""
        return None

    def _runtime_root(self, registry_path: Path) -> Path:
        """Resolve reads against the runtime used by this transport's CLI calls."""
        from .paths import effective_runtime_root

        return effective_runtime_root(registry_path, self._runtime_root_override())

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
                            select_default_runtime_root)
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
        runtime_root = self._runtime_root(registry_path)
        effective = resolve_todo_replan_cadence_goal(goal, runtime_root)
        return (effective.get("execution_profile") or {}).get("stride_strategy") == "evidence_conditioned_v1"

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
                "unrecognized arguments" in diagnostic or "invalid choice" in diagnostic
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
        # MCP parses/projects raw decisions itself. Pin only this subprocess;
        # the existing environment opt-out also works with older CLI parsers.
        environment = {**os.environ, "LOOPX_CONTEXT_DELIVERY": "off"}
        result = subprocess.run(
            [*command, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            env=environment,
        )
        if legacy_args is not None and self._runtime_profile_flag_is_unsupported(
            result
        ):
            result = subprocess.run(
                [*command, *legacy_args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
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

    def should_run_args(
        self, goal_id: str, agent_id: str | None
    ) -> tuple[list[str], list[str]]:
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

    def should_run(self, revalidate_todo_id: str = "") -> str:
        goal_id, _ = self.context()
        if not goal_id:
            return self.no_goal_message()
        args, legacy_args = self.should_run_args(goal_id, self.bound_agent_id())
        suffix = ["--todo-id", revalidate_todo_id] if revalidate_todo_id else []
        guard_suffix = list(suffix)
        if revalidate_todo_id:
            turn = self._original_work_turn(revalidate_todo_id)
            if turn is not None:
                guard_suffix += ["--turn-instance-id", turn]
        output = self.run_cli([*args, *guard_suffix], legacy_args=[*legacy_args, *guard_suffix])
        if revalidate_todo_id:
            guard = json.loads(output)
            if guard.get("ok") is True and guard.get("delivery_result_context"):
                identity = guard["delivery_result_context"]["settlement_identity"]
                refreshed = json.loads(
                    self.run_cli(
                        [
                            "checkpoint-context",
                            "--goal-id",
                            goal_id,
                            "--agent-id",
                            self.bound_agent_id(),
                            *suffix,
                            "--turn-instance-id",
                            identity["turn_instance_id"],
                            "--purpose",
                            "delivery_result",
                        ]
                    )
                )
                if refreshed.get("ok") is not True:
                    return json.dumps(refreshed)
                guard["delivery_result_context"] = {
                    key: refreshed[key]
                    for key in ("ok", "basis", "read_context_id", "settlement_identity")
                }
                guard["result_revalidation_required"] = True
                guard["next_action"] = (
                    "Revalidate the retained candidate against this basis, then call complete_task for the same Todo. The original Turn key is carried automatically."
                )
                output = json.dumps(guard)
        return project_host_interaction(output)

    def _original_work_turn(self, todo_id: str) -> str | None:
        """Resolve installed admission through the same owner used by the CLI."""
        from .control_plane.goals.checkpoint_context_io import restore_default_turn_id

        goal_id, registry = self.context()
        if not registry or not Path(registry).is_file():
            return None  # Older host contracts retain their deterministic identity.
        path = Path(registry)
        return restore_default_turn_id(
            runtime_root=self._runtime_root(path),
            registry_path=path,
            goal_id=goal_id,
            agent_id=self.bound_agent_id(),
            todo_id=todo_id,
            pending_only=False,
        )

    def list_todos(self) -> str:
        return self.should_run()

    def _checkpoint_continuation(self, todo_id: str, turn_id: str | None) -> bool:
        if turn_id is None:
            return False
        from .control_plane.quota.settlement import read_heartbeat_settlement
        from .control_plane.goals.checkpoint_context_io import (
            checkpoint_continuation_required,
        )

        goal_id, registry = self.context()
        path = Path(registry)
        root = self._runtime_root(path)
        readback = read_heartbeat_settlement(
            root,
            goal_id=goal_id,
            agent_id=self.bound_agent_id(),
            todo_id=todo_id,
            turn_instance_id=turn_id,
            registry_path=path,
            goal_ref=self.goal_ref(),
        )
        return readback is not None and checkpoint_continuation_required(readback)

    def _stored_terminal_intent(self, todo_id: str, turn_id: str) -> bool:
        from .todos import list_goal_todos
        from .control_plane.quota.settlement import SettlementIdentity
        from .control_plane.goals.checkpoint_context_io import _checkpoint_effect

        goal_id, registry = self.context()
        path = Path(registry)
        runtime_override = (
            str(self._runtime_root(path)) if self._runtime_root_override() is not None else None
        )
        todo = list_goal_todos(
            registry_path=path,
            runtime_root_arg=runtime_override,
            goal_id=goal_id,
            todo_id=todo_id,
        ).get("todo")
        identity = SettlementIdentity(
            goal_id=goal_id,
            agent_id=self.bound_agent_id(),
            todo_id=todo_id,
            turn_instance_id=turn_id,
        )
        return _checkpoint_effect(
            "goal.checkpoint_read_context.work_binding",
            {
                "phase": "direction_terminal_intent",
                "todo": todo,
                "identity": identity.as_dict(),
            },
        )["no_followup"]

    def host_prompt(self) -> str:
        from .claude_goal_mode.scripts.goalmode_cmd import loop_execution_content

        state = self.state()
        goal_id, agent_id = state.get("goal_id"), state.get("agent_id")
        if not goal_id or not agent_id:
            return json.dumps(
                {"ok": False, "error": "bound Goal and agent are required"}
            )
        return json.dumps(
            {
                "ok": True,
                "goal_id": goal_id,
                "agent_id": agent_id,
                "task_body": loop_execution_content(goal_id, agent_id),
            }
        )

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

    def wait_task(self, todo_id: str, agent_id: str, reason: str) -> str:
        goal_id, _ = self.context()
        if not goal_id:
            return self.no_goal_message()
        identity_error = self._identity_error(agent_id)
        if identity_error:
            return identity_error
        request = HostTodoSettlementRequest(
            goal_id=goal_id,
            agent_id=agent_id,
            todo_id=todo_id,
            runtime_profile=self.config.runtime_profile,
            legacy_host_surface=self.config.legacy_host_surface,
            scheduler_owner=self.config.scheduler_owner,
            execution_mode=self.config.execution_mode,
            completion_args=(),
            goal_ref=self.goal_ref(),
            turn_instance_id=self._original_work_turn(todo_id),
        )
        return settle_host_todo_wait(request, reason, run_cli=self.run_cli)

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
        turn_id = self._original_work_turn(todo_id)
        current_checkpoint = self._checkpoint_continuation(todo_id, turn_id)
        if current_checkpoint and not no_follow_up:
            no_follow_up = self._stored_terminal_intent(todo_id, turn_id)
        if next_agent_todo and no_follow_up:
            return json.dumps(
                {
                    "ok": False,
                    "error": "next_agent_todo and no_follow_up are mutually exclusive",
                }
            )
        if successor_todo_ids is not None and (
            not isinstance(successor_todo_ids, list)
            or any(
                not isinstance(value, str) or not value.strip()
                for value in successor_todo_ids
            )
        ):
            return json.dumps(
                {
                    "ok": False,
                    "error": "successor_todo_ids must be a list of nonempty ids",
                }
            )
        if successor_todo_ids and (next_agent_todo or no_follow_up):
            return json.dumps(
                {
                    "ok": False,
                    "error": "choose existing successors, a new successor, or no follow-up",
                }
            )
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
            legacy_host_surface=self.config.legacy_host_surface,
            scheduler_owner=self.config.scheduler_owner,
            execution_mode=self.config.execution_mode,
            completion_args=tuple(args),
            first_delivery=first_delivery,
            delivery_read_context_id=delivery_read_context_id or None,
            checkpoint_read_context_id=read_context_id or None,
            no_follow_up=no_follow_up,
            goal_ref=self.goal_ref(),
            turn_instance_id=turn_id,
            current_agent_checkpoint=current_checkpoint,
        )
        with host_vision_request(request, agent_vision, vision_unchanged_reason,
                                 allow_terminal_stride_path=self._allow_terminal_stride_path(goal_id, agent_vision)) as authored:
            output = settle_host_todo_completion(authored, run_cli=self.run_cli)
        completed = json.loads(output)
        if (
            completed.get("ok") is True
            and completed.get("settlement_complete") is True
            and turn_id
        ):
            from .control_plane.turn_driver.journal_store import (
                reconcile_settled_turn_journal,
            )

            _, registry = self.context()
            path = Path(registry)
            root = self._runtime_root(path)
            reconcile_settled_turn_journal(
                root,
                registry_path=path,
                settlement_identity=completed["settlement_identity"],
            )
        return output

    def review_task_vision(
        self,
        todo_id: str,
        agent_id: str,
        agent_vision: dict[str, Any] | None = None,
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
        turn_id = self._original_work_turn(todo_id)
        request = HostTodoSettlementRequest(
            goal_id=goal_id,
            agent_id=agent_id,
            todo_id=todo_id,
            runtime_profile=self.config.runtime_profile,
            legacy_host_surface=self.config.legacy_host_surface,
            scheduler_owner=self.config.scheduler_owner,
            execution_mode=self.config.execution_mode,
            completion_args=(),
            checkpoint_read_context_id=read_context_id or None,
            first_delivery=first_delivery,
            goal_ref=self.goal_ref(),
            turn_instance_id=turn_id,
            current_agent_checkpoint=self._checkpoint_continuation(todo_id, turn_id),
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

    server = GoalModeServer(config.server_name)

    if config.legacy_host_surface == "claude_code":

        @server.tool()
        def host_prompt() -> str:
            """Read current Claude Goal execution rules for this server's bound identity."""
            return control.host_prompt()

    @server.tool()
    def should_run(revalidate_todo_id: str = "") -> str:
        """Read admitted work and its original result basis. After a stale result,
        pass its Todo as revalidate_todo_id to read new facts on the same Turn.
        Revalidate the retained candidate before complete_task; no token copying
        is needed. Ordinary calls preserve the original basis across restarts.
        """
        return control.should_run(revalidate_todo_id)

    @server.tool()
    def list_todos() -> str:
        """List open todos visible to the bound agent."""
        return control.list_todos()

    @server.tool()
    def claim_task(todo_id: str, agent_id: str) -> str:
        """Claim one Todo; retain its claim_operation_id for updates and completion."""
        return control.claim_task(todo_id, agent_id)

    @server.tool()
    def review_task_vision(
        todo_id: str,
        agent_id: str,
        agent_vision: dict[str, Any] | None = None,
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
        return control.review_task_vision(
            todo_id,
            agent_id,
            agent_vision,
            vision_unchanged_reason,
            read_context_id,
            first_delivery,
        )

    @server.tool()
    def wait_task(todo_id: str, agent_id: str, reason: str) -> str:
        """Pause unfinished admitted work without completing it, judging vision
        or spending. Read should_run first; the original Turn is restored
        automatically. On current authority refusal follow that refusal.
        Resume through should_run when the dependency is ready and revalidate
        the retained candidate if its result basis changes.
        """
        return control.wait_task(todo_id, agent_id, reason)

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
        first_delivery: bool = False,
        delivery_read_context_id: str = "",
        read_context_id: str = "",
    ) -> str:
        """Complete verified work and settle once using claim_operation_id from claim_task.
        Reuse that reference on retries; ownership and expiry are checked internally.
        Ordinary work uses the result
        basis returned by should_run; completion carries its original key. After
        stale rejection call should_run(revalidate_todo_id=todo_id), revalidate
        the retained candidate, then retry with the same completion intent.
        With first_delivery=true, first
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
            claim_operation_id=claim_operation_id,
            no_follow_up=no_follow_up,
            successor_todo_ids=successor_todo_ids,
            agent_vision=agent_vision,
            vision_unchanged_reason=vision_unchanged_reason,
            first_delivery=first_delivery,
            delivery_read_context_id=delivery_read_context_id,
            read_context_id=read_context_id,
        )

    return server, control
