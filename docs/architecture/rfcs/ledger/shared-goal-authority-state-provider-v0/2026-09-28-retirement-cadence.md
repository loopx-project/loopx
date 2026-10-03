# Local authority: retirement cadence after integration

- Current plan: October 2, 2026, `9b0486dc1`; historical audit: `ce3862e33`; adoption follow-up: `71525ab90`, September 28, 2026; [中文](2026-09-28-retirement-cadence.zh-CN.md).
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
| #4931 | Owned TS state replay reduces SQLite/archive historical reconstruction; no default change or D2 qualification |

At the adoption follow-up #5106 (collaboration GoalRef), #5130
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

### Merged T4 slice: unused Python lease/handoff facades

The caller audit at `e240730ec` led to #5395, merged at `8474c8d86`.
The following unused internal crossings are retired. Native decision and
transaction owners remain; this is independent of D2 qualification and
default-entry adoption.

| Removed boundary | Last caller / replacement | Compatibility and validation |
| --- | --- | --- |
| `authority_core.py` acquire/renew/transfer/release, owner-eligibility and handoff-transition command facades | Only the old core tests; real lease and handoff adapters already use whole native transactions | No persisted command format or public CLI schema changes. Retain independent native generation, replay, conflict, cleanup and quiescence tests; exercise real File/SQLite entrypoints. |
| `task_lease.acquire.decide`, `task_lease.lifecycle.decide`, `coordination.handoff_mode.plan` RPC registrations | Only those retired facades / handler tests; native transactions call the same typed rules directly | Obsolete private RPCs now reject unsupported methods. Keep `task_lease.owner_eligibility` and write-scope overlap: actual Python callers remain. |
| Lease-only `local_snapshot.py` normalization and error projection | No remaining caller; native executors own lease facts and errors | Keep `todo_snapshot_from_mapping`, used by live Todo mutation authorization. No store, receipt, backup or migration reader is removed. |

`authority_core.py` is still a live Todo bridge. `LeaseAction` and
`LeaseModeGateCommand` also remain because the semantic-vocabulary registry
explicitly retains that input contract until its M4 review. This slice does not
lower semantic coverage floors to discard a declared compatibility obligation.
Old facade-only tests retired with their implementation; public/native behavior
tests remain. Reverting this slice restores the internal crossing without a data
conversion. Local CLI adoption at `db3672f3c` verifies a clean source manifest,
qualified SQLite runtime, current known authority formats and healthy canonical
contract readback. This does not certify every installed Host or D2.

## Current closeout: validation, migration and deletion (2026-10-02)

Rechecked against main `8b5335a72` and the linked PR heads. This is the current
execution plan for **R5 / D1–D3 / T0–T4**, replacing the previous A–D schedule;
older measurements remain source-specific evidence. R6 is a separate successor.
Storage format, authority selection and ownership policy are three distinct
migrations. A SQLite database does not imply canonical default creation or the
retirement of the `legacy` handoff policy.

### Actual baseline and merge queue

