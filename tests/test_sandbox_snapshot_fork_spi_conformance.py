"""M0 contract conformance tests for the Provider-Neutral Sandbox Snapshot & Fork SPI (v0).

Bridges a reference E2B/microVM-compatible sandbox SPI (`pause`, `resume`,
`snapshot`, `fork`, `destroy`) onto LoopX's existing Explore replay and
counterfactual suffix owners without expanding minimal-kernel runtime state.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import threading
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Protocol, cast, runtime_checkable

import pytest

from loopx.capabilities.explore.counterfactual_runtime import (
    CounterfactualCandidate,
    CounterfactualChildCandidate,
    CounterfactualDecision,
    CounterfactualHypothesis,
    CounterfactualReplayRequest,
    ValidationTarget,
    run_counterfactual_child_group,
    run_counterfactual_suffix_replay,
)
from loopx.capabilities.explore.replay_runtime import (
    AdapterStateLease,
    CaptureReplayStateRequest,
    EquivalenceCheck,
    EquivalenceReport,
    ForkReplayChildRequest,
    ReplayChildConcurrency,
    ReplayChildLease,
    ReplayFidelity,
    ReplayPointRegistry,
    ReplayRiskAssessment,
    ReplayRiskDisposition,
    RestoreReceipt,
    RestoreReplayStateRequest,
)
from loopx.capabilities.explore.trace_runtime import (
    AgentCursor,
    TraceEvent,
    TraceEventKind,
    TraceLog,
)


class SandboxSpiError(RuntimeError):
    """Typed failure raised when a sandbox lifecycle invariant is violated."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass
class SandboxInstanceState:
    sandbox_id: str
    provider_id: str
    status: str  # creating | running | paused | destroyed
    rootfs: dict[str, str]
    symlinks: dict[str, str]
    env: dict[str, str]
    processes: dict[int, str]
    memory_pages: dict[str, str]
    daemon_health: dict[str, bool] = field(default_factory=dict)
    egress_policy_id: str = "isolated-offline"
    snapshot_holder_id: str | None = None


@dataclass(frozen=True)
class SandboxPauseReceipt:
    sandbox_id: str
    quiesced_fs: bool
    dirty_page_flush_ok: bool
    active_pid_digest: str


def _freeze_snapshot_fields(instance: object) -> None:
    for attr in ("rootfs", "symlinks", "env", "processes", "daemon_health", "memory_pages"):
        object.__setattr__(
            instance,
            attr,
            MappingProxyType(dict(getattr(instance, attr))),
        )
    for tuple_attr in ("held_refs", "active_holder_ids"):
        if hasattr(instance, tuple_attr):
            object.__setattr__(
                instance,
                tuple_attr,
                tuple(getattr(instance, tuple_attr)),
            )


@dataclass(frozen=True)
class _SealedSnapshot:
    """Internal immutable sealed snapshot kept separate from caller descriptors."""

    snapshot_id: str
    parent_sandbox_id: str
    fidelity_tier: str
    rootfs: Mapping[str, str]
    symlinks: Mapping[str, str]
    env: Mapping[str, str]
    processes: Mapping[int, str]
    memory_pages: Mapping[str, str]
    scrubbed_keys: tuple[str, ...]
    rootfs_digest: str
    env_manifest_digest: str
    memory_digest: str | None
    digest: str
    refcount: int = 1
    daemon_health: Mapping[str, bool] = field(default_factory=dict)
    daemon_health_digest: str | None = None
    owner_holder_id: str = ""
    held_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _freeze_snapshot_fields(self)


@dataclass(frozen=True)
class SandboxSnapshotDescriptor:
    snapshot_id: str
    parent_sandbox_id: str
    fidelity_tier: str
    rootfs: Mapping[str, str]
    symlinks: Mapping[str, str]
    env: Mapping[str, str]
    processes: Mapping[int, str]
    memory_pages: Mapping[str, str]
    scrubbed_keys: tuple[str, ...]
    digest: str
    refcount: int = 1
    rootfs_digest: str = ""
    env_manifest_digest: str = ""
    memory_digest: str | None = None
    daemon_health: Mapping[str, bool] = field(default_factory=dict)
    daemon_health_digest: str | None = None
    owner_holder_id: str = ""
    active_holder_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _freeze_snapshot_fields(self)


@dataclass(frozen=True)
class _SnapshotRef:
    snapshot_id: str
    holder_id: str = ""


@dataclass(frozen=True)
class _BranchRef:
    branch_sandbox_id: str
    source_snapshot_id: str
    branch_isolation_id: str
    target_branch_id: str = ""
    holder_id: str = ""


@runtime_checkable
class SandboxSpiProviderProtocol(Protocol):
    """Minimal RFC Sandbox Snapshot & Fork SPI structural surface."""

    def pause(self, sandbox_id: str) -> SandboxPauseReceipt: ...

    def resume(self, sandbox_id: str) -> SandboxInstanceState: ...

    def snapshot(
        self,
        sandbox_id: str,
        *,
        target_tier: str,
        scrub_keys: tuple[str, ...] = (
            "LOOPX_LEASE_KEY",
            "LOOPX_CLAIM_OP_ID",
            "CLOUD_API_TOKEN",
        ),
    ) -> SandboxSnapshotDescriptor: ...

    def fork(
        self,
        snapshot_id: str,
        *,
        child_id: str,
        egress_policy_id: str = "isolated-offline",
    ) -> tuple[SandboxInstanceState, str]: ...


_TIER_TO_REPLAY_FIDELITY: dict[str, ReplayFidelity] = {
    "filesystem_only": ReplayFidelity.BEST_EFFORT,
    "filesystem_and_env": ReplayFidelity.SEMANTIC_EQUIVALENT,
    "process_tree_quiesced": ReplayFidelity.SEMANTIC_EQUIVALENT,
    "microvm_memory_exact": ReplayFidelity.EXACT,
}


