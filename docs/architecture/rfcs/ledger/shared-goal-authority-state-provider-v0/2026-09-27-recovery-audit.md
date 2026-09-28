# Local default cutover: recovery audit and remaining delivery scopes

- Historical recovery baseline: `157ab7b11`; current inventory: `70b3cca01`, 2026-09-27.
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

## Historical recovery delivery allocation

| Delivery | Observable result and remaining boundary |
| --- | --- |
| **1. This PR: reviewed restore and independent history audit** | A private verified input is consumed throughout restore. File/SQLite roundtrips preserve every logical row and original receipt. Audit checks historical transactions and receipt lookup independently, with explicit exact-head versus retained-prefix semantics. Real process death at a checkpoint can resume without rerunning acknowledged commits. SQLite batches receipt proofs in one read transaction without weakening scalar verification. No live selector/fence changes. |
| **2. External execution interval protection** | Existing lease owners supervise real Host execution, renewal, authority loss, cancellation and uncertain effects. Test expiry/reclaim while the old executor is still running. Post-execution rejection alone is insufficient. Attached Hosts without cancellation need an explicit supported boundary. |
| **3. Whole-Goal activation and rollback integration** | Reconcile #5054's retained-source inventory, then exercise source drain, saved reviewed cutover, all retained command consumers and fenced recovery/rollback together. Bind a recovered copy through an explicit transition; do not revive a source lease or overwrite later writes. Delete only Python decisions whose callers have actually moved. |
| **4. Default entrypoints and final bounded retirement** | New Goal creation, settings, installation, packaged frontend/Lark/CLI consistently use the qualified local profile. Existing Goals have explicit migration and disable/recovery paths. Remove last legacy business writers after their caller inventory and rollback constraints pass; retain rendering and Host IO. |

At that recovery checkpoint, this was **four planned new delivery PRs including the recovery PR, three afterwards**,
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

## Current delivery inventory and native drain (`70b3cca01`)

The current plan contains **seven delivery slots including this change**: four
already-open PRs and three scoped deliveries. It does not mean seven new PRs,
nor guarantee that seven merges suffice. Earlier counts treated whole-Goal
integration as a single PR before its recovery and execution gaps were bounded;
that was an architectural grouping, not a reliable PR commitment.

| Slot | Existing work / observable completion |
| --- | --- |
| 1 | **#5173**, open: reviewed File↔SQLite selector/fence cutover, durable backup, full-history audit, recovery and retry. Integrate it; do not rebuild it. |
| 2 | **#5144**, open: managed Host execution lifetime/lease supervision. Attached Hosts still need an explicit cancellation boundary. |
| 3 | **#5054**, open: retire legacy Todo event projection/backfill/completion and isolate the experimental supervisor log. |
| 4 | **#4931**, open: SQLite retained-proof encoding/read cost; rerun the applicable formal D2 workload instead of equating an optimization with qualification. |
| 5 | **This change**: move the complete bounded source-outbox drain to TS, remove the Python sequencing/proof/cleanup coordinator and the unused per-entry planning RPC. Keep existing durable formats, full receipt verification and the kernel-lock adapter. |
| 6 | **Whole-Goal integration**: combine the accepted slices with retained consumer parity, interrupted cutover/rollback and post-cutover writes. This drain is one completed subitem, not completion of that scope. |
| 7 | **Default entrypoints and bounded Python retirement**: qualify creation/settings/install/frontend/Lark/CLI, migrate existing Goals explicitly, and delete business writers only after their real callers have moved. |

#5169's content-aware idempotency work is adjacent and must be integrated without
rewriting it; it is not silently counted as another required default-cutover PR.
D1 consumer coverage, D2 capacity/ten-day natural soak and D3 cohort/maintainer
promotion remain **evidence gates**, outside the arithmetic. PostgreSQL service
identity, deployment and operational qualification remain a medium-term scope.

TS now inventories witnessed source files, invokes the existing receipt planner
and transaction owner, checks the monotonic budget after proof, and performs
cursor/cleanup effects under M → primary marker → kernel-lock exclusion. The
Python facade sends one bounded request with no source projection/history and
does not automatically retry a lost response. `shadow_drain_outcome_unknown`
requires a later explicit receipt-based drain; it never asserts no commit.
Unconfigured Goals with no capture state still make no drain RPC.

