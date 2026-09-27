# Local authority: retirement cadence after integration

- Audit: `ce3862e33`, September 28, 2026; [中文](2026-09-28-retirement-cadence.zh-CN.md).
- Owners: overall roadmap R3/R4/R5/R6; shared authority D1–D3; TS migration T0–T4.
- This replaces the **current inventory/estimates** in the September 27 recovery
  and Host-supervision ledgers, not their historical validation results.

## Reconciled baseline

| Already merged | What no longer belongs in the remaining-work count |
| --- | --- |
| #5054 | Experimental Todo events projection/backfill/completion retired; supervisor log separated |
| #5102 / #5105 | File format upgrade/backup and native qualification/Python prototype retirement |
| #5140 / #5156 | Archive recovery/audit and shared-runtime read fairness |
| #5144 | Managed command/Codex CLI process supervision; **not** attached-Host cancellation |
| #5173 | Reviewed File↔SQLite cutover for already-promoted, quiescent Goals |
| #5175 | One native source-outbox drain; Python sequencing and obsolete entry-planning RPC removed |
| #5169 | Verified identical operation replay on File/SQLite |
| #5170 | App delegated-result continuity; not every Turn/instance consumer |

At this audit #4931 (SQLite read cost), #5106 (collaboration GoalRef), #5130
(session GoalRef), #5139 (App Turn acceptance recovery) and #4915 (local-state
location migration) remain open. Integrate/review those owners rather than
reimplementing them. Their scopes are dependencies only for affected callers;
local default does not wait for unrelated cloud or hundred-Agent work.

**File as the default store factory is not File as the default authority for
all new/existing Goals.** Unpromoted Markdown writers remain reachable. Two
successful Goal migrations, or File↔SQLite transfer, do not prove their absence.
No fixed “5–8 PRs remaining” is carried forward: below are named delivery and
qualification boundaries, not a promise about defect count or merge count.

## When and how to delete

| Boundary | Actual reachable code/caller | Earliest safe deletion and retained obligation |
| --- | --- | --- |
| Duplicate decisions / obsolete internal crossings | Inspect each touched TS owner and its Python caller; #5175 already removed the drain coordinator | In the same PR that switches the **last** caller and proves independent semantics. Delete handler/registration, helpers and obsolete implementation-only tests together. Do not invent more shadow/bridge layers. No additional dead module is certified by this planning audit. |
| Legacy Todo mutation | `loopx/todos.py` still imports `line_update.py` plus `provider_create.py`, `provider_update.py`, `provider_terminal_lifecycle.py` | After new-Goal and upgrade paths select canonical authority, covered existing cohorts migrate, and unupgraded callers receive an explicit upgrade/recovery route. Remove the writable Markdown branch by caller family; retain human narrative rendering and qualified import/export. An absent provider must not silently reactivate the old writer. |
| Shadow capture/drain | `runtime_shadow_writer_adapter.py`, `local_authority_shadow_outbox.py`, `runtime_shadow.py`; configure/CLI and old writers still use these | Remove producers/hooks after the last supported source writer exits. Keep the migration-owned reader/reconciler until prepared/committed outboxes are reconciled or explicitly dispositioned. Zero pending entries in one local Goal is insufficient. |
| Python command facades | `authority_core.py`, canonical Todo adapters and `quota/monitor_poll.py` have real runtime callers | Delete per complete native entrypoint adoption, including private validator/Host effects, output projection and error/retry behavior. Moving pure policy to TS does not make input/IO adapters dead. Do not delete whole files based on language or line count. |
| Historical formats and receipts | File/SQLite migration codecs, logical archives and command receipt recovery | Remove obsolete normal write paths; retain explicit migration/backup restore and original receipt readers for the supported upgrade boundary. Any eventual reader removal needs a format support decision and tested conversion, independent of business-writer deletion. |