def _json_sha256(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _digest_rootfs(
    rootfs: Mapping[str, str],
    symlinks: Mapping[str, str],
) -> str:
    return _json_sha256(
        {
            "rootfs": dict(sorted(rootfs.items())),
            "symlinks": dict(sorted(symlinks.items())),
        }
    )


def _digest_env(env: Mapping[str, str]) -> str:
    return _json_sha256(dict(sorted(env.items())))


def _digest_memory(
    processes: Mapping[int, str],
    memory_pages: Mapping[str, str],
) -> str:
    return _json_sha256(
        {
            "processes": {str(k): v for k, v in sorted(processes.items())},
            "memory_pages": dict(sorted(memory_pages.items())),
        }
    )


def _quiesced_services_summary(
    processes: Mapping[int, str],
    daemon_health: Mapping[str, bool],
) -> dict[str, object]:
    service_counts: dict[str, int] = {}
    for proc_name in sorted(processes.values()):
        service_counts[proc_name] = service_counts.get(proc_name, 0) + 1
    ignored_keys = {str(pid) for pid in processes} | set(service_counts)
    stable_probes = {
        k: v for k, v in sorted(daemon_health.items()) if k not in ignored_keys
    }
    verified = _daemon_health_verified(processes, daemon_health)
    return {
        "services": service_counts,
        "service_health": {name: True for name in service_counts} if verified else {},
        "stable_probes": stable_probes,
        "verified": verified,
    }


def _digest_daemon_health(
    processes: Mapping[int, str],
    daemon_health: Mapping[str, bool],
) -> str:
    return _json_sha256(_quiesced_services_summary(processes, daemon_health))


def _daemon_health_verified(
    processes: Mapping[int, str],
    daemon_health: Mapping[str, bool],
) -> bool:
    if not processes or not daemon_health or not all(daemon_health.values()):
        return False
    return all(
        daemon_health.get(str(pid)) is True or daemon_health.get(proc_name) is True
        for pid, proc_name in processes.items()
    )


def _digest_state(
    *,
    rootfs: Mapping[str, str],
    symlinks: Mapping[str, str],
    env: Mapping[str, str],
    processes: Mapping[int, str],
    memory_pages: Mapping[str, str],
    daemon_health: Mapping[str, bool] = MappingProxyType({}),
    include_env: bool = True,
    include_daemons: bool = False,
    include_memory: bool,
) -> str:
    payload: dict[str, Any] = {
        "rootfs": dict(sorted(rootfs.items())),
        "symlinks": dict(sorted(symlinks.items())),
    }
    if include_env:
        payload["env"] = dict(sorted(env.items()))
    if include_daemons:
        payload["daemons"] = _quiesced_services_summary(processes, daemon_health)
    if include_memory:
        payload["processes"] = {str(k): v for k, v in sorted(processes.items())}
        payload["memory_pages"] = dict(sorted(memory_pages.items()))
        if daemon_health:
            payload["daemon_health"] = dict(sorted(daemon_health.items()))
    return _json_sha256(payload)


def _observe_sandbox_digest(
    sb: SandboxInstanceState,
    *,
    fidelity_tier: str,
) -> str | None:
    if sb.status == "destroyed":
        return None
    if fidelity_tier == "process_tree_quiesced" and not _daemon_health_verified(
        sb.processes,
        sb.daemon_health,
    ):
        return None
    if fidelity_tier == "microvm_memory_exact" and not sb.memory_pages:
        return None
    include_env = fidelity_tier != "filesystem_only"
    include_daemons = fidelity_tier == "process_tree_quiesced"
    include_mem = fidelity_tier == "microvm_memory_exact"
    return _digest_state(
        rootfs=sb.rootfs,
        symlinks=sb.symlinks,
        env=sb.env if include_env else {},
        processes=sb.processes if (include_daemons or include_mem) else {},
        memory_pages=sb.memory_pages if include_mem else {},
        daemon_health=sb.daemon_health if (include_daemons or include_mem) else {},
        include_env=include_env,
        include_daemons=include_daemons,
        include_memory=include_mem,
    )


def _descriptor_from_sealed(sealed: _SealedSnapshot) -> SandboxSnapshotDescriptor:
    effective_refcount = (
        len(sealed.held_refs) if sealed.held_refs else sealed.refcount
    )
    return SandboxSnapshotDescriptor(
        snapshot_id=sealed.snapshot_id,
        parent_sandbox_id=sealed.parent_sandbox_id,
        fidelity_tier=sealed.fidelity_tier,
        rootfs=sealed.rootfs,
        symlinks=sealed.symlinks,
        env=sealed.env,
        processes=sealed.processes,
        memory_pages=sealed.memory_pages,
        daemon_health=sealed.daemon_health,
        scrubbed_keys=sealed.scrubbed_keys,
        digest=sealed.digest,
        refcount=effective_refcount,
        rootfs_digest=sealed.rootfs_digest,
        env_manifest_digest=sealed.env_manifest_digest,
        daemon_health_digest=sealed.daemon_health_digest,
        memory_digest=sealed.memory_digest,
        owner_holder_id=sealed.owner_holder_id,
        active_holder_ids=sealed.held_refs,
    )


class InMemorySandboxSpiProvider:
    """Reference E2B-compatible SPI provider obeying RFC I1-I6."""

    def __init__(self, *, provider_id: str = "e2b_microvm") -> None:
        self.provider_id = provider_id
        self.sandboxes: dict[str, SandboxInstanceState] = {}
        self._sealed_snapshots: dict[str, _SealedSnapshot] = {}
        self.snapshots: dict[str, SandboxSnapshotDescriptor] = {}
        self._branch_holders: dict[str, str] = {}
        self._branch_source_snapshots: dict[str, str] = {}
        self._pending_destroyed_branch_holders: dict[str, list[str]] = {}
        self._seq = 0

    def branch_holder_id(self, sandbox_id: str) -> str | None:
        return self._branch_holders.get(sandbox_id)

    def sealed_snapshot(self, snapshot_id: str) -> _SealedSnapshot:
        if snapshot_id not in self._sealed_snapshots:
            raise SandboxSpiError("snapshot_missing", f"unknown snapshot {snapshot_id}")
        return self._sealed_snapshots[snapshot_id]

    def get_snapshot(self, snapshot_id: str) -> SandboxSnapshotDescriptor:
        return _descriptor_from_sealed(self.sealed_snapshot(snapshot_id))

    def create_sandbox(
        self,
        *,
        sandbox_id: str,
        rootfs: Mapping[str, str] | None = None,
        symlinks: Mapping[str, str] | None = None,
        env: Mapping[str, str] | None = None,
        processes: Mapping[int, str] | None = None,
        daemon_health: Mapping[str, bool] | None = None,
        memory_pages: Mapping[str, str] | None = None,
    ) -> SandboxInstanceState:
        instance = SandboxInstanceState(
            sandbox_id=sandbox_id,
            provider_id=self.provider_id,
            status="running",
            rootfs=dict(rootfs or {}),
            symlinks=dict(symlinks or {}),
            env=dict(env or {}),
            processes=dict(processes or {}),
            memory_pages=dict(memory_pages or {}),
            daemon_health=dict(daemon_health or {}),
        )
        self.sandboxes[sandbox_id] = instance
        return instance

    def pause(self, sandbox_id: str) -> SandboxPauseReceipt:
        sb = self.sandboxes[sandbox_id]
        if sb.status == "destroyed":
            raise SandboxSpiError("sandbox_destroyed", "cannot pause destroyed sandbox")
        sb.status = "paused"
        pid_digest = hashlib.sha256(
            json.dumps(sorted(sb.processes.items())).encode("utf-8")
        ).hexdigest()
        return SandboxPauseReceipt(
            sandbox_id=sandbox_id,
            quiesced_fs=True,
            dirty_page_flush_ok=True,
            active_pid_digest=pid_digest,
        )

    def resume(self, sandbox_id: str) -> SandboxInstanceState:
        sb = self.sandboxes[sandbox_id]
        if sb.status != "paused":
            raise SandboxSpiError(
                "invalid_state",
                f"resume requires paused state, got {sb.status}",
            )
        sb.status = "running"
        return sb

    def snapshot(
        self,
        sandbox_id: str,
        *,
        target_tier: str,
        scrub_keys: tuple[str, ...] = (
            "LOOPX_LEASE_KEY",
            "LOOPX_CLAIM_OP_ID",
            "CLOUD_API_TOKEN",
        ),
    ) -> SandboxSnapshotDescriptor:
        if target_tier not in _TIER_TO_REPLAY_FIDELITY:
            raise SandboxSpiError("invalid_tier", f"unknown tier {target_tier}")
        sb = self.sandboxes[sandbox_id]
        if sb.status == "destroyed":
            raise SandboxSpiError("sandbox_destroyed", "cannot snapshot destroyed sandbox")
        requires_pause = target_tier in {"process_tree_quiesced", "microvm_memory_exact"}
        if requires_pause and sb.status != "paused":
            raise SandboxSpiError(
                "quiescence_required",
                f"{target_tier} snapshot requires paused sandbox",
            )
        include_env = target_tier != "filesystem_only"
        include_daemons = target_tier == "process_tree_quiesced"
        include_mem = target_tier == "microvm_memory_exact"
        scrubbed_env = (
            {k: v for k, v in sb.env.items() if k not in scrub_keys}
            if include_env
            else {}
        )
        scrubbed_rootfs = {
            k: v
            for k, v in sb.rootfs.items()
            if not k.startswith(".loopx/") and k != "loopx.lease.json"
        }
        symlinks_copy = dict(sb.symlinks)
        processes_copy = (
            dict(sb.processes) if (include_daemons or include_mem) else {}
        )
        daemon_health_copy = (
            dict(sb.daemon_health) if (include_daemons or include_mem) else {}
        )
        if include_daemons and not _daemon_health_verified(
            processes_copy, daemon_health_copy
        ):
            raise SandboxSpiError(
                "daemon_health_required",
                "process_tree_quiesced snapshot requires verified live daemon health",
            )
        memory_copy = dict(sb.memory_pages) if include_mem else {}
        if include_mem and not memory_copy:
            raise SandboxSpiError(
                "memory_capture_required",
                "microvm_memory_exact snapshot requires readable memory_pages capture evidence",
            )
        removed_keys = tuple(sorted(k for k in sb.env if k in scrub_keys))
        rootfs_digest = _digest_rootfs(scrubbed_rootfs, symlinks_copy)
        env_manifest_digest = _digest_env(scrubbed_env)
        daemon_health_digest = (
            _digest_daemon_health(processes_copy, daemon_health_copy)
            if include_daemons
            else None
        )
        memory_digest = (
            _digest_memory(processes_copy, memory_copy)
            if include_mem and memory_copy
            else None
        )
        digest = _digest_state(
            rootfs=scrubbed_rootfs,
            symlinks=symlinks_copy,
            env=scrubbed_env,
            processes=processes_copy,
            memory_pages=memory_copy,
            daemon_health=daemon_health_copy,
            include_env=include_env,
            include_daemons=include_daemons,
            include_memory=include_mem,
        )
        self._seq += 1
        snap_id = f"snap-{self._seq:04d}"
        owner_holder_id = f"holder-{snap_id}-parent"
        sealed = _SealedSnapshot(
            snapshot_id=snap_id,
            parent_sandbox_id=sandbox_id,
            fidelity_tier=target_tier,
            rootfs=scrubbed_rootfs,
            symlinks=symlinks_copy,
            env=scrubbed_env,
            processes=processes_copy,
            memory_pages=memory_copy,
            daemon_health=daemon_health_copy,
            scrubbed_keys=removed_keys,
            rootfs_digest=rootfs_digest,
            env_manifest_digest=env_manifest_digest,
            daemon_health_digest=daemon_health_digest,
            memory_digest=memory_digest,
            digest=digest,
            refcount=1,
            owner_holder_id=owner_holder_id,
            held_refs=(owner_holder_id,),
        )
        self._sealed_snapshots[snap_id] = sealed
        self.snapshots[snap_id] = _descriptor_from_sealed(sealed)
        return _descriptor_from_sealed(sealed)

    def _set_snapshot_holders(
        self, snapshot_id: str, holders: tuple[str, ...]
    ) -> _SealedSnapshot:
        sealed = self.sealed_snapshot(snapshot_id)
        updated = _SealedSnapshot(
            snapshot_id=sealed.snapshot_id,
            parent_sandbox_id=sealed.parent_sandbox_id,
            fidelity_tier=sealed.fidelity_tier,
            rootfs=sealed.rootfs,
            symlinks=sealed.symlinks,
            env=sealed.env,
            processes=sealed.processes,
            memory_pages=sealed.memory_pages,
            daemon_health=sealed.daemon_health,
            scrubbed_keys=sealed.scrubbed_keys,
            rootfs_digest=sealed.rootfs_digest,
            env_manifest_digest=sealed.env_manifest_digest,
            daemon_health_digest=sealed.daemon_health_digest,
            memory_digest=sealed.memory_digest,
            digest=sealed.digest,
            refcount=len(holders),
            owner_holder_id=sealed.owner_holder_id,
            held_refs=holders,
        )
        self._sealed_snapshots[snapshot_id] = updated
        self.snapshots[snapshot_id] = _descriptor_from_sealed(updated)
        return updated

    def fork(
        self,
        snapshot_id: str,
        *,
        child_id: str,
        target_branch_id: str | None = None,
        egress_policy_id: str = "isolated-offline",
    ) -> tuple[SandboxInstanceState, str]:
        sealed = self.sealed_snapshot(snapshot_id)
        self._seq += 1
        branch_tag = target_branch_id or child_id
        fork_id = f"sb-fork-{child_id}-{self._seq:04d}"
        isolation_id = f"iso-{branch_tag}-{self._seq:04d}"
        fork_holder = f"holder-{snapshot_id}-fork-{self._seq:04d}-{child_id}"
        self._set_snapshot_holders(snapshot_id, sealed.held_refs + (fork_holder,))
        self._branch_holders[fork_id] = fork_holder
        self._branch_source_snapshots[fork_id] = snapshot_id
        child = SandboxInstanceState(
            sandbox_id=fork_id,
            provider_id=self.provider_id,
            status="running",
            rootfs=dict(sealed.rootfs),
            symlinks=dict(sealed.symlinks),
            env=dict(sealed.env),
            processes=dict(sealed.processes),
            memory_pages=dict(sealed.memory_pages),
            daemon_health=dict(sealed.daemon_health),
            egress_policy_id=egress_policy_id,
            snapshot_holder_id=fork_holder,
        )
        self.sandboxes[fork_id] = child
        return child, isolation_id

    def destroy_sandbox(self, sandbox_id: str) -> None:
        sb = self.sandboxes.get(sandbox_id)
        if sb is not None:
            sb.status = "destroyed"
        holder = self._branch_holders.get(sandbox_id)
        source_snap = self._branch_source_snapshots.get(sandbox_id)
        if holder is not None and source_snap is not None:
            pending = self._pending_destroyed_branch_holders.setdefault(
                source_snap, []
            )
            if holder not in pending:
                pending.append(holder)

    def release_snapshot(
        self,
        snapshot_id: str,
        *,
        holder_id: str | None = None,
    ) -> None:
        sealed = self._sealed_snapshots.get(snapshot_id)
        if sealed is None:
            return
        if holder_id is not None:
            pending_holders = self._pending_destroyed_branch_holders.get(snapshot_id)
            if pending_holders and holder_id in pending_holders:
                pending_holders.remove(holder_id)
            if holder_id not in sealed.held_refs:
                return
            remaining_holders = tuple(h for h in sealed.held_refs if h != holder_id)
        elif self._pending_destroyed_branch_holders.get(snapshot_id):
            target_holder = self._pending_destroyed_branch_holders[snapshot_id].pop(0)
            remaining_holders = tuple(
                h for h in sealed.held_refs if h != target_holder
            )
        elif sealed.held_refs:
            remaining_holders = sealed.held_refs[1:]
        else:
            remaining_holders = ()
        if not remaining_holders:
            del self._sealed_snapshots[snapshot_id]
            self.snapshots.pop(snapshot_id, None)
        else:
            self._set_snapshot_holders(snapshot_id, remaining_holders)

    def destroy(
        self,
        sandbox_or_snapshot_id: str,
        *,
        holder_id: str | None = None,
    ) -> None:
        if sandbox_or_snapshot_id in self._sealed_snapshots:
            self.release_snapshot(sandbox_or_snapshot_id, holder_id=holder_id)
            return
        self.destroy_sandbox(sandbox_or_snapshot_id)


class SpiAgentStateStore:
    """Reference IsolatedAgentStateStore keeping agent state external to VM."""

    def __init__(self) -> None:
        self.states: dict[object, str] = {}

    def capture_agent_state(self, cursor: object) -> object:
        key = object()
        self.states[key] = f"cursor-{getattr(cursor, 'next_sequence', 0)}"
        return key

    def restore_agent_state(self, state_key: object) -> None:
        assert state_key in self.states

    def release_agent_state(self, state_key: object) -> None:
        self.states.pop(state_key, None)

    def fork_agent_state(self, state_key: object, child_id: str) -> object:
        assert state_key in self.states
        child_key = object()
        self.states[child_key] = f"{self.states[state_key]}:{child_id}"
        return child_key


def _effective_replay_fidelity(
    *,
    fidelity_tier: str,
    memory_pages: Mapping[str, str],
) -> ReplayFidelity:
    fidelity = _TIER_TO_REPLAY_FIDELITY[fidelity_tier]
    if fidelity_tier == "microvm_memory_exact" and not memory_pages:
        return ReplayFidelity.BEST_EFFORT
    return fidelity


@dataclass
class SpiReplayAdapter:
    """Adapter bridging a Sandbox SPI provider onto RiskAwareReplayChildAdapter."""

    provider: SandboxSpiProviderProtocol
    source_sandbox_id: str
    target_tier: str = "microvm_memory_exact"
    prefix_reused: bool = True
    released_children: list[str] = field(default_factory=list)
    released_snapshots: list[str] = field(default_factory=list)
    last_target_sandboxes: list[_BranchRef] = field(default_factory=list)
    _captured_descriptors: dict[str, SandboxSnapshotDescriptor] = field(
        default_factory=dict, repr=False
    )
    _branch_instances: dict[str, SandboxInstanceState] = field(
        default_factory=dict, repr=False
    )
    _active_replay_targets: dict[str, _BranchRef] = field(
        default_factory=dict, repr=False
    )
    _finalized_replay_targets: dict[str, _BranchRef] = field(
        default_factory=dict, repr=False
    )
    _target_bindings: dict[tuple[str, str], _BranchRef] = field(
        default_factory=dict, repr=False
    )
    _committed_release_holders: set[str] = field(default_factory=set, repr=False)
    _committed_released_children: set[str] = field(default_factory=set, repr=False)
    _destroyed_child_sandboxes: set[str] = field(default_factory=set, repr=False)

    def _try_get_snapshot_live(
        self, snapshot_id: str
    ) -> tuple[bool, _SealedSnapshot | SandboxSnapshotDescriptor | None]:
        sealed_fn = getattr(self.provider, "sealed_snapshot", None)
        if callable(sealed_fn):
            try:
                return True, cast(_SealedSnapshot, sealed_fn(snapshot_id))
            except SandboxSpiError as error:
                if error.code == "snapshot_missing":
                    return True, None
                raise
        get_fn = getattr(self.provider, "get_snapshot", None)
        if callable(get_fn):
            try:
                return True, cast(SandboxSnapshotDescriptor, get_fn(snapshot_id))
            except SandboxSpiError as error:
                if error.code == "snapshot_missing":
                    return True, None
                raise
        return False, None

    def _resolve_snapshot(
        self, snapshot_id: str
    ) -> _SealedSnapshot | SandboxSnapshotDescriptor:
        has_lookup, live = self._try_get_snapshot_live(snapshot_id)
        if has_lookup:
            if live is None:
                raise SandboxSpiError("snapshot_missing", f"unknown snapshot {snapshot_id}")
            return live
        if snapshot_id in self._captured_descriptors:
            return self._captured_descriptors[snapshot_id]
        raise SandboxSpiError("snapshot_missing", f"unknown snapshot {snapshot_id}")

    def _resolve_sandbox(self, sandbox_id: str) -> SandboxInstanceState:
        sandboxes = getattr(self.provider, "sandboxes", None)
        if isinstance(sandboxes, Mapping) and sandbox_id in sandboxes:
            return cast(SandboxInstanceState, sandboxes[sandbox_id])
        if sandbox_id in self._branch_instances:
            return self._branch_instances[sandbox_id]
        raise SandboxSpiError("sandbox_missing", f"unknown sandbox {sandbox_id}")

    def target_sandbox_id_for(
        self, replay_point_id: str, target_branch_id: str
    ) -> str:
        branch_ref = self._target_bindings.get((replay_point_id, target_branch_id))
        if branch_ref is None:
            raise SandboxSpiError(
                "sandbox_missing",
                f"no active isolated target for {(replay_point_id, target_branch_id)}",
            )
        return branch_ref.branch_sandbox_id

    def target_sandbox_for(
        self, replay_point_id: str, target_branch_id: str
    ) -> SandboxInstanceState:
        return self._resolve_sandbox(
            self.target_sandbox_id_for(replay_point_id, target_branch_id)
        )

    def target_sandbox_id_for_request(self, request: Any) -> str:
        target_branch_id = f"counterfactual-{request.candidate_id}"
        return self.target_sandbox_id_for(
            request.replay_point.replay_point_id, target_branch_id
        )

    def target_sandbox_for_request(self, request: Any) -> SandboxInstanceState:
        return self._resolve_sandbox(self.target_sandbox_id_for_request(request))

    def _destroy_provider_sandbox(self, branch_sandbox_id: str) -> None:
        inst = self._branch_instances.get(branch_sandbox_id)
        if inst is not None:
            inst.status = "destroyed"
        destroy_sb = getattr(self.provider, "destroy_sandbox", None)
        if callable(destroy_sb):
            destroy_sb(branch_sandbox_id)
            return
        destroy_fn = getattr(self.provider, "destroy", None)
        if callable(destroy_fn):
            destroy_fn(branch_sandbox_id)

    def _release_provider_snapshot(
        self, snapshot_id: str, *, holder_id: str
    ) -> None:
        if holder_id and holder_id in self._committed_release_holders:
            return
        release_fn = getattr(self.provider, "release_snapshot", None)
        if not callable(release_fn):
            release_fn = getattr(self.provider, "destroy", None)
        if not callable(release_fn):
            return
        sig = inspect.signature(release_fn)
        accepts_holder = bool(holder_id) and (
            "holder_id" in sig.parameters
            or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
        )
        if not accepts_holder and holder_id:
            has_lookup, live = self._try_get_snapshot_live(snapshot_id)
            if has_lookup:
                if live is None:
                    self._committed_release_holders.add(holder_id)
                    return
                active_holders = (
                    live.held_refs
                    if isinstance(live, _SealedSnapshot)
                    else live.active_holder_ids
                )
                if active_holders and holder_id not in active_holders:
                    self._committed_release_holders.add(holder_id)
                    return
        if accepts_holder:
            release_fn(snapshot_id, holder_id=holder_id)
        else:
            release_fn(snapshot_id)
        if holder_id:
            self._committed_release_holders.add(holder_id)

    def capture_replay_state(
        self, request: CaptureReplayStateRequest
    ) -> AdapterStateLease:
        del request
        desc = self.provider.snapshot(
            self.source_sandbox_id,
            target_tier=self.target_tier,
        )
        self._captured_descriptors[desc.snapshot_id] = desc
        fidelity = _effective_replay_fidelity(
            fidelity_tier=desc.fidelity_tier,
            memory_pages=desc.memory_pages,
        )
        holder_id = desc.owner_holder_id or f"holder-{desc.snapshot_id}-parent"
        return AdapterStateLease(
            binding_key=_SnapshotRef(
                snapshot_id=desc.snapshot_id,
                holder_id=holder_id,
            ),
            fidelity=fidelity,
            projection_digest=desc.digest,
        )

    def _build_equivalence_report(
        self,
        *,
        sealed: _SealedSnapshot | SandboxSnapshotDescriptor,
        sb: SandboxInstanceState,
        achieved_fidelity: ReplayFidelity,
        check_prefix: str = "sandbox",
    ) -> EquivalenceReport:
        live_alive = sb.status != "destroyed"
        fs_ok = (
            live_alive and _digest_rootfs(sb.rootfs, sb.symlinks) == sealed.rootfs_digest
        )
        observed_digest = _observe_sandbox_digest(
            sb,
            fidelity_tier=sealed.fidelity_tier,
        )
        if sealed.fidelity_tier == "filesystem_only":
            checks: tuple[EquivalenceCheck, ...] = (
                EquivalenceCheck(f"{check_prefix}-rootfs-symlinks", fs_ok),
            )
        elif sealed.fidelity_tier == "filesystem_and_env":
            env_ok = live_alive and _digest_env(sb.env) == sealed.env_manifest_digest
            checks = (
                EquivalenceCheck(f"{check_prefix}-rootfs-symlinks", fs_ok),
                EquivalenceCheck(f"{check_prefix}-env-only", env_ok),
            )
        elif sealed.fidelity_tier == "process_tree_quiesced":
            env_ok = live_alive and _digest_env(sb.env) == sealed.env_manifest_digest
            daemon_ok = (
                live_alive
                and _daemon_health_verified(sb.processes, sb.daemon_health)
                and sealed.daemon_health_digest is not None
                and _digest_daemon_health(sb.processes, sb.daemon_health)
                == sealed.daemon_health_digest
            )
            checks = (
                EquivalenceCheck(f"{check_prefix}-rootfs-symlinks", fs_ok),
                EquivalenceCheck(f"{check_prefix}-env-manifest", env_ok),
                EquivalenceCheck(f"{check_prefix}-daemon-liveness", daemon_ok),
            )
        else:
            has_mem_evidence = bool(sealed.memory_pages and sb.memory_pages)
            mem_ok = (
                live_alive
                and has_mem_evidence
                and sealed.memory_digest is not None
                and _digest_memory(sb.processes, sb.memory_pages) == sealed.memory_digest
            )
            env_ok = live_alive and _digest_env(sb.env) == sealed.env_manifest_digest
            checks = (
                EquivalenceCheck(f"{check_prefix}-rootfs-symlinks", fs_ok),
                EquivalenceCheck(f"{check_prefix}-env-manifest", env_ok),
                EquivalenceCheck(f"{check_prefix}-process-memory", mem_ok),
            )
        equivalent = (
            observed_digest is not None
            and observed_digest == sealed.digest
            and all(check.passed for check in checks)
        )
        return EquivalenceReport(
            achieved_fidelity=achieved_fidelity,
            equivalent=equivalent,
            expected_digest=sealed.digest,
            observed_digest=observed_digest,
            checks=checks,
        )

    def restore_replay_state(
        self,
        binding_key: object,
        request: RestoreReplayStateRequest,
    ) -> RestoreReceipt:
        assert isinstance(binding_key, _SnapshotRef)
        self._release_active_replay_target(
            binding_key.snapshot_id,
            record_finalized=False,
        )
        fork_min_fidelity = (
            ReplayFidelity.BEST_EFFORT
            if request.minimum_fidelity == ReplayFidelity.NON_REPLAYABLE
            else request.minimum_fidelity
        )
        child_lease = self.fork_replay_child(
            binding_key,
            ForkReplayChildRequest(
                replay_point_id=request.replay_point_id,
                child_id=request.target_branch_id,
                target_branch_id=request.target_branch_id,
                minimum_fidelity=fork_min_fidelity,
            ),
        )
        branch_ref = cast(_BranchRef, child_lease.child_key)
        self._active_replay_targets[binding_key.snapshot_id] = branch_ref
        self._target_bindings[(request.replay_point_id, request.target_branch_id)] = (
            branch_ref
        )
        self.last_target_sandboxes.append(branch_ref)
        return child_lease.receipt

    def validate_replay_equivalence(
        self,
        binding_key: object,
        receipt: RestoreReceipt,
    ) -> EquivalenceReport:
        assert isinstance(binding_key, _SnapshotRef)
        sealed = self._resolve_snapshot(binding_key.snapshot_id)
        target_ref = self._active_replay_targets.get(
            binding_key.snapshot_id
        ) or self._finalized_replay_targets.get(binding_key.snapshot_id)
        if target_ref is None:
            return EquivalenceReport(
                achieved_fidelity=receipt.achieved_fidelity,
                equivalent=False,
                expected_digest=sealed.digest,
                observed_digest=None,
                checks=(EquivalenceCheck("sandbox-isolated-target", False),),
            )
        sb = self._resolve_sandbox(target_ref.branch_sandbox_id)
        return self._build_equivalence_report(
            sealed=sealed,
            sb=sb,
            achieved_fidelity=receipt.achieved_fidelity,
            check_prefix="sandbox",
        )

    def _release_active_replay_target(
        self,
        snapshot_id: str,
        *,
        record_finalized: bool,
    ) -> None:
        target_ref = self._active_replay_targets.get(snapshot_id)
        if target_ref is None:
            return
        self.release_replay_child(target_ref)
        self._active_replay_targets.pop(snapshot_id, None)
        stale_keys = [
            key
            for key, bound_ref in self._target_bindings.items()
            if bound_ref.branch_sandbox_id == target_ref.branch_sandbox_id
        ]
        for key in stale_keys:
            self._target_bindings.pop(key, None)
        if record_finalized:
            self._finalized_replay_targets[snapshot_id] = target_ref

    def finalize_replay_suffix(
        self,
        binding_key: object,
        request: RestoreReplayStateRequest,
        *,
        suffix_succeeded: bool,
    ) -> None:
        del suffix_succeeded
        assert isinstance(binding_key, _SnapshotRef)
        self._release_active_replay_target(
            binding_key.snapshot_id,
            record_finalized=True,
        )
        self._target_bindings.pop(
            (request.replay_point_id, request.target_branch_id), None
        )

    def release_replay_state(self, binding_key: object) -> None:
        assert isinstance(binding_key, _SnapshotRef)
        self._release_active_replay_target(
            binding_key.snapshot_id,
            record_finalized=True,
        )
        effective_holder = binding_key.holder_id
        if not effective_holder:
            has_lookup, live = self._try_get_snapshot_live(binding_key.snapshot_id)
            if has_lookup and live is not None:
                effective_holder = live.owner_holder_id
            elif binding_key.snapshot_id in self._captured_descriptors:
                effective_holder = self._captured_descriptors[
                    binding_key.snapshot_id
                ].owner_holder_id
        self._release_provider_snapshot(
            binding_key.snapshot_id,
            holder_id=effective_holder,
        )
        if binding_key.snapshot_id not in self.released_snapshots:
            self.released_snapshots.append(binding_key.snapshot_id)

    def fork_replay_child(
        self,
        binding_key: object,
        request: ForkReplayChildRequest,
    ) -> ReplayChildLease:
        assert isinstance(binding_key, _SnapshotRef)
        fork_fn = getattr(self.provider, "fork", None)
        if not callable(fork_fn):
            raise SandboxSpiError(
                "fork_unsupported",
                "sandbox provider does not implement RFC SPI fork",
            )
        sig = inspect.signature(fork_fn)
        fork_kwargs: dict[str, Any] = {"child_id": request.child_id}
        if "target_branch_id" in sig.parameters or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        ):
            fork_kwargs["target_branch_id"] = request.target_branch_id
        child_sb, isolation_id = fork_fn(binding_key.snapshot_id, **fork_kwargs)
        self._branch_instances[child_sb.sandbox_id] = child_sb
        sealed = self._resolve_snapshot(binding_key.snapshot_id)
        holder_id = child_sb.snapshot_holder_id or ""
        if not holder_id:
            holder_lookup = getattr(self.provider, "branch_holder_id", None)
            if callable(holder_lookup):
                holder_id = str(holder_lookup(child_sb.sandbox_id) or "")
        branch_ref = _BranchRef(
            branch_sandbox_id=child_sb.sandbox_id,
            source_snapshot_id=sealed.snapshot_id,
            branch_isolation_id=isolation_id,
            target_branch_id=request.target_branch_id,
            holder_id=holder_id,
        )
        observed_digest = _observe_sandbox_digest(
            child_sb,
            fidelity_tier=sealed.fidelity_tier,
        )
        return ReplayChildLease(
            child_key=branch_ref,
            concurrency=ReplayChildConcurrency.PROCESS_ISOLATED,
            worker_identity=isolation_id,
            receipt=RestoreReceipt(
                achieved_fidelity=_effective_replay_fidelity(
                    fidelity_tier=sealed.fidelity_tier,
                    memory_pages=sealed.memory_pages,
                ),
                restored_projection_digest=observed_digest,
                prefix_reused=self.prefix_reused,
            ),
        )

    def validate_replay_child_equivalence(
        self,
        child_key: object,
        receipt: RestoreReceipt,
    ) -> EquivalenceReport:
        assert isinstance(child_key, _BranchRef)
        sealed = self._resolve_snapshot(child_key.source_snapshot_id)
        sb = self._resolve_sandbox(child_key.branch_sandbox_id)
        return self._build_equivalence_report(
            sealed=sealed,
            sb=sb,
            achieved_fidelity=receipt.achieved_fidelity,
            check_prefix="fork-child",
        )

    def release_replay_child(self, child_key: object) -> None:
        assert isinstance(child_key, _BranchRef)
        if child_key.branch_sandbox_id in self._committed_released_children:
            return
        if child_key.branch_sandbox_id not in self._destroyed_child_sandboxes:
            self._destroy_provider_sandbox(child_key.branch_sandbox_id)
            self._destroyed_child_sandboxes.add(child_key.branch_sandbox_id)
        self._release_provider_snapshot(
            child_key.source_snapshot_id,
            holder_id=child_key.holder_id,
        )
        self._committed_released_children.add(child_key.branch_sandbox_id)
        if child_key.branch_sandbox_id not in self.released_children:
            self.released_children.append(child_key.branch_sandbox_id)

    def assess_replay_child_intent(
        self,
        child_key: object,
        intent: Mapping[str, Any],
    ) -> ReplayRiskAssessment:
        assert isinstance(child_key, _BranchRef)
        if intent.get("clone_parent_lease") is True or intent.get("egress") == "shared_prod":
            return ReplayRiskAssessment(
                disposition=ReplayRiskDisposition.DENY,
                policy_id="spi-authority-egress-fence-v0",
                sanitized_reason="child cannot reuse parent lease or shared production egress",
            )
        return ReplayRiskAssessment(
            disposition=ReplayRiskDisposition.ALLOW,
            policy_id="spi-authority-egress-fence-v0",
        )

    def assess_replay_child_outcome(
        self,
        child_key: object,
        intent: Mapping[str, Any],
        outcome: Any,
    ) -> ReplayRiskAssessment:
        del intent, outcome
        assert isinstance(child_key, _BranchRef)
        return ReplayRiskAssessment(
            disposition=ReplayRiskDisposition.ALLOW,
            policy_id="spi-authority-egress-fence-v0",
        )


