"""Explicit, identity-scoped stdio tools for a sandboxed working Agent.

The configured host owns the registry/runtime/Goal/Agent binding. Model tool
arguments cannot choose another sender, filesystem root or external audience.
These tools expose the same inbox operations as the trusted local CLI; they
never expose a shell, Todo writes, execution grants or a network listener.
An explicit operator execution configuration adds separately scoped delegation.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

from .file_lock import exclusive_file_lock, LockAcquisitionPolicy, LockAcquireTimeoutError
from .todos import list_goal_todos
from .control_plane.effect_runtime import effect_runtime_result, EffectRuntimeRemoteError
from .control_plane.goals.acceptance import inspect_goal_acceptance, validate_goal_task_acceptance, goal_task_validation_files_current
from .control_plane.turn_driver.journal_store import turn_journal_path
from .control_plane.turn_driver.host_binding import turn_host_arg_option
from .control_plane.collaboration.inbox import _hash, _read, _write, _root, _receipt
from .control_plane.collaboration.peers import return_result
from .control_plane.collaboration.inbox import acknowledge, _entry, normalize_request
from .control_plane.collaboration import delegation_results
from .control_plane.collaboration.peers import (
    _goal,
    consume_return,
    read_inbox,
    request,
    require_operation_id,
)


def create_server(
    root: Path, registry: Path, goal_id: str, agent_id: str, workspace: Path,
    execution_config: Path | None = None,
) -> FastMCP:
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("loopx-collaboration")
    register_collaboration_tools(server, root, registry, goal_id, agent_id, workspace)
    if execution_config is not None:
        register_delegation_tools(server, Delegations(root, registry, goal_id, agent_id, execution_config))
    return server


def register_collaboration_tools(server: FastMCP, root: Path, registry: Path, goal_id: str,
                                 agent_id: str, workspace: Path) -> None:
    _goal(registry, goal_id, agent_id)

    def check_scope():
        # Revocation is read on every tool call, including a long-lived server.
        _goal(registry, goal_id, agent_id)

    @server.tool()
    def read_context() -> dict:
        """Read pending requests, material version checks and unconsumed peer results."""
        return read_inbox(root, registry, goal_id, agent_id, workspace=workspace)

    @server.tool()
    def assess_request(
        request_id: str,
        decision: Literal["adopt", "defer", "reject", "no_change"],
        reason: str,
    ) -> dict:
        """Record your independent decision; this does not change task ownership or priority."""
        check_scope()
        return acknowledge(root, goal_id, agent_id, request_id, decision, reason)

    @server.tool()
    def request_peer(
        peer_agent_id: str,
        operation_id: str,
        brief: dict,
        parent_request_id: str | None = None,
    ) -> dict:
        """Request same-Goal peer help/review with a collaboration_brief_v0 and stable retry id.

        Brief fields: schema_version, purpose, context, constraints (strings), inputs
        (relative ref, description, optional sha256), acceptance (nonempty strings),
        return_requirement. Preserve relevant corrections and rejected approaches.
        New review rounds use new operation ids. No worker is launched by this tool.
        """
        check_scope()
        return request(
            root,
            registry,
            goal_id,
            agent_id,
            peer_agent_id,
            operation_id,
            brief,
            parent_request_id,
        )

    @server.tool()
    def return_result(request_id: str, text: str) -> dict:
        """Save an evidence-backed conclusion or explicit blocker for the original requester."""
        check_scope()
        row = _entry(root, goal_id, agent_id, request_id)
        if row.get("source_kind") == "peer":
            from .control_plane.collaboration.peers import return_result as save_result

            return save_result(root, goal_id, agent_id, request_id, text)
        # The host adapter selects Chat/Lark transport; the shared collaboration
        # owner never depends on presentation or manager capabilities.
        from .capabilities.manager_context.roundtrip import report

        return report(root, goal_id, agent_id, request_id, "conclusion", text)

    @server.tool()
    def consume_peer_result(request_id: str) -> dict:
        """Acknowledge a peer result after reading and using/rejecting it; no work-state mutation."""
        check_scope()
        return consume_return(root, goal_id, agent_id, request_id)


class Delegations:
    """Host IO for bound peer work; typed grants and observations stay in TS.

    Serving MCP, spawning its detached worker and validating its original Turn
    share this host entrypoint instead of maintaining a second control-plane CLI.
    """

    def __init__(self, root: Path, registry: Path, goal_id: str, agent_id: str, config: Path):
        self.root, self.registry = root.resolve(), registry.resolve()
        self.goal_id, self.agent_id, self.config = goal_id, agent_id, config.resolve()

    def binding(self, binding_id: str, *, require_active: bool = False) -> dict:
        _goal(self.registry, self.goal_id, self.agent_id, require_active=require_active)
        binding = effect_runtime_result("collaboration.delegation.binding", {
            "config": _read(self.config), "binding_id": binding_id, "agent_id": self.agent_id,
        })
        _goal(self.registry, self.goal_id, binding["agent_id"], require_active=require_active)
        if not Path(binding["workspace"]).is_absolute():
            raise ValueError("delegation workspace must be absolute")
        return binding

    def directory(self) -> dict:
        config = _read(self.config)
        bindings = [self.binding(row["id"]) for row in config["bindings"]
                    if self.agent_id in row.get("requesters", [])]
        return {"bindings": [{key: row[key] for key in ("id", "agent_id", "todo_id")}
                             for row in bindings]}

    def path(self, operation_id: str) -> Path:
        return _root(self.root) / "executions" / _hash([self.goal_id, self.agent_id]) / (_hash(operation_id) + ".json")

    def operations(self, *, limit: int = 20, cursor: str | None = None) -> dict:
        from .control_plane.collaboration.delegation_inventory import read_delegation_inventory

        return read_delegation_inventory(self, limit=limit, cursor=cursor)

    def inspect(self, binding_id: str) -> dict:
        """Observe the real Turn preflight; never create a request or run a host."""
        binding = self.binding(binding_id, require_active=True)
        if not Path(binding["workspace"]).is_dir():
            raise ValueError("delegation workspace unavailable")
        try:
            acceptance = inspect_goal_acceptance(registry_path=self.registry, goal_id=self.goal_id,
                                                  runtime_root=str(self.root))
            files_current = goal_task_validation_files_current(registry_path=self.registry,
                runtime_root=str(self.root), goal_id=self.goal_id, agent_id=binding["agent_id"], todo_id=binding["todo_id"])
        except (OSError, ValueError) as exc:
            # Authority admission is a readiness observation, not a reason for
            # inspection to invent a provider launch or collapse into a raw CLI error.
            if self.binding(binding_id, require_active=True) != binding:
                raise ValueError("delegation preflight source changed; retry inspection")
            return effect_runtime_result("collaboration.delegation.preflight", {
                "binding": {key: binding[key] for key in ("id", "agent_id", "todo_id")},
                "authority": {"ready": False, "reason": str(exc)},
                "preview": None, "acceptance": None, "validation_files_current": False,
            })
        operation = "inspect-" + _hash(binding_id)[:32]
        arguments = ["turn", "run-once", "--goal-id", self.goal_id,
                     "--agent-id", binding["agent_id"], "--todo-id", binding["todo_id"],
                     "--turn-instance-id", operation, *self._execution_arguments(binding, operation)]
        # Host arguments are operator-owned, but inspection must stay read-only
        # even when they contain an abbreviated execution flag or a selector.
        from .cli import build_parser

        try:
            selected = build_parser().parse_args(arguments)
        except SystemExit as exc:
            raise ValueError("invalid delegation Turn arguments") from exc
        workspace = Path(binding["workspace"]).resolve()
        selected_project = Path(selected.project)
        selected_scan_root = Path(selected.scan_root)
        if not selected_project.is_absolute():
            selected_project = workspace / selected_project
        if not selected_scan_root.is_absolute():
            selected_scan_root = workspace / selected_scan_root
        if (selected.execute or selected.resume_turn_key
                or (selected.goal_id, selected.agent_id, selected.todo_id, selected.turn_instance_id)
                != (self.goal_id, binding["agent_id"], binding["todo_id"], operation)
                or selected_project.resolve() != workspace
                or selected_scan_root.resolve() != workspace):
            raise ValueError("delegation inspection cannot execute or retarget bound work")
        preview = self._cli(binding, *arguments)
        if preview.get("status") != "preview":
            raise ValueError(f"delegation Turn preflight unavailable: {preview.get('error') or preview.get('status')}")
        current = inspect_goal_acceptance(registry_path=self.registry, goal_id=self.goal_id,
                                          runtime_root=str(self.root))
        if (acceptance != current or self.binding(binding_id, require_active=True) != binding
                or files_current != goal_task_validation_files_current(registry_path=self.registry,
                    runtime_root=str(self.root), goal_id=self.goal_id,
                    agent_id=binding["agent_id"], todo_id=binding["todo_id"])):
            raise ValueError("delegation preflight source changed; retry inspection")
        task = next((row for row in (acceptance.get("goal_acceptance_contract") or {}).get("tasks", [])
                     if row.get("todo_id") == binding["todo_id"]), None)
        return effect_runtime_result("collaboration.delegation.preflight", {
            "binding": {key: binding[key] for key in ("id", "agent_id", "todo_id")},
            "authority": {"ready": True, "reason": None},
            "preview": preview, "acceptance": task, "validation_files_current": files_current,
        })

    def start(self, binding_id: str, operation_id: str, brief: dict,
              parent_request_id: str | None = None) -> dict:
        binding = self.binding(binding_id, require_active=True)
        require_operation_id(operation_id)
        brief = normalize_request({"goal_id": self.goal_id, "agent_id": binding["agent_id"], "brief": brief})["brief"]
        if any(item.get("delegation", {}).get("operation_id") == operation_id for item in brief["inputs"]):
            raise ValueError("delegation cannot depend on itself")
        path = self.path(operation_id)
        with exclusive_file_lock(path.with_suffix(".dispatch")):
            exists = path.exists()
            if not exists:
                delegation_results.require_dependencies(self, binding, brief)
            delivered = request(self.root, self.registry, self.goal_id, self.agent_id,
                                binding["agent_id"], operation_id, brief, parent_request_id)
            identity = {"binding": binding, "request_id": delivered["request_id"], "operation_id": operation_id}
            if exists:
                if _read(path).get("identity") != identity:
                    raise ValueError("delegation operation identity conflict")
            else:
                _write(path, {"identity": identity, "status": "prepared", "created_at": time.time()})
                self._spawn(operation_id)
        return self.read(operation_id)

    def _spawn(self, operation_id: str) -> None:
        # No inherited stdio pipes: closing the conversation cannot cancel or
        # hang this bounded execution. The worker owns a kernel single-flight lock.
        operation_id = require_operation_id(operation_id)
        subprocess.Popen([
            sys.executable, "-m", "loopx.collaboration_mcp", "--delegation-action", "worker", "--runtime-root", str(self.root),
            "--registry", str(self.registry), "--goal-id", self.goal_id,
            "--agent-id", self.agent_id, "--execution-config", str(self.config), "--operation-id=" + operation_id,
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True)

    def resume(self, operation_id: str) -> dict:
        row = _read(self.path(operation_id))
        self._bound(row)
        if row["status"] not in {"accepted", "rejected"}:
            self.binding(row["identity"]["binding"]["id"], require_active=True)
            self._spawn(operation_id)
        return self.read(operation_id)

    def wait(self, operation_id: str) -> dict:
        """Observe for at most 15 seconds; waiting neither starts nor resumes work."""
        for _ in range(5):
            result = self.read(operation_id)
            if result["status"] in {"accepted", "rejected"} or result["recovery_required"]:
                return result
            time.sleep(3)
        return self.read(operation_id)

    def _bound(self, row: dict, *, require_active: bool = False) -> dict:
        binding = self.binding(row["identity"]["binding"]["id"], require_active=require_active)
        if row["identity"]["binding"] != binding:
            raise ValueError("delegation binding changed; reconcile original execution")
        return binding

    def adopt_result(self, operation_id: str, consumer_operation_id: str) -> dict:
        return delegation_results.adopt_result(self, operation_id, consumer_operation_id)

    def read(self, operation_id: str) -> dict:
        result = self._read_current(operation_id)
        result.update(delegation_results.result_relationships(self, operation_id))
        return result

    def _read_current(self, operation_id: str) -> dict:
        require_operation_id(operation_id)
        path = self.path(operation_id)
        if not path.exists():
            raise ValueError("unknown delegation operation; start_delegation returns the operation_id to read")
        row = _read(path)
        binding = self._bound(row)
        try:
            with exclusive_file_lock(path, policy=LockAcquisitionPolicy.SINGLE_FLIGHT):
                active = False
        except LockAcquireTimeoutError:
            active = True
        result = {"operation_id": operation_id, "request_id": row["identity"]["request_id"],
                  "agent_id": binding["agent_id"], "todo_id": binding["todo_id"],
                  "status": row["status"], "worker_active": active,
                  "recovery_required": not active and row["status"] not in {"accepted", "rejected"}
                  and time.time() - row.get("created_at", 0) > 15}
        if row["status"] == "accepted":
            # A saved receipt cannot hide an amended task, verifier or output.
            artifacts = self._accepted(binding)
            if artifacts != row["artifacts"]:
                raise ValueError("delegation output changed after completion")
            result["artifacts"] = artifacts
        if row.get("error"):
            result["error"] = row["error"]
        return result

    def _observe(self, path: Path, row: dict, status: str, **facts) -> None:
        decision = effect_runtime_result("collaboration.delegation.observe", {
            "from": row["status"], "to": status, **facts,
        })
        row.update(status=decision["status"])
        _write(path, row)

    def _cli(self, binding: dict, *args: str, timeout: int = 60) -> dict:
        completed = subprocess.run([
            sys.executable, "-m", "loopx.cli", "--registry", str(self.registry),
            "--runtime-root", str(self.root), "--format", "json", *args,
        ], cwd=binding["workspace"], capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        try:
            value = json.loads(completed.stdout)
        except ValueError as exc:
            raise ValueError("delegation CLI returned no structured result") from exc
        if completed.returncode and "turn" not in args:
            raise ValueError("delegation canonical command rejected")
        return value

    def _validate(self, binding: dict) -> None:
        value = validate_goal_task_acceptance(registry_path=self.registry, runtime_root=str(self.root),
            goal_id=self.goal_id, agent_id=binding["agent_id"], todo_id=binding["todo_id"])
        if not value["passed"]:
            raise ValueError("delegation task acceptance rejected")

    def _accepted(self, binding: dict) -> list[dict]:
        self._validate(binding)
        todos = list_goal_todos(registry_path=self.registry, goal_id=self.goal_id, runtime_root_arg=str(self.root))
        basis = inspect_goal_acceptance(registry_path=self.registry, goal_id=self.goal_id, runtime_root=str(self.root))
        if todos.get("authority_read", {}).get("provider_revision") != basis.get("provider_revision"):
            raise ValueError("delegation canonical snapshot changed; retry readback")
        todo = next((row for row in todos["todos"] if row["todo_id"] == binding["todo_id"]), {})
        guard = next((row for row in basis["goal_acceptance_contract"]["tasks"]
                      if row["todo_id"] == binding["todo_id"]), {})
        if not todo.get("done") or todo.get("status") != "done" or guard.get("state") != "ready":
            raise ValueError("delegation requires current canonical completion")
        workspace = Path(binding["workspace"]).resolve()
        artifacts = []
        for ref in binding["output_refs"]:
            path = workspace / ref
            if not path.resolve().is_relative_to(workspace) or path.is_symlink() or not path.is_file():
                raise ValueError("delegation artifact unavailable or outside workspace")
            if path.stat().st_size > 128_000:
                raise ValueError("delegation artifact exceeds bounded return size")
            with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)), "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("delegation artifact must be a regular file")
                content = stream.read(128_001)
            if len(content) > 128_000:
                raise ValueError("delegation artifact exceeds bounded return size")
            artifacts.append({"ref": ref, "sha256": hashlib.sha256(content).hexdigest(),
                              "text": content.decode("utf-8")})
        if len(json.dumps(artifacts).encode()) > 64_000:
            raise ValueError("delegation aggregate return exceeds limit")
        return artifacts

    def execute(self, operation_id: str) -> None:
        path = self.path(operation_id)
        # Status readers briefly acquire this same kernel lock. Wait for that
        # observation to finish before deciding another worker owns the operation.
        # The existing bounded mutation policy still excludes concurrent workers.
        with exclusive_file_lock(path):
            row = _read(path)
            if row["status"] in {"accepted", "rejected"}:
                return
            binding = self._bound(row, require_active=True)
            row.pop("error", None)
            _write(path, row)
            # Different request ids cannot run the same assigned task concurrently.
            task_lock = _root(self.root) / "execution-slots" / _hash([self.goal_id, binding["todo_id"]])
            with exclusive_file_lock(task_lock, policy=LockAcquisitionPolicy.SINGLE_FLIGHT):
                try:
                    self._execute(path, row, binding)
                except (ValueError, KeyError, subprocess.TimeoutExpired, EffectRuntimeRemoteError) as exc:
                    row["error"] = str(exc)[:180] if isinstance(exc, (ValueError, EffectRuntimeRemoteError)) else type(exc).__name__
                    _write(path, row)
                    if row["status"] == "prepared":
                        self._observe(path, row, "rejected")

    def _execution_arguments(self, binding: dict, operation_id: str) -> list[str]:
        """Exactly the same profile, workspace and validation arguments for preview/run."""
        # Preserve the journaled validator argv so existing Turns retain their resume identity.
        validator = [sys.executable, "-m", "loopx.collaboration_mcp", "--delegation-action", "validate", "--runtime-root", str(self.root),
                     "--registry", str(self.registry), "--goal-id", self.goal_id,
                     "--agent-id", self.agent_id, "--execution-config", str(self.config),
                     "--workspace", binding["workspace"], "--operation-id", operation_id]
        return ["--execution-mode", "isolated-headless", "--project", binding["workspace"],
                     "--scan-root", binding["workspace"], "--no-global-sync",
                     "--timeout-seconds", str(binding["timeout_seconds"]),
                     "--validation-command-json", json.dumps(validator),
                     "--validation-failure-kind", "repair_required", *binding["host_args"]]

    def _execute(self, path: Path, row: dict, binding: dict) -> None:
        request_id = row["identity"]["request_id"]
        common = ["--goal-id", self.goal_id, "--agent-id", binding["agent_id"]]
        host = binding["host_args"]
        execution = self._execution_arguments(binding, row["identity"]["operation_id"])
        if row["status"] == "prepared":
            selected_host = turn_host_arg_option(host, "--host")
            if not selected_host:
                raise ValueError("delegation host_args require --host")
            iteration_context = (
                turn_host_arg_option(host, "--iteration-context")
                or "resume-if-available"
            )
            _write(Path(binding["workspace"]) / "DELEGATION.json", {
                "request_id": request_id, "brief": _entry(self.root, self.goal_id, binding["agent_id"], request_id)["brief"],
                "instruction": "Read context and assess this request independently before working. Return results through the bound tools.",
            })
            plan = self._cli(binding, "turn", "plan", *common, "--todo-id", binding["todo_id"],
                             "--turn-instance-id", "delegation-" + request_id[:32],
                             "--execution-mode", "isolated-headless", "--scan-root", binding["workspace"],
                             "--host", selected_host,
                             "--iteration-context", iteration_context,
                             "--include-transaction-detail")
            decision = effect_runtime_result("collaboration.delegation.turn_plan", {"plan": plan})
            if decision["state"] != "planned":
                raise ValueError(f"delegation Turn plan rejected: {decision['reason']}")
            row["turn_key"] = decision["turn_key"]
            self._observe(path, row, "running")
        try:
            if row["status"] == "running":
                journal = turn_journal_path(self.root, goal_id=self.goal_id, turn_key=row["turn_key"])
                selector = (["--resume-turn-key", row["turn_key"]] if journal.exists() else
                            ["--todo-id", binding["todo_id"], "--turn-instance-id", "delegation-" + request_id[:32]])
                result = self._cli(binding, "turn", "run-once", *common, *selector, *execution,
                                   "--execute", timeout=binding["timeout_seconds"] + 60)
                row["turn_result"] = {key: result.get(key) for key in ("status", "result_kind", "resume_turn_key", "reason", "host_failure", "error")}
                self._observe(path, row, "turn_returned")
            result = row["turn_result"]
            if result.get("status") != "committed" or result.get("result_kind") != "validated_progress":
                row["error"] = "delegation Turn rejected; inspect the original Turn before retrying"
                self._observe(path, row, "rejected")
                return
            decision, error = _receipt(self.root, "decisions", _entry(self.root, self.goal_id, binding["agent_id"], request_id))
            if error or not decision or decision["decision"] != "adopt":
                row["error"] = "delegation receiver did not adopt the request"
                self._observe(path, row, "rejected")
                return
            self._bound(row, require_active=True)  # revocation or rebinding while the model ran
            delegation_results.require_dependencies(self, binding, delegation_results.operation_brief(self, row))
            self._cli(binding, "todo", "complete", *common, "--todo-id", binding["todo_id"],
                      "--no-follow-up", "--note", "Bounded delegated work; requester owns synthesis.")
            row["artifacts"] = self._accepted(binding)
            if not (_root(self.root) / "replies" / request_id / "conclusion.json").exists():
                return_result(self.root, self.goal_id, binding["agent_id"], request_id,
                              json.dumps({"todo_id": binding["todo_id"], "status": "accepted",
                                          "artifacts": [{k: v for k, v in item.items() if k != "text"} for item in row["artifacts"]]}))
            self._observe(path, row, "accepted", canonical_done=True, acceptance_ready=True, artifacts_current=True)
        except (ValueError, KeyError, subprocess.TimeoutExpired, EffectRuntimeRemoteError) as exc:
            # Retain uncertain execution for explicit same-operation recovery.
            # No fresh Turn is ever created because its client timed out.
            row["error"] = str(exc)[:180] if isinstance(exc, (ValueError, EffectRuntimeRemoteError)) else type(exc).__name__
            _write(path, row)


def register_delegation_tools(server, delegations: Delegations) -> None:
    @server.tool()
    def list_execution_bindings() -> dict:
        """Read operator-authorized peer task bindings; registration alone cannot launch."""
        return delegations.directory()

    @server.tool()
    def inspect_execution_binding(binding_id: str) -> dict:
        """Check the selected task, pinned acceptance and actual Turn executor without launching.

        Unknown runtime availability is not launch readiness; this observation grants
        no execution authority. Reuse original operations for existing work.
        """
        return delegations.inspect(binding_id)

    @server.tool()
    def list_delegations(limit: int = 20, cursor: str | None = None) -> dict:
        """Recover this requester's work after context loss. Follow next_cursor for more.

        Accepted items are rechecked; unavailable requires reconciliation, not duplicate
        dispatch. Read the original operation for full artifacts. Listing starts no work.
        """
        return delegations.operations(limit=limit, cursor=cursor)

    @server.tool()
    def start_delegation(binding_id: str, operation_id: str, brief: dict,
                         parent_request_id: str | None = None) -> dict:
        """Start one bounded peer Turn. Reuse the same operation id after lost replies.

        Supply brief with schema_version="collaboration_brief_v0", purpose, context,
        constraints (strings), inputs (relative ref/description/optional sha256;
        optional delegation={operation_id,ref,relation} requires sha256, an accepted
        source owned by this requester and the matching receiver file; relation is
        responds_to, revises or uses),
        acceptance (strings), return_requirement. Work continues independently of this MCP
        conversation. Read its durable operation later; do not repeat timed-out work.
        """
        return delegations.start(binding_id, operation_id, brief, parent_request_id)

    @server.tool()
    def adopt_delegation_result(operation_id: str, consumer_operation_id: str) -> dict:
        """Record requester adoption into an accepted later result, not mere reading.

        Both executions must be current and accepted. The consumer brief must have
        a uses input with delegation={operation_id,ref,relation} and the source SHA256.
        Its receiver-workspace input must still match. Does not complete a Goal.
        """
        return delegations.adopt_result(operation_id, consumer_operation_id)

    @server.tool()
    def read_delegation(operation_id: str) -> dict:
        """Read current work/result by original id; accepted requires canonical readback."""
        return delegations.read(operation_id)

    @server.tool()
    async def wait_delegation(operation_id: str) -> dict:
        """Wait at most 15 seconds for an original operation; returning running is normal."""
        return await asyncio.to_thread(delegations.wait, operation_id)

    @server.tool()
    def resume_delegation(operation_id: str) -> dict:
        """Reconnect an interrupted original execution; never launch a replacement Turn."""
        return delegations.resume(operation_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--workspace", type=Path, help="Required when serving MCP; workers use their pinned binding")
    parser.add_argument("--execution-config", type=Path, help="Explicit operator-owned local execution bindings")
    parser.add_argument("--delegation-action", choices=["worker", "validate"],
                        help="Run a host-owned delegation action instead of serving MCP")
    parser.add_argument("--operation-id", help="Original delegation operation identity")
    args = parser.parse_args()
    if args.delegation_action or args.operation_id:
        if not (args.delegation_action and args.operation_id and args.execution_config):
            parser.error("delegation actions require --execution-config and --operation-id")
        service = Delegations(args.runtime_root, args.registry, args.goal_id,
                              args.agent_id, args.execution_config)
        if args.delegation_action == "validate":
            service._validate(service._bound(_read(service.path(args.operation_id))))
        else:
            try:
                service.execute(args.operation_id)
            except LockAcquireTimeoutError:
                pass  # Another worker still owns the operation after the bounded wait.
        return
    if args.workspace is None:
        parser.error("--workspace is required when serving MCP")
    create_server(
        args.runtime_root.resolve(),
        args.registry.resolve(),
        args.goal_id,
        args.agent_id,
        args.workspace.resolve(), args.execution_config,
    ).run(transport="stdio")


if __name__ == "__main__":
    main()