Use one retirement manifest in the implementation PR: symbol/path, production
callers (including dynamic handlers/packaging), replacement owner, persisted
obligation, positive/negative evidence and rollback. Compare against the immutable
base. A zero-import search is necessary for internal removal, not sufficient for
public CLI/import or serialized contracts. Retain public behavior tests; remove
only characterization scaffolding whose retired implementation has no consumer.
Deletion is code retirement, not deletion of users' state, receipts or backups.

## Next delivery order

| Order | Complete outcome / owner | Concrete exit and deletion opportunity |
| --- | --- | --- |
| A — start now | Whole-Goal execution/consumer integration; R3/R5 and existing Host/Turn owners | Trace capture→drain→promotion→CLI/status/quota/App/Lark reads and writes→settlement→restart→reverse migration with new writes. Inventory managed, attached and external execution; real cancellation acknowledgement/settlement is required, expiry alone is not proof. Reuse #5173/#5175. Retire only duplicated coordination within this complete journey. |
| B — alongside A | Local profile qualification; D1/D2, reuse #4931 | Matched File/SQLite workload including domain graph, metadata, history, latency/RSS, burst/lag and cold installed CLI. Record platform/runtime and declared limits. Fix a demonstrated failing row at its owner. SQLite remains a candidate; an optimization or small rehearsal does not choose the release default. File is the control arm, not an automatic fallback if qualification fails. |
| C — after A and profile decision | New-Goal/default/install/settings adoption plus supported existing-Goal upgrade; D3/T3 | New and upgraded installs, CLI, packaged App and Lark agree on one selected authority. Verified backups, reviewed migration, crash retry, non-upgraded rejection and rollback carrying new writes all work. Release default is an explicit decision. Remove migrated legacy writer branches in the same caller-family PR; do not leave a “cleanup someday” tail. |
| D — with C, per last caller | Remaining transport and capture retirement; T4 | Delete unused facade/dispatch/producers once native consumers adopt them; retain necessary host IO and migration readers. Full Python removal is not a prerequisite for canonical defaults, nor an automatic consequence of them. |

Canonical execution tasks already cover whole-Goal promotion, local profile
qualification with a deletion inventory, and durable Markdown projection/rebuild.
Reconcile their evidence and continue those owners; projection failure must have
an explicit rebuild path without making Markdown a second writable authority.

A/C may split if distinct execution or onboarding owners need independent
rollback; name the reason and exact remaining exit when splitting. B is evidence
work and can reveal additional fixes, not a predetermined PR. After these local
outcomes, R6 still needs authenticated PostgreSQL transport, tenant/identity
operations, pooling/cancellation/failover and cross-host qualification; reuse the
existing store/archive/service owners. Do not delay local deletion for R6.

R3 instance/session adoption and R4 intent/acceptance continuity remain separate
product outcomes. Reuse the [deferred continuity scenarios](../../goal-immutability-coherence-defense-v0.md)
where the changed caller needs them; do not turn them into an unimplemented
universal gate. CAS success does not prove current Goal identity or task quality.

## Aggressive local qualification before deleting writers

These are proposed engineering windows from a frozen candidate, not promised
release dates. Run faults on disposable runtimes and detached verified copies;
never kill/rewrite live Goals to make a test pass.

1. **Now / first 1–2 working days:** pin binary/source and actual Node/SQLite
   driver; inventory installed versions, callers, providers and pending work.
   Keep independent legacy/File/SQLite arms. Prove backup restoration, exact
   Todo JSON/history/receipt parity and forward writes. Seed null/absent/false,
   archived dependencies, leases, validators, in-flight Turns and pending outbox.
2. **Next 2–3 working days, if the prior row passes:** remove the intended legacy
   branch in an isolated candidate (or make it fail loudly), exercise real
   commands and installed UI/host consumers. Inject process death before/after
   commit and selector publication, stale instance/revision, lock contention,
   unavailable runtime and interrupted projections. Retry must settle once;
   recovery must permit subsequent legitimate work. Test restore with a retained
   migration-capable binary, not by deleting the selector or restoring old bytes
   over newly acknowledged writes.