def _prepare_trace_point(
    trace_id: str,
    *,
    registry: ReplayPointRegistry,
    store: SpiAgentStateStore,
    adapter: SpiReplayAdapter,
) -> tuple[TraceLog, Any]:
    trace = TraceLog(trace_id=trace_id, branch_id="main")
    early = TraceEvent(
        event_id=f"{trace_id}-evt-1",
        trace_id=trace_id,
        branch_id="main",
        sequence=0,
        parent_event_id=None,
        kind=TraceEventKind.ENVIRONMENT_OBSERVATION,
        public_payload={"step": "prepared"},
    )
    trace.append(early)
    point = registry.capture_at_head(trace, _cursor(1), agent_state_store=store, adapter=adapter)
    trace.append(
        TraceEvent(
            event_id=f"{trace_id}-evt-2-fail",
            trace_id=trace_id,
            branch_id="main",
            sequence=2,
            parent_event_id=point.created_event_id,
            kind=TraceEventKind.VALIDATION,
            public_payload={"failed": "regression"},
        )
    )
    return trace, point


def _cursor(index: int = 1) -> AgentCursor:
    return AgentCursor(
        history_index=index,
        history_digest=f"hist-{index}",
        context_digest=f"ctx-{index}",
    )


def test_exact_memory_snapshot_requires_paused_quiescence_barrier() -> None:
    provider = InMemorySandboxSpiProvider()
    provider.create_sandbox(
        sandbox_id="sb-main",
        rootfs={"src/app.py": "v1"},
        processes={42: "typescript-language-server --stdio"},
        memory_pages={"lsp_cache": "warm-index-v1"},
    )
    adapter = SpiReplayAdapter(provider, source_sandbox_id="sb-main", target_tier="microvm_memory_exact")
    trace = TraceLog(trace_id="trace-q", branch_id="main")
    trace.append(
        TraceEvent(
            event_id="e1",
            trace_id="trace-q",
            branch_id="main",
            sequence=0,
            parent_event_id=None,
            kind=TraceEventKind.ENVIRONMENT_OBSERVATION,
            public_payload={"ready": True},
        )
    )
    registry = ReplayPointRegistry()
    store = SpiAgentStateStore()

    with pytest.raises(SandboxSpiError, match="quiescence_required"):
        registry.capture_at_head(trace, _cursor(1), agent_state_store=store, adapter=adapter)

    receipt = provider.pause("sb-main")
    assert receipt.quiesced_fs and receipt.dirty_page_flush_ok
    point = registry.capture_at_head(trace, _cursor(1), agent_state_store=store, adapter=adapter)
    assert point.fidelity == ReplayFidelity.EXACT
    assert point.captured_projection_digest is not None


def test_pause_resume_preserves_single_sandbox_identity_and_live_process_table() -> None:
    provider = InMemorySandboxSpiProvider()
    sb = provider.create_sandbox(
        sandbox_id="sb-1to1",
        rootfs={"Cargo.toml": "v1"},
        processes={101: "cargo-watch", 202: "postgres-local"},
        memory_pages={"heap_ptr": "0x7ffe1000"},
    )
    receipt = provider.pause("sb-1to1")
    assert provider.sandboxes["sb-1to1"].status == "paused"

    resumed = provider.resume("sb-1to1")
    assert resumed is sb
    assert resumed.sandbox_id == "sb-1to1"
    assert resumed.status == "running"
    assert resumed.processes == {101: "cargo-watch", 202: "postgres-local"}
    assert receipt.active_pid_digest

    with pytest.raises(SandboxSpiError, match="invalid_state"):
        provider.resume("sb-1to1")


def test_snapshot_fork_isolates_concurrent_counterfactual_branches_and_symlinks() -> None:
    provider = InMemorySandboxSpiProvider()
    provider.create_sandbox(
        sandbox_id="sb-parent",
        rootfs={
            "packages/core/index.ts": "export const v = 1;",
            "node_modules/.pnpm/dep/index.js": "module.exports = 1;",
        },
        symlinks={"packages/core/node_modules/dep": "../../node_modules/.pnpm/dep"},
        env={"NODE_ENV": "test"},
        processes={700: "vitest-worker-warm"},
        memory_pages={"module_graph": "precompiled-ast"},
    )
    provider.pause("sb-parent")
    adapter = SpiReplayAdapter(provider, source_sandbox_id="sb-parent", target_tier="microvm_memory_exact")
    registry = ReplayPointRegistry()
    store = SpiAgentStateStore()
    trace, point = _prepare_trace_point("trace-spi", registry=registry, store=store, adapter=adapter)

    hypothesis = CounterfactualHypothesis(
        hypothesis_id="fix-via-warm-microvm",
        first_affected_event_id="trace-spi-evt-2-fail",
        first_affected_sequence=2,
        fix_set=(ValidationTarget("target-fix", baseline_passed=False),),
        guard_set=(ValidationTarget("guard-symlink", baseline_passed=True),),
        minimum_fidelity=ReplayFidelity.EXACT,
    )

    observed_isolations: list[str] = []
    start_barrier = threading.Barrier(2)
    treatment_mutated = threading.Event()

    def run_arm(ctx: Any, req: Any) -> Mapping[str, bool]:
        del req
        assert isinstance(ctx.adapter_child_key, _BranchRef)
        sb = provider.sandboxes[ctx.adapter_child_key.branch_sandbox_id]
        observed_isolations.append(ctx.adapter_child_key.branch_isolation_id)
        start_barrier.wait(timeout=2.0)
        if ctx.child_id == "treatment":
            sb.rootfs["packages/core/index.ts"] = "export const v = 2;"
            sb.memory_pages["module_graph"] = "mutated-in-treatment"
            treatment_mutated.set()
            fix_ok = True
        else:
            assert treatment_mutated.wait(timeout=2.0)
            fix_ok = (
                sb.rootfs["packages/core/index.ts"] == "export const v = 2;"
                or sb.memory_pages["module_graph"] == "mutated-in-treatment"
            )
        symlink_ok = sb.symlinks.get("packages/core/node_modules/dep") == "../../node_modules/.pnpm/dep"
        warm_ok = sb.processes.get(700) == "vitest-worker-warm"
        return {"target-fix": fix_ok, "guard-symlink": symlink_ok and warm_ok}

    group = run_counterfactual_child_group(
        registry,
        trace=trace,
        hypothesis=hypothesis,
        candidates=(
            CounterfactualChildCandidate(
                candidate=CounterfactualCandidate("treatment", {"arm": "treatment"}),
                target_branch_id="arm-treatment",
                execute_suffix=run_arm,
            ),
            CounterfactualChildCandidate(
                candidate=CounterfactualCandidate("control", {"arm": "control"}),
                target_branch_id="arm-control",
                execute_suffix=run_arm,
            ),
        ),
        parallel=True,
        max_workers=2,
    )

    by_id = {c.candidate_id: c for c in group.candidates}
    assert by_id["treatment"].replay_result is not None
    assert by_id["treatment"].replay_result.decision == CounterfactualDecision.PROMOTED
    assert by_id["control"].replay_result is not None
    assert by_id["control"].replay_result.decision == CounterfactualDecision.REJECTED
    assert len(set(observed_isolations)) == 2
    snap_id = next(iter(provider.snapshots))
    assert provider.snapshots[snap_id].rootfs["packages/core/index.ts"] == "export const v = 1;"
    assert point.replay_point_id == group.replay_point_id


