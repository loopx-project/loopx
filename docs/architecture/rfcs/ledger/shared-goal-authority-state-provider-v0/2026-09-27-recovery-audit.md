# Local default cutover: recovery audit and remaining delivery scopes

- Audited baseline: `157ab7b11`, 2026-09-27, plus this delivery.
- Owner: overall roadmap #4574 R5/G2; shared authority D2/D3; TS T3/T4.
- Supersedes the **current count**, not historical evidence, in the
  [September 24 reconciliation](2026-09-24-default-cutover-reconciliation.md).

## What is already delivered

Complete source transport/assembly, transaction outbox capture, reviewed
promotion, canonical pagination, File checkpoint/delta format upgrade and
bounded Python prototype retirement are on main. In particular #5013, #5063,
#5102 and #5105 must not be commissioned again. Two running promoted Goals do
not prove every supported source, consumer, rollback or execution lifecycle.

The older three-row plan grouped migration/rollback too broadly to be three
reviewable PR commitments. This audit splits its recovery prerequisite from
activation. The reason is concrete: restore verified an archive and then
reopened its mutable source pathname; a replacement could enter the isolated
target before the final digest mismatch stopped recovery. There was also no
independent read-only CLI proof of a restored store's complete retained history
and receipt lookup. These are recovery gaps, not missing capture writers.

## Four scoped deliveries starting with this PR

| Delivery | Observable result and remaining boundary |
| --- | --- |
| **1. This PR: reviewed restore and independent history audit** | A private verified input is consumed throughout restore. File/SQLite roundtrips preserve every logical row and original receipt. Audit checks historical transactions and receipt lookup independently, with explicit exact-head versus retained-prefix semantics. Real process death at a checkpoint can resume without rerunning acknowledged commits. SQLite batches receipt proofs in one read transaction without weakening scalar verification. No live selector/fence changes. |
| **2. External execution interval protection** | Existing lease owners supervise real Host execution, renewal, authority loss, cancellation and uncertain effects. Test expiry/reclaim while the old executor is still running. Post-execution rejection alone is insufficient. Attached Hosts without cancellation need an explicit supported boundary. |
| **3. Whole-Goal activation and rollback integration** | Reconcile #5054's retained-source inventory, then exercise source drain, saved reviewed cutover, all retained command consumers and fenced recovery/rollback together. Bind a recovered copy through an explicit transition; do not revive a source lease or overwrite later writes. Delete only Python decisions whose callers have actually moved. |
| **4. Default entrypoints and final bounded retirement** | New Goal creation, settings, installation, packaged frontend/Lark/CLI consistently use the qualified local profile. Existing Goals have explicit migration and disable/recovery paths. Remove last legacy business writers after their caller inventory and rollback constraints pass; retain rendering and Host IO. |

This is **four planned new delivery PRs including this one, three afterwards**,
not a guarantee that no acceptance defect will require another PR. The original
three *architectural packages* are not a decrementing PR counter. This PR closes
one named recovery slice inside package 2; it does not close all of package 2.
Future checkpoints must identify which row actually completed rather than
repeating a range such as “5–8”.

Existing PRs are separate: #5054 retires the old Todo event path and isolates
supervisor logging; #4931 optimizes SQLite retained proof reads. They were open
at the audited baseline. Do not duplicate them or request a new capture writer
for a source being retired. #4915 is filesystem placement, not authority default
selection. Further SQLite work should reuse #4224's evidence/contract and
coordinate any overlapping implementation with #4931.

## Evidence gates are not PR allocations

The latest #4224 formal 1 MiB report still has receipt p95 269.03 ms versus 50 ms
and scan-100 p95 801.81 ms versus 250 ms. No exact-head formal rerun on #4931 or
complete passing D2 report was present at this audit. The planned soak end date
is not an observed pass. Domain workload, steady-state RSS, large-history
recovery, consumer lag, upgrade/rollback and platform coverage remain distinct
rows. This PR's small checkpoint/crash matrix does not qualify the formal
100k/300k workload or replace ten days of natural elapsed soak.

D1 consumer parity, D3 reviewed cohort activation and maintainer default choice
also require real evidence. A File opt-in, qualified SQLite default and migration
of all existing Goals are distinct claims. It is therefore not honest to give
an unconditional total PR count or a calendar deadline today.

PostgreSQL reuses the same archive auditor and logical transactions. Its real
isolated store integration is exercised here; authenticated transport, tenant
policy, restore-incarnation operations, failover/pooling and capacity remain its
separate medium-term path. Local default does not require that service deployment.

## Delivery contract

The existing authority-archive CLI owns this administrative journey. TS owns
format verification, snapshot lifetime, historical comparison, resume decisions
and provider readback; Python only projects CLI input/output. This introduces no
capability/provider registration, storage format or new settings. Frontend and
Lark business readers continue through the same provider interfaces; no
companion configuration editor is needed.

The negative matrix covers replaced and damaged input, old-history divergence
behind a matching head, missing or unavailable receipt lookup, incomplete pages,
incarnation changes and concurrent appends. File/SQLite process-death tests and
real PostgreSQL cross-provider tests retain actual storage. The local-source
rehearsal uses a detached byte-verified copy, never an active Goal mutation.
Public evidence excludes private Goal state and raw logs.

[Commands, semantic changes and operational limits](../../../../reference/file-authority-state-log.md#provider-migration-and-recovery).

The detached real-source rehearsal exposed a 300-second restore RPC timeout:
per-row receipt readback repeatedly replayed the same SQLite checkpoint window.
The bounded batch receipt method addresses that redundancy at the existing
provider port; the scalar method delegates to the same proof owner. Archive
restore and audit share page comparison, while uncertain writes force immediate
proof. This complements, rather than replaces, #4931's proof-encoding work and
neither raises the RPC budget nor qualifies D2's separate performance gates.

Validation on this delivery: 336 native archive/SQLite conformance and crash
checks, four CLI checks, and four cross-provider checks against an isolated
PostgreSQL 16 server passed, with no skipped checks in these suites. A detached
129-commit real-source snapshot passed File and SQLite restore and independent
audit. Recovery of the earlier timed-out SQLite destination passed without
reissuing its committed operations. These checks do not claim active cutover.
