"""M0 contract conformance tests for the Provider-Neutral Sandbox Snapshot & Fork SPI (v0).

Bridges a reference E2B/microVM-compatible sandbox SPI (`pause`, `resume`,
`snapshot`, `fork`, `destroy`) onto LoopX's existing Explore replay and
counterfactual suffix owners without expanding minimal-kernel runtime state.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class SandboxSnapshotDescriptor:
    snapshot_id: str
    parent_sandbox_id: str
    fidelity_tier: str
    rootfs: dict[str, str]
    symlinks: dict[str, str]
    env: dict[str, str]
    processes: dict[int, str]
    memory_pages: dict[str, str]
    scrubbed_keys: tuple[str, ...]
    digest: str
    refcount: int = 1


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


def _digest_state(
    *,
    rootfs: Mapping[str, str],
    symlinks: Mapping[str, str],
    env: Mapping[str, str],
    processes: Mapping[int, str],
    memory_pages: Mapping[str, str],
    include_memory: bool,
) -> str:
    payload: dict[str, Any] = {
        "rootfs": dict(sorted(rootfs.items())),
        "symlinks": dict(sorted(symlinks.items())),
        "env": dict(sorted(env.items())),
    }
    if include_memory:
        payload["processes"] = {str(k): v for k, v in sorted(processes.items())}
        payload["memory_pages"] = dict(sorted(memory_pages.items()))
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class InMemorySandboxSpiProvider:
    """Reference E2B-compatible SPI provider obeying RFC I1-I6."""

    def __init__(self, *, provider_id: str = "e2b_microvm") -> None:
        self.provider_id = provider_id
        self.sandboxes: dict[str, SandboxInstanceState] = {}
        self.snapshots: dict[str, SandboxSnapshotDescriptor] = {}
        self._seq = 0

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
        requires_pause = target_tier in {"process_tree_quiesced", "microvm_memory_exact"}
        if requires_pause and sb.status != "paused":
            raise SandboxSpiError(
                "quiescence_required",
                f"{target_tier} snapshot requires paused sandbox",
            )
        scrubbed_env = {k: v for k, v in sb.env.items() if k not in scrub_keys}
        scrubbed_rootfs = {
            k: v
            for k, v in sb.rootfs.items()
            if not k.startswith(".loopx/") and k != "loopx.lease.json"
        }
        removed_keys = tuple(sorted(k for k in sb.env if k in scrub_keys))
        include_mem = target_tier == "microvm_memory_exact"
        digest = _digest_state(
            rootfs=scrubbed_rootfs,
            symlinks=sb.symlinks,
            env=scrubbed_env,
            processes=sb.processes if include_mem else {},
            memory_pages=sb.memory_pages if include_mem else {},
            include_memory=include_mem,
        )
        self._seq += 1
        snap_id = f"snap-{self._seq:04d}"
        desc = SandboxSnapshotDescriptor(
            snapshot_id=snap_id,
            parent_sandbox_id=sandbox_id,
            fidelity_tier=target_tier,
            rootfs=scrubbed_rootfs,
            symlinks=dict(sb.symlinks),
            env=scrubbed_env,
            processes=dict(sb.processes) if include_mem else {},
            memory_pages=dict(sb.memory_pages) if include_mem else {},
            scrubbed_keys=removed_keys,
            digest=digest,
            refcount=1,
        )
        self.snapshots[snap_id] = desc
        return desc

    def fork(
        self,
        snapshot_id: str,
        *,
        child_id: str,
        egress_policy_id: str = "isolated-offline",
    ) -> tuple[SandboxInstanceState, str]:
        if snapshot_id not in self.snapshots:
            raise SandboxSpiError("snapshot_missing", f"unknown snapshot {snapshot_id}")
        snap = self.snapshots[snapshot_id]
        self.snapshots[snapshot_id] = SandboxSnapshotDescriptor(
            snapshot_id=snap.snapshot_id,
            parent_sandbox_id=snap.parent_sandbox_id,
            fidelity_tier=snap.fidelity_tier,
            rootfs=snap.rootfs,
            symlinks=snap.symlinks,
            env=snap.env,
            processes=snap.processes,
            memory_pages=snap.memory_pages,
            scrubbed_keys=snap.scrubbed_keys,
            digest=snap.digest,
            refcount=snap.refcount + 1,
        )
        self._seq += 1
        fork_id = f"sb-fork-{child_id}-{self._seq:04d}"
        isolation_id = f"iso-{child_id}-{self._seq:04d}"
        child = SandboxInstanceState(
            sandbox_id=fork_id,
            provider_id=self.provider_id,
            status="running",
            rootfs=dict(snap.rootfs),
            symlinks=dict(snap.symlinks),
            env=dict(snap.env),
            processes=dict(snap.processes),
            memory_pages=dict(snap.memory_pages),
            egress_policy_id=egress_policy_id,
        )
        self.sandboxes[fork_id] = child
        return child, isolation_id

    def destroy_sandbox(self, sandbox_id: str) -> None:
        sb = self.sandboxes.get(sandbox_id)
        if sb is not None:
            sb.status = "destroyed"

    def release_snapshot(self, snapshot_id: str) -> None:
        snap = self.snapshots.get(snapshot_id)
        if snap is None:
            return
        remaining = snap.refcount - 1
        if remaining <= 0:
            del self.snapshots[snapshot_id]
        else:
            self.snapshots[snapshot_id] = SandboxSnapshotDescriptor(
                snapshot_id=snap.snapshot_id,
                parent_sandbox_id=snap.parent_sandbox_id,
                fidelity_tier=snap.fidelity_tier,
                rootfs=snap.rootfs,
                symlinks=snap.symlinks,
                env=snap.env,
                processes=snap.processes,
                memory_pages=snap.memory_pages,
                scrubbed_keys=snap.scrubbed_keys,
                digest=snap.digest,
                refcount=remaining,
            )


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

    def restore_replay_state(
        self,
        binding_key: object,
        request: RestoreReplayStateRequest,
    ) -> RestoreReceipt:
        del request
        assert isinstance(binding_key, _SnapshotRef)
        snap = self.provider.snapshots[binding_key.snapshot_id]
        return RestoreReceipt(
            achieved_fidelity=_TIER_TO_REPLAY_FIDELITY[snap.fidelity_tier],
            restored_projection_digest=snap.digest,
            prefix_reused=self.prefix_reused,
        )

    def validate_replay_equivalence(
        self,
        binding_key: object,
        receipt: RestoreReceipt,
    ) -> EquivalenceReport:
        assert isinstance(binding_key, _SnapshotRef)
        snap = self.provider.snapshots[binding_key.snapshot_id]
        return EquivalenceReport(
            achieved_fidelity=receipt.achieved_fidelity,
            equivalent=receipt.restored_projection_digest == snap.digest,
            expected_digest=snap.digest,
            observed_digest=receipt.restored_projection_digest,
            checks=(
                EquivalenceCheck("sandbox-rootfs-symlinks", True),
                EquivalenceCheck(
                    "sandbox-process-memory"
                    if snap.fidelity_tier == "microvm_memory_exact"
                    else "sandbox-env-only",
                    True,
                ),
            ),
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
        snap = self.provider.snapshots[binding_key.snapshot_id]
        branch_ref = _BranchRef(
            branch_sandbox_id=child_sb.sandbox_id,
            source_snapshot_id=snap.snapshot_id,
            branch_isolation_id=isolation_id,
        )
        return ReplayChildLease(
            child_key=branch_ref,
            concurrency=ReplayChildConcurrency.PROCESS_ISOLATED,
            worker_identity=isolation_id,
            receipt=RestoreReceipt(
                achieved_fidelity=_TIER_TO_REPLAY_FIDELITY[snap.fidelity_tier],
                restored_projection_digest=snap.digest,
                prefix_reused=self.prefix_reused,
            ),
        )

    def validate_replay_child_equivalence(
        self,
        child_key: object,
        receipt: RestoreReceipt,
    ) -> EquivalenceReport:
        assert isinstance(child_key, _BranchRef)
        snap = self.provider.snapshots[child_key.source_snapshot_id]
        sb = self.provider.sandboxes[child_key.branch_sandbox_id]
        observed = _digest_state(
            rootfs=sb.rootfs,
            symlinks=sb.symlinks,
            env=sb.env,
            processes=sb.processes if snap.fidelity_tier == "microvm_memory_exact" else {},
            memory_pages=sb.memory_pages if snap.fidelity_tier == "microvm_memory_exact" else {},
            include_memory=snap.fidelity_tier == "microvm_memory_exact",
        )
        return EquivalenceReport(
            achieved_fidelity=receipt.achieved_fidelity,
            equivalent=observed == snap.digest,
            expected_digest=snap.digest,
            observed_digest=observed,
            checks=(EquivalenceCheck("fork-child-equivalence", observed == snap.digest),),
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

    def run_arm(ctx: Any, req: Any) -> Mapping[str, bool]:
        del req
        assert isinstance(ctx.adapter_child_key, _BranchRef)
        sb = provider.sandboxes[ctx.adapter_child_key.branch_sandbox_id]
        observed_isolations.append(ctx.adapter_child_key.branch_isolation_id)
        if ctx.child_id == "treatment":
            sb.rootfs["packages/core/index.ts"] = "export const v = 2;"
            sb.memory_pages["module_graph"] = "mutated-in-treatment"
            fix_ok = True
        else:
            fix_ok = sb.rootfs["packages/core/index.ts"] == "export const v = 2;"
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
                candidate=CounterfactualCandidate("denied-lease", {"kind": "bad"}),
                target_branch_id="bad-branch",
                intent={"clone_parent_lease": True},
                execute_suffix=lambda _ctx, _req: {"fix": True, "guard": True},
            ),
        ),
    )
    child_res = group.candidates[0]
    assert not child_res.evaluated
    assert child_res.execution_failure is not None
    assert child_res.execution_failure.risk_assessment is not None
    assert child_res.execution_failure.risk_assessment.disposition == ReplayRiskDisposition.DENY

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