def test_authority_and_secret_scrubbing_blocks_guest_lease_cloning_and_public_key_leak() -> None:
    provider = InMemorySandboxSpiProvider()
    provider.create_sandbox(
        sandbox_id="sb-sec",
        rootfs={
            "src/mod.py": "ok",
            ".loopx/registry.json": "{\"secret\": true}",
            "loopx.lease.json": "{\"version\": 9}",
        },
        env={"LOOPX_LEASE_KEY": "claim-secret", "CLOUD_API_TOKEN": "sk-secret", "PATH": "/usr/bin"},
        memory_pages={"env_safe": "1"},
    )
    provider.pause("sb-sec")
    adapter = SpiReplayAdapter(provider, source_sandbox_id="sb-sec")
    registry = ReplayPointRegistry()
    store = SpiAgentStateStore()
    trace, _ = _prepare_trace_point("trace-sec", registry=registry, store=store, adapter=adapter)

    snap = next(iter(provider.snapshots.values()))
    assert set(snap.scrubbed_keys) == {"CLOUD_API_TOKEN", "LOOPX_LEASE_KEY"}
    assert "LOOPX_LEASE_KEY" not in snap.env and "CLOUD_API_TOKEN" not in snap.env
    assert ".loopx/registry.json" not in snap.rootfs and "loopx.lease.json" not in snap.rootfs

    hypothesis = CounterfactualHypothesis(
        hypothesis_id="sec-fence",
        first_affected_event_id="trace-sec-evt-2-fail",
        first_affected_sequence=2,
        fix_set=(ValidationTarget("fix", baseline_passed=False),),
        guard_set=(ValidationTarget("guard", baseline_passed=True),),
        minimum_fidelity=ReplayFidelity.EXACT,
    )
    group = run_counterfactual_child_group(
        registry,
        trace=trace,
        hypothesis=hypothesis,
        candidates=(
            CounterfactualChildCandidate(
                candidate=CounterfactualCandidate("denied-lease", {"kind": "bad-lease"}),
                target_branch_id="bad-branch-lease",
                intent={"clone_parent_lease": True},
                execute_suffix=lambda _ctx, _req: {"fix": True, "guard": True},
            ),
            CounterfactualChildCandidate(
                candidate=CounterfactualCandidate("denied-egress", {"kind": "bad-egress"}),
                target_branch_id="bad-branch-egress",
                intent={"egress": "shared_prod"},
                execute_suffix=lambda _ctx, _req: {"fix": True, "guard": True},
            ),
        ),
    )
    for child_res in group.candidates:
        assert not child_res.evaluated
        assert child_res.execution_failure is not None
        assert child_res.execution_failure.risk_assessment is not None
        assert (
            child_res.execution_failure.risk_assessment.disposition
            == ReplayRiskDisposition.DENY
        )

    serialized = json.dumps(registry.records()) + json.dumps(group.to_record())
    assert "binding_key" not in serialized
    assert "child_key" not in serialized
    assert "sb-fork-" not in serialized


def test_fidelity_degradation_filters_filesystem_only_provider_when_exact_process_state_required() -> None:
    provider = InMemorySandboxSpiProvider()
    provider.create_sandbox(
        sandbox_id="sb-fid",
        rootfs={"a.py": "1"},
        processes={1: "daemon"},
        memory_pages={"k": "v"},
    )
    provider.pause("sb-fid")
    registry = ReplayPointRegistry()
    store = SpiAgentStateStore()

    trace = TraceLog(trace_id="trace-fid", branch_id="main")
    trace.append(
        TraceEvent(
            event_id="e1",
            trace_id="trace-fid",
            branch_id="main",
            sequence=0,
            parent_event_id=None,
            kind=TraceEventKind.ENVIRONMENT_OBSERVATION,
            public_payload={"step": "exact"},
        )
    )
    exact_pt = registry.capture_at_head(
        trace,
        _cursor(1),
        agent_state_store=store,
        adapter=SpiReplayAdapter(provider, source_sandbox_id="sb-fid", target_tier="microvm_memory_exact"),
    )
    trace.append(
        TraceEvent(
            event_id="e2",
            trace_id="trace-fid",
            branch_id="main",
            sequence=2,
            parent_event_id=exact_pt.created_event_id,
            kind=TraceEventKind.ENVIRONMENT_OBSERVATION,
            public_payload={"step": "fs-only"},
        )
    )
    fs_pt = registry.capture_at_head(
        trace,
        _cursor(2),
        agent_state_store=store,
        adapter=SpiReplayAdapter(provider, source_sandbox_id="sb-fid", target_tier="filesystem_only"),
    )
    trace.append(
        TraceEvent(
            event_id="e3-fail",
            trace_id="trace-fid",
            branch_id="main",
            sequence=4,
            parent_event_id=fs_pt.created_event_id,
            kind=TraceEventKind.VALIDATION,
            public_payload={"failed": "yes"},
        )
    )

    assert fs_pt.fidelity == ReplayFidelity.BEST_EFFORT
    selected = registry.select_before(
        trace_id="trace-fid",
        branch_id="main",
        first_affected_sequence=4,
        minimum_fidelity=ReplayFidelity.EXACT,
    )
    assert selected.replay_point_id == exact_pt.replay_point_id

    fs_only_trace, _ = _prepare_trace_point(
        "trace-fs-only",
        registry=registry,
        store=store,
        adapter=SpiReplayAdapter(provider, source_sandbox_id="sb-fid", target_tier="filesystem_only"),
    )
    res = run_counterfactual_suffix_replay(
        registry,
        trace=fs_only_trace,
        hypothesis=CounterfactualHypothesis(
            hypothesis_id="require-exact",
            first_affected_event_id="trace-fs-only-evt-2-fail",
            first_affected_sequence=2,
            fix_set=(ValidationTarget("fix", baseline_passed=False),),
            guard_set=(ValidationTarget("guard", baseline_passed=True),),
            minimum_fidelity=ReplayFidelity.EXACT,
        ),
        candidate=CounterfactualCandidate("cold-fs", {"patch": "fs"}),
        execute_suffix=lambda _req: {"fix": True, "guard": True},
    )
    assert res.decision == CounterfactualDecision.OBSERVED_ONLY


def test_child_branch_release_preserves_pinned_snapshot_until_explicit_destroy() -> None:
    provider = InMemorySandboxSpiProvider()
    provider.create_sandbox(sandbox_id="sb-pin", rootfs={"a.txt": "1"}, memory_pages={"m": "1"})
    provider.pause("sb-pin")
    adapter = SpiReplayAdapter(provider, source_sandbox_id="sb-pin", target_tier="microvm_memory_exact")
    registry = ReplayPointRegistry()
    store = SpiAgentStateStore()
    trace, point = _prepare_trace_point("trace-pin", registry=registry, store=store, adapter=adapter)

    hyp = CounterfactualHypothesis(
        hypothesis_id="pin-test",
        first_affected_event_id="trace-pin-evt-2-fail",
        first_affected_sequence=2,
        fix_set=(ValidationTarget("fix", baseline_passed=False),),
        guard_set=(ValidationTarget("guard", baseline_passed=True),),
        minimum_fidelity=ReplayFidelity.EXACT,
    )
    run_counterfactual_child_group(
        registry,
        trace=trace,
        hypothesis=hyp,
        candidates=(
            CounterfactualChildCandidate(
                candidate=CounterfactualCandidate("branch-a", {"x": 1}),
                target_branch_id="branch-a",
                execute_suffix=lambda _ctx, _req: {"fix": True, "guard": True},
            ),
        ),
    )

    assert len(adapter.released_children) == 1
    assert provider.sandboxes[adapter.released_children[0]].status == "destroyed"
    snap_id = next(iter(provider.snapshots))
    assert provider.snapshots[snap_id].refcount == 1

    registry.release(point.replay_point_id)
    assert snap_id not in provider.snapshots


def test_single_branch_restore_verifies_live_state_and_rejects_unrestored_or_incomplete_exact_evidence() -> (
    None
):
    class _StaleForkSpiProvider(InMemorySandboxSpiProvider):
        def fork(
            self,
            snapshot_id: str,
            *,
            child_id: str,
            target_branch_id: str | None = None,
            egress_policy_id: str = "isolated-offline",
        ) -> tuple[SandboxInstanceState, str]:
            child, isolation_id = super().fork(
                snapshot_id,
                child_id=child_id,
                target_branch_id=target_branch_id,
                egress_policy_id=egress_policy_id,
            )
            sealed = self.sealed_snapshot(snapshot_id)
            live_parent = self.sandboxes[sealed.parent_sandbox_id]
            child.rootfs = dict(live_parent.rootfs)
            child.symlinks = dict(live_parent.symlinks)
            child.memory_pages = dict(live_parent.memory_pages)
            return child, isolation_id

    class _MemoryDroppingSpiProvider(InMemorySandboxSpiProvider):
        def fork(
            self,
            snapshot_id: str,
            *,
            child_id: str,
            target_branch_id: str | None = None,
            egress_policy_id: str = "isolated-offline",
        ) -> tuple[SandboxInstanceState, str]:
            child, isolation_id = super().fork(
                snapshot_id,
                child_id=child_id,
                target_branch_id=target_branch_id,
                egress_policy_id=egress_policy_id,
            )
            child.processes = {}
            child.memory_pages = {}
            return child, isolation_id

    def _setup_resumed_drift(
        provider: InMemorySandboxSpiProvider,
        trace_id: str,
    ) -> tuple[TraceLog, Any, SpiReplayAdapter, ReplayPointRegistry]:
        sb = provider.create_sandbox(
            sandbox_id="sb-single",
            rootfs={"src/app.py": "v1-captured"},
            symlinks={"bin/tool": "v1-target"},
            env={"MODE": "v1"},
            processes={42: "lsp-v1"},
            memory_pages={"lsp_cache": "v1-mem"},
        )
        provider.pause("sb-single")
        adapter = SpiReplayAdapter(
            provider,
            source_sandbox_id="sb-single",
            target_tier="microvm_memory_exact",
        )
        registry = ReplayPointRegistry()
        store = SpiAgentStateStore()
        trace, point = _prepare_trace_point(
            trace_id, registry=registry, store=store, adapter=adapter
        )

        # Resume the original live instance and advance both workspace & memory.
        provider.resume("sb-single")
        sb.rootfs["src/app.py"] = "v2-post-capture"
        sb.symlinks["bin/tool"] = "v2-target"
        sb.memory_pages["lsp_cache"] = "v2-post-capture-mem"
        return trace, point, adapter, registry

    def _hyp(trace_id: str) -> CounterfactualHypothesis:
        return CounterfactualHypothesis(
            hypothesis_id=f"hyp-{trace_id}",
            first_affected_event_id=f"{trace_id}-evt-2-fail",
            first_affected_sequence=2,
            fix_set=(ValidationTarget("fix", baseline_passed=False),),
            guard_set=(ValidationTarget("guard", baseline_passed=True),),
            minimum_fidelity=ReplayFidelity.EXACT,
        )

    # 1) Genuine reference provider isolates a forked target without mutating live sb-single.
    real_provider = InMemorySandboxSpiProvider()
    trace_ok, point_ok, adapter_ok, reg_ok = _setup_resumed_drift(
        real_provider, "trace-real-restore"
    )
    observed_in_suffix: list[tuple[str, str, str]] = []

    def read_restored(req: Any) -> Mapping[str, bool]:
        sb = adapter_ok.target_sandbox_for_request(req)
        observed_in_suffix.append(
            (
                sb.rootfs["src/app.py"],
                sb.symlinks["bin/tool"],
                sb.memory_pages["lsp_cache"],
            )
        )
        live_source = real_provider.sandboxes["sb-single"]
        assert live_source.rootfs["src/app.py"] == "v2-post-capture"
        assert live_source.memory_pages["lsp_cache"] == "v2-post-capture-mem"
        return {
            "fix": sb.rootfs["src/app.py"] == "v1-captured",
            "guard": sb.memory_pages["lsp_cache"] == "v1-mem"
            and sb.processes.get(42) == "lsp-v1",
        }

    promoted = run_counterfactual_suffix_replay(
        reg_ok,
        trace=trace_ok,
        hypothesis=_hyp("trace-real-restore"),
        candidate=CounterfactualCandidate("real-restore", {"op": "restore"}),
        execute_suffix=read_restored,
    )
    assert promoted.decision == CounterfactualDecision.PROMOTED
    assert observed_in_suffix == [("v1-captured", "v1-target", "v1-mem")]
    assert real_provider.sandboxes["sb-single"].rootfs["src/app.py"] == "v2-post-capture"

    # A candidate checking for post-capture state on the isolated target rejects cleanly.
    post_capture_check = run_counterfactual_suffix_replay(
        reg_ok,
        trace=trace_ok,
        hypothesis=_hyp("trace-real-restore"),
        candidate=CounterfactualCandidate("check-post-capture", {"op": "probe"}),
        execute_suffix=lambda req: {
            "fix": adapter_ok.target_sandbox_for_request(req).rootfs["src/app.py"]
            == "v2-post-capture",
            "guard": True,
        },
    )
    assert post_capture_check.decision == CounterfactualDecision.REJECTED

    # Independent post-fork target drift before equivalence verification is rejected.
    snap_id = next(iter(real_provider.snapshots))
    drift_req = RestoreReplayStateRequest(
        replay_point_id=point_ok.replay_point_id,
        target_branch_id="manual-drift-probe",
        minimum_fidelity=ReplayFidelity.EXACT,
    )
    adapter_ok.restore_replay_state(_SnapshotRef(snapshot_id=snap_id), drift_req)
    drift_target = adapter_ok.target_sandbox_for(
        point_ok.replay_point_id, "manual-drift-probe"
    )
    drift_target.memory_pages["lsp_cache"] = "drifted-after-restore"
    drifted_report = adapter_ok.validate_replay_equivalence(
        _SnapshotRef(snapshot_id=snap_id),
        RestoreReceipt(
            achieved_fidelity=ReplayFidelity.EXACT,
            restored_projection_digest=point_ok.captured_projection_digest,
        ),
    )
    adapter_ok.finalize_replay_suffix(
        _SnapshotRef(snapshot_id=snap_id),
        drift_req,
        suffix_succeeded=False,
    )
    assert not drifted_report.equivalent
    assert drifted_report.observed_digest != drifted_report.expected_digest
    assert any(
        c.check_id == "sandbox-process-memory" and not c.passed
        for c in drifted_report.checks
    )

    # 2) Stale-fork provider leaves post-capture state on target; promotion must be refused.
    stale_provider = _StaleForkSpiProvider()
    trace_stale, _, _, reg_stale = _setup_resumed_drift(
        stale_provider, "trace-stale-restore"
    )
    stale_suffix_called = False

    def stale_cb(_req: Any) -> Mapping[str, bool]:
        nonlocal stale_suffix_called
        stale_suffix_called = True
        return {"fix": True, "guard": True}

    with pytest.raises(ValueError, match="exact replay requires equal"):
        run_counterfactual_suffix_replay(
            reg_stale,
            trace=trace_stale,
            hypothesis=_hyp("trace-stale-restore"),
            candidate=CounterfactualCandidate("stale-restore", {"op": "noop"}),
            execute_suffix=stale_cb,
        )
    assert stale_suffix_called is False

    # 3) Missing exact memory/process evidence on isolated target fork refuses promotion.
    mem_drop_provider = _MemoryDroppingSpiProvider()
    trace_drop, _, _, reg_drop = _setup_resumed_drift(
        mem_drop_provider, "trace-drop-mem"
    )
    drop_suffix_called = False

    def drop_cb(_req: Any) -> Mapping[str, bool]:
        nonlocal drop_suffix_called
        drop_suffix_called = True
        return {"fix": True, "guard": True}

    with pytest.raises(ValueError, match="exact replay requires equal"):
        run_counterfactual_suffix_replay(
            reg_drop,
            trace=trace_drop,
            hypothesis=_hyp("trace-drop-mem"),
            candidate=CounterfactualCandidate("drop-mem", {"op": "partial"}),
            execute_suffix=drop_cb,
        )
    assert drop_suffix_called is False


