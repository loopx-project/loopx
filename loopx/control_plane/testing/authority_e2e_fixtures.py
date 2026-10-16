"""Workspace and process fixtures for the shared-goal-authority E2E ladder.

Every helper drives the product through ``python -m loopx.cli`` in a child
process, or reads candidate bytes back through the TypeScript
``FileAuthorityStore`` probe. Nothing here imports a LoopX writer, so the
ladder can never become a second authority over the local goal state.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..coordination.authority_core import HandoffMode
from ..coordination.coordination_state_contract_generated import (
    LOCAL_AUTHORITY_SHADOW_CONFIG_SCHEMA,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
TS_READBACK_PROBE = Path("tests") / "control_plane_ts" / "authority_store_readback_probe.ts"
DEFAULT_REGISTERED_AGENTS: tuple[str, ...] = ("agent-a", "agent-b")
RUNTIME_ROOT_BINDINGS: tuple[str, ...] = ("registry", "cli_override", "cli_override_divergent")
HANDOFF_MODES: tuple[str, ...] = tuple(mode.value for mode in HandoffMode)
LOCAL_AUTHORITY_SHADOW_CONFIG = {
    "schema_version": LOCAL_AUTHORITY_SHADOW_CONFIG_SCHEMA,
    "mode": "file_one_way",
}

JsonObject = dict[str, Any]


class CliOutputError(AssertionError):
    """The CLI did not print exactly one JSON object."""


class CliCommandError(AssertionError):
    """The CLI exited non-zero while the row expected a committed response."""

    def __init__(
        self,
        *,
        command: Sequence[str],
        returncode: int,
        payload: Mapping[str, Any],
    ) -> None:
        verb = " ".join(command[:2])
        super().__init__(
            f"loopx {verb} exited {returncode}: "
            f"error_code={payload.get('error_code')!r} error={payload.get('error')!r}"
        )
        self.command = tuple(command)
        self.returncode = returncode
        self.payload = dict(payload)


class ProbeError(AssertionError):
    """The TypeScript read-back probe did not complete."""


class CliWorkspace(Protocol):
    """Anything the CLI runners can address: a home plus global CLI arguments."""

    @property
    def home(self) -> Path: ...

    def cli_prefix(self) -> list[str]: ...


@dataclass(frozen=True)
class GoalWorkspace:
    """One registered goal with its own registry, repo, runtime root, and home."""

    goal_id: str
    root: Path
    repo: Path
    registry_path: Path
    state_path: Path
    runtime_root: Path
    home: Path
    runtime_root_binding: str
    registry_runtime_root: Path

    @property
    def shadow_directory(self) -> Path:
        return self.runtime_root / "authority-shadow" / "file" / self.goal_id

    def cli_prefix(self) -> list[str]:
        prefix = ["--registry", str(self.registry_path)]
        if self.runtime_root_binding in ("cli_override", "cli_override_divergent"):
            prefix.extend(["--runtime-root", str(self.runtime_root)])
        prefix.extend(["--format", "json"])
        return prefix


@dataclass(frozen=True)
class LegacyMigrationSource:
    """A legacy registry/runtime pair whose old shadow lineage must never migrate."""

    old_goal_id: str
    new_goal_id: str
    old_store_identity: str
    legacy_revision: str
    private_marker: str
    legacy_registry: Path
    target_registry: Path
    legacy_runtime: Path
    target_runtime: Path
    source_repo: Path
    target_repo: Path
    home: Path

    @property
    def target_shadow_directory(self) -> Path:
        return self.target_runtime / "authority-shadow" / "file" / self.new_goal_id

    def cli_prefix(self) -> list[str]:
        return ["--registry", str(self.target_registry), "--format", "json"]


@dataclass(frozen=True)
class TapSummary:
    """The ``# pass`` / ``# fail`` / ``# skipped`` trailer of a node TAP run."""

    returncode: int
    tests: int | None
    passed: int | None
    failed: int | None
    skipped: int | None


def unique_goal_id(prefix: str) -> str:
    """Return a single-segment goal id that is unique across xdist workers."""

    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def parse_json_object(text: str) -> JsonObject:
    """Decode one JSON object from CLI stdout; anything else is a contract break."""

    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CliOutputError(f"CLI output is not JSON: {exc.msg}") from None
    if not isinstance(decoded, dict):
        raise CliOutputError("CLI output is not a JSON object")
    return {str(key): value for key, value in decoded.items()}


