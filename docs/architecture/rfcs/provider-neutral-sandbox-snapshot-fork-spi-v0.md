# RFC: Provider-Neutral Sandbox Snapshot and Fork SPI (v0)

- **RFC status:** Accepted (effective on merge; this change is a proposal)
- **Supersedes / closes:** none
- **Delivery maturity:** Proposal; M0 contract conformance suite included, with no microVM provider integration, default runtime change, or benchmark uplift claim.
- **Authors / owners:** LoopX control-plane, exploration, and benchmark maintainers
- **Created:** 2026-11-22
- **Last normative revision:** 2026-11-22
- **Implementation baseline:** `dc79482c88dd75ea59785d165fd3050d9d5508c3`
- **Language mirror:** [中文版](provider-neutral-sandbox-snapshot-fork-spi-v0.zh-CN.md)
- **Related contracts:** [research-state branching](hierarchical-agent-stride-control-v0.md#77-research-state-branching-and-matched-continuation), [budgeted research planning](../../research/budgeted-research-planning.md), [benchmark verification funnel & measurement](long-horizon-harness-benchmark-research-program-v0.md#55-four-tier-verification-funnel), [LayerFS exploration](layerfs-engineering-exploration-evaluation-v0.md), [small-kernel composition](monorepo-distribution-split-v0.md), [complete state recovery](complete-state-recovery-v0.md), [extension placement](../../reference/extensions.md#placement-decision-for-agents).

## Document map and maintenance contract

Sections 1–3 and 5–11 define the proposed Provider-Neutral Sandbox Snapshot and
Fork SPI contract and authority boundaries. Section 4 records audited baseline
facts against `dc79482c88dd75ea59785d165fd3050d9d5508c3` and the canonical
merged research/eval contracts (`#8461`, `#9209`, `#9211`; closed unmerged
prototype `#8516` was superseded by focused `main` slices); Section 12 lists
unresolved decisions. The Chinese file is a semantic mirror; keep normative
sections synchronized. Dated progress entries belong in the [RFC ledger](ledger/README.md),
not as dated headings above Appendix A. Merging this RFC establishes a design
basis and deterministic conformance oracle; it grants no runtime default,
provider support claim, or benchmark gain.

---

## 1. Decision summary

1. Define an **optional, default-off, extension-hosted** 4-operation Sandbox
   Snapshot and Fork SPI (`pause`, `resume`, `snapshot`, `fork`, plus explicit
   `destroy`) that adapts E2B-compatible microVM, Firecracker/Cloud Hypervisor,
   LayerFS CoW, container, and Git-worktree backends into LoopX's existing
   [explore replay](../../../loopx/capabilities/explore/replay_runtime.py) and
   [counterfactual suffix](../../../loopx/capabilities/explore/counterfactual_runtime.py)
   contracts.
2. **Externalize control-plane authority:** LoopX core keeps Goals, Todos,
   claims/leases, quota budgets, settlement receipts, and host cursor authority
   outside guest sandboxes; snapshotting or forking a sandbox never clones an
   execution lease or settlement right.
3. **Keep the small kernel hypervisor-free:** No hypervisor SDK, kernel-specific
   daemon, or speculative runtime enum enters `loopx/` core packages; concrete
   providers remain extensions (`capability_id: none`, `origin: extension`).
4. **Separate 1:1 hibernate from 1:N counterfactual fork:** `pause`/`resume`
   preserves one live sandbox identity for suspend/resume; `snapshot`/`fork`
   produces immutable checkpoints and isolated `branch_isolation_id` children
   suitable for matched counterfactual continuation pairs
   ($\tau_B(s; a, a_0, \pi)$).
5. **No unverified runtime claim:** Only the M0 synthetic conformance suite in
   [`test_sandbox_snapshot_fork_spi_conformance.py`](../../../tests/test_sandbox_snapshot_fork_spi_conformance.py)
   ships with this RFC; live Linux/E2B integration (`M1`–`M3`) remains gated.

---

## 2. Problem, motivation, and invariants

Counterfactual continuation and multi-branch agent engineering require branching
from an exact five-tuple checkpoint
$s = (c_{\text{host}}, \sigma_{\text{loopx}}, w_{\text{ws}}, \phi_{\text{env}}, b_{\text{eval}})$
while comparing treatment and control continuations under matched budgets.
Today's shipped file-only capture path reveals four structural blockers:

1. **Symlink rejection & capture ceilings:** [`checkpoint_artifacts.ts`](../../../loopx/control_plane/runtime/checkpoint_artifacts.ts)
   rightly rejects symlinks and enforces `MAX_LOCAL_SNAPSHOT_BYTES` (64 MiB),
   while [`checkpoint.py`](../../../benchmark/runtime/checkpoint.py) enforces a
   30 s / 90 s capture window (`CHECKPOINT_CAPTURE_SECONDS = 90`). Modern Node
   (`.pnpm`), Python (`.venv`), and Rust workspaces depend heavily on symlinks
   and gigabyte-scale build caches.
2. **Process/memory state erasure:** File copies lose running language servers,
   build daemons, local databases, open sockets, and GPU/in-memory state,
   converting `exact` microVM continuations into cold reconstruction attempts.
3. **Contamination & lease split-brain hazards:** Cloning a naive VM image that
   holds LoopX `.loopx/` state, `claim_operation_id` proofs, or ambient API
   tokens allows sibling branches to mutate shared external state or double-spend
   the parent turn.
4. **Provider lock-in risk:** Hardcoding E2B-specific REST types or Firecracker
   sockets into `loopx/` would breach LoopX's model- and runtime-neutral small
   kernel ([distribution split](monorepo-distribution-split-v0.md)).

### Invariants

- **I1 (Quiescence Barrier before Exact Capture):** A `snapshot` claiming
  `process_tree_quiesced` or `microvm_memory_exact` MUST only run across a
  settled or explicitly `paused` quiescence barrier after flushing guest
  filesystems; capturing live mutating memory fails closed.
- **I2 (Authority Externalization):** Control-plane SQLite stores, registries,
  writer leases, and settlement receipts reside strictly outside the captured
  guest state. `snapshot`/`fork` strips inherited host lease proofs and secrets;
  each admitted child receives a fresh branch identity (`branch_id`,
  `branch_isolation_id`) and explicit risk review via `RiskAwareReplayChildAdapter`.
- **I3 (1:1 Suspend vs. 1:N Non-Interference):** `pause` -> `resume` is 1:1 on
  a single `sandbox_id` (and rejects duplicate `resume` when already running).
  `snapshot` -> `fork` is 1:N across immutable snapshots; mutations to RAM,
  sockets, tmpfs, or symlinks in child branch A MUST NOT leak to sibling B or the
  pinned snapshot.
- **I4 (Honest Fidelity Mapping onto `ReplayFidelity`):** Every provider
  declares its achieved `SandboxFidelityTier`, mapped monotonically onto
  `ReplayFidelity`. Downstream evaluators never promote an unverified or
  lower-fidelity restore (`BEST_EFFORT` stays `OBSERVED_ONLY`).
- **I5 (Egress & Secret Fence):** Child branches inherit a declared network and
  credential envelope; ambient host secrets are scrubbed before snapshot seal,
  and cross-branch shared external writes are blocked or gated via
  `assess_replay_child_intent`.
- **I6 (Zero Kernel Bloat & Runtime-Only Handles):** Opaque provider bindings
  (`sandbox_id`, `snapshot_id`, sockets) remain strictly runtime-only behind
  `AdapterStateLease.binding_key` / `ReplayChildLease.child_key`, never in public
  Trace/Todo/Explore records or new core enums without an active shipped caller.

---

## 3. Scope and non-goals

### In scope

- Gap inventory across the five-tuple checkpoint contract ([`#8461`](https://github.com/loopx-project/loopx/pull/8461) / [`#9209`](https://github.com/loopx-project/loopx/pull/9209) / [`#9211`](https://github.com/loopx-project/loopx/pull/9211) & [`replay_runtime.py`](../../../loopx/capabilities/explore/replay_runtime.py)) and
  E2B-compatible sandbox semantics (`pause`, `resume`, `snapshot`, `fork`, `destroy`).
- Provider-neutral SPI data records, lifecycle state machine, `ReplayFidelity`
  mapping, and `RiskAwareReplayChildAdapter` bridge contract.
- Deterministic M0 conformance verification (`pytest`) reusing existing Explore
  and Counterfactual owners.

Placement rationale: `capability_id: none` (bridges existing `explore`
replay/counterfactual contracts to external optional sandbox extensions),
`origin: extension`, future runtime packages under `packages/` or external
provider packages once M1 qualifies. No new `loopx/` package or semantic
vocabulary entry is introduced by this RFC.

### Non-goals

- Shipping a live E2B/Firecracker client inside `loopx/` core.
- Replacing Git worktrees (`git worktree`) or signed updater flows for ordinary
  CI/PR maintenance turns.
- Solving host-side multi-tenant VM security guarantees beyond the declared
  isolation envelope and secret-scrubbing boundary.
- Claiming benchmark score uplift ($\Delta J(B \mid s_0) > 0$) before live M2/M3
  controlled experiments complete.

---

## 4. Audited current-system contract and 5-tuple gap inventory

On baseline `dc79482c88` and the canonical Explore/Benchmark contracts on
`main` ([`#8461`](https://github.com/loopx-project/loopx/pull/8461),
[`#9209`](https://github.com/loopx-project/loopx/pull/9209),
[`#9211`](https://github.com/loopx-project/loopx/pull/9211); closed prototype
`#8516` was decomposed onto these owners), each component of LoopX's five-tuple
checkpoint
$s = (c_{\text{host}}, \sigma_{\text{loopx}}, w_{\text{ws}}, \phi_{\text{env}}, b_{\text{eval}})$
exhibits the following current boundary and gap toward E2B-style sandbox fork:

| Tuple element | Audited current owner | Current capability | Gap for process & workspace fork |
| --- | --- | --- | --- |
| $c_{\text{host}}$ Host session cursor | [`research_checkpoint.py`](../../../loopx/hosts/research_checkpoint.py), [Stride §7.7](hierarchical-agent-stride-control-v0.md#77-research-state-branching-and-matched-continuation) | Exports/forks completed native Codex turn prefix via immutable unit | Does not bind sandbox VM guest PID/daemon cursors or suspended tool PTYs |
| $\sigma_{\text{loopx}}$ Control-plane state | `authority_store` SQLite, [`replay_runtime.py`](../../../loopx/capabilities/explore/replay_runtime.py) | `ReplayPointRegistry` binds trace cursor + opaque `AdapterStateLease` | Externalized cleanly, but no standard SPI scrubbing check prevents caller mounting `.loopx/` inside guest VM |
| $w_{\text{ws}}$ Workspace filesystem | [`checkpoint_artifacts.ts`](../../../loopx/control_plane/runtime/checkpoint_artifacts.ts), [LayerFS RFC](layerfs-engineering-exploration-evaluation-v0.md) | Byte-verified directory archive (64 MiB limit; rejects symlinks) | Breaks symlink-heavy `.pnpm`/`.venv`, slow on large build trees; LayerFS CoW covers FS only |
| $\phi_{\text{env}}$ Process, RAM & network envelope | [`checkpoint.py`](../../../benchmark/runtime/checkpoint.py), [`counterfactual_runtime.py`](../../../loopx/capabilities/explore/counterfactual_runtime.py) | `IsolatedReplayChildAdapter` defines `PROCESS_ISOLATED`, `worker_identity`, risk hooks | Zero standard `pause`/`resume`/`snapshot`/`fork` SPI bridging CoW rootfs + VM RAM + network fence |
| $b_{\text{eval}}$ Budget & eval lineage | [Budgeted Planning](../../research/budgeted-research-planning.md) (`#8461`), [Benchmark Program §5.5 & §6.2](long-horizon-harness-benchmark-research-program-v0.md#62-efficiency) (`#9211`) | Tier-2 quiescent checkpoint calibration, complete-cost accounting (`ReplayPointMetricsSnapshot`), and isolated `worker_identity` guard | Lacks provider cost breakdown (`suspend_ms`, `snapshot_bytes`, `restore_ms`) and branch isolation ID provenance |

---

## 5. Proposed architecture and SPI

### Ownership and three-way boundary

```text
+-----------------------------------+      opaque runtime bindings       +------------------------------------+
|        LoopX Core (TS/Py)         |<---------------------------------->|   Sandbox SPI Extension (Opt-in)   |
| - TraceLog & ReplayPointRegistry  |  AdapterStateLease / ChildLease    | - pause / resume (1:1 handle)      |
| - Run / Claim / Quota / CQR       |  SandboxFidelityTier -> Fidelity   | - snapshot / fork (1:N CoW/VM)     |
| - RiskAwareReplayChildAdapter     |<==================================>| - secret/lease scrub & egress fence|
|   orchestration (intent/outcome)  |  public digests + 5D cost receipt  | - destroy & refcount pinning       |
+-----------------------------------+                                    +------------------------------------+
                 ^                                                                          ^
                 | externalized authority (never inside guest image)                        | guest execution
                 v                                                                          v
+---------------------------------------------------------------------------------------------------------------+
| Host Agent Session Owner (Codex / DSH / Pi) + Guest MicroVM / CoW Workspace (ephemeral branch_isolation_id)   |
+---------------------------------------------------------------------------------------------------------------+
```

### Canonical SPI records and fidelity mapping

1. **`SandboxHandle`:** `{ provider_id, sandbox_id, state: creating|running|paused|destroyed, rootfs_kind, guest_arch }`
2. **`SandboxPauseReceipt`:** `{ sandbox_id, paused_at, quiesced_fs: bool, active_pid_digest, dirty_page_flush_ok: bool }`
3. **`SandboxSnapshotDescriptor`:** `{ snapshot_id, parent_sandbox_id, fidelity_tier, rootfs_digest, env_manifest_digest, memory_digest|null, scrubbed_keys: tuple[str, ...], byte_size: int }`
4. **`SandboxBranchHandle`:** `{ branch_id, branch_isolation_id, source_snapshot_id, fork_sandbox_id, egress_policy_id, achieved_fidelity: SandboxFidelityTier }`

| Declared `SandboxFidelityTier` | What is preserved | Mapped `ReplayFidelity` | Required equivalence check (`EquivalenceCheck`) |
| --- | --- | --- | --- |
| `filesystem_only` | Clean tracked checkout or CoW rootfs without env/procs | `BEST_EFFORT` (or `SEMANTIC_EQUIVALENT` when deps verified) | Rootfs digest + symlink preservation check |
| `filesystem_and_env` | CoW rootfs (`.pnpm`/`.venv`, build cache) + clean env manifest | `SEMANTIC_EQUIVALENT` | Rootfs/symlink digest + pinned toolchain ABI check |
| `process_tree_quiesced` | CoW rootfs + env manifest + cleanly stopped & restarted deterministic daemons | `SEMANTIC_EQUIVALENT` | Rootfs + env digests & live daemon health/port readiness proof (fail closed on missing daemon) |
| `microvm_memory_exact` | Paused microVM vCPU/RAM pages + block CoW + open FDs/LSP | `EXACT` | `expected_digest == observed_digest` across rootfs + memory state |

### Lifecycle, idempotency, and provider profiles

- **`pause(sandbox_id, quiesce=True)`:** Transitions `running -> paused`; flushes
  guest buffers and records `SandboxPauseReceipt`. Re-pausing an already `paused`
  sandbox with identical `operation_id` is idempotent.
- **`resume(sandbox_id)`:** Transitions `paused -> running` on the **same**
  `sandbox_id`. Rejects calling `resume` on a `running` or `destroyed` handle.
- **`snapshot(sandbox_id, *, target_tier, scrub_keys)`:** Requires `paused`
  when `target_tier in {process_tree_quiesced, microvm_memory_exact}` (**I1**).
  Strips declared control-plane leases/credentials, seals immutable
  `SandboxSnapshotDescriptor` with content digests, and pins refcount > 0.
- **`fork(snapshot_id, *, child_id, target_branch_id, egress_policy_id)`:**
  Clones isolated copy-on-write storage (and daemon readiness manifests or memory pages when `process_tree_quiesced` / `microvm_memory_exact`)
  under a distinct `branch_isolation_id` mapped directly to
  `ReplayChildLease(concurrency=PROCESS_ISOLATED, worker_identity=...)` in
  [`replay_runtime.py`](../../../loopx/capabilities/explore/replay_runtime.py).
- **`destroy(sandbox_or_snapshot_id)`:** Decrements snapshot refcount or
  tears down a child VM without disturbing sibling branches or pinned snapshots
  until explicitly released.

| Provider profile | Supported ops | Peak `SandboxFidelityTier` | Notes / constraints |
| --- | --- | --- | --- |
| `local_worktree` | `fork`, `destroy` | `filesystem_only` | Existing Git baseline; zero RAM capture |
| `layerfs_cow` | `snapshot`, `fork`, `destroy` | `filesystem_and_env` | Linux ext4/xfs/btrfs preferred; preserves symlinks |
| `docker_oci` | `pause`, `resume`, `snapshot`, `fork`, `destroy` | `process_tree_quiesced` | `docker pause`/CRIU experimental; kernel-dependent |
| `e2b_microvm` | `pause`, `resume`, `snapshot`, `fork`, `destroy` | `microvm_memory_exact` | Hosted/self-hosted Firecracker microVMs; requires scrub + egress fence |
| `firecracker_local` | `pause`, `resume`, `snapshot`, `fork`, `destroy` | `microvm_memory_exact` | KVM host required; baseline for local M1/M2 study |

---

## 6. Alternatives and design choices

1. **Embedding E2B SDK inside `loopx/` core (`control_plane` or `hosts`):**
   Rejected. Couples the small kernel to external microVM wire protocols,
   inflates dependency surface, and contradicts [distribution split](monorepo-distribution-split-v0.md).
2. **Capturing `.loopx/` SQLite inside the VM snapshot:**
   Rejected (**I2**). Causes split-brain Todo leases, replayed settlement
   effects, and invalidates strict counterfactual branch independence.
3. **Collapsing `pause`/`resume` into `snapshot`/`fork`:**
   Rejected (**I3**). `pause`/`resume` optimizes 1:1 human-in-the-loop idle VM
   hibernation on the same `sandbox_id`; `snapshot`/`fork` requires immutability,
   scrubbing, fresh `branch_isolation_id`, and multi-arm fan-out accounting.

---

## 7. Safety, privacy, and compatibility

- **Default-off & feature-off parity:** When no Sandbox SPI extension is
  enabled, `ReplayPointRegistry`, `research-checkpoint`, and ordinary Git
  worktree turns behave identically with zero extra latency or schema fields.
- **Credential & lease hygiene:** Parent sandboxes MUST NOT hold persistent
  LoopX writer credentials inside snapshotted RAM/rootfs; `snapshot()` scrubs
  configured env vars and token paths before sealing, and child forks receive
  scoped ephemeral tokens (if authorized) after `assess_replay_child_intent`
  passes.
- **Zero opaque handles in public records:** Provider `sandbox_id`, URIs,
  sockets, and tokens live exclusively in `AdapterStateLease.binding_key` /
  `ReplayChildLease.child_key`; `ReplayPoint.to_record()` and
  `CounterfactualReplayResult.to_record()` remain strictly public-safe.
- **Legacy downgrade:** Selecting `minimum_fidelity=ReplayFidelity.EXACT`
  when only a `filesystem_only` (`SEMANTIC_EQUIVALENT` / `BEST_EFFORT`)
  point exists deterministically marks outcomes `OBSERVED_ONLY` rather than
  `PROMOTED`, preventing unverified cold restarts from masquerading as exact
  process-level forks.

---

## 8. Migration and rollback

1. **Preflight & admission:** Verify provider availability, KVM/kernel prerequisites
   when local, guest OS/arch match, and explicit network egress policy.
2. **Quiescence & cutover:** Quiesce or `pause` parent sandbox before snapshot;
   keep cold `local_worktree` control arm untouched.
3. **Rollback:** Disable the opt-in extension profile or omit SPI inputs;
   destroy unpinned child VMs while retaining existing LoopX SQLite/Trace
   history intact. No irreversible schema migration exists.

---

## 9. Validation and acceptance

| Claim | Verification command / test | Required result | Boundary / exclusions |
| --- | --- | --- | --- |
| V1: Quiescence barrier for exact memory snapshot | `test_exact_memory_snapshot_requires_paused_quiescence_barrier` | Fails closed if `running`; succeeds with `EXACT` once `paused` | Synthetic SPI fixture; does not test Linux Firecracker KVM page flush |
| V2: 1:1 suspend/resume vs. 1:N fork isolation, immutability & symlinks | `test_pause_resume_preserves_single_sandbox_identity_and_live_process_table`, `test_snapshot_fork_isolates_concurrent_counterfactual_branches_and_symlinks`, `test_sealed_snapshot_rejects_descriptor_mutation_and_isolates_readback_and_forks` | Same ID + PID across `pause`/`resume`; isolated CoW/RAM + `.pnpm`/`.venv` across parallel `fork`s; sealed snapshot isolated from descriptor edits | Does not prove E2B wall-clock latency or memory overhead in production |
| V3: Authority/secret scrub & public boundary | `test_authority_and_secret_scrubbing_blocks_guest_lease_cloning_and_public_key_leak` | Scrubbed parent secrets; parent-lease reuse and `shared_prod` egress denied by `assess_replay_child_intent`; zero leaked keys | Requires real provider scrub audit before M1 live traffic |
| V4: Fidelity filtering, live-restore equivalence & snapshot lifetime | `test_fidelity_degradation_filters_filesystem_only_provider_when_exact_process_state_required`, `test_single_branch_restore_verifies_live_state_and_rejects_unrestored_or_incomplete_exact_evidence`, `test_process_tree_quiesced_requires_live_daemon_proof_and_refuses_lost_daemon`, `test_process_tree_quiesced_promotes_healthy_cold_restart_with_changed_pid`, `test_microvm_memory_exact_requires_independent_memory_pages_capture`, `test_child_branch_release_preserves_pinned_snapshot_until_explicit_destroy` | Non-EXACT, un-restored, or `memory_pages`-missing states refuse/downgrade `EXACT` `PROMOTED`, real restore preserves `EXACT`; `process_tree_quiesced` requires live healthy daemons, allows healthy cold-restart PID changes under `SEMANTIC_EQUIVALENT`, and refuses lost/unhealthy daemons; released children leave snapshot pinned until explicit destroy | Live E2B/Firecracker qualification and packaged UI remain M1–M3 |

---

## 10. Operational contract

Extensions implementing this SPI MUST emit five compact cost metrics aligned
with [Benchmark Program §6.2](long-horizon-harness-benchmark-research-program-v0.md#62-efficiency)
and [`ReplayPointMetricsSnapshot`](../../../loopx/capabilities/explore/replay_runtime.py)
(`wall_seconds`, `total_tokens`, `evaluator_runs`, `control_plane_requests`,
`recovery_events`), plus typed failure codes (`quiescence_required`,
`daemon_health_required`, `memory_capture_required`, `snapshot_missing`,
`egress_violation`, `scrub_incomplete`, `capacity_exceeded`), enforced snapshot
refcount TTLs, and read-only status readback via existing Explore/Benchmark run
surfaces.

---

## 11. Normative delivery plan

| Milestone | Shipped behavior | Entry gate | Exit evidence | Rollback |
| --- | --- | --- | --- | --- |
| M0 | Bilingual RFC + 11-case conformance suite in pytest (`test_sandbox_snapshot_fork_spi_conformance.py`) | Audited `#8461` / `#9209` / `#9211` & `replay_runtime.py` contracts | V1–V4 pytest pass, `check_rfc_bilingual`, `docs-governance-smoke`, `canary premerge` | Revert docs/test PR; zero runtime effect |
| M1 | First opt-in provider adapter (`firecracker_local` or `e2b_microvm`) behind extension package | M0 accepted; Linux/E2B test fixture pinned | Real pause/resume/snapshot/fork + scrub + symlink preservation + feature-off parity | Disable extension profile |
| M2 | Paired 5-tuple counterfactual pilot against warm `git worktree` / `layerfs_cow` | M1 conformance green; frozen task/budget manifest | Complete 5-metric cost attribution + Tier-2 $\tau_B(s; a, a_0, \pi)$ evaluation via `counterfactual_runtime.py` & `#9211` §§5.5/6.2 | Stop pilot; retain negative/null data |
| M3 | Optional product / benchmark promotion decision | M2 replicated net gain ($\Delta J(B \mid s_0) > 0$) + UI readback | Maintainer sign-off + packaged unavailability/recovery UX | Keep optional / retire adapter |

---

## 12. Open decisions

1. **OD1 (M1 Provider Ordering — Benchmark / Runtime Owners):** Choose whether
   M1 qualifies `firecracker_local` or `e2b_microvm` first, based on KVM host
   availability and reproducible offline CI fixtures. Default stays off.
2. **OD2 (Process-Restore Digest Granularity — Explore / Replay Owners):**
   Decide before M1 exit whether `microvm_memory_exact` equivalence digests cover
   only declared workspace + process-table hashes or full VM memory chunk hashes.
3. **OD3 (Cross-Arm Cost Amortization — Benchmark Measurement Owners):** Confirm
   how parent `snapshot()` build cost is split across $K$ forked counterfactual
   arms vs. reported unamortized per-pair under [Benchmark Program §6.2](long-horizon-harness-benchmark-research-program-v0.md#62-efficiency).

---

## Appendix A: Execution ledger (non-normative)

Dated execution records belong in [`ledger/README.md`](ledger/README.md) with
`.md` / `.zh-CN.md` mirrors once live provider milestones run.

## Appendix B: Decision log

| Date | Decision | Owner / approval | Alternatives | Normative sections changed |
| --- | --- | --- | --- | --- |
| 2026-11-22 | Proposed extension-hosted Sandbox Snapshot/Fork SPI over existing `replay_runtime` contracts (unmerged PR proposal) | Pending maintainer review | Core-embedded E2B SDK; guest-stored SQLite | §§1–12 |

## Appendix C: Evidence registry

| Evidence id | Claim | Baseline / environment | Artifact or command | Result | Privacy / validity boundary |
| --- | --- | --- | --- | --- | --- |
| E1 | Audited 5-tuple gap vs. `checkpoint_artifacts.ts` & `replay_runtime.py` | `dc79482c88dd75ea59785d165fd3050d9d5508c3` | Source inspection + [#8461](https://github.com/loopx-project/loopx/pull/8461), [#9209](https://github.com/loopx-project/loopx/pull/9209), [#9211](https://github.com/loopx-project/loopx/pull/9211) | Verified on canonical `main` owners | Read-only audit; superseded prototype `#8516` closed unmerged |
| E2 | M0 V1–V4 SPI conformance over shipped `ReplayPointRegistry` & `run_counterfactual_child_group` | Isolated worktree `python -m pytest` | [`test_sandbox_snapshot_fork_spi_conformance.py`](../../../tests/test_sandbox_snapshot_fork_spi_conformance.py) | Pass in PR validation (11/11 M0 tests) | Synthetic SPI provider fixture only; no live hypervisor run |

## Appendix D: Rejected alternatives

See Section 6 for why kernel-embedded hypervisor SDKs, in-guest `.loopx/`
cloning, and merging `pause`/`resume` with `snapshot`/`fork` were rejected;
revisit only if host isolation requirements fundamentally change.
