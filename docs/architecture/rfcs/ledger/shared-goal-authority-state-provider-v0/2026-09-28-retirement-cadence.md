# Local authority: retirement cadence after integration

- Audit: `ce3862e33`; adoption follow-up: `71525ab90`, September 28, 2026; [中文](2026-09-28-retirement-cadence.zh-CN.md).
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

The next B work is SQLite admission on a frozen source/runtime profile: rerun
the existing reference capacity axes, reconcile concurrency/recovery/consumer-lag
evidence, and verify the applicability of retained natural-time soak results.
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
or permission to change the default provider. Full-Goal summary/list/detail
adoption and sustained observation remain separate work.

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
Completion reads the current claim, persists its terminal CAS intent before the
effect, and replays that exact completion after an ambiguous reply. Canonical
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
hung renewal, including control-pipe loss with a TERM-resistant process. It
does not qualify a paid model, remote job cancellation or
Windows process-tree cleanup. Stop acknowledgements and interrupted-Turn
no-progress settlement remain with the existing delegation-stop work (#5308);
this slice leaves an interrupted operation explicitly recoverable, never
accepted from incomplete output. Sustained D2 operation, default onboarding and
last-writer retirement still require their owning evidence.