def test_sealed_snapshot_rejects_descriptor_mutation_and_isolates_readback_and_forks() -> (
    None
):
    provider = InMemorySandboxSpiProvider()
    sb = provider.create_sandbox(
        sandbox_id="sb-seal",
        rootfs={"src/main.py": "sealed-v1"},
        symlinks={"current": "releases/v1"},
        env={"SAFE_ENV": "1", "CLOUD_API_TOKEN": "secret-tok"},
        processes={88: "daemon-v1"},
        memory_pages={"heap": "sealed-heap-v1"},
    )
    provider.pause("sb-seal")
    desc = provider.snapshot("sb-seal", target_tier="microvm_memory_exact")

    # Descriptor mappings are deeply immutable; caller mutations fail closed.
    for mapping in (
        desc.rootfs,
        desc.symlinks,
        desc.env,
        desc.processes,
        desc.memory_pages,
    ):
        with pytest.raises(TypeError):
            mapping["caller_note"] = "mutated"  # type: ignore[index]

    # Even if a caller replaces the public descriptor entry with an annotated
    # copy carrying the same digest, sealed state, readback, and forks stay intact.
    tampered = SandboxSnapshotDescriptor(
        snapshot_id=desc.snapshot_id,
        parent_sandbox_id=desc.parent_sandbox_id,
        fidelity_tier=desc.fidelity_tier,
        rootfs={**desc.rootfs, "src/main.py": "caller-tampered", "note.txt": "x"},
        symlinks=desc.symlinks,
        env=desc.env,
        processes=desc.processes,
        memory_pages={**desc.memory_pages, "heap": "caller-tampered-heap"},
        scrubbed_keys=desc.scrubbed_keys,
        digest=desc.digest,
        refcount=desc.refcount,
        rootfs_digest=desc.rootfs_digest,
        env_manifest_digest=desc.env_manifest_digest,
        memory_digest=desc.memory_digest,
    )
    provider.snapshots[desc.snapshot_id] = tampered

    # Resuming and mutating the live parent also cannot mutate the sealed snapshot.
    provider.resume("sb-seal")
    sb.rootfs["src/main.py"] = "parent-resumed-v2"
    sb.memory_pages["heap"] = "parent-resumed-heap-v2"

    readback = provider.get_snapshot(desc.snapshot_id)
    assert readback.digest == desc.digest
    assert readback.rootfs == {"src/main.py": "sealed-v1"}
    assert "note.txt" not in readback.rootfs
    assert readback.memory_pages == {"heap": "sealed-heap-v1"}

    child, _ = provider.fork(desc.snapshot_id, child_id="isolated-after-tamper")
    assert child.rootfs == {"src/main.py": "sealed-v1"}
    assert "note.txt" not in child.rootfs
    assert child.symlinks == {"current": "releases/v1"}
    assert child.processes == {88: "daemon-v1"}
    assert child.memory_pages == {"heap": "sealed-heap-v1"}
    assert (
        _observe_sandbox_digest(child, fidelity_tier="microvm_memory_exact")
        == desc.digest
    )


def test_process_tree_quiesced_requires_live_daemon_proof_and_refuses_lost_daemon() -> (
    None
):
    # 1) Snapshotting process_tree_quiesced without live healthy daemon fails closed.
    no_health_provider = InMemorySandboxSpiProvider()
    no_health_provider.create_sandbox(
        sandbox_id="sb-no-health",
        rootfs={"src/app.py": "v1"},
        env={"MODE": "quiesced"},
        processes={501: "lsp-daemon"},
        daemon_health={"lsp-daemon": False},
    )
    no_health_provider.pause("sb-no-health")
    with pytest.raises(SandboxSpiError, match="daemon_health_required"):
        no_health_provider.snapshot("sb-no-health", target_tier="process_tree_quiesced")

    def _setup_quiesced_drift(
        provider: InMemorySandboxSpiProvider,
        trace_id: str,
    ) -> tuple[TraceLog, Any, SpiReplayAdapter, ReplayPointRegistry]:
        sb = provider.create_sandbox(
            sandbox_id="sb-quiesced",
            rootfs={"src/app.py": "v1-quiesced"},
            env={"MODE": "v1"},
            processes={501: "lsp-daemon"},
            daemon_health={"lsp-daemon": True},
            memory_pages={"ephemeral_ram": "not-captured"},
        )
        provider.pause("sb-quiesced")
        adapter = SpiReplayAdapter(
            provider,
            source_sandbox_id="sb-quiesced",
            target_tier="process_tree_quiesced",
        )
        registry = ReplayPointRegistry()
        store = SpiAgentStateStore()
        trace, point = _prepare_trace_point(
            trace_id, registry=registry, store=store, adapter=adapter
        )
        # Advance workspace, env, and stop daemon after capture.
        provider.resume("sb-quiesced")
        sb.rootfs["src/app.py"] = "v2-post"
        sb.processes = {}
        sb.daemon_health = {}
        return trace, point, adapter, registry

    def _sem_hyp(trace_id: str) -> CounterfactualHypothesis:
        return CounterfactualHypothesis(
            hypothesis_id=f"hyp-{trace_id}",
            first_affected_event_id=f"{trace_id}-evt-2-fail",
            first_affected_sequence=2,
            fix_set=(ValidationTarget("fix", baseline_passed=False),),
            guard_set=(ValidationTarget("guard", baseline_passed=True),),
            minimum_fidelity=ReplayFidelity.SEMANTIC_EQUIVALENT,
        )

    # 2) Valid process_tree_quiesced capture preserves daemon/health (not memory pages),
    # seals immutable daemon_health, and promotes single-branch + child-fork replay.
    ok_provider = InMemorySandboxSpiProvider()
    trace_ok, point_ok, adapter_ok, reg_ok = _setup_quiesced_drift(
        ok_provider, "trace-quiesced-ok"
    )
    snap_desc = next(iter(ok_provider.snapshots.values()))
    assert snap_desc.memory_pages == {}
    assert snap_desc.processes == {501: "lsp-daemon"}
    assert snap_desc.daemon_health == {"lsp-daemon": True}
    with pytest.raises(TypeError):
        cast("dict[str, bool]", snap_desc.daemon_health)["lsp-daemon"] = False

    promoted = run_counterfactual_suffix_replay(
        reg_ok,
        trace=trace_ok,
        hypothesis=_sem_hyp("trace-quiesced-ok"),
        candidate=CounterfactualCandidate("quiesced-ok", {"op": "restart-daemon"}),
        execute_suffix=lambda req: {
            "fix": adapter_ok.target_sandbox_for_request(req).rootfs["src/app.py"]
            == "v1-quiesced",
            "guard": adapter_ok.target_sandbox_for_request(req).processes
            == {501: "lsp-daemon"}
            and adapter_ok.target_sandbox_for_request(req).daemon_health.get(
                "lsp-daemon"
            )
            is True,
        },
    )
    assert promoted.decision == CounterfactualDecision.PROMOTED
    assert ok_provider.sandboxes["sb-quiesced"].rootfs["src/app.py"] == "v2-post"

    child_group = run_counterfactual_child_group(
        reg_ok,
        trace=trace_ok,
        hypothesis=_sem_hyp("trace-quiesced-ok"),
        candidates=(
            CounterfactualChildCandidate(
                candidate=CounterfactualCandidate("child-quiesced", {"op": "fork"}),
                target_branch_id="branch-quiesced",
                execute_suffix=lambda ctx, _req: {
                    "fix": True,
                    "guard": (
                        isinstance(ctx.adapter_child_key, _BranchRef)
                        and ok_provider.sandboxes[
                            ctx.adapter_child_key.branch_sandbox_id
                        ].daemon_health.get("lsp-daemon")
                        is True
                    ),
                },
            ),
        ),
    )
    assert child_group.candidates[0].replay_result is not None
    assert (
        child_group.candidates[0].replay_result.decision
        == CounterfactualDecision.PROMOTED
    )

    # 3) Lost daemon with identical rootfs + env fails closed and refuses promotion.
    class _DaemonDroppingSpiProvider(InMemorySandboxSpiProvider):
        def fork(
            self,
            snapshot_id: str,
            *,
            child_id: str,
            target_branch_id: str | None = None,
            egress_policy_id: str = "isolated-offline",
        ) -> tuple[SandboxInstanceState, str]:
            child, isolation_id = super().fork(
                snapshot_id,
                child_id=child_id,
                target_branch_id=target_branch_id,
                egress_policy_id=egress_policy_id,
            )
            child.processes = {}
            child.daemon_health = {}
            return child, isolation_id

    drop_provider = _DaemonDroppingSpiProvider()
    trace_drop, _, adapter_drop, reg_drop = _setup_quiesced_drift(
        drop_provider, "trace-quiesced-drop"
    )
    drop_suffix_called = False

    def _never_promote(_req: Any) -> Mapping[str, bool]:
        nonlocal drop_suffix_called
        drop_suffix_called = True
        return {"fix": True, "guard": True}

    with pytest.raises(
        ValueError, match="exact or semantic replay requires verified state equivalence"
    ):
        run_counterfactual_suffix_replay(
            reg_drop,
            trace=trace_drop,
            hypothesis=_sem_hyp("trace-quiesced-drop"),
            candidate=CounterfactualCandidate("lost-daemon", {"op": "noop"}),
            execute_suffix=_never_promote,
        )
    assert drop_suffix_called is False
    drop_snap_id = next(iter(drop_provider.snapshots))
    assert drop_provider.snapshots[drop_snap_id].refcount == 1
    assert (
        drop_provider.sandboxes[
            adapter_drop.last_target_sandboxes[0].branch_sandbox_id
        ].status
        == "destroyed"
    )
    probe_req = RestoreReplayStateRequest(
        replay_point_id=reg_drop.records()[0]["replay_point_id"],
        target_branch_id="lost-daemon-probe",
        minimum_fidelity=ReplayFidelity.SEMANTIC_EQUIVALENT,
    )
    adapter_drop.restore_replay_state(_SnapshotRef(snapshot_id=drop_snap_id), probe_req)
    try:
        lost_report = adapter_drop.validate_replay_equivalence(
            _SnapshotRef(snapshot_id=drop_snap_id),
            RestoreReceipt(
                achieved_fidelity=ReplayFidelity.SEMANTIC_EQUIVALENT,
                restored_projection_digest=None,
            ),
        )
    finally:
        adapter_drop.finalize_replay_suffix(
            _SnapshotRef(snapshot_id=drop_snap_id),
            probe_req,
            suffix_succeeded=False,
        )
    assert drop_provider.snapshots[drop_snap_id].refcount == 1
    assert not lost_report.equivalent
    checks_by_id = {c.check_id: c.passed for c in lost_report.checks}
    assert checks_by_id == {
        "sandbox-rootfs-symlinks": True,
        "sandbox-env-manifest": True,
        "sandbox-daemon-liveness": False,
    }

    child_drop_called = False

    def _never_child_drop(_ctx: Any, _req: Any) -> Mapping[str, bool]:
        nonlocal child_drop_called
        child_drop_called = True
        return {"fix": True, "guard": True}

    with pytest.raises(
        ValueError, match="exact or semantic replay requires verified state equivalence"
    ):
        run_counterfactual_child_group(
            reg_drop,
            trace=trace_drop,
            hypothesis=_sem_hyp("trace-quiesced-drop"),
            candidates=(
                CounterfactualChildCandidate(
                    candidate=CounterfactualCandidate("child-lost-daemon", {"op": "fork"}),
                    target_branch_id="branch-lost-daemon",
                    execute_suffix=_never_child_drop,
                ),
            ),
        )
    assert child_drop_called is False

    # 4) Unhealthy daemon (processes present, health probe False) also refuses promotion.
    class _UnhealthyDaemonSpiProvider(InMemorySandboxSpiProvider):
        def fork(
            self,
            snapshot_id: str,
            *,
            child_id: str,
            target_branch_id: str | None = None,
            egress_policy_id: str = "isolated-offline",
        ) -> tuple[SandboxInstanceState, str]:
            child, isolation_id = super().fork(
                snapshot_id,
                child_id=child_id,
                target_branch_id=target_branch_id,
                egress_policy_id=egress_policy_id,
            )
            child.daemon_health = {"lsp-daemon": False}
            return child, isolation_id

    unhealthy_provider = _UnhealthyDaemonSpiProvider()
    trace_unhealthy, _, adapter_unhealthy, reg_unhealthy = _setup_quiesced_drift(
        unhealthy_provider, "trace-quiesced-unhealthy"
    )
    unhealthy_suffix_called = False

    def _never_unhealthy(_req: Any) -> Mapping[str, bool]:
        nonlocal unhealthy_suffix_called
        unhealthy_suffix_called = True
        return {"fix": True, "guard": True}

    with pytest.raises(
        ValueError, match="exact or semantic replay requires verified state equivalence"
    ):
        run_counterfactual_suffix_replay(
            reg_unhealthy,
            trace=trace_unhealthy,
            hypothesis=_sem_hyp("trace-quiesced-unhealthy"),
            candidate=CounterfactualCandidate("unhealthy-daemon", {"op": "noop"}),
            execute_suffix=_never_unhealthy,
        )
    assert unhealthy_suffix_called is False
    unhealthy_report = adapter_unhealthy.validate_replay_equivalence(
        _SnapshotRef(snapshot_id=next(iter(unhealthy_provider.snapshots))),
        RestoreReceipt(
            achieved_fidelity=ReplayFidelity.SEMANTIC_EQUIVALENT,
            restored_projection_digest=None,
        ),
    )
    assert not unhealthy_report.equivalent
    assert any(
        c.check_id == "sandbox-daemon-liveness" and not c.passed
        for c in unhealthy_report.checks
    )

    # 5) Honest downgrade to BEST_EFFORT when daemon cannot restart avoids PROMOTED.
    @dataclass
    class _DowngradingDaemonAdapter(SpiReplayAdapter):
        def restore_replay_state(
            self,
            binding_key: object,
            request: RestoreReplayStateRequest,
        ) -> RestoreReceipt:
            receipt = super().restore_replay_state(binding_key, request)
            sb = self.target_sandbox_for(
                request.replay_point_id, request.target_branch_id
            )
            if not _daemon_health_verified(sb.processes, sb.daemon_health):
                return RestoreReceipt(
                    achieved_fidelity=ReplayFidelity.BEST_EFFORT,
                    restored_projection_digest=receipt.restored_projection_digest,
                    prefix_reused=receipt.prefix_reused,
                )
            return receipt

    dg_provider = _DaemonDroppingSpiProvider()
    sb_dg = dg_provider.create_sandbox(
        sandbox_id="sb-dg",
        rootfs={"src/app.py": "v1"},
        env={"MODE": "v1"},
        processes={501: "lsp-daemon"},
        daemon_health={"lsp-daemon": True},
    )
    dg_provider.pause("sb-dg")
    dg_adapter = _DowngradingDaemonAdapter(
        dg_provider, source_sandbox_id="sb-dg", target_tier="process_tree_quiesced"
    )
    dg_reg = ReplayPointRegistry()
    dg_store = SpiAgentStateStore()
    trace_dg, _ = _prepare_trace_point(
        "trace-quiesced-dg", registry=dg_reg, store=dg_store, adapter=dg_adapter
    )
    dg_provider.resume("sb-dg")
    sb_dg.processes = {}
    sb_dg.daemon_health = {}
    res_dg = run_counterfactual_suffix_replay(
        dg_reg,
        trace=trace_dg,
        hypothesis=_sem_hyp("trace-quiesced-dg"),
        candidate=CounterfactualCandidate("downgraded-daemon", {"op": "cold"}),
        execute_suffix=lambda _req: {"fix": True, "guard": True},
    )
    assert res_dg.decision == CounterfactualDecision.OBSERVED_ONLY
    _ = point_ok


