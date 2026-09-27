# Local defaults: managed Host supervision and reconciled delivery plan

- Baseline: `fd96e5e25`, audited September 27, 2026.
- Outcome: overall roadmap S2/S4/R5, shared authority external-execution closure,
  TS replacement-first migration. No provider or capability is introduced.
- This replaces the **remaining delivery estimate**, not the historical evidence,
  in the September 24 reconciliation. The recovery slice proposed in
  [#5140](https://github.com/loopx-project/loopx/pull/5140) is still open.
- [中文](2026-09-27-host-supervision.zh-CN.md).

## Count deliveries, not architectural headings

Complete-source transport/assembly, transaction capture, canonical pagination,
File v1 automatic backup/upgrade and Python prototype retirement are already on
main. #5013, #5063, #5102 and #5105 are not future work. Two promoted Goals prove
those particular cutovers, not every execution, migration or recovery boundary.

The old “three packages” and #5140's “three PRs afterwards” were too coarse for
execution protection. An actual subprocess reproduction shows the missing
prerequisite: generic Host timeout kills only its leader, while Codex cleanup
returns early after the leader exits. Both can leave descendants doing work.
Deleting a lease or rejecting a later result does not stop that process.

The current **four newly planned deliveries include this PR**:

| Delivery | Observable exit and Python retirement |
| --- | --- |
| **1. Managed subprocess supervision (this PR)** | Generic command and Codex CLI share one TS lifecycle through timeout, caller loss, pipe drain and process-group termination. Retire their separate Python termination/thread-reader implementations. This closes the process component, not the lease component below. |
| **2. Authority-bound execution interval** | Connect the existing provider-neutral lease owner to actual execution: current proof before start, bounded renewal, cancellation on expiry/reclaim/revocation, and uncertain-effect recovery. Reclaim must not silently overlap an old executor. Test with real processes and File/SQLite; explicitly qualify attached Hosts without cancellation. Remove replaced Python decisions rather than create a second lease store. |
| **3. Whole-Goal migration and fenced recovery integration** | Adopt #5140 recovery and #5054 source retirement; cover source drain, reviewed cutover, retained command consumers, projection readback and rollback after later writes. Inventory existing callers before adding writers. Delete legacy decisions only when their actual callers have moved. |
| **4. Default onboarding and bounded Python retirement** | New-Goal creation, settings, CLI, packaged frontend and Lark select the qualified local profile consistently. Existing Goals have explicit upgrade, backup and recovery. Remove remaining replaced Python business writers, retaining necessary rendering and Host IO adapters. |

**Three planned new PRs remain after this one.** This is a scoped delivery plan,
not an unconditional total or proof that lease supervision has shipped. It is
one additional execution slice compared with #5140's proposed estimate; the
reproduction above is the reason, and this PR does not subtract the uncompleted
lease row. If another slice is needed, amend its named row and evidence.

Separately, existing open PRs are #5140 (recovery/audit), #5054 (old Todo event
retirement and supervisor logging), and #4931 (SQLite receipt-proof encoding).
Thus the integration inventory is **six named PR deliveries for the File route**
(this + three planned + #5140 + #5054), or **seven for the SQLite route** including
#4931. These counts include already implemented open PRs; they do not mean six
or seven new implementations. #5140's SQLite batch proof read complements #4931;
neither small-suite success qualifies D2. New defects discovered by qualification
can still require changes, so there is no justified guaranteed PR total today.

D1 consumer parity, profile-specific D2 capacity/recovery/soak and D3 cohort
cutover remain acceptance work, not invented PR allocations. The audited #4224
1 MiB report still fails receipt p95 (269.03 ms / 50 ms) and scan-100 p95
(801.81 ms / 250 ms); this process change cannot fix or certify those metrics.
PostgreSQL retains its separate authenticated transport, tenant/identity,
cross-host execution, pooling/failover and operations qualification. Local
process cleanup is reusable across providers because it does not read their
physical layouts or create authority.

## Ownership and neighboring work

`control_plane/turn_driver/host_process.ts` owns the managed process lifetime;
its private bridge treats the Python owner's control-pipe EOF as cancellation.
Python adapts transient output, Codex sessions and typed results. Existing
`turn run-once` callers adopt this automatically; no new CLI option, configuration
editor, capability registration, frontend or Lark surface is needed. Attached
App sessions and in-process DSH adapters do not pass through this subprocess
owner and are not represented as newly protected.

#5141 fences Host state by GoalRef, while #5142 preserves effect uncertainty in
Turn error readback. Neither replaces process supervision. Integration must
retain their admission checks before launching and their recovery observations;
this PR changes neither GoalRef authority nor settlement semantics.

[Operational behavior and limits](../../../../reference/protocols/loopx-turn-v0.md#managed-host-process-lifetime).
