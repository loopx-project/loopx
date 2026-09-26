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
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

from .file_lock import exclusive_file_lock, LockAcquisitionPolicy, LockAcquireTimeoutError
from .todos import list_goal_todos
from .control_plane.effect_runtime import effect_runtime_result, EffectRuntimeRemoteError
from .control_plane.goals.acceptance import inspect_goal_acceptance, validate_goal_task_acceptance, goal_task_validation_files_current
from .control_plane.coordination.local_authority import local_authority_is_promoted
from .control_plane.todos.handoff_mode import show_goal_handoff_mode
from .control_plane.turn_driver.journal_store import (
    find_loopx_turn_key_by_settlement_identity,
    load_turn_journal,
    turn_journal_path,
)
from .control_plane.turn_driver.host_binding import turn_host_arg_option
from .control_plane.collaboration.inbox import _hash, _read, _write, _root, _receipt
from .control_plane.collaboration.peers import return_result
from .control_plane.collaboration.inbox import acknowledge, _entry, normalize_request
from .control_plane.collaboration.goal_instance_scope import (
    capture_collaboration_goal_ref,
    collaboration_goal_scope,
    decide_collaboration_lifecycle,
)
from .control_plane.collaboration import delegation_results
from .control_plane.collaboration.peers import (
    _goal,
    consume_return,
    read_inbox,
    request,
    require_operation_id,
)


_PINNED_MODULE_LAUNCHER = (
    "import runpy,sys;"
    "release_root,module=sys.argv[1:3];"
    "sys.path.insert(0,release_root);"
    "sys.argv=[module,*sys.argv[3:]];"
    "runpy.run_module(module,run_name='__main__')"
)


def _release_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _pinned_release_environment() -> dict[str, str]:
    """Keep managed children on the release that admitted the delegation.

    A delegated workspace may itself be a LoopX checkout.  Plain
    ``python -m loopx...`` prepends that workspace to ``sys.path`` and can
    silently run an older control plane than the parent process.  Safe-path
    mode removes the current directory while an explicit release-root
    ``PYTHONPATH`` keeps source checkouts and installed releases deterministic.
    Safe-path is selected on LoopX's own argv instead of exported globally:
    host and acceptance scripts may legitimately import sibling modules.
    """

    environment = os.environ.copy()
    release_root = str(_release_root())
    inherited = [
        entry
        for entry in environment.get("PYTHONPATH", "").split(os.pathsep)
        if entry and Path(entry).resolve(strict=False) != Path(release_root)
    ]
    environment["PYTHONPATH"] = os.pathsep.join([release_root, *inherited])
    environment.pop("PYTHONSAFEPATH", None)
    return environment


def _python_module_command(module: str) -> list[str]:
    return [sys.executable, "-P", "-m", module]


def _pinned_module_command(module: str, *, interpreter: str | None = None) -> list[str]:
    """Build a self-contained module argv for hosts that sanitize env vars."""

    return [
        interpreter or sys.executable,
        "-P",
        "-c",
        _PINNED_MODULE_LAUNCHER,
        str(_release_root()),
        module,
    ]


def _mcp_python_candidates() -> tuple[Path, ...]:
    configured = os.environ.get("LOOPX_MCP_PYTHON")
    managed = (
        Path.home()
        / ".local"
        / "share"
        / "loopx"
        / "mcp-venv"
        / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    )
    values: list[Path] = []
    if configured:
        selected = Path(configured).expanduser()
        if not selected.is_absolute():
            raise ValueError("LOOPX_MCP_PYTHON must be an absolute path")
        values.append(selected)
    values.extend([Path(sys.executable), managed])
    # Do not resolve interpreter symlinks: a venv's ``python`` commonly points
    # at the base executable, but its original path is what selects the venv
    # site-packages containing FastMCP.
    return tuple(dict.fromkeys(values))