def test_process_tree_quiesced_promotes_healthy_cold_restart_with_changed_pid() -> (
    None
):
    """Healthy cold daemon restart (PID 42 -> 77, same service/health) must promote under SEMANTIC_EQUIVALENT while EXACT stays strict."""

    class _ColdRestartDaemonSpiProvider(InMemorySandboxSpiProvider):
        def fork(
            self,
            snapshot_id: str,
            *,
            child_id: str,
            target_branch_id: str | None = None,
            egress_policy_id: str = "isolated-offline",
        ) -> tuple[SandboxInstanceState, str]:
            child, isolation_id = super().fork(
                snapshot_id,
                child_id=child_id,
                target_branch_id=target_branch_id,
                egress_policy_id=egress_policy_id,
            )
            child.processes = {77: "lsp-daemon"}
            child.daemon_health = {"lsp-daemon": True, "port:8443": True}
            return child, isolation_id

    cold_provider = _ColdRestartDaemonSpiProvider()
    sb = cold_provider.create_sandbox(
        sandbox_id="sb-cold",
        rootfs={"src/app.py": "v1-cold"},
        env={"MODE": "quiesced"},
        processes={42: "lsp-daemon"},
        daemon_health={"lsp-daemon": True, "port:8443": True},
        memory_pages={"ephemeral_ram": "ignored-by-quiesced"},
    )
    cold_provider.pause("sb-cold")
    adapter = SpiReplayAdapter(
        cold_provider,
        source_sandbox_id="sb-cold",
        target_tier="process_tree_quiesced",
    )
    reg = ReplayPointRegistry()
    store = SpiAgentStateStore()
    trace, _ = _prepare_trace_point(
        "trace-quiesced-cold", registry=reg, store=store, adapter=adapter
    )
    cold_provider.resume("sb-cold")
    sb.rootfs["src/app.py"] = "v2-drifted"
    sb.processes = {}
    sb.daemon_health = {}

    hyp = CounterfactualHypothesis(
        hypothesis_id="hyp-cold-restart",
        first_affected_event_id="trace-quiesced-cold-evt-2-fail",
        first_affected_sequence=2,
        fix_set=(ValidationTarget("fix", baseline_passed=False),),
        guard_set=(ValidationTarget("guard", baseline_passed=True),),
        minimum_fidelity=ReplayFidelity.SEMANTIC_EQUIVALENT,
    )
    observed_pids: list[dict[int, str]] = []

    def _check_cold(req: Any) -> Mapping[str, bool]:
        cur = adapter.target_sandbox_for_request(req)
        observed_pids.append(dict(cur.processes))
        assert cold_provider.sandboxes["sb-cold"].rootfs["src/app.py"] == "v2-drifted"
        return {
            "fix": cur.rootfs["src/app.py"] == "v1-cold",
            "guard": cur.processes == {77: "lsp-daemon"}
            and cur.daemon_health.get("port:8443") is True,
        }

    promoted = run_counterfactual_suffix_replay(
        reg,
        trace=trace,
        hypothesis=hyp,
        candidate=CounterfactualCandidate("cold-pid-77", {"op": "restart"}),
        execute_suffix=_check_cold,
    )
    assert promoted.decision == CounterfactualDecision.PROMOTED
    assert observed_pids == [{77: "lsp-daemon"}]

    child_group = run_counterfactual_child_group(
        reg,
        trace=trace,
        hypothesis=hyp,
        candidates=(
            CounterfactualChildCandidate(
                candidate=CounterfactualCandidate("child-cold-pid-77", {"op": "fork"}),
                target_branch_id="branch-cold-pid-77",
                execute_suffix=lambda ctx, _req: {
                    "fix": True,
                    "guard": (
                        isinstance(ctx.adapter_child_key, _BranchRef)
                        and cold_provider.sandboxes[
                            ctx.adapter_child_key.branch_sandbox_id
                        ].processes
                        == {77: "lsp-daemon"}
                    ),
                },
            ),
        ),
    )
    assert child_group.candidates[0].replay_result is not None
    assert (
        child_group.candidates[0].replay_result.decision
        == CounterfactualDecision.PROMOTED
    )

    # Exact memory tier still refuses changed PID identity (42 -> 77).
    exact_provider = _ColdRestartDaemonSpiProvider()
    sb_exact = exact_provider.create_sandbox(
        sandbox_id="sb-exact-pid",
        rootfs={"src/app.py": "v1"},
        env={"MODE": "exact"},
        processes={42: "lsp-daemon"},
        daemon_health={"lsp-daemon": True, "port:8443": True},
        memory_pages={"heap": "warm-pages"},
    )
    exact_provider.pause("sb-exact-pid")
    exact_reg = ReplayPointRegistry()
    exact_store = SpiAgentStateStore()
    trace_exact, _ = _prepare_trace_point(
        "trace-exact-pid",
        registry=exact_reg,
        store=exact_store,
        adapter=SpiReplayAdapter(
            exact_provider,
            source_sandbox_id="sb-exact-pid",
            target_tier="microvm_memory_exact",
        ),
    )
    exact_provider.resume("sb-exact-pid")
    sb_exact.processes = {42: "lsp-daemon"}
    exact_suffix_called = False

    def _never_exact(_req: Any) -> Mapping[str, bool]:
        nonlocal exact_suffix_called
        exact_suffix_called = True
        return {"fix": True, "guard": True}

    with pytest.raises(ValueError, match="exact replay requires equal"):
        run_counterfactual_suffix_replay(
            exact_reg,
            trace=trace_exact,
            hypothesis=CounterfactualHypothesis(
                hypothesis_id="hyp-exact-pid",
                first_affected_event_id="trace-exact-pid-evt-2-fail",
                first_affected_sequence=2,
                fix_set=(ValidationTarget("fix", baseline_passed=False),),
                guard_set=(ValidationTarget("guard", baseline_passed=True),),
                minimum_fidelity=ReplayFidelity.EXACT,
            ),
            candidate=CounterfactualCandidate("exact-pid-change", {"op": "restart"}),
            execute_suffix=_never_exact,
        )
    assert exact_suffix_called is False


def test_microvm_memory_exact_requires_independent_memory_pages_capture() -> None:
    """Capturing or validating EXACT with workspace+processes but zero memory_pages must fail or downgrade."""
    # 1) Reference provider refuses microvm_memory_exact snapshot with empty memory_pages.
    strict_provider = InMemorySandboxSpiProvider()
    strict_provider.create_sandbox(
        sandbox_id="sb-no-pages",
        rootfs={"src/app.py": "v1"},
        env={"MODE": "exact"},
        processes={42: "lsp-daemon"},
        memory_pages={},
    )
    strict_provider.pause("sb-no-pages")
    with pytest.raises(SandboxSpiError, match="memory_capture_required"):
        strict_provider.snapshot("sb-no-pages", target_tier="microvm_memory_exact")

    # 2) Even if a provider returns an empty-memory descriptor claiming microvm_memory_exact,
    # adapter capture truthfully downgrades below EXACT and equivalence refuses EXACT promotion.
    class _ZeroPageExactProvider(InMemorySandboxSpiProvider):
        def snapshot(
            self,
            sandbox_id: str,
            *,
            target_tier: str,
            scrub_keys: tuple[str, ...] = (
                "LOOPX_LEASE_KEY",
                "LOOPX_CLAIM_OP_ID",
                "CLOUD_API_TOKEN",
            ),
        ) -> SandboxSnapshotDescriptor:
            sb = self.sandboxes[sandbox_id]
            if target_tier == "microvm_memory_exact" and not sb.memory_pages:
                self._seq += 1
                snap_id = f"snap-{self._seq:04d}"
                processes_copy = dict(sb.processes)
                daemon_health_copy = dict(sb.daemon_health)
                scrubbed_env = {k: v for k, v in sb.env.items() if k not in scrub_keys}
                scrubbed_rootfs = dict(sb.rootfs)
                symlinks_copy = dict(sb.symlinks)
                digest = _digest_state(
                    rootfs=scrubbed_rootfs,
                    symlinks=symlinks_copy,
                    env=scrubbed_env,
                    processes=processes_copy,
                    memory_pages={},
                    daemon_health=daemon_health_copy,
                    include_env=True,
                    include_daemons=False,
                    include_memory=True,
                )
                owner_holder = f"holder-{snap_id}-parent"
                sealed = _SealedSnapshot(
                    snapshot_id=snap_id,
                    parent_sandbox_id=sandbox_id,
                    fidelity_tier=target_tier,
                    rootfs=scrubbed_rootfs,
                    symlinks=symlinks_copy,
                    env=scrubbed_env,
                    processes=processes_copy,
                    memory_pages={},
                    daemon_health=daemon_health_copy,
                    scrubbed_keys=(),
                    rootfs_digest=_digest_rootfs(scrubbed_rootfs, symlinks_copy),
                    env_manifest_digest=_digest_env(scrubbed_env),
                    daemon_health_digest=None,
                    memory_digest=None,
                    digest=digest,
                    refcount=1,
                    owner_holder_id=owner_holder,
                    held_refs=(owner_holder,),
                )
                self._sealed_snapshots[snap_id] = sealed
                desc = _descriptor_from_sealed(sealed)
                self.snapshots[snap_id] = desc
                return desc
            return super().snapshot(
                sandbox_id, target_tier=target_tier, scrub_keys=scrub_keys
            )

    zero_provider = _ZeroPageExactProvider()
    zero_provider.create_sandbox(
        sandbox_id="sb-zero-page",
        rootfs={"src/app.py": "v1"},
        env={"MODE": "exact"},
        processes={42: "lsp-daemon"},
        daemon_health={"lsp-daemon": True},
        memory_pages={},
    )
    zero_provider.pause("sb-zero-page")
    zero_adapter = SpiReplayAdapter(
        zero_provider,
        source_sandbox_id="sb-zero-page",
        target_tier="microvm_memory_exact",
    )
    zero_reg = ReplayPointRegistry()
    zero_store = SpiAgentStateStore()
    trace_zero, pt_zero = _prepare_trace_point(
        "trace-zero-pages",
        registry=zero_reg,
        store=zero_store,
        adapter=zero_adapter,
    )
    assert pt_zero.fidelity != ReplayFidelity.EXACT
    res_downgraded = run_counterfactual_suffix_replay(
        zero_reg,
        trace=trace_zero,
        hypothesis=CounterfactualHypothesis(
            hypothesis_id="hyp-zero-pages",
            first_affected_event_id="trace-zero-pages-evt-2-fail",
            first_affected_sequence=2,
            fix_set=(ValidationTarget("fix", baseline_passed=False),),
            guard_set=(ValidationTarget("guard", baseline_passed=True),),
            minimum_fidelity=ReplayFidelity.EXACT,
        ),
        candidate=CounterfactualCandidate("zero-pages", {"op": "noop"}),
        execute_suffix=lambda _req: {"fix": True, "guard": True},
    )
    assert res_downgraded.decision == CounterfactualDecision.OBSERVED_ONLY

    # Direct equivalence verification against a zero-page sealed snapshot also fails closed.
    eq_report = zero_adapter.validate_replay_equivalence(
        _SnapshotRef(snapshot_id=next(iter(zero_provider.snapshots))),
        RestoreReceipt(
            achieved_fidelity=ReplayFidelity.EXACT,
            restored_projection_digest=None,
        ),
    )
    assert eq_report.equivalent is False
    assert any(
        c.check_id == "sandbox-process-memory" and not c.passed
        for c in eq_report.checks
    )
    # And forcing an EXACT lease onto a zero-page snapshot still refuses promotion.
    forced_reg = ReplayPointRegistry()

    class _ForcedExactAdapter(SpiReplayAdapter):
        def capture_replay_state(
            self, request: CaptureReplayStateRequest
        ) -> AdapterStateLease:
            lease = super().capture_replay_state(request)
            return AdapterStateLease(
                binding_key=lease.binding_key,
                fidelity=ReplayFidelity.EXACT,
                projection_digest=lease.projection_digest,
            )

        def restore_replay_state(
            self, binding_key: object, request: RestoreReplayStateRequest
        ) -> RestoreReceipt:
            receipt = super().restore_replay_state(binding_key, request)
            return RestoreReceipt(
                achieved_fidelity=ReplayFidelity.EXACT,
                restored_projection_digest=receipt.restored_projection_digest,
                prefix_reused=receipt.prefix_reused,
            )

    forced_trace, _ = _prepare_trace_point(
        "trace-forced-zero",
        registry=forced_reg,
        store=zero_store,
        adapter=_ForcedExactAdapter(
            zero_provider,
            source_sandbox_id="sb-zero-page",
            target_tier="microvm_memory_exact",
        ),
    )
    forced_called = False

    def _never_forced(_req: Any) -> Mapping[str, bool]:
        nonlocal forced_called
        forced_called = True
        return {"fix": True, "guard": True}

    with pytest.raises(ValueError, match="exact replay requires equal"):
        run_counterfactual_suffix_replay(
            forced_reg,
            trace=forced_trace,
            hypothesis=CounterfactualHypothesis(
                hypothesis_id="hyp-forced-zero",
                first_affected_event_id="trace-forced-zero-evt-2-fail",
                first_affected_sequence=2,
                fix_set=(ValidationTarget("fix", baseline_passed=False),),
                guard_set=(ValidationTarget("guard", baseline_passed=True),),
                minimum_fidelity=ReplayFidelity.EXACT,
            ),
            candidate=CounterfactualCandidate("forced-zero", {"op": "noop"}),
            execute_suffix=_never_forced,
        )
    assert forced_called is False