Real process-death validation found a shared lock defect: a zero-wait acquire
reclaimed a dead owner but returned timeout before trying the now-free path.
It now allows one immediate retry after proven reclamation; live owners still
reject immediately. This uses the shared lock owner rather than a drain-only
sleep or increased timeout. The two crash harnesses now share one scheduling
fixture and kill/reap the actual TS owner at the durable boundary.

The runtime shadow remains a **File candidate**, not a promoted authority or a
new SQLite shadow provider. SQLite remains a supported canonical promotion and
archive-restore target. No provider selector, registry or active Goal is changed
by this delivery. The existing CLI and inline writer drain entrypoints adopt the
same owner; no frontend/Lark configuration contract changes.

Validation for native drain: the new full-batch tests exercise the existing
mixed production-scale Todo fixture; real CLI SIGKILL, filesystem permission,
cursor tampering and File/SQLite reviewed-promotion tests cover the persistence
boundaries. Shared-store regression passes against isolated PostgreSQL 16.
A detached authorized snapshot supplies 1,101 complete Todo records; three
explicitly synthetic source writes produce a fresh four-transaction candidate.
Every original Todo JSON record survives drain and SQLite/File archive restore.
This is not replay of that snapshot's old transaction history or a live migration.

On three local three-entry trials, median drain time is 1.34 s on the audited
base and 0.41 s here, with 11 facade RPCs reduced to one. The kernel-lock process
is lazy and reused within a batch; it releases locks between sections. These
small warm-runtime measurements are not D2 p95/capacity claims. The existing
CLI output-budget suite fails on both base and head at 14,514 versus 14,500
characters for the crowded Turn JSON packet. No ceiling is raised; merge
qualification retains that failure rather than declaring all checks green.

## Shared-runtime latency reconciliation (`96a3b90f4`)

The recovery delivery above merged as #5140. The subsequent latency repair is
also on the current audited main. Its observations below remain historical
validation, not another open delivery or a D2 qualification.

The latency repair does not retire another Python owner or close D2. Isolated
fixed File snapshots reproduce 9.9–10.5 second cold history verification,
including a ping timeout at the original 10 second budget. Warm reads hide the
problem; alternating Goals evict the single verified read cache. An isolated
CPU profile attributes about 56% of samples to allocating code-point arrays in
the shared key comparator. An allocation-free comparator preserves ordering;
File verification yields between complete transactions and coalesces identical
in-flight proofs keyed by path, store identity and exact byte digest. A late
corrupt row still rejects an early receipt; failed proofs never become cache
entries. No schema, revision formula, timeout or selector changes.

On the same snapshots, cold reads take about 2.9 seconds and concurrent light
requests 18–52 ms. These are local observations, not formal capacity/p95 or
cross-platform qualification. One very large transaction, JSON parse, other
synchronous handlers and SQLite replay can still occupy the event loop; this
is not general worker isolation. The common comparator benefits every provider;
the yielding and in-flight proof lifecycle belong to File. #4931's digest
window remains a separate optimization. The regression uses a private real
server and the existing mixed Todo/lease/decision fixture; production locators,
active Goals and raw evidence are never modified or published.

## Local provider cutover (`76ff7c73c`)

Recovery/audit #5140 and runtime fairness #5156 are merged. This delivery adds
reviewed File ↔ SQLite cutover for an already-promoted canonical Goal: verified
backup, exact source/fence binding, identity-bound target, history/receipt audit,
serialized selector publication, crash resume and reverse migration carrying
new writes. Persisted active leases hold migration even after expiry. It does
not stop Hosts or migrate independently-owned Turn/spend state.

The current inventory is three existing open PRs (#5054 retirement, #4931 SQLite
proof encoding, #5144 managed Host supervision), this cutover PR, and remaining
whole-Goal integration/default-entry scopes. Whole-Goal acceptance still needs
source drain and all retained consumers/external execution boundaries; defaults
still need new-Goal/settings/install adoption and bounded Python writer removal.
Do not subtract one from a broad work package merely because its cutover subitem
shipped. An exact remaining PR count cannot be promised until that integration
inventory and D2 results determine whether additional bounded fixes are needed.
Capacity, platform coverage and natural-time soak remain evidence gates rather
than PR quotas. PostgreSQL keeps the shared logical archive/audit contract;
service authentication, tenancy and failover are not qualified by this command.

See [the reviewed cutover journey](../../../../reference/file-authority-state-log.md#reviewed-filesqlite-cutover).
