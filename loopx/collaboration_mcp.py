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
import signal
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

from .file_lock import (
    exclusive_file_lock, lock_holder_host_label, lock_holder_liveness,
    LOCK_HOLDER_ABSENT, LOCK_HOLDER_DEAD, LOCK_HOLDER_RELEASED,
    LOCK_HOLDER_FOREIGN_HOST, LOCK_HOLDER_LIVE, LockAcquisitionPolicy, LockAcquireTimeoutError,
)
from .control_plane.effect_runtime import (
    effect_runtime_request_scope, effect_runtime_result, EffectRuntimeRemoteError,
)
from .control_plane.coordination.local_authority import local_authority_is_promoted
from .control_plane.todos.handoff_mode import show_goal_handoff_mode
from .control_plane.turn_driver.journal_store import (
    find_loopx_turn_key_by_settlement_identity,
    load_turn_journal,
    turn_journal_path,
)
from .control_plane.turn_driver.host_binding import turn_host_arg_option
from .control_plane.turn_driver.host_process_transport import (
    HOST_PROCESS_DRAINING, HOST_PROCESS_RECORD_ENV,
    execution_host_drain, prepare_host_process_record, require_execution_host_drain_supported,
)
from .control_plane.turn_driver.lane_fence import (
    TURN_LANE_ABSENT, TURN_LANE_DEAD, TURN_LANE_LIVE, TURN_LANE_RELEASED,
    turn_lane_liveness, turn_lane_target,
)
from .control_plane.collaboration.delegation_stop_signal import (
    DelegationFenced, DelegationStopRequested, WorkerStopSignal, install_worker_stop_signal,
)
from .control_plane.collaboration.inbox import _hash, _read, _write, _root, _receipt
from .control_plane.collaboration.delegation_inventory import (
    DELEGATION_HOST_PROCESS_SUFFIX, DELEGATION_STOP_RECEIPT_SUFFIX,
)
from .control_plane.collaboration.peers import return_result
from .control_plane.collaboration.inbox import acknowledge, _entry, normalize_request
from .control_plane.collaboration.goal_instance_scope import (
    capture_collaboration_goal_ref,
    collaboration_goal_scope,
    decide_collaboration_lifecycle,
)
from .control_plane.collaboration import delegation_results, delegation_stop_lease, delegation_validation
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


def _observe_bound_workspace(path: Path) -> tuple[str, tuple[int, int, Path] | None]:
    """Observe a directory and its target identity without exposing its path.

    The binding is operator-owned, but a worktree or symlink can disappear or
    change targets while a read-only Turn preview is running.  Identity is a
    host fact; the shared TypeScript preflight still owns the readiness rule.
    """

    try:
        observed = path.stat()
        if not stat.S_ISDIR(observed.st_mode):
            return "not_directory", None
        resolved = path.resolve(strict=True)
        target = resolved.stat()
        if (observed.st_dev, observed.st_ino) != (target.st_dev, target.st_ino):
            return "unavailable", None
        return "available", (observed.st_dev, observed.st_ino, resolved)
    except FileNotFoundError:
        return "missing", None
    except NotADirectoryError:
        return "not_directory", None
    except (OSError, RuntimeError):
        return "unavailable", None


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
        register_delegation_tools(server, Delegations(
            root, registry, goal_id, agent_id, execution_config, reuse_preview=True,
        ))
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
    def link_work(request_id: str, todo_ids: list[str] | None = None,
                  evidence_ids: list[str] | None = None) -> dict:
        """Link this request to your existing Core work or opaque evidence IDs.

        This creates no Todo, claim or execution grant. read_context reads
        current linked work; busy or completed unrelated work proves nothing
        about this request. Short answers do not need a Todo link.
        """
        check_scope()
        from .control_plane.collaboration.links import link

        return link(
            root, registry, goal_id, agent_id, request_id,
            todo_ids or [], evidence_ids or [], caller_goal_ref=caller_goal_ref,
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
    def return_result(request_id: str, text: str, update_id: str | None = None) -> dict:
        """Return a conclusion to the original requester. For a later changed fact,
        append an update with a stable update_id; retry with the same id and text.
        Neither a blocker nor a returned result certifies completion of the work.
        """
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
            update_id=update_id,
            registry=registry,
            caller_goal_ref=caller_goal_ref,
        )

    @server.tool()
    def consume_peer_result(request_id: str, result_key: str = "conclusion") -> dict:
        """Acknowledge a read peer result, using its result_key for a later update.
        This consumes only that result and never changes work state.
        """
        check_scope()
        return consume_return(
            root,
            goal_id,
            agent_id,
            request_id,
            result_key=result_key,
            registry=registry,
            caller_goal_ref=caller_goal_ref,
        )


DELEGATION_STOP_SCHEMA_VERSION = "loopx_delegation_stop_v0"
# Observations that no worker may reopen; a stop against one is a no-op receipt.
DELEGATION_TERMINAL_STATUSES = frozenset({"accepted", "rejected", "stopped"})
DELEGATION_STOP_OPEN_PHASES = frozenset({"requested", "acknowledged"})
DELEGATION_STOP_TERMINAL_PHASES = frozenset({"settled", "unknown"})
# How long a signalled same-host worker may take to acknowledge before SIGKILL.
DELEGATION_STOP_GRACE_SECONDS = 10.0
DELEGATION_STOPPED_MESSAGE = "delegation operation was stopped; start a new operation id"


def execution_row_path(root: Path, goal_id: str, agent_id: str, operation_id: str) -> Path:
    """The requester-scoped durable operation record; readable without a service."""
    return _root(root) / "executions" / _hash([goal_id, agent_id]) / (_hash(operation_id) + ".json")


_WAKE_INTENT_KEYS = ("schema_version", "intent_id", "requester", "conversation", "operation_id", "request_id")


def wake_receipt(intent: dict, state: str, **facts) -> dict:
    """One receipt shape: the typed intent plus only the current state's facts."""
    return {**{key: intent[key] for key in _WAKE_INTENT_KEYS if key in intent}, "state": state, **facts}