def test_single_branch_replay_forks_isolated_target_without_overwriting_live_source() -> (
    None
):
    """Single-branch suffix replay isolates onto an SPI fork while keeping live source progress untouched."""

    # 1) Live source advances 1 -> 2; single-branch replay observes 1 in target and preserves 2 in source.
    provider = InMemorySandboxSpiProvider()
    live_sb = provider.create_sandbox(
        sandbox_id="sb-live-progress",
        rootfs={"src/state.txt": "1"},
        symlinks={"bin/cur": "v1"},
        env={"REV": "1"},
        processes={42: "worker-v1"},
        daemon_health={"worker-v1": True},
        memory_pages={"state_mem": "1"},
    )
    provider.pause("sb-live-progress")
    adapter = SpiReplayAdapter(
        provider,
        source_sandbox_id="sb-live-progress",
        target_tier="microvm_memory_exact",
    )
    registry = ReplayPointRegistry()
    store = SpiAgentStateStore()
    trace, point = _prepare_trace_point(
        "trace-single-iso", registry=registry, store=store, adapter=adapter
    )
    provider.resume("sb-live-progress")
    live_sb.rootfs["src/state.txt"] = "2"
    live_sb.symlinks["bin/cur"] = "v2"
    live_sb.env["REV"] = "2"
    live_sb.memory_pages["state_mem"] = "2"

    hyp = CounterfactualHypothesis(
        hypothesis_id="hyp-single-iso",
        first_affected_event_id="trace-single-iso-evt-2-fail",
        first_affected_sequence=2,
        fix_set=(ValidationTarget("fix", baseline_passed=False),),
        guard_set=(ValidationTarget("guard", baseline_passed=True),),
        minimum_fidelity=ReplayFidelity.EXACT,
    )
    observed: list[tuple[str, str, str, str, str]] = []

    def check_isolated(req: Any) -> Mapping[str, bool]:
        target_sb = adapter.target_sandbox_for_request(req)
        observed.append(
            (
                target_sb.sandbox_id,
                target_sb.rootfs["src/state.txt"],
                target_sb.memory_pages["state_mem"],
                live_sb.rootfs["src/state.txt"],
                live_sb.memory_pages["state_mem"],
            )
        )
        assert target_sb.sandbox_id != "sb-live-progress"
        return {
            "fix": target_sb.rootfs["src/state.txt"] == "1",
            "guard": target_sb.memory_pages["state_mem"] == "1"
            and live_sb.rootfs["src/state.txt"] == "2"
            and live_sb.memory_pages["state_mem"] == "2",
        }

    result = run_counterfactual_suffix_replay(
        registry,
        trace=trace,
        hypothesis=hyp,
        candidate=CounterfactualCandidate("iso-branch-1", {"branch": "single"}),
        execute_suffix=check_isolated,
    )
    assert result.decision == CounterfactualDecision.PROMOTED
    assert len(observed) == 1
    assert observed[0][1:] == ("1", "1", "2", "2")
    assert live_sb.rootfs["src/state.txt"] == "2"
    assert live_sb.memory_pages["state_mem"] == "2"
    assert len(adapter.last_target_sandboxes) == 1
    target_ref = adapter.last_target_sandboxes[0]
    assert provider.sandboxes[target_ref.branch_sandbox_id].status == "destroyed"
    assert provider.snapshots[next(iter(provider.snapshots))].refcount == 1
    public_blob = json.dumps(registry.records()) + json.dumps(result.to_record())
    assert "sb-fork-" not in public_blob
    assert "sb-live-progress" not in public_blob
    assert "holder-" not in public_blob

    # 2) Pure RFC SPI-only wrapper exposing strictly pause/resume/snapshot/fork/destroy (1-arg, no holder_id/restore_snapshot) succeeds.
    inner = InMemorySandboxSpiProvider()
    inner_sb = inner.create_sandbox(
        sandbox_id="sb-spi-only",
        rootfs={"src/state.txt": "1"},
        env={"REV": "1"},
        processes={91: "worker-spi"},
        daemon_health={"worker-spi": True},
        memory_pages={"state_mem": "1"},
    )
    inner.pause("sb-spi-only")

    class _SpiOnlyProviderWrapper:
        """Exposes strictly the 5 documented RFC SPI primitives and nothing else."""

        def pause(self, sandbox_id: str) -> SandboxPauseReceipt:
            return inner.pause(sandbox_id)

        def resume(self, sandbox_id: str) -> SandboxInstanceState:
            return inner.resume(sandbox_id)

        def snapshot(
            self,
            sandbox_id: str,
            *,
            target_tier: str,
            scrub_keys: tuple[str, ...] = (
                "LOOPX_LEASE_KEY",
                "LOOPX_CLAIM_OP_ID",
                "CLOUD_API_TOKEN",
            ),
        ) -> SandboxSnapshotDescriptor:
            return inner.snapshot(
                sandbox_id, target_tier=target_tier, scrub_keys=scrub_keys
            )

        def fork(
            self,
            snapshot_id: str,
            *,
            child_id: str,
            egress_policy_id: str = "isolated-offline",
        ) -> tuple[SandboxInstanceState, str]:
            return inner.fork(
                snapshot_id,
                child_id=child_id,
                egress_policy_id=egress_policy_id,
            )

        def destroy(self, sandbox_or_snapshot_id: str) -> None:
            inner.destroy(sandbox_or_snapshot_id)

    wrapper = _SpiOnlyProviderWrapper()
    assert not hasattr(wrapper, "restore_snapshot")
    assert not hasattr(wrapper, "sealed_snapshot")
    assert not hasattr(wrapper, "sandboxes")
    spi_adapter = SpiReplayAdapter(
        wrapper,
        source_sandbox_id="sb-spi-only",
        target_tier="microvm_memory_exact",
    )
    spi_reg = ReplayPointRegistry()
    spi_store = SpiAgentStateStore()
    spi_trace, spi_point = _prepare_trace_point(
        "trace-spi-only", registry=spi_reg, store=spi_store, adapter=spi_adapter
    )
    wrapper.resume("sb-spi-only")
    inner_sb.rootfs["src/state.txt"] = "2"
    inner_sb.memory_pages["state_mem"] = "2"

    spi_res = run_counterfactual_suffix_replay(
        spi_reg,
        trace=spi_trace,
        hypothesis=CounterfactualHypothesis(
            hypothesis_id="hyp-spi-only",
            first_affected_event_id="trace-spi-only-evt-2-fail",
            first_affected_sequence=2,
            fix_set=(ValidationTarget("fix", baseline_passed=False),),
            guard_set=(ValidationTarget("guard", baseline_passed=True),),
            minimum_fidelity=ReplayFidelity.EXACT,
        ),
        candidate=CounterfactualCandidate("spi-only-target", {"op": "fork"}),
        execute_suffix=lambda req: {
            "fix": spi_adapter.target_sandbox_for_request(req).rootfs["src/state.txt"]
            == "1",
            "guard": spi_adapter.target_sandbox_for_request(req).memory_pages[
                "state_mem"
            ]
            == "1"
            and inner_sb.rootfs["src/state.txt"] == "2",
        },
    )
    assert spi_res.decision == CounterfactualDecision.PROMOTED
    assert inner_sb.rootfs["src/state.txt"] == "2"
    spi_reg.release(spi_point.replay_point_id)
    assert not inner.snapshots

    # 3) A provider missing SPI `fork` fails with typed SandboxSpiError (never raw AttributeError).
    class _NoForkProvider:
        def pause(self, sandbox_id: str) -> SandboxPauseReceipt:
            return inner.pause(sandbox_id)

        def resume(self, sandbox_id: str) -> SandboxInstanceState:
            return inner.resume(sandbox_id)

        def snapshot(
            self,
            sandbox_id: str,
            *,
            target_tier: str,
            scrub_keys: tuple[str, ...] = (
                "LOOPX_LEASE_KEY",
                "LOOPX_CLAIM_OP_ID",
                "CLOUD_API_TOKEN",
            ),
        ) -> SandboxSnapshotDescriptor:
            return inner.snapshot(
                sandbox_id, target_tier=target_tier, scrub_keys=scrub_keys
            )

        def destroy(self, target_id: str) -> None:
            inner.destroy(target_id)

    no_fork_inner = InMemorySandboxSpiProvider()
    no_fork_inner.create_sandbox(
        sandbox_id="sb-nofork",
        rootfs={"a": "1"},
        memory_pages={"m": "1"},
    )
    no_fork_inner.pause("sb-nofork")

    class _WrappedNoFork(_NoForkProvider):
        def snapshot(
            self,
            sandbox_id: str,
            *,
            target_tier: str,
            scrub_keys: tuple[str, ...] = (
                "LOOPX_LEASE_KEY",
                "LOOPX_CLAIM_OP_ID",
                "CLOUD_API_TOKEN",
            ),
        ) -> SandboxSnapshotDescriptor:
            return no_fork_inner.snapshot(
                sandbox_id, target_tier=target_tier, scrub_keys=scrub_keys
            )

        def destroy(self, target_id: str) -> None:
            no_fork_inner.destroy(target_id)

    no_fork_adapter = SpiReplayAdapter(
        cast(SandboxSpiProviderProtocol, _WrappedNoFork()),
        source_sandbox_id="sb-nofork",
        target_tier="microvm_memory_exact",
    )
    no_fork_reg = ReplayPointRegistry()
    no_fork_trace, _ = _prepare_trace_point(
        "trace-no-fork",
        registry=no_fork_reg,
        store=SpiAgentStateStore(),
        adapter=no_fork_adapter,
    )
    with pytest.raises(SandboxSpiError, match="fork_unsupported"):
        run_counterfactual_suffix_replay(
            no_fork_reg,
            trace=no_fork_trace,
            hypothesis=CounterfactualHypothesis(
                hypothesis_id="hyp-no-fork",
                first_affected_event_id="trace-no-fork-evt-2-fail",
                first_affected_sequence=2,
                fix_set=(ValidationTarget("fix", baseline_passed=False),),
                guard_set=(ValidationTarget("guard", baseline_passed=True),),
                minimum_fidelity=ReplayFidelity.EXACT,
            ),
            candidate=CounterfactualCandidate("missing-fork", {"op": "none"}),
            execute_suffix=lambda _req: {"fix": True, "guard": True},
        )
    assert point.replay_point_id != spi_point.replay_point_id


def test_parent_release_retry_after_lost_ack_preserves_live_child_holder_reference() -> (
    None
):
    """Retrying ReplayPointRegistry.release after a lost ack must be idempotent and preserve running child holder refs."""

    def _run_lost_ack_probe(*, drop_holder_kwarg: bool) -> None:
        provider = InMemorySandboxSpiProvider()
        provider.create_sandbox(
            sandbox_id="sb-ack-parent",
            rootfs={"src/app.py": "v1"},
            env={"MODE": "exact"},
            processes={42: "lsp-v1"},
            daemon_health={"lsp-v1": True},
            memory_pages={"heap": "v1-heap"},
        )
        provider.pause("sb-ack-parent")
        adapter = SpiReplayAdapter(
            provider,
            source_sandbox_id="sb-ack-parent",
            target_tier="microvm_memory_exact",
        )
        registry = ReplayPointRegistry()
        store = SpiAgentStateStore()
        _, point = _prepare_trace_point(
            f"trace-ack-{int(drop_holder_kwarg)}",
            registry=registry,
            store=store,
            adapter=adapter,
        )
        snap_id = next(iter(provider.snapshots))
        assert provider.snapshots[snap_id].refcount == 1
        assert len(provider.snapshots[snap_id].active_holder_ids) == 1

        # Fork a live running child branch before parent releases.
        child_lease = adapter.fork_replay_child(
            _SnapshotRef(snapshot_id=snap_id),
            ForkReplayChildRequest(
                replay_point_id=point.replay_point_id,
                child_id="live-child",
                target_branch_id="branch-live-child",
                minimum_fidelity=ReplayFidelity.EXACT,
            ),
        )
        child_ref = cast(_BranchRef, child_lease.child_key)
        child_sb = provider.sandboxes[child_ref.branch_sandbox_id]
        assert child_sb.status == "running"
        snap_desc = provider.snapshots[snap_id]
        assert snap_desc.refcount == 2
        assert len(snap_desc.active_holder_ids) == 2

        orig_release = provider.release_snapshot
        lost_ack_injected = False

        if drop_holder_kwarg:

            def flaky_release_1arg(target_snap_id: str) -> None:
                nonlocal lost_ack_injected
                orig_release(target_snap_id)
                if not lost_ack_injected:
                    lost_ack_injected = True
                    raise TimeoutError("lost release ack after commit")

            setattr(provider, "release_snapshot", flaky_release_1arg)
        else:

            def flaky_release_holder(
                target_snap_id: str, *, holder_id: str | None = None
            ) -> None:
                nonlocal lost_ack_injected
                orig_release(target_snap_id, holder_id=holder_id)
                if not lost_ack_injected:
                    lost_ack_injected = True
                    raise TimeoutError("lost release ack after commit")

            setattr(provider, "release_snapshot", flaky_release_holder)

        # First parent release commits (2 -> 1) and loses ack before returning to ReplayPointRegistry.release.
        with pytest.raises(TimeoutError, match="lost release ack"):
            registry.release(point.replay_point_id)
        assert lost_ack_injected is True
        assert snap_id in provider.snapshots
        assert provider.snapshots[snap_id].refcount == 1
        assert provider.snapshots[snap_id].active_holder_ids == (child_ref.holder_id,)
        assert child_sb.status == "running"

        # Retry parent release on ReplayPointRegistry: must be idempotent and preserve child's holder reference.
        released_pt = registry.release(point.replay_point_id)
        assert released_pt.lifecycle.value == "released"
        assert snap_id in provider.snapshots
        assert provider.snapshots[snap_id].refcount == 1
        assert provider.snapshots[snap_id].active_holder_ids == (child_ref.holder_id,)
        assert child_sb.status == "running"
        assert provider.get_snapshot(snap_id).rootfs["src/app.py"] == "v1"
        assert (
            adapter.validate_replay_child_equivalence(
                child_lease.child_key, child_lease.receipt
            ).equivalent
            is True
        )

        # Only after the child branch terminates and releases its own holder reference is the snapshot reclaimed.
        adapter.release_replay_child(child_lease.child_key)
        assert child_sb.status == "destroyed"
        assert snap_id not in provider.snapshots

    _run_lost_ack_probe(drop_holder_kwarg=False)
    _run_lost_ack_probe(drop_holder_kwarg=True)