3. **Continuous observation on a qualified candidate:** collect actual elapsed
   time and workload coverage, command latency, memory/disk/WAL growth, oldest
   pending item/consumer lag, ambiguous-result recovery, duplicate-effect and
   stale-instance incidents. Daily readback and periodic recovery checks use an
   isolated observer/copy. Formal D2's applicable ten-day natural-time soak
   cannot be accelerated by looping tests or backdating timestamps. Count it
   only from a recorded start, with restart gaps and source changes explicit.
4. **Cohort then default:** after the above required evidence, perform reviewed
   backup/migration and observation of a bounded authorized cohort; expand only
   on demonstrated recovery. A local all-Goal migration does not prove external
   installs upgraded. Keep the old binary/artifacts for diagnosis, but select
   only a binary compatible with the current format for operation/rollback.

Stop candidate writes on lost acknowledged data, duplicate effect, cross-instance
contamination, selector/receipt disagreement or unrecoverable ambiguity; keep
read-only evidence and recover through the owning journal. Treat latency/memory
regressions against declared budgets as failed rows, not invitations to increase
limits. Local investigation may be aggressive; promotion/deletion evidence must
remain independently checkable.

## Evidence from this planning pass

At `ce3862e33`, local real-backend migration/crash suites passed 15 cases;
19 real CLI archive/upgrade/cutover and bounded source-capture tests passed.
The existing SQLite rehearsal completed 100 and 1,000 commits with cold CLI
sampling and cleanup. Its report remains **incomplete**, with formal workload,
capacity, platform and elapsed-soak rows missing; this run starts no soak.

A detached previously captured real source with 1,101 complete Todo records was
reconstructed into three synthetic source transactions. The current production
CLI drained all three, with full original Todo JSON unchanged. Four resulting
transactions were restored/audited into SQLite; a fifth synthetic acknowledged
write was then exported/restored/audited into File and retained. No active Goal
was changed. This proves bounded source drain and logical archive continuity,
**not** replay of all 224 original transactions, a live selector cutover, fresh
capture of current production state, or D2 qualification. Raw private snapshots
and diagnostics remain outside the repository. No production code is deleted
by this planning PR; it establishes the deletion exits and records their actual
validation boundary.

### Read-cost qualification update

After #4931 and #5215 integrated, matched detached File/SQLite copies retained
379 original commits and the same final projection hash. On Node 24.21.0,
three fresh processes per provider measured File head reads at 5.98–6.32 s
versus SQLite at 34.5–36.0 ms; repeated reads were 9.1–10.2 ms and 25.7–28.2 ms
respectively. This is process-cold, not OS-cache-cold: File proves its entire
retained journal, whereas SQLite reads current state without making the same
full-history proof. It is evidence for a long-history SQLite candidate, not
equivalent integrity-work throughput or release-default acceptance.

Alternating two unchanged File stores exposed singleton proof-cache eviction:
every read cost 6.30–6.49 s. A bounded four-store working set keeps the first
proof for each store (6.15–6.16 s) and subsequent alternation at 9.8–11.2 ms,
with identical cursors/hashes. Exact-byte and identity checks remain mandatory;
eviction and corruption regressions cover the changed cache boundary.

Quota observation reused the existing should-run compactors: a captured single
Goal row serialized from 1,252,747 to 78,688 UTF-8 bytes, with explicit full
detail restoring the original row. This is a display measurement; collection,
decision inputs and first-read verification are not reduced by it.

A separate 148-second isolated run appended 12 commits per provider through
fresh processes, crossing a checkpoint and checking original-receipt replay,
changed-intent rejection and projection/hash parity at every step. It qualifies
that bounded storage journey, **not** Host execution, live Goal adoption or D2's
ten-day soak. No active authority, release default or legacy-writer deletion
decision changes. B still needs sustained workload/platform/capacity evidence;
C still needs consumer/onboarding and supported upgrade acceptance.
