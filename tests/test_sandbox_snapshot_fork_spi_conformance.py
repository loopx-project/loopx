"""M0 contract conformance tests for the Provider-Neutral Sandbox Snapshot & Fork SPI (v0).

Bridges a reference E2B/microVM-compatible sandbox SPI (`pause`, `resume`,
`snapshot`, `fork`, `destroy`) onto LoopX's existing Explore replay and
counterfactual suffix owners without expanding minimal-kernel runtime state.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

import pytest

from loopx.capabilities.explore.counterfactual_runtime import (
    CounterfactualCandidate,
    CounterfactualChildCandidate,
    CounterfactualDecision,
    CounterfactualHypothesis,
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
    egress_policy_id: str = "isolated-offline"


@dataclass(frozen=True)
class SandboxPauseReceipt:
    sandbox_id: str
    quiesced_fs: bool
    dirty_page_flush_ok: bool
    active_pid_digest: str


def _freeze_snapshot_fields(instance: object) -> None:
    for attr in ("rootfs", "symlinks", "env", "processes", "memory_pages"):
        object.__setattr__(
            instance,
            attr,
            MappingProxyType(dict(getattr(instance, attr))),
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

    def __post_init__(self) -> None:
        _freeze_snapshot_fields(self)


@dataclass
class _SnapshotRef:
    snapshot_id: str


@dataclass
class _BranchRef:
    branch_sandbox_id: str
    source_snapshot_id: str
    branch_isolation_id: str


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


def _digest_state(
    *,
    rootfs: Mapping[str, str],
    symlinks: Mapping[str, str],
    env: Mapping[str, str],
    processes: Mapping[int, str],
    memory_pages: Mapping[str, str],
    include_env: bool = True,
    include_memory: bool,
) -> str:
    payload: dict[str, Any] = {
        "rootfs": dict(sorted(rootfs.items())),
        "symlinks": dict(sorted(symlinks.items())),
    }
    if include_env:
        payload["env"] = dict(sorted(env.items()))
    if include_memory:
        payload["processes"] = {str(k): v for k, v in sorted(processes.items())}
        payload["memory_pages"] = dict(sorted(memory_pages.items()))
    return _json_sha256(payload)


def _observe_sandbox_digest(
    sb: SandboxInstanceState,
    *,
    fidelity_tier: str,
) -> str | None:
    if sb.status == "destroyed":
        return None
    if fidelity_tier == "microvm_memory_exact" and not (
        sb.processes or sb.memory_pages
    ):
        return None
    include_env = fidelity_tier != "filesystem_only"
    include_mem = fidelity_tier == "microvm_memory_exact"
    return _digest_state(
        rootfs=sb.rootfs,
        symlinks=sb.symlinks,
        env=sb.env if include_env else {},
        processes=sb.processes if include_mem else {},
        memory_pages=sb.memory_pages if include_mem else {},
        include_env=include_env,
        include_memory=include_mem,
    )


def _descriptor_from_sealed(sealed: _SealedSnapshot) -> SandboxSnapshotDescriptor:
    return SandboxSnapshotDescriptor(
        snapshot_id=sealed.snapshot_id,
        parent_sandbox_id=sealed.parent_sandbox_id,
        fidelity_tier=sealed.fidelity_tier,
        rootfs=sealed.rootfs,
        symlinks=sealed.symlinks,
        env=sealed.env,
        processes=sealed.processes,
        memory_pages=sealed.memory_pages,
        scrubbed_keys=sealed.scrubbed_keys,
        digest=sealed.digest,
        refcount=sealed.refcount,
        rootfs_digest=sealed.rootfs_digest,
        env_manifest_digest=sealed.env_manifest_digest,
        memory_digest=sealed.memory_digest,
    )


class InMemorySandboxSpiProvider:
    """Reference E2B-compatible SPI provider obeying RFC I1-I6."""

    def __init__(self, *, provider_id: str = "e2b_microvm") -> None:
        self.provider_id = provider_id
        self.sandboxes: dict[str, SandboxInstanceState] = {}
        self._sealed_snapshots: dict[str, _SealedSnapshot] = {}
        self.snapshots: dict[str, SandboxSnapshotDescriptor] = {}
        self._seq = 0

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
        processes_copy = dict(sb.processes) if include_mem else {}
        memory_copy = dict(sb.memory_pages) if include_mem else {}
        removed_keys = tuple(sorted(k for k in sb.env if k in scrub_keys))
        rootfs_digest = _digest_rootfs(scrubbed_rootfs, symlinks_copy)
        env_manifest_digest = _digest_env(scrubbed_env)
        memory_digest = (
            _digest_memory(processes_copy, memory_copy)
            if include_mem and (processes_copy or memory_copy)
            else None
        )
        digest = _digest_state(
            rootfs=scrubbed_rootfs,
            symlinks=symlinks_copy,
            env=scrubbed_env,
            processes=processes_copy,
            memory_pages=memory_copy,
            include_env=include_env,
            include_memory=include_mem,
        )
        self._seq += 1
        snap_id = f"snap-{self._seq:04d}"
        sealed = _SealedSnapshot(
            snapshot_id=snap_id,
            parent_sandbox_id=sandbox_id,
            fidelity_tier=target_tier,
            rootfs=scrubbed_rootfs,
            symlinks=symlinks_copy,
            env=scrubbed_env,
            processes=processes_copy,
            memory_pages=memory_copy,
            scrubbed_keys=removed_keys,
            rootfs_digest=rootfs_digest,
            env_manifest_digest=env_manifest_digest,
            memory_digest=memory_digest,
            digest=digest,
            refcount=1,
        )
        self._sealed_snapshots[snap_id] = sealed
        self.snapshots[snap_id] = _descriptor_from_sealed(sealed)
        return _descriptor_from_sealed(sealed)

    def _set_snapshot_refcount(self, snapshot_id: str, refcount: int) -> _SealedSnapshot:
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
            scrubbed_keys=sealed.scrubbed_keys,
            rootfs_digest=sealed.rootfs_digest,
            env_manifest_digest=sealed.env_manifest_digest,
            memory_digest=sealed.memory_digest,
            digest=sealed.digest,
            refcount=refcount,
        )
        self._sealed_snapshots[snapshot_id] = updated
        self.snapshots[snapshot_id] = _descriptor_from_sealed(updated)
        return updated

    def fork(
        self,
        snapshot_id: str,
        *,
        child_id: str,
        egress_policy_id: str = "isolated-offline",
    ) -> tuple[SandboxInstanceState, str]:
        sealed = self.sealed_snapshot(snapshot_id)
        self._set_snapshot_refcount(snapshot_id, sealed.refcount + 1)
        self._seq += 1
        fork_id = f"sb-fork-{child_id}-{self._seq:04d}"
        isolation_id = f"iso-{child_id}-{self._seq:04d}"
        child = SandboxInstanceState(
            sandbox_id=fork_id,
            provider_id=self.provider_id,
            status="running",
            rootfs=dict(sealed.rootfs),
            symlinks=dict(sealed.symlinks),
            env=dict(sealed.env),
            processes=dict(sealed.processes),
            memory_pages=dict(sealed.memory_pages),
            egress_policy_id=egress_policy_id,
        )
        self.sandboxes[fork_id] = child
        return child, isolation_id

    def restore_snapshot(
        self,
        sandbox_id: str,
        snapshot_id: str,
    ) -> SandboxInstanceState:
        sealed = self.sealed_snapshot(snapshot_id)
        if sandbox_id not in self.sandboxes:
            raise SandboxSpiError("sandbox_missing", f"unknown sandbox {sandbox_id}")
        sb = self.sandboxes[sandbox_id]
        if sb.status == "destroyed":
            raise SandboxSpiError(
                "sandbox_destroyed",
                "cannot restore destroyed sandbox",
            )
        sb.rootfs = dict(sealed.rootfs)
        sb.symlinks = dict(sealed.symlinks)
        sb.env = dict(sealed.env)
        sb.processes = dict(sealed.processes)
        sb.memory_pages = dict(sealed.memory_pages)
        sb.status = "running"
        return sb

    def destroy_sandbox(self, sandbox_id: str) -> None:
        sb = self.sandboxes.get(sandbox_id)
        if sb is not None:
            sb.status = "destroyed"

    def release_snapshot(self, snapshot_id: str) -> None:
        sealed = self._sealed_snapshots.get(snapshot_id)
        if sealed is None:
            return
        remaining = sealed.refcount - 1
        if remaining <= 0:
            del self._sealed_snapshots[snapshot_id]
            self.snapshots.pop(snapshot_id, None)
        else:
            self._set_snapshot_refcount(snapshot_id, remaining)


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


@dataclass
class SpiReplayAdapter:
    """Adapter bridging InMemorySandboxSpiProvider onto RiskAwareReplayChildAdapter."""

    provider: InMemorySandboxSpiProvider
    source_sandbox_id: str
    target_tier: str = "microvm_memory_exact"
    prefix_reused: bool = True
    released_children: list[str] = field(default_factory=list)
    released_snapshots: list[str] = field(default_factory=list)

    def capture_replay_state(
        self, request: CaptureReplayStateRequest
    ) -> AdapterStateLease:
        del request
        desc = self.provider.snapshot(
            self.source_sandbox_id,
            target_tier=self.target_tier,
        )
        fidelity = _TIER_TO_REPLAY_FIDELITY[desc.fidelity_tier]
        return AdapterStateLease(
            binding_key=_SnapshotRef(snapshot_id=desc.snapshot_id),
            fidelity=fidelity,
            projection_digest=desc.digest,
        )

    def _build_equivalence_report(
        self,
        *,
        sealed: _SealedSnapshot,
        sb: SandboxInstanceState,
        achieved_fidelity: ReplayFidelity,
        check_prefix: str = "sandbox",
    ) -> EquivalenceReport:
        live_alive = sb.status != "destroyed"
        fs_ok = live_alive and _digest_rootfs(sb.rootfs, sb.symlinks) == sealed.rootfs_digest
        observed_digest = _observe_sandbox_digest(
            sb,
            fidelity_tier=sealed.fidelity_tier,
        )
        if sealed.fidelity_tier == "filesystem_only":
            checks: tuple[EquivalenceCheck, ...] = (
                EquivalenceCheck(f"{check_prefix}-rootfs-symlinks", fs_ok),
            )
        elif sealed.fidelity_tier in {"filesystem_and_env", "process_tree_quiesced"}:
            env_ok = live_alive and _digest_env(sb.env) == sealed.env_manifest_digest
            checks = (
                EquivalenceCheck(f"{check_prefix}-rootfs-symlinks", fs_ok),
                EquivalenceCheck(f"{check_prefix}-env-only", env_ok),
            )
        else:
            has_mem_evidence = bool(sb.processes or sb.memory_pages)
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
        del request
        assert isinstance(binding_key, _SnapshotRef)
        sealed = self.provider.sealed_snapshot(binding_key.snapshot_id)
        restored_sb = self.provider.restore_snapshot(
            self.source_sandbox_id,
            binding_key.snapshot_id,
        )
        observed_digest = _observe_sandbox_digest(
            restored_sb,
            fidelity_tier=sealed.fidelity_tier,
        )
        return RestoreReceipt(
            achieved_fidelity=_TIER_TO_REPLAY_FIDELITY[sealed.fidelity_tier],
            restored_projection_digest=observed_digest,
            prefix_reused=self.prefix_reused,
        )

    def validate_replay_equivalence(
        self,
        binding_key: object,
        receipt: RestoreReceipt,
    ) -> EquivalenceReport:
        assert isinstance(binding_key, _SnapshotRef)
        sealed = self.provider.sealed_snapshot(binding_key.snapshot_id)
        sb = self.provider.sandboxes[self.source_sandbox_id]
        return self._build_equivalence_report(
            sealed=sealed,
            sb=sb,
            achieved_fidelity=receipt.achieved_fidelity,
        )

    def release_replay_state(self, binding_key: object) -> None:
        assert isinstance(binding_key, _SnapshotRef)
        self.released_snapshots.append(binding_key.snapshot_id)
        self.provider.release_snapshot(binding_key.snapshot_id)

    def fork_replay_child(
        self,
        binding_key: object,
        request: ForkReplayChildRequest,
    ) -> ReplayChildLease:
        assert isinstance(binding_key, _SnapshotRef)
        child_sb, isolation_id = self.provider.fork(
            binding_key.snapshot_id,
            child_id=request.child_id,
        )
        sealed = self.provider.sealed_snapshot(binding_key.snapshot_id)
        branch_ref = _BranchRef(
            branch_sandbox_id=child_sb.sandbox_id,
            source_snapshot_id=sealed.snapshot_id,
            branch_isolation_id=isolation_id,
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
                achieved_fidelity=_TIER_TO_REPLAY_FIDELITY[sealed.fidelity_tier],
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
        sealed = self.provider.sealed_snapshot(child_key.source_snapshot_id)
        sb = self.provider.sandboxes[child_key.branch_sandbox_id]
        return self._build_equivalence_report(
            sealed=sealed,
            sb=sb,
            achieved_fidelity=receipt.achieved_fidelity,
            check_prefix="fork-child",
        )

    def release_replay_child(self, child_key: object) -> None:
        assert isinstance(child_key, _BranchRef)
        self.released_children.append(child_key.branch_sandbox_id)
        self.provider.destroy_sandbox(child_key.branch_sandbox_id)
        self.provider.release_snapshot(child_key.source_snapshot_id)

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
    class _NonRestoringSpiProvider(InMemorySandboxSpiProvider):
        def restore_snapshot(
            self, sandbox_id: str, snapshot_id: str
        ) -> SandboxInstanceState:
            self.sealed_snapshot(snapshot_id)
            return self.sandboxes[sandbox_id]

    class _MemoryDroppingSpiProvider(InMemorySandboxSpiProvider):
        def restore_snapshot(
            self, sandbox_id: str, snapshot_id: str
        ) -> SandboxInstanceState:
            sb = super().restore_snapshot(sandbox_id, snapshot_id)
            sb.processes = {}
            sb.memory_pages = {}
            return sb

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

    # 1) Genuine reference provider restores sb-single before equivalence check & suffix.
    real_provider = InMemorySandboxSpiProvider()
    trace_ok, point_ok, adapter_ok, reg_ok = _setup_resumed_drift(
        real_provider, "trace-real-restore"
    )
    observed_in_suffix: list[tuple[str, str, str]] = []

    def read_restored(_req: Any) -> Mapping[str, bool]:
        sb = real_provider.sandboxes["sb-single"]
        observed_in_suffix.append(
            (
                sb.rootfs["src/app.py"],
                sb.symlinks["bin/tool"],
                sb.memory_pages["lsp_cache"],
            )
        )
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

    # A candidate checking for post-capture state cannot pass after real restore.
    post_capture_check = run_counterfactual_suffix_replay(
        reg_ok,
        trace=trace_ok,
        hypothesis=_hyp("trace-real-restore"),
        candidate=CounterfactualCandidate("check-post-capture", {"op": "probe"}),
        execute_suffix=lambda _req: {
            "fix": real_provider.sandboxes["sb-single"].rootfs["src/app.py"]
            == "v2-post-capture",
            "guard": True,
        },
    )
    assert post_capture_check.decision == CounterfactualDecision.REJECTED

    # Independent post-restore drift before equivalence verification is rejected.
    real_provider.sandboxes["sb-single"].memory_pages["lsp_cache"] = "drifted-after-restore"
    drifted_report = adapter_ok.validate_replay_equivalence(
        _SnapshotRef(snapshot_id=next(iter(real_provider.snapshots))),
        RestoreReceipt(
            achieved_fidelity=ReplayFidelity.EXACT,
            restored_projection_digest=point_ok.captured_projection_digest,
        ),
    )
    assert not drifted_report.equivalent
    assert drifted_report.observed_digest != drifted_report.expected_digest
    assert any(
        c.check_id == "sandbox-process-memory" and not c.passed
        for c in drifted_report.checks
    )

    # 2) Non-restoring provider leaves post-capture state; promotion must be refused.
    stale_provider = _NonRestoringSpiProvider()
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

    # 3) Missing exact memory/process evidence on restore must refuse promotion.
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