def test_single_branch_exact_equivalence_failure_destroys_isolated_fork_and_allows_retry() -> (
    None
):
    """Failed equivalence or post-fork restore must immediately clean up the temporary target fork."""

    class _TamperOnceForkSpiProvider(InMemorySandboxSpiProvider):
        def __init__(self) -> None:
            super().__init__()
            self.fork_calls = 0

        def fork(
            self,
            snapshot_id: str,
            *,
            child_id: str,
            target_branch_id: str | None = None,
            egress_policy_id: str = "isolated-offline",
        ) -> tuple[SandboxInstanceState, str]:
            self.fork_calls += 1
            child, isolation_id = super().fork(
                snapshot_id,
                child_id=child_id,
                target_branch_id=target_branch_id,
                egress_policy_id=egress_policy_id,
            )
            if self.fork_calls == 1:
                child.rootfs["src/state.txt"] = "tampered-branch"
            return child, isolation_id

    provider = _TamperOnceForkSpiProvider()
    live_sb = provider.create_sandbox(
        sandbox_id="sb-eq-cleanup",
        rootfs={"src/state.txt": "1"},
        symlinks={"bin/cur": "v1"},
        env={"REV": "1"},
        processes={42: "worker-v1"},
        daemon_health={"worker-v1": True},
        memory_pages={"state_mem": "1"},
    )
    provider.pause("sb-eq-cleanup")
    adapter = SpiReplayAdapter(
        provider,
        source_sandbox_id="sb-eq-cleanup",
        target_tier="microvm_memory_exact",
    )
    registry = ReplayPointRegistry()
    store = SpiAgentStateStore()
    trace, point = _prepare_trace_point(
        "trace-eq-cleanup", registry=registry, store=store, adapter=adapter
    )
    snap_id = next(iter(provider.snapshots))
    parent_holder_id = provider.snapshots[snap_id].owner_holder_id

    provider.resume("sb-eq-cleanup")
    live_sb.rootfs["src/state.txt"] = "2"
    live_sb.symlinks["bin/cur"] = "v2"
    live_sb.env["REV"] = "2"
    live_sb.memory_pages["state_mem"] = "2"

    hyp = CounterfactualHypothesis(
        hypothesis_id="hyp-eq-cleanup",
        first_affected_event_id="trace-eq-cleanup-evt-2-fail",
        first_affected_sequence=2,
        fix_set=(ValidationTarget("fix", baseline_passed=False),),
        guard_set=(ValidationTarget("guard", baseline_passed=True),),
        minimum_fidelity=ReplayFidelity.EXACT,
    )

    first_suffix_called = False

    def _never_execute(_req: CounterfactualReplayRequest) -> Mapping[str, bool]:
        nonlocal first_suffix_called
        first_suffix_called = True
        return {"fix": True, "guard": True}

    with pytest.raises(ValueError, match="exact replay requires equal"):
        run_counterfactual_suffix_replay(
            registry,
            trace=trace,
            hypothesis=hyp,
            candidate=CounterfactualCandidate("tampered-attempt", {"op": "retry"}),
            execute_suffix=_never_execute,
        )

    assert first_suffix_called is False
    assert len(adapter.last_target_sandboxes) == 1
    leaked_candidate = adapter.last_target_sandboxes[0]
    assert provider.sandboxes[leaked_candidate.branch_sandbox_id].status == "destroyed"
    assert provider.snapshots[snap_id].refcount == 1
    assert provider.snapshots[snap_id].active_holder_ids == (parent_holder_id,)
    assert live_sb.rootfs["src/state.txt"] == "2"
    assert live_sb.memory_pages["state_mem"] == "2"
    assert registry.get(point.replay_point_id).lifecycle.value == "ready"
    eq_metrics = registry.metrics()
    assert eq_metrics["equivalence_failure_count"] == 1
    assert eq_metrics["suffix_finalization_attempt_count"] == 1
    assert eq_metrics["suffix_finalization_success_count"] == 1
    assert eq_metrics["suffix_failure_count"] == 0
    assert eq_metrics["replay_point_quarantine_count"] == 0
    assert eq_metrics["replay_failure_count"] == 1

    # Retrying the same READY ReplayPoint once the fork is untampered succeeds cleanly.
    retry_result = run_counterfactual_suffix_replay(
        registry,
        trace=trace,
        hypothesis=hyp,
        candidate=CounterfactualCandidate("clean-retry", {"op": "retry"}),
        execute_suffix=lambda req: {
            "fix": adapter.target_sandbox_for_request(req).rootfs["src/state.txt"]
            == "1",
            "guard": adapter.target_sandbox_for_request(req).memory_pages["state_mem"]
            == "1"
            and live_sb.rootfs["src/state.txt"] == "2",
        },
    )
    assert retry_result.decision == CounterfactualDecision.PROMOTED
    assert len(adapter.last_target_sandboxes) == 2
    assert (
        provider.sandboxes[adapter.last_target_sandboxes[1].branch_sandbox_id].status
        == "destroyed"
    )
    assert provider.snapshots[snap_id].refcount == 1
    assert live_sb.rootfs["src/state.txt"] == "2"

    # A post-fork failure inside restore_replay_state also triggers immediate cleanup without quarantine.
    class _FailAfterForkAdapter(SpiReplayAdapter):
        def __init__(
            self,
            spi_provider: InMemorySandboxSpiProvider,
            *,
            source_sandbox_id: str,
            target_tier: str,
        ) -> None:
            super().__init__(
                spi_provider,
                source_sandbox_id=source_sandbox_id,
                target_tier=target_tier,
            )
            self.fail_next_restore = True

        def restore_replay_state(
            self,
            binding_key: object,
            request: RestoreReplayStateRequest,
        ) -> RestoreReceipt:
            receipt = super().restore_replay_state(binding_key, request)
            if self.fail_next_restore:
                self.fail_next_restore = False
                raise RuntimeError("post-fork restore setup failed")
            return receipt

    restore_provider = InMemorySandboxSpiProvider()
    restore_sb = restore_provider.create_sandbox(
        sandbox_id="sb-restore-cleanup",
        rootfs={"src/state.txt": "1"},
        symlinks={"bin/cur": "v1"},
        env={"REV": "1"},
        processes={43: "worker-v1"},
        daemon_health={"worker-v1": True},
        memory_pages={"state_mem": "1"},
    )
    restore_provider.pause("sb-restore-cleanup")
    restore_adapter = _FailAfterForkAdapter(
        restore_provider,
        source_sandbox_id="sb-restore-cleanup",
        target_tier="microvm_memory_exact",
    )
    restore_reg = ReplayPointRegistry()
    restore_trace, restore_pt = _prepare_trace_point(
        "trace-restore-cleanup",
        registry=restore_reg,
        store=SpiAgentStateStore(),
        adapter=restore_adapter,
    )
    restore_snap_id = next(iter(restore_provider.snapshots))
    restore_provider.resume("sb-restore-cleanup")
    restore_sb.rootfs["src/state.txt"] = "2"

    restore_hyp = CounterfactualHypothesis(
        hypothesis_id="hyp-restore-cleanup",
        first_affected_event_id="trace-restore-cleanup-evt-2-fail",
        first_affected_sequence=2,
        fix_set=(ValidationTarget("fix", baseline_passed=False),),
        guard_set=(ValidationTarget("guard", baseline_passed=True),),
        minimum_fidelity=ReplayFidelity.EXACT,
    )
    with pytest.raises(RuntimeError, match="post-fork restore setup failed"):
        run_counterfactual_suffix_replay(
            restore_reg,
            trace=restore_trace,
            hypothesis=restore_hyp,
            candidate=CounterfactualCandidate("post-fork-fail", {"op": "noop"}),
            execute_suffix=_never_execute,
        )
    assert len(restore_adapter.last_target_sandboxes) == 1
    assert (
        restore_provider.sandboxes[
            restore_adapter.last_target_sandboxes[0].branch_sandbox_id
        ].status
        == "destroyed"
    )
    assert restore_provider.snapshots[restore_snap_id].refcount == 1
    assert restore_reg.get(restore_pt.replay_point_id).lifecycle.value == "ready"
    assert restore_reg.metrics()["restore_failure_count"] == 1
    assert restore_reg.metrics()["replay_point_quarantine_count"] == 0

    released_restore = restore_reg.release(restore_pt.replay_point_id)
    assert released_restore.lifecycle.value == "released"
    assert restore_snap_id not in restore_provider.snapshots
    released_eq = registry.release(point.replay_point_id)
    assert released_eq.lifecycle.value == "released"
    assert snap_id not in provider.snapshots


def test_finalize_child_ref_release_failure_retries_via_public_release_and_reclaims_snapshot() -> (
    None
):
    """Pre-commit child holder release failure during finalize keeps child binding so ReplayPoint.release retries cleanly."""

    class _PreCommitChildReleaseFailureProvider(InMemorySandboxSpiProvider):
        def __init__(self) -> None:
            super().__init__()
            self.destroy_calls: list[str] = []
            self.child_release_attempts: list[tuple[str, str | None]] = []

        def destroy_sandbox(self, sandbox_id: str) -> None:
            self.destroy_calls.append(sandbox_id)
            super().destroy_sandbox(sandbox_id)

        def release_snapshot(
            self,
            snapshot_id: str,
            *,
            holder_id: str | None = None,
        ) -> None:
            sealed = self._sealed_snapshots.get(snapshot_id)
            if (
                holder_id is not None
                and sealed is not None
                and holder_id != sealed.owner_holder_id
            ):
                self.child_release_attempts.append((snapshot_id, holder_id))
                if len(self.child_release_attempts) == 1:
                    raise RuntimeError("transient pre-commit child ref release failure")
            super().release_snapshot(snapshot_id, holder_id=holder_id)

    provider = _PreCommitChildReleaseFailureProvider()
    live_sb = provider.create_sandbox(
        sandbox_id="sb-child-release-retry",
        rootfs={"src/state.txt": "1"},
        symlinks={"bin/cur": "v1"},
        env={"REV": "1"},
        processes={44: "worker-v1"},
        daemon_health={"worker-v1": True},
        memory_pages={"state_mem": "1"},
    )
    provider.pause("sb-child-release-retry")
    adapter = SpiReplayAdapter(
        provider,
        source_sandbox_id="sb-child-release-retry",
        target_tier="microvm_memory_exact",
    )
    registry = ReplayPointRegistry()
    store = SpiAgentStateStore()
    trace, point = _prepare_trace_point(
        "trace-child-release-retry", registry=registry, store=store, adapter=adapter
    )
    snap_id = next(iter(provider.snapshots))
    parent_holder_id = provider.snapshots[snap_id].owner_holder_id

    provider.resume("sb-child-release-retry")
    live_sb.rootfs["src/state.txt"] = "2"

    hyp = CounterfactualHypothesis(
        hypothesis_id="hyp-child-release-retry",
        first_affected_event_id="trace-child-release-retry-evt-2-fail",
        first_affected_sequence=2,
        fix_set=(ValidationTarget("fix", baseline_passed=False),),
        guard_set=(ValidationTarget("guard", baseline_passed=True),),
        minimum_fidelity=ReplayFidelity.EXACT,
    )

    with pytest.raises(
        RuntimeError, match="transient pre-commit child ref release failure"
    ):
        run_counterfactual_suffix_replay(
            registry,
            trace=trace,
            hypothesis=hyp,
            candidate=CounterfactualCandidate("fail-child-ref-release", {"op": "ok"}),
            execute_suffix=lambda req: {
                "fix": adapter.target_sandbox_for_request(req).rootfs["src/state.txt"]
                == "1",
                "guard": True,
            },
        )

    assert len(adapter.last_target_sandboxes) == 1
    target_ref = adapter.last_target_sandboxes[0]
    assert provider.sandboxes[target_ref.branch_sandbox_id].status == "destroyed"
    assert provider.destroy_calls == [target_ref.branch_sandbox_id]
    assert provider.child_release_attempts == [(snap_id, target_ref.holder_id)]
    assert snap_id in provider.snapshots
    assert provider.snapshots[snap_id].refcount == 2
    assert set(provider.snapshots[snap_id].active_holder_ids) == {
        parent_holder_id,
        target_ref.holder_id,
    }
    assert registry.get(point.replay_point_id).lifecycle.value == "failed"
    metrics_after_fail = registry.metrics()
    assert metrics_after_fail["suffix_finalization_failure_count"] == 1
    assert metrics_after_fail["replay_point_quarantine_count"] == 1

    # Public ReplayPoint.release retries the child holder cleanup cleanly without double-destroying
    # the already-destroyed target sandbox, then releases the parent holder and reclaims snapshot.
    released_second = registry.release(point.replay_point_id)
    assert released_second.lifecycle.value == "released"
    assert provider.destroy_calls == [target_ref.branch_sandbox_id]
    assert provider.child_release_attempts == [
        (snap_id, target_ref.holder_id),
        (snap_id, target_ref.holder_id),
    ]
    assert snap_id not in provider.snapshots
    assert registry.metrics()["release_success_count"] == 1

    # Subsequent release call is an idempotent no-op returning released.
    released_third = registry.release(point.replay_point_id)
    assert released_third.lifecycle.value == "released"
    assert provider.destroy_calls == [target_ref.branch_sandbox_id]
    assert len(provider.child_release_attempts) == 2
    assert snap_id not in provider.snapshots


@pytest.mark.parametrize("interrupt_stage", ["restore_agent_state", "execute"])
def test_base_exception_with_child_ref_release_failure_quarantines_and_releases_holders(
    interrupt_stage: str,
) -> None:
    """KeyboardInterrupt paired with failing finalizer quarantines FAILED with cause and still lets release reclaim holders."""

    class _ChildReleaseFailureProvider(InMemorySandboxSpiProvider):
        def __init__(self) -> None:
            super().__init__()
            self.destroy_calls: list[str] = []
            self.child_release_attempts: list[tuple[str, str | None]] = []

        def destroy_sandbox(self, sandbox_id: str) -> None:
            self.destroy_calls.append(sandbox_id)
            super().destroy_sandbox(sandbox_id)

        def release_snapshot(
            self,
            snapshot_id: str,
            *,
            holder_id: str | None = None,
        ) -> None:
            sealed = self._sealed_snapshots.get(snapshot_id)
            if (
                holder_id is not None
                and sealed is not None
                and holder_id != sealed.owner_holder_id
            ):
                self.child_release_attempts.append((snapshot_id, holder_id))
                if len(self.child_release_attempts) == 1:
                    raise RuntimeError("child ref release failed during unwind")
            super().release_snapshot(snapshot_id, holder_id=holder_id)

    provider = _ChildReleaseFailureProvider()
    live_sb = provider.create_sandbox(
        sandbox_id=f"sb-interrupt-{interrupt_stage}",
        rootfs={"src/state.txt": "1"},
        symlinks={"bin/cur": "v1"},
        env={"REV": "1"},
        processes={45: "worker-v1"},
        daemon_health={"worker-v1": True},
        memory_pages={"state_mem": "1"},
    )
    provider.pause(f"sb-interrupt-{interrupt_stage}")
    adapter = SpiReplayAdapter(
        provider,
        source_sandbox_id=f"sb-interrupt-{interrupt_stage}",
        target_tier="microvm_memory_exact",
    )
    registry = ReplayPointRegistry()
    store = SpiAgentStateStore()
    trace, point = _prepare_trace_point(
        f"trace-interrupt-{interrupt_stage}",
        registry=registry,
        store=store,
        adapter=adapter,
    )
    snap_id = next(iter(provider.snapshots))
    parent_holder_id = provider.snapshots[snap_id].owner_holder_id
    provider.resume(f"sb-interrupt-{interrupt_stage}")
    live_sb.rootfs["src/state.txt"] = "2"

    if interrupt_stage == "restore_agent_state":

        def _interrupt_restore(_state_key: object) -> None:
            raise KeyboardInterrupt("aborted SPI agent restore")

        setattr(store, "restore_agent_state", _interrupt_restore)

    hyp = CounterfactualHypothesis(
        hypothesis_id=f"hyp-interrupt-{interrupt_stage}",
        first_affected_event_id=f"trace-interrupt-{interrupt_stage}-evt-2-fail",
        first_affected_sequence=2,
        fix_set=(ValidationTarget("fix", baseline_passed=False),),
        guard_set=(ValidationTarget("guard", baseline_passed=True),),
        minimum_fidelity=ReplayFidelity.EXACT,
    )

    def _suffix(req: CounterfactualReplayRequest) -> dict[str, bool]:
        if interrupt_stage == "execute":
            raise KeyboardInterrupt("aborted SPI suffix execute")
        return {
            "fix": adapter.target_sandbox_for_request(req).rootfs["src/state.txt"] == "1",
            "guard": True,
        }

    with pytest.raises(KeyboardInterrupt, match="aborted SPI") as exc_info:
        run_counterfactual_suffix_replay(
            registry,
            trace=trace,
            hypothesis=hyp,
            candidate=CounterfactualCandidate(f"interrupt-{interrupt_stage}", {"op": "ok"}),
            execute_suffix=_suffix,
        )

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert "child ref release failed during unwind" in str(exc_info.value.__cause__)
    assert len(adapter.last_target_sandboxes) == 1
    target_ref = adapter.last_target_sandboxes[0]
    assert provider.sandboxes[target_ref.branch_sandbox_id].status == "destroyed"
    assert provider.destroy_calls == [target_ref.branch_sandbox_id]
    assert provider.child_release_attempts == [(snap_id, target_ref.holder_id)]
    assert snap_id in provider.snapshots
    assert provider.snapshots[snap_id].refcount == 2
    assert set(provider.snapshots[snap_id].active_holder_ids) == {
        parent_holder_id,
        target_ref.holder_id,
    }
    assert registry.get(point.replay_point_id).lifecycle.value == "failed"
    metrics = registry.metrics()
    assert metrics["replay_point_quarantine_count"] == 1
    assert metrics["suffix_finalization_failure_count"] == 1
    assert metrics["replay_failure_count"] == 0
    assert metrics["suffix_failure_count"] == (1 if interrupt_stage == "execute" else 0)

    released = registry.release(point.replay_point_id)
    assert released.lifecycle.value == "released"
    assert provider.destroy_calls == [target_ref.branch_sandbox_id]
    assert provider.child_release_attempts == [
        (snap_id, target_ref.holder_id),
        (snap_id, target_ref.holder_id),
    ]
    assert snap_id not in provider.snapshots
    assert registry.metrics()["release_success_count"] == 1