@lru_cache(maxsize=1)
def _mcp_python_executable() -> str:
    """Resolve a Python that can actually serve the required stdio MCP.

    LoopX itself intentionally has no mandatory third-party dependencies.  Its
    installers provision the shared MCP venv separately, so a managed Codex
    child must not assume that the control-plane interpreter also has FastMCP.
    """

    for candidate in _mcp_python_candidates():
        if not candidate.is_file():
            continue
        try:
            probe = subprocess.run(
                [
                    str(candidate),
                    "-P",
                    "-c",
                    "from mcp.server.fastmcp import FastMCP",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=_pinned_release_environment(),
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0:
            return str(candidate)
    raise ValueError(
        "LoopX collaboration MCP runtime is unavailable; provision the shared "
        "LoopX MCP venv or set LOOPX_MCP_PYTHON to a compatible interpreter"
    )


def _mcp_module_command() -> list[str]:
    # Codex intentionally sanitizes PYTHONPATH for stdio MCP children.  Carry
    # the admitted release root in argv and rebuild sys.path inside the child
    # rather than trusting ambient process state.
    return _pinned_module_command(
        "loopx.collaboration_mcp",
        interpreter=_mcp_python_executable(),
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
    caller_goal_ref = capture_collaboration_goal_ref(
        registry,
        goal_id=goal_id,
        agent_id=agent_id,
    )

    def check_scope():
        # Revocation is read on every tool call, including a long-lived server.
        _goal(registry, goal_id, agent_id)

    @server.tool()
    def read_context(cursor: str | None = None) -> dict:
        """Read pending requests, material version checks and unconsumed peer results.

        Follow next_cursor for later requests. Omit cursor to start a fresh scan.
        Pages are live; reading all pages does not complete outstanding work.
        """
        return read_inbox(
            root,
            registry,
            goal_id,
            agent_id,
            workspace=workspace,
            cursor=cursor,
            caller_goal_ref=caller_goal_ref,
        )

    @server.tool()
    def assess_request(
        request_id: str,
        decision: Literal["adopt", "defer", "reject", "no_change"],
        reason: str,
    ) -> dict:
        """Record your independent decision; this does not change task ownership or priority."""
        check_scope()
        return acknowledge(
            root,
            goal_id,
            agent_id,
            request_id,
            decision,
            reason,
            registry=registry,
            caller_goal_ref=caller_goal_ref,
        )

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
            caller_goal_ref=caller_goal_ref,
        )

    @server.tool()
    def return_result(request_id: str, text: str) -> dict:
        """Save an evidence-backed conclusion or explicit blocker for the original requester."""
        check_scope()
        # The host adapter selects Chat/Lark transport; the shared collaboration
        # owner never depends on presentation or manager capabilities.
        from .capabilities.manager_context.roundtrip import report

        return report(
            root,
            goal_id,
            agent_id,
            request_id,
            "conclusion",
            text,
            registry=registry,
            caller_goal_ref=caller_goal_ref,
        )

    @server.tool()
    def consume_peer_result(request_id: str) -> dict:
        """Acknowledge a peer result after reading and using/rejecting it; no work-state mutation."""
        check_scope()
        return consume_return(
            root,
            goal_id,
            agent_id,
            request_id,
            registry=registry,
            caller_goal_ref=caller_goal_ref,
        )


class Delegations:
    """Host IO for bound peer work; typed grants and observations stay in TS.

    Serving MCP, spawning its detached worker and validating its original Turn
    share this host entrypoint instead of maintaining a second control-plane CLI.
    """

    def __init__(self, root: Path, registry: Path, goal_id: str, agent_id: str, config: Path):
        self.root, self.registry = root.resolve(), registry.resolve()
        self.goal_id, self.agent_id, self.config = goal_id, agent_id, config.resolve()
        self._goal_ref_lock = Lock()
        try:
            self.goal_ref = capture_collaboration_goal_ref(
                self.registry,
                goal_id=self.goal_id,
                agent_id=self.agent_id,
            )
        except FileNotFoundError:
            self.goal_ref = None

    def _caller_goal_ref(self) -> dict[str, str] | None:
        if self.goal_ref is not None:
            return self.goal_ref
        with self._goal_ref_lock:
            if self.goal_ref is None:
                self.goal_ref = capture_collaboration_goal_ref(
                    self.registry,
                    goal_id=self.goal_id,
                    agent_id=self.agent_id,
                )
        return self.goal_ref

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
            try:
                promoted = local_authority_is_promoted(
                    runtime_root=self.root,
                    goal_id=self.goal_id,
                )
            except (OSError, RuntimeError):
                # A mode readback failure is an unavailable authority, never a
                # reason to suggest that a fresh promotion should be attempted.
                promoted = True
            return effect_runtime_result("collaboration.delegation.preflight", {
                "binding": {key: binding[key] for key in ("id", "agent_id", "todo_id")},
                "authority": {
                    "ready": False,
                    "reason": str(exc),
                    "state": "unavailable" if promoted else "promotion_required",
                    "next_action": (
                        "repair_canonical_authority"
                        if promoted
                        else "preview_reviewed_goal_authority_promotion"
                    ),
                },
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
                                binding["agent_id"], operation_id, brief, parent_request_id,
                                caller_goal_ref=self._caller_goal_ref())
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
        subprocess.Popen([*_python_module_command("loopx.collaboration_mcp"),
            "--delegation-action", "worker", "--runtime-root", str(self.root),
            "--registry", str(self.registry), "--goal-id", self.goal_id,
            "--agent-id", self.agent_id, "--execution-config", str(self.config), "--operation-id=" + operation_id,
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True, env=_pinned_release_environment())

    def resume(self, operation_id: str) -> dict:
        path = self.path(operation_id)
        try:
            with exclusive_file_lock(
                path, policy=LockAcquisitionPolicy.SINGLE_FLIGHT
            ):
                row = _read(path)
                binding = self._bound(row)
                if row["status"] == "rejected":
                    self._recover_validated_settlement(path, row, binding)
                should_spawn = row["status"] not in {"accepted", "rejected"}
                if should_spawn:
                    self.binding(
                        row["identity"]["binding"]["id"], require_active=True
                    )
        except LockAcquireTimeoutError:
            # A live worker already owns the operation lock.  Observation is
            # sufficient; spawning another process cannot advance settlement.
            return self.read(operation_id)
        if should_spawn:
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

    @staticmethod
    def _turn_instance_id(row: dict) -> str:
        return str(
            row.get("turn_instance_id")
            or "delegation-" + row["identity"]["request_id"][:32]
        )

    def _matching_turn_key(self, row: dict, binding: dict) -> str | None:
        """Find only the journal bound to this operation's settlement identity."""

        return find_loopx_turn_key_by_settlement_identity(
            self.root,
            goal_id=self.goal_id,
            agent_id=binding["agent_id"],
            todo_id=binding["todo_id"],
            turn_instance_id=self._turn_instance_id(row),
        )

    def _validated_turn_journal(self, row: dict, binding: dict) -> dict | None:
        turn_key = self._matching_turn_key(row, binding)
        if turn_key is None:
            return None
        journal = load_turn_journal(
            turn_journal_path(self.root, goal_id=self.goal_id, turn_key=turn_key)
        )
        if journal is None:
            return None
        phases = journal.get("completed_phases")
        validation = journal.get("task_validation")
        host_result = journal.get("host_result")
        if (
            journal.get("status") != "in_progress"
            or journal.get("result_kind") != "validated_progress"
            or phases != ["host_execute", "typed_result", "validation"]
            or not isinstance(validation, dict)
            or validation.get("ok") is not True
            or not isinstance(host_result, dict)
            or host_result.get("turn_key") != turn_key
            or host_result.get("result_kind") != "validated_progress"
        ):
            return None
        row["turn_key"] = turn_key
        return journal

    def _recover_validated_settlement(
        self, path: Path, row: dict, binding: dict
    ) -> bool:
        """Reopen only an exact, independently validated settlement boundary."""

        journal = self._validated_turn_journal(row, binding)
        if journal is None:
            return False
        decision = effect_runtime_result(
            "collaboration.delegation.recover_validated_settlement",
            {
                "from": row["status"],
                "identity_matched": True,
                "journal_status": journal.get("status"),
                "result_kind": journal.get("result_kind"),
                "completed_phases": journal.get("completed_phases"),
                "task_validation_passed": (
                    isinstance(journal.get("task_validation"), dict)
                    and journal["task_validation"].get("ok") is True
                ),
            },
        )
        row["status"] = decision["status"]
        row["turn_result"] = {
            "status": journal.get("status"),
            "result_kind": journal.get("result_kind"),
            "resume_turn_key": row["turn_key"],
            "reason": "validated Turn settlement requires same-operation recovery",
            "host_failure": None,
            "error": None,
        }
        row.pop("error", None)
        _write(path, row)
        return True

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
        completed = subprocess.run([*_python_module_command("loopx.cli"),
            "--registry", str(self.registry),
            "--runtime-root", str(self.root), "--format", "json", *args,
        ], cwd=binding["workspace"], capture_output=True, text=True, encoding="utf-8",
            timeout=timeout, env=_pinned_release_environment())
        try:
            value = json.loads(completed.stdout)
        except ValueError as exc:
            raise ValueError("delegation CLI returned no structured result") from exc
        if completed.returncode and "turn" not in args:
            raise ValueError(
                str(
                    value.get("error")
                    or value.get("reason")
                    or value.get("reason_code")
                    or "delegation canonical command rejected"
                )
            )
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
        # Turn validation intentionally runs with a reduced environment.  Carry
        # the admitted release root in argv just like the native MCP command;
        # PYTHONPATH pinning on the parent CLI is not a durable validator
        # identity and may be removed by the host boundary.
        validator = [*_pinned_module_command("loopx.collaboration_mcp"),
                     "--delegation-action", "validate", "--runtime-root", str(self.root),
                     "--registry", str(self.registry), "--goal-id", self.goal_id,
                     "--agent-id", self.agent_id, "--execution-config", str(self.config),
                     "--workspace", binding["workspace"], "--operation-id", operation_id]
        native_tools: list[str] = []
        if turn_host_arg_option(binding["host_args"], "--host") == "codex-cli":
            mcp_server = {
                "schema_version": "codex_stdio_mcp_server_v0",
                "name": "loopx_delegation",
                "command": [*_mcp_module_command(),
                    "--runtime-root",
                    str(self.root),
                    "--registry",
                    str(self.registry),
                    "--goal-id",
                    self.goal_id,
                    "--agent-id",
                    binding["agent_id"],
                    "--workspace",
                    binding["workspace"],
                    "--execution-config",
                    str(self.config),
                ],
            }
            native_tools = ["--codex-mcp-server-json", json.dumps(mcp_server)]
        return ["--execution-mode", "isolated-headless", "--project", binding["workspace"],
                     "--scan-root", binding["workspace"], "--no-global-sync",
                     "--timeout-seconds", str(binding["timeout_seconds"]),
                     "--validation-command-json", json.dumps(validator),
                     "--validation-failure-kind", "repair_required", *native_tools,
                     *binding["host_args"]]

    def _record_turn_result(
        self, path: Path, row: dict, result: dict, *, publish: bool = True
    ) -> None:
        turn_key = result.get("resume_turn_key")
        if turn_key:
            # Validate the public shape before persisting an address supplied
            # by the CLI boundary.
            turn_journal_path(self.root, goal_id=self.goal_id, turn_key=turn_key)
            row["turn_key"] = turn_key
        row["turn_result"] = {
            key: result.get(key)
            for key in (
                "status",
                "result_kind",
                "resume_turn_key",
                "reason",
                "host_failure",
                "error",
            )
        }
        if publish:
            self._observe(path, row, "turn_returned")
        else:
            _write(path, row)

    def _receiver_adopted(self, row: dict, binding: dict) -> bool:
        request_id = row["identity"]["request_id"]
        with collaboration_goal_scope(
            self.registry,
            goal_id=self.goal_id,
            agents=(),
            caller_goal_ref=self._caller_goal_ref(),
        ) as goal_scope:
            entry = _entry(
                self.root,
                self.goal_id,
                binding["agent_id"],
                request_id,
                scope=goal_scope,
            )
            decide_collaboration_lifecycle(
                goal_scope,
                operation="history_inspect",
                record=entry,
            )
            decision, error = _receipt(
                self.root,
                "decisions",
                entry,
            )
        return not error and bool(decision) and decision["decision"] == "adopt"

    def _delegation_bootstrap(self, row: dict, binding: dict) -> dict:
        request_id = row["identity"]["request_id"]
        with collaboration_goal_scope(
            self.registry,
            goal_id=self.goal_id,
            agents=(),
            caller_goal_ref=self._caller_goal_ref(),
        ) as goal_scope:
            entry = _entry(
                self.root,
                self.goal_id,
                binding["agent_id"],
                request_id,
                scope=goal_scope,
            )
            decide_collaboration_lifecycle(
                goal_scope,
                operation="history_inspect",
                record=entry,
            )
        return {
            "request_id": request_id,
            "brief": entry["brief"],
            "instruction": (
                "Use the loopx_delegation tools to read_context and call "
                "assess_request for this request before working. If you adopt "
                "it, call return_result with the evidence-backed conclusion "
                "after validation. Final-answer prose alone is not an adoption "
                "or return receipt."
            ),
        }

    def _write_delegation_bootstrap(self, row: dict, binding: dict) -> None:
        """Expose the compatibility file only while the delegated host runs.

        Some generic hosts still read ``DELEGATION.json`` directly.  It is a
        host input, not a delivery artifact, so retaining the untracked file
        after the host exits would make the completion workspace fail its own
        clean-worktree guard.  Never overwrite an unrelated caller file.
        """

        path = Path(binding["workspace"]) / "DELEGATION.json"
        expected = self._delegation_bootstrap(row, binding)
        if path.exists():
            if _read(path) != expected:
                raise ValueError(
                    "delegation bootstrap path is occupied by another request"
                )
            return
        _write(path, expected)

    def _clear_delegation_bootstrap(self, row: dict, binding: dict) -> None:
        """Remove only this operation's host input before workspace validation."""

        path = Path(binding["workspace"]) / "DELEGATION.json"
        if not path.exists():
            return
        if _read(path) != self._delegation_bootstrap(row, binding):
            raise ValueError(
                "delegation bootstrap changed while the delegated host was running"
            )
        path.unlink()

    def _acquire_delegation_lease(
        self, path: Path, row: dict, binding: dict
    ) -> dict:
        """Acquire the promoted hard lease before worker or completion effects."""

        if not local_authority_is_promoted(
            runtime_root=self.root,
            goal_id=self.goal_id,
        ):
            row["task_lease"] = {"required": False, "handoff_mode": "legacy"}
            _write(path, row)
            return row["task_lease"]
        handoff_mode = show_goal_handoff_mode(
            registry_path=self.registry,
            runtime_root_arg=str(self.root),
            goal_id=self.goal_id,
        )["handoff_mode"]
        if handoff_mode != "hard_lease":
            row["task_lease"] = {
                "required": False,
                "handoff_mode": handoff_mode,
            }
            _write(path, row)
            return row["task_lease"]
        lease_key = self._turn_instance_id(row)
        result = self._cli(
            binding,
            "todo",
            "claim",
            "--goal-id",
            self.goal_id,
            "--todo-id",
            binding["todo_id"],
            "--claimed-by",
            binding["agent_id"],
            "--agent-id",
            binding["agent_id"],
            "--claim-operation-id",
            "delegation-claim-" + row["identity"]["request_id"][:32],
            "--task-lease-idempotency-key",
            lease_key,
        )
        lease = result.get("lease")
        if (
            result.get("ok") is not True
            or not isinstance(lease, dict)
            or lease.get("owner") != binding["agent_id"]
            or lease.get("idempotency_key") != lease_key
            or lease.get("status") != "active"
            or not isinstance(lease.get("version"), int)
        ):
            raise ValueError(
                str(
                    result.get("error")
                    or result.get("reason")
                    or "delegation task lease acquisition rejected"
                )
            )
        row["task_lease"] = {
            "required": True,
            "handoff_mode": "hard_lease",
            "idempotency_key": lease_key,
            "version": lease["version"],
        }
        _write(path, row)
        return row["task_lease"]

    def _complete_delegated_todo(self, row: dict, binding: dict) -> None:
        lease = row.get("task_lease")
        if not isinstance(lease, dict):
            raise ValueError("delegation Todo completion requires its acquired task lease")
        arguments = [
            "todo",
            "complete",
            "--goal-id",
            self.goal_id,
            "--agent-id",
            binding["agent_id"],
            "--todo-id",
            binding["todo_id"],
            "--claimed-by",
            binding["agent_id"],
            "--turn-instance-id",
            self._turn_instance_id(row),
            "--note",
            "Bounded delegated work; requester owns synthesis.",
        ]
        if lease.get("required") is True:
            arguments += [
                "--task-lease-idempotency-key",
                str(lease["idempotency_key"]),
                "--task-lease-expected-version",
                str(lease["version"]),
            ]
        else:
            arguments.append("--no-follow-up")
        result = self._cli(binding, *arguments)
        if result.get("ok") is not True:
            raise ValueError(
                str(result.get("error") or result.get("reason") or "delegation Todo completion rejected")
            )

    def _execute(self, path: Path, row: dict, binding: dict) -> None:
        request_id = row["identity"]["request_id"]
        common = ["--goal-id", self.goal_id, "--agent-id", binding["agent_id"]]
        execution = self._execution_arguments(binding, row["identity"]["operation_id"])
        try:
            if row["status"] == "prepared":
                row["turn_instance_id"] = self._turn_instance_id(row)
                self._write_delegation_bootstrap(row, binding)
                self._acquire_delegation_lease(path, row, binding)
                self._observe(path, row, "running")
            if row["status"] == "running":
                turn_key = self._matching_turn_key(row, binding)
                selector = (
                    ["--resume-turn-key", turn_key]
                    if turn_key
                    else [
                        "--todo-id",
                        binding["todo_id"],
                        "--turn-instance-id",
                        self._turn_instance_id(row),
                    ]
                )
                result = self._cli(binding, "turn", "run-once", *common, *selector, *execution,
                                   "--execute", timeout=binding["timeout_seconds"] + 60)
                self._record_turn_result(path, row, result)
        finally:
            # The compatibility bootstrap is private host input.  Keeping it
            # after the host returns (including an exception or timeout) makes
            # an otherwise clean Git worktree fail canonical validation.
            self._clear_delegation_bootstrap(row, binding)
        try:
            todo_completed_for_settlement = False
            result = row["turn_result"]
            if result.get("status") != "committed" or result.get("result_kind") != "validated_progress":
                journal = self._validated_turn_journal(row, binding)
                if journal is None:
                    row["error"] = str(
                        result.get("error")
                        or result.get("reason")
                        or "delegation Turn rejected; inspect the original Turn before retrying"
                    )[:180]
                    self._observe(path, row, "rejected")
                    return
                if not self._receiver_adopted(row, binding):
                    row["error"] = "delegation receiver did not adopt the request"
                    self._observe(path, row, "rejected")
                    return
                self._bound(row, require_active=True)
                delegation_results.require_dependencies(
                    self, binding, delegation_results.operation_brief(self, row)
                )
                if not isinstance(row.get("task_lease"), dict):
                    self._acquire_delegation_lease(path, row, binding)
                self._complete_delegated_todo(row, binding)
                todo_completed_for_settlement = True
                result = self._cli(
                    binding,
                    "turn",
                    "run-once",
                    *common,
                    "--resume-turn-key",
                    row["turn_key"],
                    *execution,
                    "--execute",
                    timeout=binding["timeout_seconds"] + 60,
                )
                self._record_turn_result(path, row, result, publish=False)
            if result.get("status") != "committed" or result.get("result_kind") != "validated_progress":
                raise ValueError(
                    str(
                        result.get("error")
                        or result.get("reason")
                        or "validated delegation settlement remains incomplete"
                    )
                )
            if not self._receiver_adopted(row, binding):
                row["error"] = "delegation receiver did not adopt the request"
                self._observe(path, row, "rejected")
                return
            self._bound(row, require_active=True)  # revocation or rebinding while the model ran
            delegation_results.require_dependencies(self, binding, delegation_results.operation_brief(self, row))
            if not todo_completed_for_settlement:
                self._complete_delegated_todo(row, binding)
            row["artifacts"] = self._accepted(binding)
            if not (_root(self.root) / "replies" / request_id / "conclusion.json").exists():
                return_result(
                    self.root,
                    self.goal_id,
                    binding["agent_id"],
                    request_id,
                    json.dumps(
                        {
                            "todo_id": binding["todo_id"],
                            "status": "accepted",
                            "artifacts": [
                                {k: v for k, v in item.items() if k != "text"}
                                for item in row["artifacts"]
                            ],
                        }
                    ),
                    registry=self.registry,
                    caller_goal_ref=self._caller_goal_ref(),
                )
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