| State | Delivered boundary / next action |
| --- | --- |
| Merged: #4931, #5251 | SQLite replay/proof and allocation improvements. Reuse these implementations and retain their matched evidence; D2 is not certified by their merge. |
| Merged: #5395, #5417 | Unused Python lease/handoff crossings and duplicate settlement admission/recovery decisions retired. Continue deletion at actual last callers; do not count these again. |
| Merged: #5436 | Original delegated Host lease renewal. Final Todo validation and stop acknowledgement remain distinct boundaries. |
| Merged: [#5413](https://github.com/loopx-project/loopx/pull/5413), head `2c99505c7` | Separate provider promotion from backed-up policy migration; reject fresh legacy configuration but recover historical operations. Retain the original CLI recovery evidence. Existing legacy Goals are not automatically migrated; a successful plan does not qualify their execution consumers. |
| Merged: [#5466](https://github.com/loopx-project/loopx/pull/5466), merge `066b5bf26` | Preserve the original lease through final acceptance. Installed consumer qualification remains distinct from merge. |
| Review: [#5283](https://github.com/loopx-project/loopx/pull/5283), `1012d37f3` | Preflight optimization remains under review. Retain failed cold-CLI qualification rows; functional projection parity alone does not establish a performance pass. Do not declare the historical transient open failure explained by a synthetic failure. |
| Approved, awaiting maintainer merge: [#5500](https://github.com/loopx-project/loopx/pull/5500), `a9e3d722c` | Recover the original canonical Goal creation operation through App retries. This creation/default-adoption prerequisite does not retire existing ownership policies. |
| Affected-lane dependencies | [#5308](https://github.com/loopx-project/loopx/pull/5308) must prove child stop before settled acknowledgement; [#5398](https://github.com/loopx-project/loopx/pull/5398) preserves complete UI history/inspector facts. Scope these to consumers actually included in the trial. They are not SQLite-engine prerequisites or permission to ship a known broken journey. |

The remaining open heads above concern preflight and creation recovery, not a
fixed number of PRs to universal completion. The implementation packages remain creation/default adoption,
policy migration plus legacy-policy retirement, and old-writer/capture retirement.
They may combine only when caller ownership and rollback are coherent. Validation
can expose concrete repairs; do not manufacture a fixed remaining-PR total or
restart completed work to maintain one.

The Goal-settings policy-migration slice now uses the same backed-up TS owner
as the CLI: read current policy, preview claims/leases, apply, and recover the
original operation after a lost response or same-tab reload. Synthetic real
File/SQLite HTTP journeys cover metadata preservation, stale source, cross-Goal
and digest rejection, expired unreleased leases and corrupt backups. The packaged
App journey uses a real SQLite authority behind a synthetic workspace directory,
including dropped apply response, reload/retry and narrow-screen readback. This is
source validation, not installed adoption or legacy execution retirement. Lark
policy editing remains outside this slice; existing Lark actions read canonical
state. Next: qualified creation/upgrade callers and authorized Goal adoption,
then delete live legacy branches at their last callers.

### Ordered delivery packages and exits

| Package / existing owner | Work and decisive exit | Dependency / deletion / schedule |
| --- | --- | --- |
| Close current heads; R3/R5 | Resolve exact-head findings in the open PRs above, inspect affected failures/conflicts, and present reviewed heads for maintainer merge. Record what is merged versus installed. | First target: 1–2 working days, subject to actual review/fix results. No unrelated optimization PR before closing these outcomes. |
| Installed recovery candidate; D1/D3, existing whole-Goal promotion task | Pin one merged source and actual CLI/App/Effect Node/SQLite identity. Independently restore a verified backup, run the matrix below on detached real data plus synthetic negatives, and complete File→SQLite→new writes→File. Then perform authorized per-Goal adoption and ordinary readback. | Begin immediately after relevant merges; target 1–2 working days for the bounded matrix. Keep the compatible recovery binary and archives. No live corruption/crash injection. |
| Bounded opt-in cohort; D2/D3 | When installed recovery and relevant execution controls pass, offer a reversible trial to at most 20 core developers. Publish workload/platform limits, backup/migration/disable instructions, known gaps, stop conditions and reporting route. Collect real daily use and failed cases. | Does not wait for every formal D2 axis or a new ten-day certificate. No invitation until rollback retains new writes. Does not certify a release default. |
| Canonical creation/default adoption; D3/T3 | Reuse `machine_configuration/goal_storage.py` and `local_authority_defaults.ts`. Current setting only chooses the **post-promotion target** (`promotion_performed: false`). Complete new-Goal initialization, retry and upgrade, settings plus packaged App/CLI/Lark readback; explicit existing selectors stay pinned. | Implement after the bounded candidate is useful; activate the release default only at the decision below. Remove replaced creation/selection decisions in this package. Changing `file` to `sqlite` in one setting is insufficient. |
| Two ownership policies; R3/R5/T4 | Use #5413's backup/plan/migrate owner. Inventory old/missing modes, finish eligible claims/leases and Host effects, migrate each authorized Goal, then narrow normal runtime types and defaults to `soft_claim` / `hard_lease`. Expose preview, authorized apply, result and failure/recovery in the existing Goal settings surface through the same owner; a CLI-only migration stage is partial. | Can proceed alongside cohort observation. Policy migration is independent of File↔SQLite conversion. Delete legacy execution only after the supported upgrade path and affected callers pass; never silently reinterpret legacy as soft. |
| Legacy writer and crossing removal; T3/T4 | Switch each last real caller to its TS owner, verify the matrix, delete Python decisions/private dispatch and old Markdown writes together. Reconcile shadow backlog before removing producers. Test the packaged CLI with retired paths absent. | Start already-proven internal deletions now; writer deletion follows that caller family's migration/adoption, not every R6 task or all Python disappearing. Each deletion has a concrete inventory and rollback. |
| Release-default decision; R5/D2/D3 | Reconcile supported installations, current-release comparison, representative sustained reads/writes/recovery, resource growth and existing soak applicability. Publish exact supported profile, failed/missing rows, release/upgrade guidance and disable path; disclose the default change. | No date inferred from test/PR counts. Formal ten-day/100k qualification retains its own required evidence. Existing File selections remain supported and pinned; unavailable SQLite never silently revives an old writer. |

These windows are engineering targets, not acceptance certificates. Assess older
soak evidence from #4224 by source and changed boundary before deciding which
parts need rerunning; an unrelated commit does not erase elapsed time. The
current public record does not establish a completed applicable soak result.

### One reusable validation matrix

Record candidate and independent control revisions, actual runtime, complete
fixture/history digest, commands, pass/fail/untested, and stop/rollback outcome.
Use the current supported release as the performance control; retain the original
pre-migration baseline as a separate product comparison. Isolate both data and
Effect processes. Do not truncate metadata, history or decision inputs to win.

| Boundary | Required experiment and invariant | Existing evidence owner |
| --- | --- | --- |
| Backup and complete data | Verify online SQLite snapshot and logical archive restore. Compare full Todo JSON, absent/null/false, unknown metadata, role/task class, archived dependencies, validation contracts/revisions, claims/lease generations, original events/receipts/cursors and the supported Goal/source state. Enumerate every stored family; counts or a final-head hash alone are insufficient. | `test_authority_archive.py`, `authority_archive_audit.test.ts`, archive crash/restore and migration suites |
| Forward and reverse migration | File→SQLite; add/update/complete and replay a real new operation; restart; export to File; assert all old facts **and the new writes** survive. Lost responses and identical retries return original outcomes; a different intent with the same operation ID rejects. | `local_authority_migration.test.ts`, archive and reviewed-cutover CLI suites |
| Mutation and ownership | Create/claim/update/complete/supersede/archive; quota selection→refresh→spend; same-Todo contention, stale revision/epoch, lease renew/release and applicable policy migration. One commit/effect/settlement, no ownership invention. | Real File/SQLite command suites; `test_quota_authority_settlement_journey.py` joins legacy→hard migration, rejected unleased edit, leased write, returned settlement retry, migration replay after work and next-Turn admission with the Markdown source absent. #5413/#5436/#5466 cover adjacent migration/lifetime boundaries; shared changes also use isolated real PostgreSQL. |
| Interruption and recovery | Process death before/after durable commit and selector publication; provider unavailable/busy, disk-full injection, stalled projection and lagged consumer. Reopen/retry settles once and permits legitimate subsequent work. A still-running child cannot be called stopped/settled. | Existing crash/migration/process suites; #5308's affected Host lane |
| Installed consumers | CLI `status`, quota, Todo list/detail; packaged App list/inspector and ordinary mutation; Lark when included. Counts, metadata, freshness, error/recovery feedback and original-route results agree with canonical facts. Test restart and old page resource loading. | Existing projection/consumer tasks and packaged frontend smokes; #5398 where affected |
| Cost and endurance | Same data, history, durability and commands: cold full CLI versus warm store, p50/p95/p99/sample count, RSS, database/WAL and write growth, lock contention and consumer lag. Preserve failed formal macOS cold-CLI and missing axes; disclose absolute and relative current-release regressions. | #4224, SQLite comparison/rehearsal runner and existing performance-diagnosis capability |
| Deletion proof | Remove/disable the candidate old path in a disposable checkout; run real entrypoints and historical recovery. Inspect imports, dynamic handlers, packaging and fixtures for the last caller. Unsupported old input requests migration, never a Markdown fallback. | Implementation PR's retirement inventory and independent semantics/negative tests |

For a bounded cohort, a material user-journey regression or failed recovery blocks
that affected lane. A proposed microbenchmark target is not a universal veto;
review measured tradeoffs without rewriting frozen reports. Data loss, altered
receipts, duplicate effects, wrong Goal identity or broken fencing always stop
writes at the affected boundary. Keep read-only evidence and recover through the
journal; rollback must export current committed state, not overwrite it with a
pre-migration snapshot.

### Migration order and exact retirement boundaries

1. Inventory each Goal's selected provider/format, promotion state, policy,
   runtime, pending Turn/outbox/projection and actual writers. Existing canonical
   SQLite Goals need validation, not another promotion. Canonical File Goals need
   provider migration only when selected; unpromoted Markdown Goals need complete
   capture and writer fencing. Do not confuse a database file with its selector.
2. Make and independently restore verified backups before migration. Stop new
   admission for that Goal, drain/settle real in-flight work, revalidate the
   source digest and plan, then use the reviewed CAS/selector owner. Neither lease
   expiry nor a process exit alone proves external effects stopped.
3. Adopt and read back each authorized Goal; later writes remain canonical.
   Source/provider/policy migrations keep separate receipts and recovery. A plan
   without a result or an ambiguous response is resumed through its operation ID,
   never by editing registry/selector bytes or replaying effects as new.
4. Delete according to this inventory; current source paths are candidates, not
   a claim that every listed module is already dead:

| Retire | Replacement and earliest exit | Keep / explicitly do not delete |
| --- | --- | --- |
| `legacy` as a live ownership policy; missing-mode runtime default | `handoff_mode_policy.ts`, `handoff_mode_facts.ts` and actual lease/Todo/Host callers use the two explicit policies after versioned upgrade and consumer qualification | Old values only in migration decoding and original-receipt recovery. Replaying an old operation grants no new execution and cannot replace a later policy. Missing receipt rejects fresh legacy intent. |
| Writable Markdown Todo branches in `todos.py` / `todos/line_update.py` | Canonical create/update/terminal owners; new/default and supported existing Goal paths migrated | Human narrative, permanent Markdown projection/rebuild, validated import/export and old backup recovery; no missing-provider fallback |
| `runtime_shadow_writer_adapter.py` and obsolete capture producers | Last supported source writer removed; pending prepared/committed outbox classified and reconciled | Migration-owned historical outbox reader until its actual recovery obligation ends; no second ongoing capture authority |
| Duplicate Python decisions and private RPC facades | TS transaction owns semantics, effects and output; last production/dynamic/packaged caller switched with parity and recovery | Still-used transport, Host IO, specialist providers; do not delete `authority_core.py` or provider adapters wholesale because they are Python |
| Old normal format readers/writers | Versioned backup/upgrade before normal runtime opening; runtime uses current format only | Migration-only codecs and original history/receipts for the declared support window. Reader removal needs a separate compatibility decision, not all installed users inferred upgraded from local success. |

Policy defaults are based on execution responsibility, not database brand:
`soft_claim` is the candidate for a local workflow whose ownership is cooperative
and whose effects need no exclusive execution grant; `hard_lease` is the candidate
for shared/cloud or overlapping workers and fenced external effects, including
local managed execution when required. Unknown topology requires explicit choice;
no implicit legacy default and no blanket soft fallback. Existing explicit
policies remain pinned until a reviewed migration. Final defaults are qualified
through their actual callers, not decided solely by “local” versus “cloud”.

### Canonical Todo ownership and update rules

Reuse the existing SQLite admission/Python retirement task as the program owner;
record the next concrete package, dependency, exact evidence and deletion exit in
its note. Reuse the whole-Goal promotion, canonical consumer inventory, permanent
Markdown projection, Host lease lifetime and full-summary/detail tasks for their
boundaries. Do not duplicate their work because an older note names a merged PR.
The existing closeout monitor should group the related head/review/merge changes,
then wake the relevant owning task; quiet polling is not advancement.

Implementation gaps need actionable work with an explicit owner and acceptance:
canonical default/upgrade adoption; two-policy migration plus legacy execution
removal; and last-writer/capture deletion. Link these through existing task
successor/dependency fields rather than creating a second RFC or one monitor per
PR. Complete them only after installed readback and the documented deletion,
not at plan publication, review request or merge. Private Goal inventories,
backup paths, measurements and canonical task IDs stay outside public docs.

## Aggressive local qualification before deleting writers

Physical backup consistency is part of the recovery row. Copying a live SQLite
database and WAL as separate tar members can lose already acknowledged rows
when a checkpoint lands between the copies, even if the restored database
passes `integrity_check`. The general state-backup entry therefore reuses the
qualified TS online snapshot owner from format upgrade, omits its live sidecars,
and publishes only after snapshot verification. Python retains directory
discovery and archive IO, not a second SQLite engine or migration decision.
This closes a per-database backup defect; whole-Goal multi-file quiescence,
subsequent-write rollback, installed consumer recovery and D2 observation remain
separate evidence requirements.

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
4. **Cohort then default:** use the three-decision table in RFC Section 7.2; a
   bounded opt-in trial may precede formal elapsed qualification after installed
   recovery and the relevant execution controls pass. Perform reviewed
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

## Adoption follow-up and next decision

At `71525ab90`, the installed CLI, locally built App/bundled runtime and both
services resolve to the same source. Installation doctor reports the pair as
matching; the actual chat page renders and the previous delivery's entry JS/CSS
remain available with identical bytes. This is local installation evidence,
not a signed/notarized release or a messaging/settlement acceptance result.

Fresh logical archives retain 379 and 993 original transactions. Restore plus
exact audit matches the 379-transaction archive on File and SQLite and the
993-transaction archive on SQLite, including the original transaction/receipt
proofs and complete projections. This extends the earlier synthetic-drain
evidence to retained real history. It does not test reverse migration after a
new write in this run; the earlier bounded result remains separately scoped.
Private archives, registry data and raw diagnostics remain outside Git.

The initial rehearsal separated data but reused a live Effect process. Those
latency samples are excluded. The final audit used a verified independent
process; ordinary command resampling succeeded after shared work settled.
The [testing guide](../../../../development/testing-and-quality.md#isolate-the-managed-effect-process-as-well-as-the-data)
now specifies both isolation boundaries. Concurrent heavy-admin fairness is
not qualified by the clean resample.

Keep existing authority providers unchanged. Reuse #4931's measured SQLite
candidate decision for B, rather than reopening the same optimization. Before
selecting a consumer optimization, trace whole-command costs and duplicated
projections: a history row limit does not bound semantic history, and status
and quota can still produce multi-megabyte diagnostic packets. Preserve
decision completeness and existing drill-down contracts at their shared typed
owner; do not infer that backend switching alone fixes these costs. A/C still
need integrated execution/adoption evidence, and no D2 elapsed soak starts or
legacy-writer deletion is certified by this follow-up.

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

### Contract health follows Todo authority

#5222 is merged and locally adopted after backup, CLI/App/service upgrade and
actual page readback. Default quota output is about 93 KB versus 1.37 MB with
full detail, with equal Todo counts; 13 previous-delivery static resources match
byte for byte. This is adoption evidence, not a new formal release, provider
default switch or completed D2 soak.

An isolated public CLI counterexample found that the Todo list reads canonical
state while contract health still parses Markdown Todos. Adding only a stale
User Todo without task_class to the display copy makes a healthy File or SQLite
Goal fail status with exit code 1. The repair routes promoted contract checks
through the existing TS canonical snapshot/record validator and shared User Todo
class/scope rules and supported Todo metadata health; Python transports bounded
semantic fields and adapts the diagnostic. Agent routing, claim/exclusion
conflicts, removed policies and legacy status errors remain unhealthy. Structural validity alone does not make an open User Todo healthy.
Missing providers and corrupt read models remain Goal-scoped errors,
with no Markdown fallback. Unpromoted Goals retain legacy checks; invalid UTF-8 yields a structured read
error while still rejecting the command. Narrative,
registry, history and public-boundary checks remain. This does not introduce or
replace Todo authoring validation, nor reauthorize completed/deferred history.
Real File/SQLite controls cover both persisted record shapes, absent display,
invalid active class/scope and Agent metadata, valid implied historical bindings,
legal executor exclusions and completed/archived records without a class. Narrative text stays outside the diagnostic RPC; large
collections are transported in bounded batches without changing message limits.

A paired isolated contract-only measurement uses the 1,109-Todo current
projection from retained history and an approximately 7 MB display file. Three
warm samples for the initial structural-only repair fell from 0.52–0.58 seconds
to 0.11–0.12 seconds for File and 0.14–0.16 seconds for SQLite. These timings
precede the active User Todo semantic correction and do not qualify its cost.
The experiment reinitializes the current projection;
it is not full history replay, whole-status latency or cross-platform capacity
qualification. Private inputs remain outside Git.

Scale characterization with 4,101 synthetic Agent Todos still hits the existing
`todo.succession.project` RPC response budget in whole `status` on both base and
repair for File/SQLite. The repaired contract API can read that collection;
this does not qualify the remaining whole-command payload boundary.

B distinguishes a bounded provider PR, a recoverable opt-in developer cohort,
and the release default, as specified in Section 7.2 of the owning RFC. Proposed
absolute latency budgets do not veto every merge or trial: use matched current
release measurements, disclose absolute and relative regressions, and inspect
their consumer impact. Correctness, original receipts, complete metadata and
recoverable migrations remain hard requirements. Frozen reports retain their
original budgets and failed/missing rows; revising a decision does not rewrite
past evidence.

[PR #5251](https://github.com/loopx-project/loopx/pull/5251) refines strict JSON
materialization behind the existing codec owner. It preserves persisted
canonical encoding while avoiding repeated immutable primitive allocation in
historical projections. Its historical formal reports are source-specific:
the author reports 8 passed / 6 failed / 10 missing on `d767b06f1`, then
14 passed / 0 failed / 10 missing on `02d3dee83`; neither report qualifies a
later head, nor completes the missing axes. The next qualification reconciles
changed-path measurements and affected real providers/callers, then verifies
concurrency, recovery, consumer lag and retained natural-time soak applicability.
The comparison runner's former conflict expectation contradicted merged #5169:
an identical historical intent must return its original applied revision/cursor.
The runner now checks that result, independently rejects projection/event/receipt
drift, and walks the complete history before and after retries without retaining
all expected snapshots. A failing invariant prevents report publication; checks
stay outside the unchanged timing windows. This repairs the qualification tool,
not a provider defect or a D2/default pass. #4224 already reports a soak started
on September 14 at `e98191faa`; its final result and applicability to the current
candidate still need evidence. Do not call it unstarted or restart its clock
solely because an unrelated source revision changed.

At source `613180ae`, the matched 64 KiB macOS run passes 13 rows, fails
cold CLI status p95 (4.39 s against 2 s), and leaves 11 missing. The Linux
storage-only run passes 12 rows with 13 missing; its smaller CLI rehearsal is
not the formal CLI axis. Opt-in `performance-diagnosis` captures Python wall
time, independent Node CPU, and Linux thread stacks on disposable targets.
Startup, candidate scanning and waits are hypotheses to test with unchanged
uninstrumented workloads; profile weights do not replace this admission failure.

[#5283](https://github.com/loopx-project/loopx/pull/5283) is the adjacent
read-only delegation preflight optimization, not a provider implementation.
Its current process-reuse candidate reports paired File/SQLite warm gains with
an explicit cold-start cost; retain it for exact-head review, installed readback
and real requester adoption. Qualify that repeated consumer independently from
the cold-only status workload above. Keep decisions fresh at the existing TS/CLI
owners; the pinned Python worker remains transport, not a new decision cache.
Retire Python rules only when their TS replacement and last callers are proven.

Last-caller Python decision retirement can proceed independently where the TS
replacement and affected real callers are proven. Whole Markdown writer removal
still requires C's new-Goal/upgrade/recovery exits. Complete consumer metadata,
freshness and decision inputs remain acceptance requirements. Contract checks
and attention now share one request-local, validated canonical
Todo snapshot per runtime/Goal. Standalone checks and subsequent requests read
afresh; lease and projection-writeback reads do not participate. Consumer edits
cannot mutate retained input, and a failed first read cannot recover midway
through the request. This is not an atomic snapshot across registry, Markdown,
history or multiple Goals. Recheck installed consumers after integration; A/C and D2
retain their own open acceptance. Retire each writer only after its last
supported caller and recovery acceptance are qualified.


A matched isolated current projection with 1,117 retained Todos reduces full
status assembly from two complete Todo reads to one. Three warm in-process
samples had medians of 496→430 ms for File and 583→488 ms for SQLite. Base/head
payload differences were confined to observation timestamps and age fields;
full metadata and public response schemas are retained. Tracemalloc measured
Python peak allocations of about 13.5→16.5 MB on both providers: retaining
consumer-isolated input trades roughly 3 MB peak memory for the avoided read;
retained allocations after return remain about 2.1 MB. This is current-state
read-cost evidence, not historical replay, CLI cold-start, D2 qualification or
a provider-default comparison. Python owns the request's transport-input
lifetime; TS still owns validation, resume, succession, acceptance and selection.
Resume input preparation now runs only for groups that contain a wait condition;
succession still receives complete lineage, and waits still see archived and
cross-role dependencies. On the same 1,117-Todo isolated current projection,
against the baseline that already shares snapshots, structured-item calls fall
from 2,687 to 1,570; native reads remain one and TS effect calls remain 16.
Three warm samples give File medians of 430→425 ms and SQLite 493→481 ms.
The small latency difference is not cold-start or provider-default evidence.
Actual agent and full-Goal CLI responses retain their size and semantics apart
from observation time/age fields. The full-Goal response remains about 2 MB.
A follow-up on `b9a34c3e7` isolates the shared read-model validator: it
serialized the full Todo array twice solely to check record order, despite an
already validated unique-id index. Compare that index's insertion order with
its existing Unicode-sorted ids instead; retain the full content digest,
record validation and provider reads. On a detached 1,117-Todo/36-lease current
projection, ten warm Node samples per provider reduced validator medians from
42–43 ms to 27 ms. This is a common TS cost, not evidence to rank providers or
change the default. No cached authority, lease omission, response cap or
frontend contract change is introduced. Unicode order, duplicates, malformed
JSON, archived-record tampering and both record formats remain rejection tests.
The next bounded step, based on `c57454e40`, reuses one validated Todo identity
index within each synchronous collection or ownership consumer. It removes
repeated record copies while preserving each consumer's validation order and
Todo-only independence from lease integrity. It does not share a provider load
between RPCs. On the same detached projection, ten alternating warm samples per
arm give full Todo/lease collection medians of 32.4→27.5 ms (File-loaded input)
and 32.9→28.0 ms (SQLite-loaded input). Ownership, including its provider load,
changes 43.2→39.8 ms and 62.9→61.4 ms respectively; the latter has an outlier.
Full CLI status retains all records and metadata, differing only in observation
timestamps and read ages. Real File, SQLite and PostgreSQL suites pass. These
component results do not establish a cold-start gain, sustained-operation
acceptance or a provider-default decision. Cross-RPC ownership/status reads and
full-Goal frontend summary/list/detail remain separate unfinished work. Agent
status already has bounded display; final JSON compaction alone does not remove
full-source computation.

### Succession transport capacity

A current 5,000-row summary reproduces a separate B boundary: the initial
whole-graph succession evaluation fits, but resending its facts and evaluations
for display verification exceeds the existing 2 MiB request limit. The
co-deployed internal succession RPC now uses declared, strictly checked columns
for both facts and evaluations, following the summary adapter's existing pattern.
No rows, lineage edges, hashes or metadata are dropped; the TS graph and reuse
validators are unchanged. The old internal wire shape is replaced, not retained
as a second parser; persisted Todo formats and public responses do not change.
The representative request falls from over 2 MiB to about 0.96 MB without raising
budgets. Real File/SQLite CLI tests cover exact counts, distant inferred edges,
metadata preservation and unchanged provider state. This is a bounded capacity
repair, not unlimited graph capacity, stable latency evidence, D2 qualification
or permission to change the default provider.

### Retained completion consumption

The completed-history consumer
now explicitly reads retained active and archived records through the existing
typed summary/filter owner. Previously, archiving 84 of 85 completions made
the workspace history return only one row. Its bounded HTTP pages now include
all 85, preserve full task text/evidence and Agent filtering, and expose only
display fields. Explicit history reads opt out of the existing 500-character
summary limit; the default active Todo list and scheduling input still exclude
archives and retain their display limit. Real File/SQLite and legacy Markdown
reads preserve provider state. The packaged workspace keeps its existing
pagination and opens retained evidence in the read-only Todo inspector using
the shared Markdown renderer, without another navigation step or edit action.
History also preserves completion time, resume facts and validation-declaration
revision/actor facts. Active and history consumers reuse the same TS inspector
mapping and the existing Todo schema; the active queue's preview budgets remain
unchanged. Unprovided dependencies are not presented as an empty dependency
list, and absent resume readiness or validation revisions are not invented.
This closes that history-enumeration/detail gap, not whole-Goal summary/list/detail
adoption, sustained observation, D2 or default-provider admission. No persisted
format, writer or PostgreSQL consumer is changed.

### Packaged-source fingerprint cost

The B-lane increment overlaps source-byte reads through the existing bounded,
ordered file reader. It preserves relative names, raw bytes, metadata
invalidation, request-scoped memoization and failure/retry behavior. The Python
filesystem adapter gains no state-policy owner or persistent cache.

Current validation compared baseline `0538bf1631a7` with this implementation on
macOS arm64, Python 3.13.13 and Node 24.21.0. Each arm ran nine alternating fresh
CLI processes after one startup warm-up, against the same disposable synthetic
File/SQLite fixtures. Effect processes were isolated; OS caches were not flushed.
The source snapshot contained 249 TS/JSON files (3,065,799 bytes). Fingerprint
stage medians were 123.5→53.8 ms for File and 111.3→48.0 ms for SQLite.
Whole `status` medians were 1.032→1.054 s and 1.019→1.010 s; sampled p95 values
were 1.745→1.104 s and 1.114→1.086 s (with nine samples, p95 is the maximum).
Twenty full-response pairs differed only at explicitly enumerated observation
timestamps; malformed-registry rejection was unchanged.

This supports a bounded cold-caller cost improvement, not a general status
speedup, provider throughput or D2/default qualification. A warm same-process
microbenchmark with fingerprint memoization explicitly cleared regressed from
6.7 to 12.8 ms; normal unchanged requests retain memoization. Thread scheduling
costs more when all bytes are already hot. Neither workload establishes a fleet
latency guarantee. Whole-Goal payload/consumer work and sustained operation
remain open; this increment authorizes no legacy-writer deletion or UI truncation.

### File recovery receipt batches

Archive restore and audit already use the provider-neutral 1–64 operation
receipt batch contract. File now implements that contract with one exact-byte
and store-identity proof per batch instead of rereading its envelope for each
receipt. Caller order, duplicates, missing results and original receipt bodies
remain intact; each returned body is detached. Invalid input or corrupt retained
history rejects the batch. Array holes are rejected before storage access,
including through the shared helper. Single-receipt error projection stays unchanged.

On the same detached, restored 1,287-commit history, nine warm samples per arm
on macOS arm64 / Node 24.21.0 reduce a 16-receipt File batch median from
346.2 to 21.3 ms; the unchanged SQLite control measures 111.3 and 111.1 ms.
Receipt results and authority heads match within each provider. These are warm
component timings, not equivalent provider-integrity work, whole-restore latency,
cold-read or D2/default qualification. File still rewrites the retained envelope
on each restored commit; a prior full-history restore exceeded its caller's
300-second timeout and later published an exact matching acknowledgement.
That remaining recovery cost is not closed by this receipt-read optimization.
The CLI now distinguishes an ambiguous restore response from a known failure
and returns the read-only `authority-archive restore-receipt` command. This
observes the existing goal/digest/provider-bound historical completion receipt
using bounded metadata reads, without another replay or archive scan. Missing
receipts do not imply a running or failed worker. Current integrity still needs
the existing audit; no provider activation or D2/default gate changes. The next
recovery-cost work remains the measured full-envelope rewrite and decode path,
not another timeout increase or a second receipt/state format.

The next measured File increment reuses `AuthorityStateReplay` while decoding
one checkpoint window, instead of rebuilding a replay owner and canonicalizing
the unchanged projection for every historical row. Untrusted metadata still
uses the shared transaction decoder; every original revision, receipt and final
head is verified. This changes no format, cache budget, writer or public API.
On one detached 2,107-commit File history, three fresh Node 24.21.0 processes per
arm on macOS arm64 reduce cold-load medians from 35.6 to 25.9 seconds (27%).
Complete heads, revisions and cursors match. These consecutive sample groups
do not flush OS caches or control other host activity; they are not p95, a
provider comparison or a whole-restore result. File's full-envelope rewrite
and remaining revision hashing cost still need their existing recovery work;
no D2/default or legacy-writer retirement exit is closed by this measurement.
The #4224 soak was started; its final evidence and applicability remain pending.

### Runtime retirement drains admitted effects

The shared TS Effect server now counts pending handlers independently of TCP
connections. The idle window starts after the last handler and private response
sink finish. Explicit shutdown stops accepting connections, waits for admitted
effects (including disconnected clients), and then removes only its own locator.
Authentication, request budgets, original receipts and caller recovery remain
unchanged. No additional provider or Python decision owner is introduced.

The previous close handler could exit while a disconnected caller's write still
waited for a live mutation lock. A real-server regression reproduces this under
both idle retirement and explicit shutdown; connected callers are controls.
It also verifies concurrent ping, rejected authentication, listener closure,
durable write readback and eventual retirement. Existing replacement-locator,
restart and File/SQLite archive crash/recovery tests remain required.

This repairs one S4/runtime-lifetime dependency of R5/D2 recovery. It does not
qualify sustained operation, choose the release default, increase a frozen
capacity budget or authorize deleting a legacy writer.

### Delegated execution keeps its original lease

The delegated CLI now reuses the TS managed-process owner to renew the original
canonical execution while the Host and independent Turn validation run. Its
private control pipe carries the initial lease and unchanged claim/renew
commands; the model request does not carry those commands or acquire authority.
Claim replay must prove the same owner, key and epoch. A lease read cannot
replace mutation-time CAS, and an expired execution is never reacquired to
accept its old result.

Renewal uses the latest proved version, one unchanged-intent retry for a lost
transport reply, and the last proved expiry even when renewal hangs. A rejected
proof cancels the CLI; its TERM adapter unwinds nested managed Hosts before
returning. Ordinary non-hard delegation keeps the existing subprocess route.
After the supervised CLI returns, completion renews the original execution
using its acquisition TTL before starting independent Todo acceptance. It
journals the renewal version before the effect, then proves current authority
and freezes the terminal CAS version. Lost renewal and completion replies each
replay their original intent; neither replay can revive an expired or replaced
execution. The validation phase makes no lease writes that would invalidate
its provider-revision witness. Canonical
completion releases the execution lease; subsequent original-Turn accounting
uses its terminal receipt rather than reacquiring an open-work lease.

Explicit registry/runtime commands also survive coexistence of both machine
roots: projection discovery inspects both declarations without selecting an
implicit authority, and still reports competing routes as ambiguous. Implicit
Goal CLI defaults retain their existing conflict rejection. Repository canaries
without a Goal receipt do not select machine authority; a first explicit
bootstrap has no previous Goal authority to fence. Existing Goals still require
their original-route replacement authorization. Local smoke fixtures declare
their own runtime instead of inheriting operator state.

The acceptance slice uses disposable File/SQLite providers, real CLI/Turn
execution and a synthetic model process: crossing the initial expiry, canonical
release, a new execution epoch, lost completion/renewal replies and rejected or
hung renewal, including control-pipe loss with a TERM-resistant process. Final
Todo acceptance also crosses a short remaining lease deadline, with lost-reply,
expiry and replacement controls. The Python delegation adapter only sequences
the existing TS-owned claim, renew and terminal contracts; no provider rule,
public setting or frontend permission changes. It
does not qualify a paid model, remote job cancellation or
Windows process-tree cleanup. Stop acknowledgements and interrupted-Turn
no-progress settlement remain with the existing delegation-stop work (#5308);
this slice leaves an interrupted operation explicitly recoverable, never
accepted from incomplete output. Sustained D2 operation, default onboarding and
last-writer retirement still require their owning evidence.