def record_wake(path: Path, decide) -> dict | None:
    """Settle a pending wake receipt under the same lock adopt_result uses.

    ``decide`` receives the pending intent and returns the replacement receipt,
    or None to leave it unchanged.  Only an accepted result with a pending
    intent is decidable; any other terminal state wakes nobody.
    """
    with exclusive_file_lock(path):
        row = _read(path)
        wake = row.get("wake")
        if not isinstance(wake, dict) or wake.get("state") != "pending" or row.get("status") != "accepted":
            return None
        updated = decide(wake)
        if updated is None:
            return None
        row["wake"] = updated
        _write(path, row)
    return updated


class Delegations:
    """Host IO for bound peer work; typed grants and observations stay in TS.

    Serving MCP, spawning its detached worker and validating its original Turn
    share this host entrypoint instead of maintaining a second control-plane CLI.
    """

    def __init__(self, root: Path, registry: Path, goal_id: str, agent_id: str, config: Path,
                 *, reuse_preview: bool = False):
        self.root, self.registry = root.resolve(), registry.resolve()
        self.goal_id, self.agent_id, self.config = goal_id, agent_id, config.resolve()
        self._goal_ref_lock = Lock()
        self._stop_signal: WorkerStopSignal | None = None
        # Only an entrypoint that owns a reusable service lifetime opts in.
        # CLI and per-request Goal Chat services keep the original one-shot IO;
        # starting a supervisor there cannot amortize its cold/cleanup cost.
        self._preview_transport = None
        if reuse_preview:
            from .control_plane.collaboration.delegation_preview_transport import DelegationPreviewTransport

            self._preview_transport = DelegationPreviewTransport()
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
        return execution_row_path(self.root, self.goal_id, self.agent_id, operation_id)

    @staticmethod
    def _stop_path(path: Path) -> Path:
        """The stop receipt sits beside its execution record and is never merged into it."""
        return path.with_name(path.stem + DELEGATION_STOP_RECEIPT_SUFFIX)

    @staticmethod
    def _host_process_record(path: Path) -> Path:
        """Where this operation's Turn names the native Host it launched, for drain readback."""
        return path.with_name(path.stem + DELEGATION_HOST_PROCESS_SUFFIX)

    @staticmethod
    def _dispatch_lock(path: Path) -> Path:
        return path.with_suffix(".dispatch")

    @staticmethod
    def _read_stop(path: Path) -> dict | None:
        stop_path = Delegations._stop_path(path)
        if not stop_path.exists():
            return None
        return _read(stop_path)

    def operations(self, *, limit: int = 20, cursor: str | None = None) -> dict:
        from .control_plane.collaboration.delegation_inventory import read_delegation_inventory

        return read_delegation_inventory(self, limit=limit, cursor=cursor)

    def inspect(self, binding_id: str) -> dict:
        """Observe the real Turn preflight; never create a request or run a host."""
        # Pin executable source only for this observation, not authority data.
        # Bindings, acceptance and validation files are still read twice below;
        # the next inspection must resolve its own current source revision.
        with effect_runtime_request_scope():
            return self._inspect(binding_id)

    def _inspect(self, binding_id: str) -> dict[str, object]:
        binding = self.binding(binding_id, require_active=True)
        # Host filesystem facts only; the shared TS owner projects readiness.
        # Do not expose a path/error body or probe authority in a missing cwd.
        workspace_path = Path(binding["workspace"])
        workspace_state, workspace_identity = _observe_bound_workspace(workspace_path)

        def workspace_fault(state: str) -> dict[str, object]:
            return effect_runtime_result("collaboration.delegation.preflight", {
                "binding": {key: binding[key] for key in ("id", "agent_id", "todo_id")},
                "workspace": {"state": state},
                "authority": None, "preview": None, "acceptance": None,
                "validation_files_current": False,
            })

        def recheck_workspace() -> dict[str, object] | None:
            if self.binding(binding_id, require_active=True) != binding:
                raise ValueError("delegation preflight source changed; retry inspection")
            current_state, current_identity = _observe_bound_workspace(workspace_path)
            if current_state == "available" and current_identity == workspace_identity:
                return None
            # A replacement directory is not the directory whose authority
            # and acceptance were observed at entry.  No path or error leaks.
            return workspace_fault(
                current_state if current_state != "available" else "unavailable"
            )

        def authority_fault(exc: OSError | ValueError) -> dict[str, object]:
            fault = recheck_workspace()
            if fault is not None:
                return fault
            # Authority admission is a readiness observation, not a reason for
            # inspection to invent a provider launch or collapse into a raw CLI error.
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
                "workspace": {"state": workspace_state},
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

        if workspace_state != "available":
            if self.binding(binding_id, require_active=True) != binding:
                raise ValueError("delegation preflight source changed; retry inspection")
            return workspace_fault(workspace_state)
        assert workspace_identity is not None
        try:
            acceptance = delegation_validation.capture(self, binding)
        except (OSError, ValueError) as exc:
            return authority_fault(exc)
        fault = recheck_workspace()
        if fault is not None:
            return fault
        operation = "inspect-" + _hash(binding_id)[:32]
        arguments = ["turn", "run-once", "--goal-id", self.goal_id,
                     "--agent-id", binding["agent_id"], "--todo-id", binding["todo_id"],
                     "--turn-instance-id", operation, *self._execution_arguments(binding, operation)]
        # Host arguments are operator-owned, but inspection must stay read-only
        # even when they contain an abbreviated execution flag or a selector.
        from .cli_runtime import add_subcommand_format, build_cli_parser
        from .cli_commands.turn_registration import register_turn_commands

        parser, subparsers = build_cli_parser()
        register_turn_commands(subparsers, add_subcommand_format)
        try:
            selected = parser.parse_args(arguments)
        except SystemExit as exc:
            raise ValueError("invalid delegation Turn arguments") from exc
        workspace = workspace_identity[2]
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
            fault = recheck_workspace()
            if fault is not None:
                return fault
            raise ValueError("delegation inspection cannot execute or retarget bound work")
        try:
            preview = self._cli(binding, *arguments)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            fault = recheck_workspace()
            if fault is not None:
                return fault
            raise
        fault = recheck_workspace()
        if fault is not None:
            return fault
        if preview.get("status") != "preview" and "selection_rejection" not in preview:
            raise ValueError(f"delegation Turn preflight unavailable: {preview.get('error') or preview.get('status')}")
        try:
            current = delegation_validation.capture(self, binding)
        except (OSError, ValueError) as exc:
            # Do not return the first acceptance or an already-read preview
            # when current authority cannot be confirmed at the final fence.
            return authority_fault(exc)
        fault = recheck_workspace()
        if fault is not None:
            return fault
        if acceptance != current or self.binding(binding_id, require_active=True) != binding:
            raise ValueError("delegation preflight source changed; retry inspection")
        return effect_runtime_result("collaboration.delegation.preflight", {
            "binding": {key: binding[key] for key in ("id", "agent_id", "todo_id")},
            "workspace": {"state": workspace_state},
            "authority": {"ready": True, "reason": None},
            "preview": preview, "acceptance": acceptance["plan"],
            "validation_files_current": acceptance["files_current"],
        })

    def start(self, binding_id: str, operation_id: str, brief: dict,
              parent_request_id: str | None = None, *, conversation: dict | None = None,
              confirmed_operation_id: str | None = None) -> dict:
        """Start or replay one bound operation.

        ``conversation`` is supplied only by the trusted Chat host, never by
        the model: the session and Turn that started the operation.  It is
        kept on first creation and never replaced, so a later wake returns to
        that conversation and no other.
        """
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
            if confirmed_operation_id is not None:
                # Internal callback adapter only: a canonical locator/CAS fence,
                # not an executor identity or domain execution permission.
                identity["confirmed_operation_id"] = require_operation_id(confirmed_operation_id)
            if exists:
                if _read(path).get("identity") != identity:
                    raise ValueError("delegation operation identity conflict")
            else:
                origin = ({"session_id": str(conversation["session_id"]), "turn_id": str(conversation["turn_id"])}
                          if conversation else None)
                prepare_host_process_record(self._host_process_record(path))
                _write(path, {"identity": identity, "status": "prepared", "created_at": time.time(),
                              **({"conversation": origin} if origin else {})})
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
        if self._read_stop(path) is not None:
            raise ValueError(DELEGATION_STOPPED_MESSAGE)
        try:
            with exclusive_file_lock(
                path, policy=LockAcquisitionPolicy.SINGLE_FLIGHT
            ):
                row = _read(path)
                binding = self._bound(row)
                if row["status"] == "stopped":
                    raise ValueError(DELEGATION_STOPPED_MESSAGE)
                if row["status"] == "rejected":
                    try:
                        self._recover_validated_settlement(path, row, binding)
                    except DelegationFenced:
                        raise ValueError(DELEGATION_STOPPED_MESSAGE) from None
                should_spawn = row["status"] not in DELEGATION_TERMINAL_STATUSES
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
            if (result["status"] in DELEGATION_TERMINAL_STATUSES or result["recovery_required"]
                    or result.get("stop", {}).get("phase") in DELEGATION_STOP_TERMINAL_PHASES):
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
        self._fenced_write(path, row)
        return True

    def adopt_result(self, operation_id: str, consumer_operation_id: str) -> dict:
        return delegation_results.adopt_result(self, operation_id, consumer_operation_id)

    def read(self, operation_id: str) -> dict:
        result = self._read_current(operation_id)
        result.update(delegation_results.result_relationships(self, operation_id))
        wake = _read(self.path(operation_id)).get("wake")
        if isinstance(wake, dict):
            # Distinct from the result itself: whether the requester was continued.
            result["wake"] = wake
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
        # Resume refuses an operation with a stop receipt, so it never needs recovery.
        stop = self._read_stop(path)
        result = {"operation_id": operation_id, "request_id": row["identity"]["request_id"],
                  "agent_id": binding["agent_id"], "todo_id": binding["todo_id"],
                  "status": row["status"], "worker_active": active,
                  "recovery_required": not active and row["status"] not in DELEGATION_TERMINAL_STATUSES
                  and stop is None and time.time() - row.get("created_at", 0) > 15}
        if stop is not None:
            result["stop"] = {"stop_id": stop["stop_id"], "phase": stop["phase"]}
        if row["status"] == "accepted":
            # A saved receipt cannot hide an amended task, verifier or output.
            accepted = self._accepted(binding)
            if accepted["artifacts"] != row["artifacts"]:
                raise ValueError("delegation output changed after completion")
            result.update(accepted)
        if row.get("error"):
            result["error"] = row["error"]
        return result

    def _observe(self, path: Path, row: dict, status: str, *, already_locked: bool = False, **facts) -> None:
        decision = effect_runtime_result("collaboration.delegation.observe", {
            "from": row["status"], "to": status, **facts,
        })
        row.update(status=decision["status"])
        if isinstance(decision.get("wake_intent"), dict):
            row["wake"] = {**decision["wake_intent"], "state": "pending"}
        if already_locked:
            self._fenced_write_locked(path, row)
        else:
            self._fenced_write(path, row)

    def _fenced_write(self, path: Path, row: dict) -> None:
        """Write the execution record only while no unacknowledged stop fences this process.

        The stop receipt is re-read under the dispatch lock on every write, so a
        worker that returns after a stop it never saw writes nothing at all.
        """

        with exclusive_file_lock(self._dispatch_lock(path)):
            self._fenced_write_locked(path, row)

    def _fenced_write_locked(self, path: Path, row: dict) -> None:
        """The same write for a caller that already holds the dispatch lock.

        The lock is a kernel file lock, so it is not reentrant: a caller that
        widened its critical section to cover a whole effect group must use this
        entry point rather than nesting ``_fenced_write``.
        """

        stop = self._read_stop(path)
        if stop is not None and not self._acknowledged_here(stop):
            raise DelegationFenced()
        _write(path, row)

    @staticmethod
    def _acknowledged_here(stop: dict) -> bool:
        ack = stop.get("ack")
        return (isinstance(ack, dict) and ack.get("pid") == os.getpid()
                and ack.get("host") == lock_holder_host_label())

    def _raise_if_stop_requested(self, path: Path) -> None:
        """Worker checkpoint: leave before the next host launch or Todo effect."""
        if self._read_stop(path) is not None:
            raise DelegationStopRequested("stop_file")

    @staticmethod
    def _worker_identity() -> dict:
        return {
            "pid": os.getpid(),
            "pgid": os.getpgid(0) if hasattr(os, "getpgid") else None,
            "host": lock_holder_host_label(),
        }

    def _lane_target(self, binding: dict) -> Path:
        return turn_lane_target(runtime_root=self.root, goal_id=self.goal_id,
                                plan={"turn_envelope": {"agent_id": binding["agent_id"]}})

    def _operation_lock_free(self, path: Path) -> bool:
        """Probe this operation's own kernel lock; only ever called once its stop receipt exists.

        Unlike the Turn lane, this lock admits nothing but this operation, and a
        probe holding it for an instant refuses no legitimate acquisition once
        the receipt is written: ``resume``, its only single-flight acquirer,
        refuses a stopped operation before it touches the lock; ``execute``,
        adoption and the requester acknowledgement wait through brief holders
        with the mutation policy; and a status read already makes this same
        instant observation. Before the receipt exists a ``resume`` is still
        legitimate, so the holder is then read from its record instead.
        """

        try:
            with exclusive_file_lock(path, policy=LockAcquisitionPolicy.SINGLE_FLIGHT):
                return True
        except LockAcquireTimeoutError:
            return False

    @staticmethod
    def _recorded_worker(row: dict, stop: dict) -> dict | None:
        worker = row.get("worker")
        if not isinstance(worker, dict):
            worker = stop.get("worker")
        return worker if isinstance(worker, dict) else None

    def _worker_lane_released(self, row: dict, stop: dict, binding: dict) -> tuple[bool, str]:
        """Say whether the stopped worker's Turn has let go of the member's lane, read-only.

        This never takes the lane lock: a probe holding it for an instant would
        refuse a legitimate Turn of the same member racing that instant with
        ``turn_lane_in_flight``. The lane's last holder record decides instead.
        Released, dead or absent is released. A live holder on this machine is
        released only when it sits outside the recorded worker's process group,
        because the worker's run-once child runs in that group; a holder that
        cannot be attributed, another host's holder and an unreadable record
        prove nothing, so the typed decision keeps the stop open.
        """

        lane = turn_lane_liveness(self._lane_target(binding))
        state = lane["state"]
        if state in {TURN_LANE_RELEASED, TURN_LANE_DEAD, TURN_LANE_ABSENT}:
            return True, state
        worker = self._recorded_worker(row, stop)
        if (state != TURN_LANE_LIVE or worker is None or not hasattr(os, "getpgid")
                or worker.get("host") != lock_holder_host_label()
                or not isinstance(worker.get("pgid"), int)):
            return False, state
        try:
            return os.getpgid(lane["holder"]["pid"]) != worker["pgid"], state
        except ProcessLookupError:
            return True, TURN_LANE_DEAD  # the holder exited between the two reads
        except OSError:
            return False, state

    def _turn_journal_status(self, row: dict, binding: dict) -> str | None:
        turn_key = row.get("turn_key") or self._matching_turn_key(row, binding)
        if not turn_key:
            return None
        journal = load_turn_journal(turn_journal_path(self.root, goal_id=self.goal_id, turn_key=turn_key))
        status = journal.get("status") if isinstance(journal, dict) else None
        return str(status) if status else None

    def _new_stop_record(self, row: dict, *, requested_by: str, worker: dict | None) -> dict:
        requested_at = time.time()
        return {
            "schema_version": DELEGATION_STOP_SCHEMA_VERSION,
            "stop_id": _hash([row["identity"]["operation_id"], requested_by, requested_at])[:32],
            "operation_id": row["identity"]["operation_id"],
            "request_id": row["identity"]["request_id"],
            "phase": "requested",
            "reason": "awaiting_acknowledgement",
            "requested_by": requested_by,
            "requested_at": requested_at,
            "requested_status": row["status"],
            "worker": worker,
            "ack": None,
            "lease": None,
            "settled": None,
        }

    def _stop_receipt(self, row: dict, binding: dict, stop: dict | None) -> dict:
        receipt = {
            "operation_id": row["identity"]["operation_id"],
            "request_id": row["identity"]["request_id"],
            "agent_id": binding["agent_id"], "todo_id": binding["todo_id"],
            "status": row["status"],
        }
        if stop is None:
            # Nothing was written: a terminal observation cannot be stopped, and
            # repeating the request returns exactly this receipt again.
            receipt.update(phase="noop", reason="delegation already " + row["status"], stop=None)
        else:
            receipt.update(phase=stop["phase"], reason=stop.get("reason"), stop=stop)
        return receipt

    def stop(self, operation_id: str, *, execute: bool) -> dict:
        """Stop one bounded member and return a receipt that says what was proven.

        ``requested`` is written beside the execution record, never into it.
        When no worker holds the operation, this caller takes the lock and
        marks the record stopped. A same-host holder is signalled by process
        group and given a bounded grace to acknowledge; another host's holder
        is left to find the request at its next checkpoint or fenced write.
        ``settled`` and ``unknown`` come from the typed decision over lock
        facts, the Host transport's drain of everything the Turn launched,
        whose TS supervisor alone terminates it, and the hard lease, which is
        resolved against canonical authority only once that execution is
        proven gone; elapsed time proves nothing.
        """

        require_operation_id(operation_id)
        if not execute:
            raise ValueError("delegation stop requires execute")
        path = self.path(operation_id)
        if not path.exists():
            raise ValueError("unknown delegation operation; start_delegation returns the operation_id to stop")
        with exclusive_file_lock(self._dispatch_lock(path)):
            row = _read(path)
            binding = self._bound(row)
            stop = self._read_stop(path)
            if stop is None:
                if row["status"] in DELEGATION_TERMINAL_STATUSES:
                    return self._stop_receipt(row, binding, None)
                require_execution_host_drain_supported(self._host_drain(path))
                stop = self._new_stop_record(row, requested_by=self.agent_id,
                                             worker=self._lock_holder_worker(path, row))
                _write(self._stop_path(path), stop)
            elif stop["phase"] in DELEGATION_STOP_OPEN_PHASES:
                require_execution_host_drain_supported(self._host_drain(path))
        if stop["phase"] in DELEGATION_STOP_OPEN_PHASES and stop.get("ack") is None:
            if stop.get("worker") is None:
                # No worker was named when the request was written, so whoever owns
                # the operation acknowledges it: this caller once the lock is free.
                # The wait rides out a status read's instant hold, which must not
                # be mistaken for a holder that vanished.
                try:
                    with exclusive_file_lock(path):
                        self._acknowledge_stop(path, _read(path), binding, source="requester")
                except LockAcquireTimeoutError:
                    pass  # an unnamed holder meets the request at its next checkpoint or write
                else:
                    # A Host left behind by an earlier worker may still be terminating.
                    self._await_host_drain(path, time.monotonic() + DELEGATION_STOP_GRACE_SECONDS)
            else:
                # Only the named worker acknowledges. If it vanishes first, the typed
                # decision reports unknown instead of a requester settlement.
                self._signal_worker(path, stop)
        return self._settle_stop(path)

    def _lock_holder_worker(self, path: Path, row: dict) -> dict | None:
        """Name the recorded worker while it is the operation lock's unreleased holder.

        Read from the holder record, never the kernel lock: this runs before the
        stop receipt exists, when a probe could refuse a legitimate ``resume``.
        Only the worker identity the execution record names can become a signal
        target, so a status reader's instant holder record is never taken for it.
        """

        state, holder = lock_holder_liveness(path)
        recorded = row.get("worker")
        if state not in {LOCK_HOLDER_LIVE, LOCK_HOLDER_FOREIGN_HOST} or not isinstance(recorded, dict):
            return None
        if holder.get("pid") != recorded.get("pid") or holder.get("host") != recorded.get("host"):
            return None
        return {key: recorded.get(key) for key in ("pid", "pgid", "host")}

    def _signal_worker(self, path: Path, stop: dict) -> None:
        """Terminate a same-host holder's process group; never signal across hosts."""

        worker = stop.get("worker")
        if (not isinstance(worker, dict) or worker.get("host") != lock_holder_host_label()
                or not hasattr(os, "killpg") or not isinstance(worker.get("pid"), int)):
            return
        pid, pgid = worker["pid"], worker.get("pgid") or worker["pid"]
        if pgid == os.getpgid(0):
            raise ValueError("delegation stop refuses to signal its own process group")
        try:
            if os.getpgid(pid) != pgid:
                return  # the pid was reused by an unrelated process
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        # Poll the release facts the decision needs; the deadline only bounds this
        # call, and a Host still draining when it passes leaves the stop open.
        deadline = time.monotonic() + DELEGATION_STOP_GRACE_SECONDS
        while time.monotonic() < deadline:
            if self._operation_lock_free(path):
                self._await_host_drain(path, deadline)
                return
            time.sleep(0.2)
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not self._operation_lock_free(path):
            time.sleep(0.1)
        # The Host supervisor sits outside the worker's group and cleans up on its own.
        self._await_host_drain(path, time.monotonic() + DELEGATION_STOP_GRACE_SECONDS)

    def _host_drain(self, path: Path) -> str:
        """The Host transport's read of everything this operation's Turn launched; never signals."""
        # Read only: before stop intent exists, taking the operation lock can
        # refuse a legitimate launch. The Host boundary owns platform policy.
        holder, _ = lock_holder_liveness(path)
        return execution_host_drain(self._host_process_record(path), launch_possible=holder not in {
            LOCK_HOLDER_ABSENT, LOCK_HOLDER_DEAD, LOCK_HOLDER_RELEASED,
        })

    def _await_host_drain(self, path: Path, deadline: float) -> None:
        """Wait, never kill: the TS Host supervisor owns terminating its process group."""
        while self._host_drain(path) == HOST_PROCESS_DRAINING and time.monotonic() < deadline:
            time.sleep(0.1)

    def _acknowledge_stop(self, path: Path, row: dict, binding: dict, *, source: str) -> None:
        """Acknowledge under the operation lock; resource readback releases the lease later.

        Only the operation-lock holder calls this. The record is transitioned as
        it is on disk, so state that a fenced write refused stays unwritten. An
        ACK cannot prove the nested Host drained and must not release its
        lease. A missing stop, one already acknowledged or finished,
        and a record that already reached a terminal observation stay untouched.
        """

        if self._stop_signal is not None:
            self._stop_signal.disarm()
        with exclusive_file_lock(self._dispatch_lock(path)):
            stop = self._read_stop(path)
            current = _read(path)
            if (stop is None or stop.get("ack") is not None
                    or stop["phase"] not in DELEGATION_STOP_OPEN_PHASES
                    or current["status"] in DELEGATION_TERMINAL_STATUSES):
                return
            observed = current["status"]
            transition = effect_runtime_result("collaboration.delegation.observe", {
                "from": observed, "to": "stopped",
            })
            # This process holds the operation lock; its lane, Host drain and lease are read later.
            phase = effect_runtime_result("collaboration.delegation.stop", {
                "phase": stop["phase"], "acknowledged": True,
                "operation_lock_free": False, "worker_lane_released": False,
                "host_process": HOST_PROCESS_DRAINING, "lease": delegation_stop_lease.LEASE_UNCHECKED,
            })
            current["status"] = transition["status"]
            _write(path, current)
            stop.update(phase=phase["phase"], reason=phase["reason"], ack={
                "pid": os.getpid(), "host": lock_holder_host_label(), "at": time.time(),
                "source": source, "observed_status": observed,
                "turn_key": (current.get("turn_key") or row.get("turn_key")
                             or self._matching_turn_key(current, binding)),
            })
            _write(self._stop_path(path), stop)
        try:
            self._clear_delegation_bootstrap(row, binding)
        except (OSError, ValueError):
            pass  # the bootstrap is host input; its state never blocks the receipt

    def _settle_stop(self, path: Path) -> dict:
        with exclusive_file_lock(self._dispatch_lock(path)):
            row = _read(path)
            binding = self._bound(row)
            stop = self._read_stop(path)
            if stop is None:
                return self._stop_receipt(row, binding, None)
            if stop["phase"] not in DELEGATION_STOP_OPEN_PHASES:
                return self._stop_receipt(row, binding, stop)
            require_execution_host_drain_supported(self._host_drain(path))
            facts = {"operation_lock_free": self._operation_lock_free(path)}
            facts["worker_lane_released"], lane_state = self._worker_lane_released(row, stop, binding)
            # Read last: a Host seen drained after its worker and lane let go stays drained.
            facts["host_process"] = self._host_drain(path)
            facts["lease"] = delegation_stop_lease.LEASE_UNCHECKED
            inputs = {
                "phase": stop["phase"], "acknowledged": stop.get("ack") is not None,
                "timed_out": time.time() - stop["requested_at"] > DELEGATION_STOP_GRACE_SECONDS,
            }
            decision = effect_runtime_result("collaboration.delegation.stop", {**inputs, **facts})
            if decision["action"] == "resolve_lease":
                # The typed owner found the stopped execution gone: only now is
                # its lease resolved, against canonical authority by the
                # execution's own identity, and only the resolved fact can make
                # the receipt terminal. Under the same lock as the record, every
                # later explicit stop retries a release that has not been proven.
                facts["lease"] = delegation_stop_lease.settle(self, path, row, binding, stop)
                decision = effect_runtime_result("collaboration.delegation.stop", {**inputs, **facts})
            if decision["phase"] != stop["phase"] or decision.get("reason") != stop.get("reason"):
                stop.update(phase=decision["phase"], reason=decision.get("reason"))
                if decision["phase"] in DELEGATION_STOP_TERMINAL_PHASES:
                    stop["settled"] = {
                        "at": time.time(), **facts, "lane_state": lane_state,
                        "turn_journal_status": self._turn_journal_status(row, binding),
                    }
                _write(self._stop_path(path), stop)
            return self._stop_receipt(row, binding, stop)

    def _wake_requester(self, row: dict) -> dict:
        """Requester and exact result identity for the typed wake intent."""
        return {
            "goal_id": self.goal_id,
            "agent_id": self.agent_id,
            "goal_ref": self._caller_goal_ref(),
            "operation_id": row["identity"]["operation_id"],
            "request_id": row["identity"]["request_id"],
            "artifacts": [{k: v for k, v in item.items() if k != "text"} for item in row["artifacts"]],
            "conversation": row.get("conversation"),
        }

    def wake_observed_in_turn(self, operation_id: str, *, session_id: str) -> dict | None:
        """The requester read this accepted result inside its own Turn; no wake follows."""
        path = self.path(require_operation_id(operation_id))
        if not path.exists():
            return None
        def observe(wake: dict) -> dict | None:
            decision = effect_runtime_result("collaboration.delegation.observe_wake", {
                "intent": wake,
                "observer": {"session_id": session_id, "goal_id": self.goal_id,
                             "agent_id": self.agent_id, "goal_ref": self._caller_goal_ref()},
            })
            return (wake_receipt(wake, "observed_in_turn", observed_at=time.time())
                    if decision["observed"] else None)
        try:
            return record_wake(path, observe)
        except LockAcquireTimeoutError:
            # The worker or another decision still holds the record; the pump
            # re-reads the current state and the observation remains readable.
            return None

    def _cli(self, binding: dict, *args: str, timeout: int = 60,
             delegated_lease: dict | None = None,
             host_record: Path | None = None) -> dict:
        if self._preview_transport is not None and delegated_lease is None and args[:2] == ("turn", "run-once") and not any(
            flag in args for flag in ("--execute", "--resume-turn-key")
        ):
            # Inspection already fences selectors using the original parser;
            # the worker independently rejects execution/resume/retargeting.
            # Mutating commands keep the original one-shot or leased Host path.
            return self._preview_transport.preview(
                command=_python_module_command(
                    "loopx.control_plane.collaboration.delegation_preview_worker"
                ), workspace=Path(binding["workspace"]), release=_release_root(),
                environment=_pinned_release_environment(), registry=self.registry,
                runtime_root=self.root, goal_id=self.goal_id,
                agent_id=binding["agent_id"], todo_id=binding["todo_id"],
                argv=args, timeout=timeout,
            )
        arguments = [
            "--registry", str(self.registry),
            "--runtime-root", str(self.root), "--format", "json", *args,
        ]
        environment = _pinned_release_environment()
        environment.pop(HOST_PROCESS_RECORD_ENV, None)
        if host_record is not None:
            # The Turn's Host transport names the process group its supervisor owns.
            environment[HOST_PROCESS_RECORD_ENV] = str(host_record)
        if delegated_lease is None:
            completed = subprocess.run([*_python_module_command("loopx.cli"), *arguments],
                cwd=binding["workspace"], capture_output=True, text=True, encoding="utf-8",
                timeout=timeout, env=environment)
            stdout, returncode = completed.stdout, completed.returncode
        else:
            from .control_plane.turn_driver.host_process_transport import run_host_process

            chunks = []
            nested_record_args = []
            if host_record is not None:
                # The transport records its leased supervisor beside this record;
                # only our private CLI re-arms it for the actual Host it starts.
                # Never pass it through an arbitrary user Host.
                nested_record_args = ["--host-process-record", str(host_record)]
            try:
                observation = run_host_process(
                    [*_python_module_command("loopx.control_plane.turn_driver.delegated_cli"),
                     *nested_record_args, *arguments],
                    project=Path(binding["workspace"]), input_text="", timeout_seconds=timeout,
                    environment=environment, delegated_lease=delegated_lease,
                    on_stdout=chunks.append,
                )
            except RuntimeError as exc:
                raise ValueError("delegation managed CLI supervision unavailable; reconcile the original Turn") from exc
            if observation["outcome"] != "exited" or not observation["output_complete"]:
                detail = observation["outcome"]
                failure = observation.get("lease_failure")
                if failure is not None:
                    detail += f"; lease:{failure['boundary']}/{failure['reason']}"
                raise ValueError(f"delegation lease supervision stopped ({detail}); reconcile the original Turn")
            stdout, returncode = "".join(chunks), observation["returncode"]
        try:
            value = json.loads(stdout)
        except ValueError as exc:
            raise ValueError("delegation CLI returned no structured result") from exc
        if returncode and "turn" not in args:
            raise ValueError(
                str(
                    value.get("error")
                    or value.get("reason")
                    or value.get("reason_code")
                    or "delegation canonical command rejected"
                )
            )
        return value

    def _validate(self, binding: dict) -> dict:
        return delegation_validation.validate(self, binding)

    def _accepted(self, binding: dict) -> dict:
        return delegation_results.accepted_result(self, binding)

    def execute(self, operation_id: str) -> None:
        path = self.path(operation_id)
        # Status readers briefly acquire this same kernel lock. Wait for that
        # observation to finish before deciding another worker owns the operation.
        # The existing bounded mutation policy still excludes concurrent workers.
        with exclusive_file_lock(path):
            row = _read(path)
            if row["status"] in DELEGATION_TERMINAL_STATUSES:
                return
            binding = self._bound(row)
            if self._read_stop(path) is not None:
                # The stop arrived before any worker owned the operation: this
                # holder acknowledges it from under the lock and launches nothing.
                self._acknowledge_stop(path, row, binding, source="worker_entry")
                return
            try:
                binding = self._bound(row, require_active=True)
                row.pop("error", None)
                row["worker"] = self._worker_identity()
                self._fenced_write(path, row)
                # Different request ids cannot run the same assigned task concurrently.
                task_lock = _root(self.root) / "execution-slots" / _hash([self.goal_id, binding["todo_id"]])
                with exclusive_file_lock(task_lock, policy=LockAcquisitionPolicy.SINGLE_FLIGHT):
                    try:
                        self._execute(path, row, binding)
                    except (ValueError, KeyError, subprocess.TimeoutExpired, EffectRuntimeRemoteError) as exc:
                        row["error"] = str(exc)[:180] if isinstance(exc, (ValueError, EffectRuntimeRemoteError)) else type(exc).__name__
                        self._fenced_write(path, row)
                        if row["status"] == "prepared":
                            self._observe(path, row, "rejected")
            except DelegationStopRequested as stop:
                # SIGTERM, a checkpoint or a fenced write acknowledges intent.
                # Each Host supervisor may still be cleaning its own group;
                # only subsequent resource readback may settle or release.
                self._acknowledge_stop(path, row, binding, source=stop.source)

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
        continuation: list[str] = []
        path = self.path(operation_id)
        if path.is_file():
            confirmed = _read(path)["identity"].get("confirmed_operation_id")
            if confirmed is not None:
                continuation = ["--codex-confirmed-operation-id", require_operation_id(confirmed)]
        return ["--execution-mode", "isolated-headless", "--project", binding["workspace"],
                     "--scan-root", binding["workspace"], "--no-global-sync",
                     "--timeout-seconds", str(binding["timeout_seconds"]),
                     "--validation-command-json", json.dumps(validator),
                     "--validation-failure-kind", "repair_required", *native_tools,
                     *binding["host_args"], *continuation]

    def _record_turn_result(
        self, path: Path, row: dict, result: dict, *, publish: bool = True,
        already_locked: bool = False,
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
            self._observe(path, row, "turn_returned", already_locked=already_locked)
        elif already_locked:
            self._fenced_write_locked(path, row)
        else:
            self._fenced_write(path, row)

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
            if row.get("task_lease", {}).get("required") is True:
                raise ValueError("delegation canonical authority disappeared; reconcile the original execution")
            row["task_lease"] = {"required": False, "handoff_mode": "legacy"}
            self._fenced_write(path, row)
            return row["task_lease"]
        handoff_mode = show_goal_handoff_mode(
            registry_path=self.registry,
            runtime_root_arg=str(self.root),
            goal_id=self.goal_id,
        )["handoff_mode"]
        if handoff_mode != "hard_lease":
            if row.get("task_lease", {}).get("required") is True:
                raise ValueError("delegation authority mode changed; reconcile the original execution")
            row["task_lease"] = {
                "required": False,
                "handoff_mode": handoff_mode,
            }
            self._fenced_write(path, row)
            return row["task_lease"]
        lease_key = self._turn_instance_id(row)
        claim = self._delegation_claim_arguments(row, binding)
        result = self._cli(binding, *claim)
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
            "lease": lease,
        }
        self._fenced_write(path, row)
        return row["task_lease"]

    def _delegation_claim_arguments(self, row: dict, binding: dict) -> list[str]:
        return [
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
            self._turn_instance_id(row),
        ]

    def _delegated_lease_context(self, row: dict, binding: dict) -> dict | None:
        """Private commands carry the original claim intent, never a model grant."""
        lease = row.get("task_lease", {})
        if lease.get("required") is not True:
            return None
        prefix = [*_python_module_command("loopx.cli"), "--registry", str(self.registry),
                  "--runtime-root", str(self.root), "--format", "json"]
        selected = ["--goal-id", self.goal_id, "--todo-id", binding["todo_id"]]
        return {
            "lease": lease["lease"],
            "ttl_seconds": lease["lease"].get("acquire_ttl_seconds"),
            "renew_argv": [*prefix, "task-lease", "renew", *selected, "--owner", binding["agent_id"],
                           "--idempotency-key", lease["lease"]["idempotency_key"]],
            "read_argv": [*prefix, *self._delegation_claim_arguments(row, binding)],
        }

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
            # Atomic claim replay verifies current eligibility and the original
            # key/epoch; it cannot reacquire an expired execution. Renewal has
            # changed its version, so the historical acquisition is not CAS.
            if "completion_lease_version" not in row:
                # The Host supervisor has stopped. Renew the original execution
                # before validation captures its provider revision; renewing
                # during validation would invalidate that source witness. The
                # canonical TS lease owner decides admission and replay. This
                # adapter journals one intent, not a new lease or a longer TTL.
                if "completion_lease_renewal_version" not in row:
                    proof = self._cli(binding, *self._delegation_claim_arguments(row, binding))
                    if proof.get("ok") is not True:
                        raise ValueError("delegation current execution proof lost before completion")
                    row["completion_lease_renewal_version"] = proof["lease"]["version"]
                    _write(self.path(row["identity"]["operation_id"]), row)
                renewed = self._cli(
                    binding, "task-lease", "renew", "--goal-id", self.goal_id,
                    "--todo-id", binding["todo_id"], "--owner", binding["agent_id"],
                    "--idempotency-key", lease["lease"]["idempotency_key"],
                    "--expected-version", str(row["completion_lease_renewal_version"]),
                    "--ttl-seconds", str(lease["lease"]["acquire_ttl_seconds"]),
                )
                if renewed.get("ok") is not True:
                    raise ValueError("delegation original lease renewal rejected before completion")
                # A renewal receipt can be historical after a lost reply. Read
                # current authority before freezing the terminal intent below.
                proof = self._cli(binding, *self._delegation_claim_arguments(row, binding))
                current = proof.get("lease", {})
                if (proof.get("ok") is not True or current.get("owner") != binding["agent_id"]
                        or current.get("idempotency_key") != lease["lease"]["idempotency_key"]
                        or current.get("lease_epoch") != lease["lease"].get("lease_epoch")):
                    raise ValueError("delegation current execution proof lost before completion")
                # Persist the exact terminal intent before crossing the effect
                # boundary. A lost completion reply must replay its receipt,
                # even after that legitimate completion released the lease.
                row["completion_lease_version"] = current["version"]
                _write(self.path(row["identity"]["operation_id"]), row)
            arguments += [
                "--task-lease-idempotency-key",
                str(lease["lease"]["idempotency_key"]),
                "--task-lease-expected-version",
                str(row["completion_lease_version"]),
            ]
        # A bounded member task returns to its requester; it is not terminal
        # Goal intent. Ordinary completion may precede its original Turn's
        # accounting (controller validation requires that order). Do not add
        # no-follow-up, which correctly requires already-settled receipts.
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
            self._raise_if_stop_requested(path)
            if row["status"] == "prepared":
                acceptance = delegation_validation.capture(self, binding)
                if acceptance["plan"]["state"] != "ready" or not acceptance["files_current"]:
                    raise ValueError("delegation task acceptance rejected before host launch")
                row["turn_instance_id"] = self._turn_instance_id(row)
                self._write_delegation_bootstrap(row, binding)
                self._acquire_delegation_lease(path, row, binding)
                self._observe(path, row, "running")
            if row.get("task_lease", {}).get("required") is True and "lease" not in row["task_lease"]:
                # Upgrade a still-current operation record by replaying its
                # original claim, not by inventing a replacement execution.
                self._acquire_delegation_lease(path, row, binding)
            if row["status"] == "running":
                self._raise_if_stop_requested(path)
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
                                   "--execute", timeout=binding["timeout_seconds"] + 60,
                                   delegated_lease=self._delegated_lease_context(row, binding),
                                   host_record=self._host_process_record(path))
                self._record_turn_result(path, row, result)
        finally:
            # The compatibility bootstrap is private host input.  Keeping it
            # after the host returns (including an exception or timeout) makes
            # an otherwise clean Git worktree fail canonical validation.
            self._clear_delegation_bootstrap(row, binding)
        try:
            result = row["turn_result"]
            needs_settlement = result.get("status") != "committed" or result.get("result_kind") != "validated_progress"
            if needs_settlement:
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
            self._bound(row, require_active=True)  # revocation or rebinding while the model ran
            delegation_results.require_dependencies(self, binding, delegation_results.operation_brief(self, row))
            if needs_settlement and not isinstance(row.get("task_lease"), dict):
                self._acquire_delegation_lease(path, row, binding)
            # Both effects commit inside the dispatch lock that a stop also
            # takes, so the two sides linearize: either a stop is written first
            # and neither effect runs, or both effects commit first and the stop
            # that follows reports a record that already reached its terminal
            # observation. Committing them outside the lock let a stop settle
            # for a member whose Todo and reply had already landed. Recovery of
            # a validated journal uses this same completion entry, retaining the
            # fence through settlement, result publication and acceptance.
            with exclusive_file_lock(self._dispatch_lock(path)):
                self._raise_if_stop_requested(path)
                self._complete_delegated_todo(row, binding)
                if needs_settlement:
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
                        host_record=self._host_process_record(path),
                    )
                    self._record_turn_result(path, row, result, publish=False, already_locked=True)
                    if result.get("status") != "committed" or result.get("result_kind") != "validated_progress":
                        raise ValueError(str(result.get("error") or result.get("reason")
                                             or "validated delegation settlement remains incomplete"))
                    if not self._receiver_adopted(row, binding):
                        row["error"] = "delegation receiver did not adopt the request"
                        self._observe(path, row, "rejected", already_locked=True)
                        return
                    self._bound(row, require_active=True)
                    delegation_results.require_dependencies(
                        self, binding, delegation_results.operation_brief(self, row)
                    )
                row["artifacts"] = self._accepted(binding)["artifacts"]
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
                self._observe(path, row, "accepted", already_locked=True,
                              canonical_done=True, acceptance_ready=True, artifacts_current=True,
                              requester=self._wake_requester(row))
        except (ValueError, KeyError, subprocess.TimeoutExpired, EffectRuntimeRemoteError) as exc:
            # Retain uncertain execution for explicit same-operation recovery.
            # No fresh Turn is ever created because its client timed out.
            row["error"] = str(exc)[:180] if isinstance(exc, (ValueError, EffectRuntimeRemoteError)) else type(exc).__name__
            self._fenced_write(path, row)


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

    @server.tool()
    async def stop_delegation(operation_id: str) -> dict:
        """Stop one original operation and return what was proven, not what was hoped.

        settled: the worker acknowledged, released the operation and let go of its
        Turn lane, and the native host and its process group exited.
        acknowledged/requested: still winding down; call again. unknown:
        the named worker vanished before acknowledging; inspect its Turn and task
        lease before reusing the task. noop: already accepted/rejected/stopped.
        Stopped work is not resumed; a new scope needs a new operation id. Elapsed
        time is never a receipt.
        """
        return await asyncio.to_thread(delegations.stop, operation_id, execute=True)


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
            service._stop_signal = install_worker_stop_signal(
                service._stop_path(service.path(args.operation_id)))
            try:
                service.execute(args.operation_id)
            except LockAcquireTimeoutError:
                pass  # Another worker still owns the operation after the bounded wait.
            except DelegationStopRequested:
                pass  # Stopped before owning the operation; the holder acknowledges.
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