def _write_active_state(state_path: Path, *, goal_id: str, handoff_mode: str) -> None:
    state_path.write_text(
        "---\n"
        f"goal_id: {goal_id}\n"
        f"handoff_mode: {handoff_mode}\n"
        "updated_at: 2026-09-02T00:00:00+00:00\n"
        "---\n\n"
        "## Agent Todo\n\n",
        encoding="utf-8",
    )


def build_goal_workspace(
    root: Path,
    *,
    goal_id: str,
    handoff_mode: str = "legacy",
    shadow_enabled: bool = False,
    runtime_root_binding: str = "registry",
    registered_agents: Sequence[str] = DEFAULT_REGISTERED_AGENTS,
) -> GoalWorkspace:
    """Materialize one goal exactly as the local-shadow CLI E2E fixture does.

    ``runtime_root_binding`` selects how the CLI learns the runtime root:
    ``registry`` relies on ``common_runtime_root`` alone, ``cli_override`` also
    passes the same directory as ``--runtime-root``, and
    ``cli_override_divergent`` registers a different ``common_runtime_root``
    than the ``--runtime-root`` override so a row can prove that every writer
    hook of one CLI call shares the override root.
    """

    if handoff_mode not in HANDOFF_MODES:
        raise ValueError(f"unsupported handoff_mode {handoff_mode!r}")
    if runtime_root_binding not in RUNTIME_ROOT_BINDINGS:
        raise ValueError(f"unsupported runtime_root_binding {runtime_root_binding!r}")
    repo = root / goal_id
    repo.mkdir()
    state_path = repo / "ACTIVE_GOAL_STATE.md"
    _write_active_state(state_path, goal_id=goal_id, handoff_mode=handoff_mode)
    runtime_root = root / f"{goal_id}-runtime"
    registry_runtime_root = (
        root / f"{goal_id}-registry-runtime"
        if runtime_root_binding == "cli_override_divergent"
        else runtime_root
    )
    home = root / f"{goal_id}-home"
    home.mkdir()
    coordination: JsonObject = {
        "agent_model": "peer_v1",
        "registered_agents": list(registered_agents),
    }
    if shadow_enabled:
        coordination["authority_shadow"] = dict(LOCAL_AUTHORITY_SHADOW_CONFIG)
    registry_path = root / f"{goal_id}-registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(registry_runtime_root),
                "goals": [
                    {
                        "id": goal_id,
                        "status": "active",
                        "repo": str(repo),
                        "state_file": state_path.name,
                        "coordination": coordination,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return GoalWorkspace(
        goal_id=goal_id,
        root=root,
        repo=repo,
        registry_path=registry_path,
        state_path=state_path,
        runtime_root=runtime_root,
        home=home,
        runtime_root_binding=runtime_root_binding,
        registry_runtime_root=registry_runtime_root,
    )


def build_canonical_file_workspace(root: Path, *, goal_id: str) -> GoalWorkspace:
    """Create a new Goal through the public CLI with File as its authority."""

    repo = root / goal_id
    repo.mkdir()
    runtime_root = root / f"{goal_id}-runtime"
    home = root / f"{goal_id}-home"
    home.mkdir()
    registry_path = root / f"{goal_id}-registry.json"
    provisional = GoalWorkspace(
        goal_id=goal_id,
        root=root,
        repo=repo,
        registry_path=registry_path,
        state_path=repo / "ACTIVE_GOAL_STATE.md",
        runtime_root=runtime_root,
        home=home,
        runtime_root_binding="cli_override",
        registry_runtime_root=runtime_root,
    )
    defaults_path = root / f"{goal_id}-storage-defaults.json"
    defaults_path.write_text(
        json.dumps(
            {
                "schema_version": "loopx_goal_storage_defaults_v1",
                "new_goal_provider": "file",
                "canonical_creation": True,
                "new_goal_handoff_mode": "hard_lease",
            }
        ),
        encoding="utf-8",
    )
    preview = run_cli(
        provisional,
        "machine-config",
        "preview",
        "--namespace",
        "goal_storage",
        "--config-json",
        str(defaults_path),
    )
    plan_revision = preview.get("plan_revision")
    if not isinstance(plan_revision, str) or not plan_revision:
        raise AssertionError("goal storage preview omitted its plan revision")
    run_cli(
        provisional,
        "machine-config",
        "apply",
        "--namespace",
        "goal_storage",
        "--config-json",
        str(defaults_path),
        "--expected-plan-revision",
        plan_revision,
        "--execute",
    )
    created = run_cli(
        provisional,
        "bootstrap",
        "--project",
        str(repo),
        "--goal-id",
        goal_id,
        "--objective",
        "Qualify canonical File authority through the public CLI.",
        "--no-global-sync",
    )
    selection = created.get("storage_selection")
    if not isinstance(selection, dict) or (
        selection.get("provider") != "file"
        or selection.get("authority_initialized") is not True
        or selection.get("legacy_fallback_used") is not False
    ):
        raise AssertionError("bootstrap did not initialize canonical File authority")
    state_value = created.get("state_file")
    if not isinstance(state_value, str) or not state_value:
        raise AssertionError("bootstrap omitted the canonical projection path")
    workspace = GoalWorkspace(
        goal_id=goal_id,
        root=root,
        repo=repo,
        registry_path=registry_path,
        state_path=Path(state_value),
        runtime_root=runtime_root,
        home=home,
        runtime_root_binding="cli_override",
        registry_runtime_root=runtime_root,
    )
    run_cli(
        workspace,
        "configure-goal",
        "--goal-id",
        goal_id,
        "--registered-agent",
        "agent-a",
        "--registered-agent",
        "agent-b",
        "--execute",
    )
    return workspace


def build_legacy_migration_source(
    root: Path,
    *,
    old_goal_id: str,
    new_goal_id: str,
    old_store_identity: str = "file:11111111111111111111111111111111",
    registered_agents: Sequence[str] = DEFAULT_REGISTERED_AGENTS,
) -> LegacyMigrationSource:
    """Lift the state-migration shadow fixture: a legacy goal with a stale lineage."""

    legacy_runtime = root / "legacy-runtime"
    target_runtime = root / "target-runtime"
    source_repo = root / "legacy-repo"
    target_repo = root / "target-repo"
    home = root / "migration-home"
    for directory in (source_repo, target_repo, home):
        directory.mkdir()
    source_state = source_repo / "ACTIVE_GOAL_STATE.md"
    source_state.write_text(
        "---\n"
        f"goal_id: {old_goal_id}\n"
        "handoff_mode: soft_claim\n"
        "updated_at: 2026-09-02T00:00:00+10:00\n"
        "---\n\n"
        "## Agent Todo\n\n"
        "- [ ] Preserve the new local authority only.\n",
        encoding="utf-8",
    )
    legacy_registry = root / "legacy-registry.json"
    legacy_registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "common_runtime_root": str(legacy_runtime),
                "goals": [
                    {
                        "id": old_goal_id,
                        "status": "active",
                        "repo": str(source_repo),
                        "state_file": source_state.name,
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": list(registered_agents),
                            "authority_shadow": dict(LOCAL_AUTHORITY_SHADOW_CONFIG),
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    lease_dir = legacy_runtime / "goals" / old_goal_id / "task-leases"
    lease_dir.mkdir(parents=True)
    (lease_dir / "safe-local.json").write_text(
        json.dumps(
            {
                "goal_id": old_goal_id,
                "todo_id": "safe-local",
                "owner": registered_agents[0],
                "version": 1,
                "lease_epoch": 1,
                "status": "released",
            }
        ),
        encoding="utf-8",
    )
    legacy_revision = "file:99:legacy-lineage"
    private_marker = "must-never-migrate"
    source_shadow = legacy_runtime / "authority-shadow" / "file" / old_goal_id
    source_shadow.mkdir(parents=True)
    (source_shadow / "store-identity").write_text(old_store_identity, encoding="utf-8")
    (source_shadow / "authority-store-legacy.json").write_text(
        json.dumps(
            {
                "goal_id": old_goal_id,
                "store_identity": old_store_identity,
                "provider_revision": legacy_revision,
                "cursor": "99",
                "private_provider_byte": private_marker,
                "source_path": str(source_repo),
            }
        ),
        encoding="utf-8",
    )
    return LegacyMigrationSource(
        old_goal_id=old_goal_id,
        new_goal_id=new_goal_id,
        old_store_identity=old_store_identity,
        legacy_revision=legacy_revision,
        private_marker=private_marker,
        legacy_registry=legacy_registry,
        target_registry=root / "target-registry.json",
        legacy_runtime=legacy_runtime,
        target_runtime=target_runtime,
        source_repo=source_repo,
        target_repo=target_repo,
        home=home,
    )


def cli_env(workspace: CliWorkspace) -> dict[str, str]:
    """Child environment: this checkout on ``PYTHONPATH`` and an isolated home."""

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["HOME"] = str(workspace.home)
    if os.name == "nt":
        env["USERPROFILE"] = str(workspace.home)
    return env


def cli_command(workspace: CliWorkspace, *args: str) -> list[str]:
    return [sys.executable, "-m", "loopx.cli", *workspace.cli_prefix(), *args]


def run_cli(
    workspace: CliWorkspace,
    *args: str,
    timeout: float = 60,
    check: bool = True,
) -> JsonObject:
    """Run one product CLI command and return its JSON object response."""

    command = cli_command(workspace, *args)
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=cli_env(workspace),
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
        check=False,
    )
    payload = parse_json_object(completed.stdout)
    if check and completed.returncode != 0:
        raise CliCommandError(
            command=args,
            returncode=completed.returncode,
            payload=payload,
        )
    return payload


def spawn_cli(workspace: CliWorkspace, *args: str) -> subprocess.Popen[str]:
    """Start one product CLI command without waiting for it."""

    return subprocess.Popen(
        cli_command(workspace, *args),
        cwd=REPO_ROOT,
        env=cli_env(workspace),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )


def wait_until(
    predicate: Callable[[], bool],
    timeout: float,
    *,
    interval: float = 0.01,
) -> bool:
    """Poll ``predicate`` until it holds or ``timeout`` seconds elapse."""

    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def kill_now(process: subprocess.Popen[str]) -> None:
    """SIGKILL (or TerminateProcess) the child and reap it."""

    process.kill()
    process.communicate(timeout=5)


def node_executable() -> str | None:
    return shutil.which("node")


def ts_readback(
    workspace: GoalWorkspace,
    *,
    receipt: str | None = None,
    page_size: int = 2,
    directory: Path | None = None,
) -> JsonObject | None:
    """Read the candidate back through ``FileAuthorityStore``; ``None`` without node."""

    node = node_executable()
    if node is None:
        return None
    command = [
        node,
        "--no-warnings",
        "--experimental-strip-types",
        str(REPO_ROOT / TS_READBACK_PROBE),
        "--directory",
        str(directory or workspace.shadow_directory),
        "--goal-id",
        workspace.goal_id,
        "--page-size",
        str(page_size),
    ]
    if receipt is not None:
        command.extend(["--receipt", receipt])
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise ProbeError(f"read-back probe exited {completed.returncode}")
    return parse_json_object(completed.stdout)


def _tap_counter(line: str, label: str) -> int | None:
    prefix = f"# {label} "
    if not line.startswith(prefix):
        return None
    try:
        return int(line[len(prefix):].strip())
    except ValueError:
        return None


def parse_tap_summary(output: str, *, returncode: int) -> TapSummary:
    """Extract the node TAP reporter trailer counters."""

    counters: dict[str, int | None] = {
        "tests": None,
        "pass": None,
        "fail": None,
        "skipped": None,
    }
    aliases = {"tests": ("tests",), "pass": ("pass",), "fail": ("fail",), "skipped": ("skipped", "skip")}
    for raw in output.splitlines():
        line = raw.strip()
        for key, labels in aliases.items():
            for label in labels:
                value = _tap_counter(line, label)
                if value is not None:
                    counters[key] = value
    return TapSummary(
        returncode=returncode,
        tests=counters["tests"],
        passed=counters["pass"],
        failed=counters["fail"],
        skipped=counters["skipped"],
    )


def tap_summary(
    argv: Sequence[str],
    *,
    cwd: Path = REPO_ROOT,
    env: Mapping[str, str] | None = None,
    timeout: float = 600,
) -> TapSummary:
    """Run a node TAP command and summarize its trailer."""

    completed = subprocess.run(
        list(argv),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
        check=False,
    )
    return parse_tap_summary(completed.stdout, returncode=completed.returncode)


__all__ = [
    "CliCommandError",
    "CliOutputError",
    "CliWorkspace",
    "DEFAULT_REGISTERED_AGENTS",
    "GoalWorkspace",
    "HANDOFF_MODES",
    "JsonObject",
    "LOCAL_AUTHORITY_SHADOW_CONFIG",
    "LegacyMigrationSource",
    "ProbeError",
    "REPO_ROOT",
    "RUNTIME_ROOT_BINDINGS",
    "TS_READBACK_PROBE",
    "TapSummary",
    "build_canonical_file_workspace",
    "build_goal_workspace",
    "build_legacy_migration_source",
    "cli_command",
    "cli_env",
    "kill_now",
    "node_executable",
    "parse_json_object",
    "parse_tap_summary",
    "run_cli",
    "spawn_cli",
    "tap_summary",
    "ts_readback",
    "unique_goal_id",
    "wait_until",
]
