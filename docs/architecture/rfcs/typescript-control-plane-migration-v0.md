# RFC: TypeScript Control-Plane Migration Direction v0

- Status: Accepted, transaction-payoff phase in progress
- Proposed by: LoopX maintainers
- Date: 2026-08-15
- Last revised: 2026-09-13
- Scope: an incremental, replacement-first migration of the LoopX control-plane
  core from Python to TypeScript without maintaining two semantic
  implementations
- Tracking issue: [#3225](https://github.com/huangruiteng/loopx/issues/3225)
- Language note: the
  [Chinese version](./typescript-control-plane-migration-v0.zh-CN.md) and this
  English version are semantic mirrors. A difference between them is a defect.

---

## Cross-RFC execution priority (2026-09-16)

[Roadmap](loopx-overall-roadmap-v0.md) R1–R5 are current product consumers of T0–T4, not another migration ladder. The team confirmation path now uses `work_items/team_plan.ts` for preview, batch planning and immutable operation identity, plus the existing AuthorityStore receipt/CAS boundary. Python retains public-safety and legacy Markdown IO adaptation; the per-lane writer loop is retired. The R1 checkpoint records the delivered assignment/retry delta and remaining receiver/execution boundaries.

Retain T0 caller/parity inventory, T1/T2 transaction/effect convergence, T3 complete-source consumption and T4 deletion conditions. #4472 is merged; inspect `todos/public_update.ts` and actual callers before rebuilding Todo update. Converge new team domain rules in existing typed work-items/collaboration ownership; Python retains input/IO adaptation. R1 independent counterexamples and real-path verification gate delivery. More leaf RPCs, enums or files are not migration payoff. Shared-authority retains D1–D3.

## Current implementation checkpoint

Canonical command observation now has one typed receipt/head boundary. Team,
Todo creation/edit/claim/terminal/archive, Monitor, lease maintenance and Goal
acceptance recheck receipts after the head read before interpreting new state.
This repairs same-operation races without provider API changes, write retries
or a Python copy of the decision. Standalone and atomic claim acquisition share
current lease proof; renewed proof is returned without rewriting history, while
retired execution and unavailable current authority cannot return stale success.
This closes a concurrency/current-proof slice of L2/L3, not whole-Goal migration,
default onboarding, contributor-owned SQLite D2 or T4 Python retirement. The
[operator contract](../../reference/canonical-lease-renew.md#commit-retry-and-readback)
distinguishes historical results from present execution.

Handoff-mode transition now shares typed ownership facts and an explicit
valid/invalid previous-mode state across legacy and canonical paths. Python's
blocker classification, artificial previous mode and whole-text rewrite are
removed; its retained boundary is source projection/locking and capture IO.
The legacy scan includes event-only claims, and canonical mode receipts reuse
command recovery with strict historical decisions. Full-source snapshot and
real-provider validation guard this T1/T2 replacement. This closes a rule and
caller discrepancy, not a whole default-cutover package; the conditional 5–8
package estimate remains. [Changed behavior and recovery](../../reference/handoff-mode.md).

Terminal review and validation now converge in the existing TS terminal owner.
Agent completion and Monitor stop reuse Chat's canonical receipt-first recovery
and display acknowledgement; v2 binds validation continuation to its source
revision and resolves private declarations only after admission/replay. Python's
separate terminal review selection and eager declaration sequencing are removed.
This closes a T1/T2/L2 terminal caller family, not the remaining leased metadata,
executor fences or T4 retirement. [Semantics, crossings and rollback](../../reference/canonical-terminal-review.md).

Linked User completion now has one typed owner, `todos/user_completion.ts`.
The terminal transaction commits exact-target scope consumption, reject/cancel
outcomes and conditional resume with its own completion/receipt; the Markdown
adapter invokes the same plan once instead of applying Python rules and making
per-scope RPCs. This repairs a reproduced native-provider omission rather than
assuming a migrated terminal serializer closes the caller. The shared mixed
fixture covers concurrent CAS loss, lost response, replay and a remaining User
blocker across File/SQLite/PostgreSQL. See [semantics, callers and compatibility](../../reference/canonical-todo-completion-update.md#linked-user-completion-effects).
T1/T2's linked User completion slice is closed; the remaining L2–L9 program and
whole-Goal default remain unqualified.

关联 User 完成的决策消解、拒绝/取消和条件恢复统一归属 TS，并与完成回执原子提交；
旧 Markdown 适配器只传一次完整快照并执行计划，删除重复 Python 规则及逐 scope RPC。
这修复了原生 provider 漏掉联动的真实缺口，仅关闭 T1/T2 中这一完整调用链，不能据此
宣布 L2–L9、整 Goal 默认切换或全部 Python 删除完成。

Canonical create/claim/update/Monitor poll/terminal transactions now share
`coordination/authority_source.ts`; Python adapters bracket registration/grant
projection through `authority_registry_source`. The original witness survives
external validation and is checked before new effects/commit. Historical receipts
retain their identities; claim replay separately preserves current acceptance.
See the [versioned witness contract](../../project-agent-todo-contract.md#canonical-registry-source-witnesses).
This closes the registry-fact boundary across these T1/T2 callers, not the whole
migration, local default cutover, task-lease source protocol or shared transport.


The projection-delivery stage now closes the cross-language boundary: typed
TypeScript mutation results and the Python compatibility provider share the
same four-state contract (`pending`, `delivered`, `current`, `not_required`).
Provider readback is validated before acknowledgement decisions, and the
end-to-end causal chain is covered by a shared composition fixture. This is a
completed delivery stage, not a promotion of Markdown or a claim that the
remaining lifecycle writers have migrated.

The same stage also removes duplicated Python read policy around that boundary.
Task-class resolution, title-aware actionability, dependency readiness, agent
eligibility and canonical Todo read records have one Python semantic owner
while TypeScript remains the transaction owner. Priority authoring and ordering
now share `todos/priority.ts`; Python compatibility readers consume its generated
vocabulary and legacy grammar instead of maintaining independent patterns. The old
projection module is an import-only compatibility facade. This keeps the
replacement-first rule intact: compatibility remains available, but it cannot
silently become a second semantic implementation.

The priority-intent slice connects CLI add/update/clear, reviewed Chat edits and
the Dashboard selector to the existing typed Todo transaction. Text-only edits
preserve priority; conflicting declarations fail before writes. P3/P4 ordering,
legacy decorated labels and successor inheritance share the same owner. See the
[caller contract](../../project-agent-todo-contract.md#priority-intent). Real CLI
File/SQLite readback, isolated PostgreSQL and the shared complex fixture qualify
this boundary. It retires duplicate priority knowledge, not the remaining T1/T3
callers, Python compatibility IO or the D1–D3 default-cutover gates.

Native update now composes `todos/public_update.ts` for a bounded nonterminal
planning intent (status, evidence/reason, resume/clear and successor links),
against the same complete canonical head used for authority checks and CAS.
The separate intent namespace preserves raw text/note limits and old receipt
fingerprints. The v2 transport carries lifecycle grants, the authority reason,
a registry source witness and optional reviewed provider revision; v0/v1 requests
retain their established identity and cannot smuggle these new obligations.
`todo_update_admission.ts` composes the existing lifecycle/lease rule owners;
Python projects registry facts, transports intent and drains the display outbox.
Chat Todo and Monitor nonterminal previews execute the same update as apply,
bind canonical revision/registration rather than Markdown, and reuse one operation
ID after response loss. Historical recovery precedes current admission; projection
recovery drains the current head. Failed delivery stays recoverable on a reloaded
Dashboard. Cadence timestamps are derived in the TS transaction, not regenerated
inside Chat retry intent. Monitor preview no longer claims validation without
running it, and Chat does not claim verified display for a pending outbox.
Lease ownership/requirements/status transitions and terminal/observation effects
retain their own owners. This closes a reviewed-edit T1/L5 journey, not all T1,
provider defaults or promotion. Registry witnessing is an optimistic source check,
not an atomic transaction spanning registry configuration and provider storage.

Provider-first text/note updates now accept the active execution key and lease
version through the existing terminal fence, with automatic acquisition and
delegated overrides disabled. The same provider revision guards the edit and
receipt; the lease is never mutated. Explicit `--update-operation-id` supports
CLI retries with unchanged proof and intent, including historical replay after
expiry or transfer. Missing/stale proof and historical inactive leases fail
closed. No-proof receipt fingerprints remain compatible. This is the bounded
#4152 lease-fence slice, not full T1 metadata or T2 effect closure; legacy updates
without these options remain unchanged. See the [Todo contract](../../project-agent-todo-contract.md#lease-fenced-canonical-textnote-updates).

Monitor metadata authoring and poll transitions now share `todos/monitor_metadata.ts`.
Public update composes that owner inside its existing field-plan request; cadence
calculation stays in-process instead of making two additional scheduler RPCs.
The Python observation/replay/counter/scope/boundedness rules are retired. Create
and the low-level Markdown add codec retain a metadata-plan adapter; this is not
the complete T1 transaction or T2 atomic monitor-plus-successor commit.

Intentional corrections: older observations cannot rewind state merely because
either effect ID is absent; issue-fix grouped membership updates use the locked
observation path and advance generation when a material result hash changes.
New counters reject negative or unsafe integers. ISO dates are calendar-checked;
the codec retains Python compact/week-date forms, offset seconds and microsecond
ordering without rewriting history. Lifecycle/ownership admission now precedes poll
diagnostics, so an unauthorized request cannot use malformed metadata to avoid
its authority rejection. Exact replay, same-second unkeyed polls, explicit
clears and legacy boundedness exemptions remain. The plan grants no permission,
receipt or promotion; Monitor planning is not added to native update by this slice.

Public Todo add/update now resolve role, continuation binding, gate scope and
deferred-condition requirements through `todos/authoring_scope.ts`. Python's
`write_policy.py` and duplicated scope selection in `todos.py` are retired;
the Markdown codec keeps only its early class-check adapter. Materialized
terminal successors share the resolved-scope invariant without draft inference.
Intentional corrections: explicit global/lane scope outranks author defaults;
explicit conflicting binding is rejected rather than overwritten; global gates
are never inferred from actor identity or `goal_bound`. Existing omitted scope,
completed-history repair and lifecycle/lease permission boundaries remain.

This closes T1's authoring-scope prerequisite, not the whole update transaction.
Remaining metadata expansion and validation/effect closure are T1/T2 work.
Native update retains its raw text/note allowlist alongside bounded planning intent;
legacy codecs/locks/writers still have active callers and are not retired here.

A checked-in generator validates the language-neutral contract and emits
deeply immutable Python/TypeScript bindings, including the native domain and
projection sections. Both runtimes import these bindings; CI checks source
parity and rejects stale generated files. This removes duplicate contract
loaders without changing Todo semantics or promotion policy.

The coordination path uses one language-neutral
`coordination_state_contract_v0.json`. Its native `TodoDomainRecord` keeps task
semantics, including `archive_state`; `TodoProjectionMetadata` contains
`source_section` and optional `index`. The TypeScript reducer and provider-first
collection reader accept the separately versioned native domain manifest;
native creation, archival, receipt replay, and store reopen are tested without
Markdown metadata. Python only adapts the typed read result to the compatibility
summary. This is a contract checkpoint, not a completed CLI lifecycle cutover.

### Lease acquisition and lifecycle convergence (2026-09-18)

Standalone acquire/takeover and maintenance now share the local provider/source
fence. `task_lease_acquire_decision.ts` owns acquire admission and materialization;
legacy acquire and canonical atomic Todo claim reuse it. `task_lease_state.ts`
provides full canonical facts, including archived-holder exclusion from scope
conflicts. Python sends registration facts through one native request, without
reconstructing the canonical Todo/lease head. Generation exhaustion fails closed.

An acquire receipt alone is not current execution authority: exact create-CAS
retry recovers the original decision and verifies the current owner/key/epoch;
renewal returns current proof while expiry/release/transfer cannot revive it.
Canonical completion can rebuild missing Markdown display through the existing
outbox. Real CLI, scale/native/imported fixtures, process loss and four-arm
read-only rehearsal cover the boundary. See [operation and compatibility](../../reference/canonical-lease-renew.md).
Executor-held external-effect locks, remaining L2/L4/L5 consumers, D2/D3 and
new-Goal defaults remain separate; this is not full L3 or T4 retirement.

### Local provider opening boundary (2026-09-13)

The provider-first runtime now has one typed local opening seam. An absent
selector resolves explicitly to the File profile (`source_authority=file_v0`);
the same handle reports SQLite when the qualified local selector is present and
can report PostgreSQL only through a service-owned factory. Runtime commands no
longer repeat provider construction or infer a PostgreSQL store as File from an
`AuthorityStore` implementation.

The selector carries only provider, goal, tenant, and store-incarnation facts.
It never carries credentials or a database client. Selected-provider failures
retain their provider source and fail closed; they do not silently fall back to
File or Markdown. This is the default provider boundary and TypeScript
ownership refactor, not a SQLite promotion, a whole-Goal cutover, or a
PostgreSQL service claim. Existing promotion, soak, retention, and writer-fence
holds remain unchanged.

Provider-first `todo update --text/--note` preserves claim-neutral correction:
a registered, non-excluded actor may edit an unclaimed active, non-completed
agent Todo, subject to its agent binding. It must not introduce `claimed_by`.
Another claim owner's Todo remains rejected. Only text/note may be patched or
cleared; governance fields and hard-lease execution authority are not granted.
The TS transaction owns eligibility, CAS, and receipt replay; promotion must
not turn a copy correction into a claim. Provider conformance covers both
native and v0 records, and the production CLI is tested without Markdown.

The default Markdown and explicitly promoted `todo claim` paths now share one
TypeScript claim decision for actor, registration, role, status, archive,
exclusion, and existing-owner checks; the Python legacy writer only commits
that decision while holding its lock. After explicit promotion, claim crosses
once into the same TS-owned
transaction for both native and v0 records. New claims require active, open
Todos and the current actor/lease checks. Exact operation retries recover the
original claim receipt before current-state eligibility; observation time and
current registration facts are not request identity. Replaying a receipt does
not renew a lease or assert current ownership. Successful non-preview
`no_change` also persists a terminal receipt under head CAS: storage revision
may advance, but Todo state, `updated_at`, and domain events do not change.
A structurally valid empty registration list permits historical replay, never
a fresh claim; malformed lists still fail. Preview remains zero-write, and
invalid preview booleans fail before provider access. The CLI still creates a
fresh operation id by default. On an already promoted canonical authority,
callers can opt into cross-invocation retry with
`loopx todo claim --goal-id <goal> --todo-id <todo> --claimed-by <agent> --agent-id <agent> --claim-operation-id <public-safe-id>`.
Reuse the same id and intent after a lost response; changed intent under that
id fails closed. A preview does not consume the id. The option rejects legacy
mode without writing or promoting anything; omit it to retain default behavior.
It grants neither a lease nor current ownership on historical replay. Combined
claim/lease acquisition remains follow-up work.

Promoted `todo add` is a native create
transaction on that same authority owner. Python validates the established CLI
arguments and adapts them once into the versioned domain record; TypeScript
owns duplicate identity, replay, actor/owner eligibility, CAS, receipt, and
projection-outbox mutation. Preview and the real subprocess CLI path are tested
after deleting the Markdown state file, so promotion cannot silently regain a
Markdown write path. Completion-validation argv remains typed data rather than
a shell-encoded compatibility field. Default, unpromoted goals retain their
existing Markdown transaction until their explicit promotion boundary.

The terminal-lifecycle stage package extends that boundary to promoted
`complete`, `supersede`, and role-scoped `archive`. TypeScript owns admission,
claim/lease fencing, successor validation, completion-policy reduction, CAS,
receipts, projection intent, and archive selection. Python projects registry
facts, executes an explicitly declared validation effect between typed
reductions, and drains compatibility projections; it no longer recreates the
terminal state machine for promoted goals. The canonical Todo stores only a
validation-required marker and declaration digest. Raw argv remains in a
0600 host-local sidecar and must match that digest on recovery before the
effect may run. Imported v0 Todos retain legacy `index` archive ordering;
provider-native records use durable completion/update time and Todo identity.
Historical lease files absent from the current Todo graph remain audit history
and are not projected back into the canonical live head.

Every non-preview terminal or archive entrypoint acquires the same per-goal
shadow-maintenance mutex used by bootstrap and rollback, then rechecks the
durable management state before opening the canonical provider. A lifecycle
write therefore cannot overlap a bootstrap/rollback transition or bypass its
write hold. After the durable promotion fence is present, a missing canonical
head is reported as a typed canonical-authority outage with an explicit
restore-before-retry recovery action. It is not relabeled as a legacy-writer
fence, and it never authorizes a Markdown fallback.

This stage is qualified with a three-arm rehearsal from one read-only,
production-complex snapshot: an immutable legacy baseline clone, an isolated
file provider, and an isolated real PostgreSQL provider. The provider heads
must match exactly and the legacy arm must match semantically after normalizing
the declared compatibility projection. For archive, that projection excludes
provider-retained archived records and their historical leases from the legacy
hot view, and ignores absolute imported indexes only after proving identical
per-role relative order. It never normalizes domain fields, archive selection,
active leases, or non-target records; the source snapshot remains unchanged.
The versioned rehearsal command lives in
`examples/control_plane/authority-three-arm-rehearsal.py`. A deterministic
public-safe scale fixture exercises
the same status mix, current/retired leases, standing decisions, validation,
successor, replay, concurrency, archive pressure, and hard-lease fences in every provider suite.
That fixture is durable regression coverage, not a substitute for the current
read-only three-arm rehearsal.

From this checkpoint onward, every pull request that claims progress against
this RFC follows the
[production-scale fixture stewardship contract](../../development/testing-and-quality.md#production-scale-fixture-stewardship--生产规模-fixture-维护契约).
It declares fixture impact, exercises every affected provider arm, and keeps
the read-only three-arm rehearsal as a separate promotion gate.

### Provider-neutral projection conformance checkpoint (2026-09-12)

The conformance boundary now has one projection-fixture builder for both the
legacy v0 and native Todo record shapes. It owns deterministic Unicode ordering,
read-model digest/field construction, and the compatibility-only conversion;
provider tests no longer hand-rebuild those fields. The scale envelope declares
status ordering explicitly and validates its counts, so changing JSON key order
cannot silently change which Todo receives a lease, successor, or archive role.

The File, SQLite, and NoKV suites now execute the same production-scale terminal
cases in both record shapes. A separate parity harness replays one seed,
observation, and lease sequence through all three isolated providers and compares
the logical head plus committed event/projection/receipt trace while ignoring
provider-specific revision tokens. This is conformance evidence, not a new
authority writer, provider default, or promotion claim; PostgreSQL remains under
its existing real-service qualification gate.

The old v0 consumer manifest remains readable and retains all existing fields.
Default Markdown capture still emits v0; this PR neither rewrites stored heads
nor auto-promotes a goal. The schema split is not permission to drop v0
provenance or change legacy ordering during a later migration.

### Canonical Todo presentation checkpoint (2026-09-12)

The authority boundary now treats presentation as a first-class projection
contract rather than naming it `legacy_projection`. A shared TS presentation
normalizer maps the v0 wire shape's `source_section`/`index` to
`display_section`/`display_order`, while native records derive their display
section from domain role/archive state and never receive a fake persisted
index. The normalized presentation contract is shared; the wire coordinate is
not a second Todo state machine.

Todo creation, terminal successor materialization, projection validation,
standing-decision ordering, and archive ordering all use the same presentation
owner. The canonical domain validator is shared by both wire shapes, and the
v0 record is produced by an adapter from a validated domain record. This
unifies the semantic owner without rewriting v0 heads or receipts.

Python read callers now import the semantic owner directly; the compatibility
facade is no longer an internal dependency. Python presentation sorting keeps
source `index` order when it is present and uses completion/update time plus
Todo identity for native records, so the compatibility shape cannot leak into
business eligibility or lifecycle decisions.

The next migration may persist an optional canonical `presentation` object, but
only after proving whether an imported section is provenance or current display
intent and after qualifying a stable display-order policy. Until then, native
display positions remain derived at the renderer boundary and must not affect
authority lifecycle decisions.

### Long-goal persistence is part of the migration payoff

The product target is at least ten elapsed days per goal, not a short-lived
transaction demo. The shared-authority RFC's
[Section 7.2](./shared-goal-authority-state-provider-v0.md#72-ten-day-goals-local-storage-qualification-target-proposal)
owns the workload, performance budgets, retention and actual-soak acceptance;
keep changing capacity numbers there rather than duplicating them here.

Start a cohesive local-persistence slice alongside the provider-first Todo
caller: qualify an embedded transactional store (SQLite first candidate),
bounded live head/receipt lookup, crash-safe checkpoints and exact historical
readback. File-v0 remains the conformance/import baseline. Merely replacing
Python with TypeScript, swapping databases while retaining ever-growing heads,
or passing accelerated volume tests is not ten-day continuity evidence.
Local promotion waits for both volume and elapsed-time qualification; it does
not wait for a PostgreSQL service and never expires receipts at day ten.

### Delivery semantics: correctness before migration

The replan obligation outcome policy now lives in
`work_items/replan_semantics.ts`: required-outcome selection, vision-path and
terminal consistency matching, and the matching refresh input projection share
one owner. Python retains progress normalization/novelty and persistence adapters,
but no longer duplicates obligation-to-outcome matching. This is a bounded rule
convergence, not a settlement-writer or store migration. Existing outcome
characterization precedes the move; the intentional correction is executable
authoring for all vision triggers, with real bound CLI closeout/readback and
negative qualification-scope cases. Checkpoint recovery and in-flight rules
remain in their existing owners; no new capability, provider or setting is added.

The delivery-history boundary now treats `classification`, `health_check`, and
`recommended_action` as narrative. They cannot create or discharge a
follow-through obligation, prove an outcome, or classify delivery scale.
For example, `unblocked after dependency update` is not a blocker receipt and
`implemented network protocol parser` is not preparation-only evidence.

`control_plane/work_items/delivery_history.ts` now owns the complete delivery
history-to-obligation read projection: outcome, turn kind, scale, consecutive
streaks and follow-through. Status selects one bounded history batch before one
`work_item.delivery_history.project` request; quota's latest-run consumer uses
the same projection with one row. This adds a managed-runtime crossing where
Python previously decided locally, not one request per field or historical row.
The Python bridge sends compact typed facts, never narrative or evidence bodies;
display-only classification is attached after the decision. The replaced
`delivery_signals.py`, `outcome_followthrough.py`, turn-kind inference and status
streak wrappers are deleted. Existing TS blocker binding is reused. Python enum
codecs and the settlement writer predicate still have real callers and remain;
this is not a writer/transaction or provider migration.

The acceptance invariant is **narrative non-interference**: holding typed
fields and configuration fixed, rewriting narrative or adding an unvalidated
`compact_evidence` / `case_result` object cannot change delivery semantics or
its follow-through obligation. Classification remains visible as a history
label; no legacy prediction is retained without a concrete display consumer.

- Valid explicit outcome, turn-kind, and scale fields retain their meanings.
  An explicit blocker kind remains readable. A scoped typed blocked observation
  must pass the existing work-item/evidence binding before it resolves a gap
  into blocker writeback. A bare `outcome_gap` is insufficient.
- Missing or unsupported historical delivery fields remain unknown; unknown
  stops consecutive small-scale/outcome-gap evidence streaks and never counts
  as success or as an inferred failure. Missing outcome with no configured
  floor retains the `not_configured` presentation sentinel.
- New delivery claims use explicit enums through the existing writer APIs
  (for example `refresh-state --delivery-outcome ... --delivery-batch-scale ...`).
  State-only refresh remains legal without a delivery claim; this patch does
  not require every status refresh to declare progress. Existing write-time
  enum rejection, settlement evidence, quota, and gate checks remain in force.
- Follow-up claim validation shares one TS diagnosis with historical projection.
  New writes reject progress paired with preparation-only work, primary outcome
  paired with blocker work/typed blocked observation, and primary outcome paired
  with an explicit follow-through requirement. Historical conflicts remain
  readable as `unknown` plus `delivery_claim_conflicts`; persisted records and
  settled receipts are never rewritten. Partial progress and valid blocker
  writebacks remain legal. This is an intentional authoring/readback behavior
  correction, not a change to small-delivery policy or a new evidence validator.
  Refresh validates individual fields in their established order, then checks
  the normalized claim before registry access or lock creation. Invalid input
  therefore takes precedence over store errors, including in dry-run mode;
  state-dependent admission and writeback still share the same runtime lock.
- Delivery response is a separate typed read decision consumed by quota,
  handoff and work-lane projection. A scoped blocked observation exempts the
  historical outcome floor only while its canonical Todo has a positively
  identified pending resume target. Missing/invalid source, another actor's
  claim, exclusions and unbound legacy blocker labels cannot establish that
  exemption. Other runnable work remains selectable by the canonical planner.
  Unknown refreshes interrupt statistics, not Todo/replan obligations; no new
  persistent delivery ledger is added. Surface-only supervision and the
  independent small-delivery rule remain unchanged.
  This exemption requires the parsed target identity and a supported task class;
  monitor baseline, capability and PR repository/number also bind to the current
  Todo. Missing actors or stale/mismatched conditions cannot relax supervision.
  Incomplete legacy conditions remain readable, but are not positive wait proof.
- Legacy outcome-marker/hint configuration remains readable and preserves
  whether an outcome floor is configured. Its words no longer classify runs.
  No persisted history is rewritten and no new default-off flag restores the
  erroneous behavior. This intentionally changes status, handoff/review, and
  quota decisions previously derived from untyped historical labels.

The migration preserves independently characterized legal typed behavior and
validates real refresh/history/status/quota entrypoints, batch cardinality and
narrative non-interference. One intentional correction is separate from parity:
two invalid work-item identifiers must not compare equal merely because both
normalize to a missing value. Such observations cannot infer blocker writeback
or discharge a follow-through obligation. The remaining Python writer predicate
rejects that case too; no active history is rewritten.

Next, inventory writers still omitting material-result fields and retire obsolete
marker/hint configuration with an explicit compatibility plan. Exact legacy
lifecycle classification codes, history selection and unrelated cadence policies
remain outside this slice. Do not claim all writers migrated or all prose rules
retired. This read-policy closure does not displace the provider-first Todo
sequence below or wait for a provider cutover.

### Legacy field-rule retirement checkpoint

`todos/field_update.ts` now owns the complete metadata intent assembly used by
the legacy `update`, `claim`, `complete`, and `supersede` line writer: status and
completion timestamps, omission versus explicit clears, binding precedence,
removed-policy repair, resume-generation pairing, and completion metadata.
It composes the existing TS completion rule directly. The replaced Python
decision branches and the last-caller `todo.completion_state.metadata_updates`
RPC/facade are removed, not kept as a fallback.

This is a pure plan, not admission or a provider commit. Python still owns
Markdown lookup/encoding, byte-level no-op detection, locking and external
effects. Public role/binding admission and the event writer are not declared
migrated by that slice. Native planning now composes this owner as described in
T1; unsupported fields gain no authority, no goal is promoted, and no third storage path appears.
Rejected plans now leave even the caller's in-memory line buffer unchanged;
public rejected transactions were already non-committing.

There is one field-plan crossing per legacy line write. It replaces the former
metadata RPC on ordinary edits; already-finalized completions with an override
gain one planning crossing. Cached codec normalization calls remain. This is
semantic deletion, not a claim of fewer crossings on every command. Retire the
adapter with its final legacy lifecycle caller after full-goal cutover, or fold
it into that caller's coarse transaction when migrating the caller; do not grow
a series of field-level RPCs. Retain Markdown rendering permanently.

### Next delivery sequence

The destination retains Markdown as a **permanent readable projection**, not a
second database. This RFC owns one typed business-rule/transaction owner and
its deletion payoff; the [shared-authority RFC](shared-goal-authority-state-provider-v0.md#next-delivery-and-parallel-provider-work)
owns durable truth, recovery, cutover, and projection delivery. Neither a fully
TypeScript CLI nor `loopxd` is a prerequisite for removing Python decisions.
An input adapter or external-effect executor may remain Python.

Objective serialization and readback now share the existing Goal metadata and
section owners; legacy registration compares decoded values and exact narrative.
This closes a presentation boundary without adding TS transport or moving business
authority. See the [document boundary](../../reference/protocols/active-state-structured-projection-v0.md#markdown-ownership-boundary).

The lifecycle-admission slice now uses `todo_lifecycle_decision.ts` for legacy
claim/update admission, delegated action/reason checks, ownership-holder routing,
and native complete/supersede. Native text/note edits and terminal transitions
reuse the preauthorized lease fence in-process. `authority_core.py` projects only
the live admission and terminal decisions; there is no standalone Python command
or effect-runtime handler for the fence. Mutation admission cannot complete a
Todo, and the in-process fence cannot grant actor authority or commit a change.
This deletes duplicate rules now, **not** the complete legacy update writer.
Field patches, omission/clear semantics, monitor/resume effects and validation
still need one complete update transaction before the writer can retire. Legacy
callers still cross the runtime boundary for admission and their locked gate;
this slice reduces semantic owners, not crossing count. Native transactions stay
in-process. Fold the remaining crossings into that complete transaction rather
than extending these adapters field by field.

The waiting/resume planning slice now uses `todos/resume_planning.ts` for the
complete deferred, resume-blocked, monitor-repair and blocked-successor selection.
Quota composes capacity evaluation with these lanes in one request per source summary, reusing the
existing TS resume evaluator in-process; vision-wait, agent-scope, frontier and
replan consumers use the same projection. The old `deferred_resume.py` rule owner
is removed, not retained behind a second implementation. The Python adapter keeps
the reader compatibility boundary, not claim/exclusion selection or wait routing.
Resume, route-continuation and succession-warning share `compact_projection.py`
for field omission and scope normalization; caller-specific text inference and
succession-only fields remain explicit. Priority rank normalization stays in the
resume adapter. This retires
one read-policy family, not the whole quota reducer or the monitor/lease writers.
Equal public sort keys retain source order; full counts precede display limits;
`monitor_changed` is not the legacy `todo_done:<monitor>` repair path. This
read-only result grants neither execution authority nor a lifecycle receipt.
The adapter exits when its callers consume typed Todo records in-process.

Resume condition diagnosis is now shared by the evaluator and planning owner;
agent-scope consumes the selected repair lane rather than reinterpreting target
type/status. Old compact inputs may recover omitted kind/class from typed
`resume_when` and the same snapshot's monitor records, never from narrative.
This refinement includes explicit behavior corrections: self-dependencies and
`todo_done` dependencies on unfinished monitors are `resume_condition_invalid`,
not ordinary pending waits. Completed historical monitor dependencies remain
satisfied; missing completion targets remain pending because absence in a
partial snapshot is not proof of an invalid dependency. Valid generation fences,
claim/exclusion, capacity and PR waits retain their existing semantics. Invalid
conditions cannot become exact blocked-successor waits. Monitor completion
repair stays visible and selectable only in the permitted executor scope.
No automatic conversion to `monitor_changed`, baseline reset, persisted-state
rewrite or new writer admission is implied. General add/update admission and a
generic repair action for every invalid condition remain separate scopes; this
is not a claim of zero behavior change or full Todo writer closure.

#### Command receipt and recovery ownership

At baseline `bfd1ec8db`, create, claim, update, complete/supersede, archive and
Monitor poll repeated envelope matching, result projection and post-CAS
readback. `coordination/command_receipt.ts` now owns those shared semantics;
command modules retain request normalization/digests, admission, payload
validation and mutations. `coordination/todo_archive.ts` owns retention,
separate from terminal validation and lease release. Internal callers import
that owner directly; the old module does not retain an unused re-export.

Intentional observable changes on these canonical command paths:

- An applied/ambiguous commit followed by unreadable receipt remains
  `ambiguous`, with `recovery.operation_id` and
  `retry_with_same_operation_id=true`. A read failure cannot erase possible
  durable acceptance. A thrown commit response receives one receipt lookup,
  never an automatic second write.
- A conclusive CAS conflict or failed commit remains that result if diagnostic
  readback fails. An exact historical receipt still takes precedence. An
  applied response with a missing receipt remains a protocol failure.
- Malformed create/Monitor result objects and update/terminal/archive change decisions
  fail with `invalid_coordination_command_receipt`; they cannot become successful
  replay/no-op through coercion or escape as an unchecked decoder error. Claim
  retains its existing receipt-error code and historical omitted-change codec.
- Read failures consistently include `changed=false`; this with an `ambiguous`
  status means no proven successful result, **not** proof that nothing was written.
  Identity-conflict and missing-receipt messages use shared coordination wording;
  their existing reason codes remain stable.

Existing request digests, receipt schemas, success payloads, no-op consumption,
lease/grant checks, permanent Markdown delivery and provider defaults remain
compatible. The production-scale fixture now drives all seven command operations
through normal, lost-response, unreadable-readback and thrown-response cases,
including replay after an intervening commit. Real File/SQLite/PostgreSQL and
NoKV transport conformance share that matrix. The three-arm read-only source
rehearsal also loses archive responses on actual File/PostgreSQL commits.

This removes duplicated TS transaction authority, not Python business writers:
no new bridge or RPC is introduced, and cross-runtime calls are unchanged.
T1 metadata/effect closure, T2 retained Monitor leases and D1–D3 qualification
remain separate work; the compatibility editor and other command protocols
retain their distinct receipt contracts. No Goal promotion is implied.

#### Execution cards after the current stack

This is a **conditional execution plan**, not a merged-status declaration.
At the 2026-09-09 checkpoint, #4053, #4117, #4129, #4122
(resume diagnosis/planning), #4134 (delivery history) and #4136 (claim diagnosis)
are merged. The canonical delivery-response follow-up targets that landed main.
Check their actual merge commits before starting. #4121 (SQLite candidate)
and #4101 (projection receipt retention) are independent candidates, not
implicit prerequisites or approved defaults.

Execute the first unclosed card below; do not start all cards or rebuild a
completed transaction. Keep the task ledger in LoopX state; this document is
the shared plan, not another per-agent checklist database.

**T0 — reconcile the landed baseline, inside the next implementation PR.**

- Fetch the intended remote base; record its SHA and each dependency's actual
  merged/not-merged status. Compare code, not just PR titles. If a dependency
  is open, use an explicitly selected stacked base or stop that dependent unit.
- Start from `coordination/todo_update.ts`, `todos/field_update.ts`,
  `todos/provider_update.py`, `todos/native_update_plan.ts`, `todos/line_update.py`,
  `scheduler/monitor_poll_writeback.py` and their public callers. These paths
  are under `loopx/control_plane/`. Re-resolve moved symbols instead of
  restoring removed compatibility wrappers.
- Produce a compact caller matrix: public operation, authority source before/
  after promotion, TS owner, external effects, retained legacy caller, and
  exact deletion condition. Update this section's completion facts with the
  implementation; do not deliver an inventory-only framework PR.
- When #4122 and delivery response meet, reconcile pending/invalid condition
  diagnosis in the existing resume owner. A missing target is not proof of a
  valid wait; readable historical pending state is not permission to relax
  supervision. Retire duplicate checks only after both contracts are tested.

**T1 — close the public Todo update transaction.**

User completion updates now compose the canonical edit planner and terminal
transaction, including original/edited authority, source-bound validation,
lease release and reviewed Chat recovery. The Python transport shares effect
execution and failure projection. This closes one remaining public caller,
not all T1 callers or the legacy writer. See [operation and compatibility](../../reference/canonical-todo-completion-update.md).

The current ownership slice closes promoted claim transfer, claim clearing and
executor-exclusion edits through this typed update planner. Normalization is
part of request identity, so replay cannot restore a superseded claim. A
lease-bearing ownership change remains a lifecycle operation, not metadata
authority; the legacy writer remains for unpromoted Goals. This is a bounded T1
closure, not completion of all Todo fields or Goal promotion.

Bounded prerequisite: `todos/public_update.ts` now composes authoring scope,
external-wait topology and Monitor/field planning over one locked source.
The public Python writer no longer sequences their leaf RPCs or derives the
Monitor wait baseline. `update_source.py` supplies complete compact active/archive
facts, never a display-limited inventory. A partial topology edit validates its
retained wait; copy-only edits preserve the original fence without re-arming it.
Explicitly clearing the condition still permits changing its former topology.
Locked completion proof is checked before this pure plan, so a stale proof wins
over unrelated invalid field diagnostics; no write occurs in either case.
This deletes orchestration, not persistence: lifecycle/lease admission, completion
effects, writer lock, capture and provider CAS/replay remain with their existing
owners. The internal terminal/import field codec still has actual callers and
does not acquire the public update policy. Native metadata expansion and T2
atomic follow-up are not fully closed. Lease-edit PR #4152 is merged; bounded
planning updates now reuse that fence and the existing CAS/receipt transaction.
Continue with the remaining field/effect inventory, not another update engine.

Work-requirement editing was first closed for non-Monitor Agent Todos without a
retained lease: `action_kind`, `task_domain`, `task_repository`,
`required_write_scopes`, `required_capabilities`, `target_capabilities` and
`explore_result_node_refs` use the existing v1 planning transaction. Public
legacy edits and native planning share `todos/work_requirements.ts`; Monitor
successor authoring and receipt verification reuse its repository/capability
codecs instead of retaining scheduler-owned copies. No new RPC or store is added.
Omitted/blank scalar input preserves state; explicit empty collections clear
requirements. Deliberate correction: invalid members, unsafe repository routes
and over-capacity Explore references reject the whole public update rather than
silently dropping requirements or truncating references.
SCP-style password-bearing userinfo is rejected too, including Monitor successor
routes; username-only Git transports remain valid. Unrelated historical
fields are not revalidated by a copy edit. Repository/capability aliases retain
one normalized replay identity. Requirements declare needed work, not a grant:
ownership, decision outcomes, generic raw patches, Monitor edits and leased
requirement changes remain fenced. The Python reader/bootstrap codec and legacy
writer still have real callers; this slice does not retire them or complete T1.
Next close ownership/decision metadata with their lifecycle admission and
validation effects, then the remaining leased Monitor transaction in T2.

Declarative decision metadata is now part of the same v1 planning transaction.
`decision_scope` is accepted only on `user_gate` records and
`required_decision_scopes` only on Agent Todos; both are normalized to the
public `decision_scope_v0` shape, deduplicated in first-seen order, and rejected
atomically when malformed or attached to the wrong role. Explicit empty
`required_decision_scopes` clears a stale dependency. `decision_outcome` and
`decision_scope_outcomes` remain effect-owned terminal state and are rejected by
the native planning boundary. The public planner also preserves omitted scope
fields instead of materializing nulls, so an unrelated metadata correction no
longer erases a retained user-gate scope. This closes the declarative metadata
part of T1 without granting approval, lease, completion, or promotion authority.

- Reuse the current provider text/note transaction, lifecycle admission,
  field-plan and completion rules. Enumerate actual public metadata edits and
  explicit-clear behavior before implementation; this is not permission to
  widen `UPDATE_FIELDS` to every stored field or admit terminal transitions
  through a generic patch.
- Deliver one coarse typed transaction covering admitted intent, actor/claim/
  exclusion/lease checks, field semantics, final validation, CAS and replay.
  Keep external execution/checkpointing outside pure reduction. Monitor effects
  that cannot fit safely remain explicitly unsupported until T2; list them.
- Delete replaced Python update decisions and leaf-RPC orchestration in the
  same PR. Keep the legacy codec/lock and compatibility writer while
  unpromoted callers still need them; do not claim full writer deletion.
- Prove omission versus clear, unclaimed copy correction versus privileged
  metadata, other-owner/lease rejection, no-op, invalid-input no-write,
  competing revisions, retry and lost-response recovery through the public
  command and affected real providers.

Monitor configuration now uses the existing native planning transaction as well
as the public legacy planner. A typed authoring codec owns target/cadence/due/
expiry/watch-only fields; observation hashes, timestamps, effect identities and
generations stay with the polling lifecycle. The Python duplicate field allowlist
and blanket native Monitor exclusion are removed. Configuration preserves
observation history and cannot retarget an already observed Monitor. The lower
import/observation codec retains its callers and is not exposed as a raw update.
Ordinary CLI/API edits, explicit clears, receipt recovery and the existing active
lease proof are covered; owner-confirmed Chat delegation and leased Monitor
polling remain separate incomplete paths. No configuration prose grants authority.

The local-default program is maintained once in the shared RFC's
[execution sequence](shared-goal-authority-state-provider-v0.md#execution-handoff-and-integration-order).
L1–L4 close mutation semantics before L5 consumer integration; L6/L7 cover
storage and capture; L8 qualifies whole-Goal migration; L9 changes new-Goal
creation defaults. Each package must remove duplicate decisions with its new
owner. A full TS launcher is not required: a bounded Python input/effect adapter
is acceptable while one coarse TS request owns the transaction. Do not turn
these packages into repeated leaf-RPC additions or bypass a retained caller.

Canonical lease request decoding now separates provider mutations from legacy held-fence requests with a discriminated union. The existing lease lifecycle owner composes explicit claim handover with its proven lease transition and one CAS/receipt; Python transports the opt-in and drains the existing projection. Shared registered-Todo restrictions remain the source and recipient eligibility owner. This removes legacy lock/PID/terminal-release parameters from canonical commands and completes the bounded claimed-work handover; automatic continuation/context delivery and executor-held external-effect fences remain separate. See [lease operation and recovery](../../reference/canonical-lease-renew.md#atomically-hand-over-claimed-work).

**T2 — close monitor writeback and its atomic follow-up.**

Bounded prerequisite delivered: `scheduler/monitor_successor.ts` owns successor
route validation and normalization for quota preflight, legacy writeback and
receipt verification. The Python route guard/resolver and the separate TS
receipt-default/capability interpretation are removed. Invalid capability entries,
malformed successor claims and follow-ups without material change fail before
the observation write; valid action/claim/capability aliases and Git transports
are compared as the same route at readback. The original wire observation still
owns the v0 replay digest; normalization must not silently invalidate pending
receipts. The node-independent repository/bootstrap codec remains separately
characterized, not replaced by a runtime dependency.
The native `coordination.local_authority.monitor_poll` transaction now commits a
lease-free Monitor observation and its requested independent successors against
one canonical revision, with one CAS and durable operation receipt. It composes
the existing generation, successor-route, User authoring-scope and Todo-create
planners. Public create and Monitor batches share create admission/duplicate
planning; target selection is shared by legacy preflight and native commit.
Python only routes provider intent and drains the existing projection outbox.

Explicit semantic corrections: completed/archived Monitor targets are rejected;
target-key selection ignores finished history but never guesses between live matches;
successor authoring requires an actually advanced material-change generation,
not merely a repeated `material_change=true` assertion for the same evidence.
Retrying the original operation recovers the original successors instead of
creating new work. A fresh observation with no successor remains valid. User
gates use the existing actor-bound scope, never an inferred global gate.

The leased Monitor path now reuses the current nonterminal lease fence with
Todo metadata updates. Public `quota monitor-poll` carries the execution key and
version through its pending plan, canonical transaction and business receipt.
Observation, generation and independent successors share one CAS; the lease is
unchanged. Canonical due monitors are selectable again, but scheduling does not
grant mutation authority. Runtime time, not the supplied observation timestamp,
determines whether a fresh write's lease is active.

Quota preflight now freezes its admitted decision in a versioned pending receipt.
Recovery settles the original business receipt even after the Monitor becomes
not due or its lease is released; it never substitutes a new lease or re-runs
business effects. Proof-less v0 request identities and completed receipts remain
compatible. Old pending receipts without an admission basis retain current-state
admission and explicitly report when historical recovery cannot be proven.
See [Monitor observation and recovery](../../reference/protocols/quota-monitor-observation-receipt-v0.md).

Boundaries still open: cross-owner successor claims are not implicitly authorized;
unpromoted Goals retain their legacy writer and reject explicit lease proof.
Quota and business authority remain separate recoverable transactions. Canonical
commit success is independent of pending Markdown delivery. This does not finish
all T2 commands or authorize whole-Goal promotion.

- The retained issue-fix grouped Monitor caller now sends observation intent
  through the existing Todo update transaction (request v4). Observation,
  explicit reactivation, terminal-marker cleanup, generation and
  receipt commit together. Legacy and canonical updates reuse the same field
  and Monitor planners; no new RPC, raw patch authority or polling engine is
  added. A fresh post-completion observation advances the cycle even with an
  equal result hash; historical replay never reopens current work. Display
  retry also runs for unchanged groups, including priority-prefixed native text.
  See [observation updates](../../reference/protocols/quota-monitor-observation-receipt-v0.md#observation-updates-and-reactivation).
  Canonical retained-lease/hard-lease reactivation now retires the old execution
  in that same CAS; already-released history is preserved and a fresh execution
  must be acquired explicitly. `todo_monitor_cycle.ts` owns shared update/poll
  admission, removing their duplicate actor/lease branches and correcting their
  soft-claim disagreement. No new Python transition owner or RPC is introduced.
  Grouped reconciliation now plans the complete bucket set in
  `capabilities/issue_fix_monitor_reconciliation.ts`; Python retains ledger IO,
  public writer calls and display delivery. The caller acquires its own bounded
  execution for hard-lease observations/stops, rechecks the plan after acquisition,
  and releases only that attempt. Retry after an observation commit can clean up
  a surviving lease without repeating the business mutation. Reactivation remains
  a non-execution transition through the existing TS owner.
  Missing/malformed ledger evidence, duplicate active targets and older empty
  observations now reject instead of silently dropping or completing a target.
  Membership hashes preserve the original Python Unicode/ASCII encoding contract.
  Explicit runtime-root routing reaches every affected writer and readback.
  This is a complete issue-fix caller closure, not an atomic transaction over all
  buckets: earlier committed buckets survive a later failure. Unchanged retries
  may release their own interrupted execution and drain display. The remaining
  Python adapter is a real caller, not a removable compatibility wrapper.
  See the [operator contract](../../../loopx/capabilities/issue_fix/README.md#pr-lifecycle-monitor).
  Other lifecycle callers, external-effect execution fences, legacy
  persistence/capture and whole-Goal qualification remain separate.
- Preserve unchanged polling/reschedule behavior, generation fences,
  material-change successor deduplication and accountable settlement.
  A monitor remains non-executable delivery context; its independent
  advancement Todo is not the monitor itself.
- Delete the replaced Python transition decisions. External polling remains
  an effect adapter. Prove duplicate polls, crash between phases, races,
  failed effects, another actor's claim, and no-change no-delivery semantics.
  If a required command effect is still unsupported, hold whole-Goal promotion;
  never fall back to a Markdown business write.

**T3 — close remaining structured consumers, then remove their old reads.**

Periodic-report staging, live editorial fallback and approval retry now share
one canonical-first Todo source. Frontier and progress selection reuse the same
complete evaluated snapshot; missing/stale display cannot invent or hide work.
`capabilities/periodic_report_progress.ts` owns report selection and rejection
retry ordering, retiring Python selection/sorting loops. Offset-aware instants
retain microseconds, canonical archived rejection records remain effective, and
explicit runtime-root applies to both intent and Todo IO. Frozen editorial
requests retain their original basis. See [operation and boundaries](../../../loopx/capabilities/periodic_report/README.md#todo-authority-and-report-retries).
This closes that T3/L5 consumer family, not D1 permanent display freshness,
D2 durability, D3 whole-Goal qualification or default-provider selection. The
conditional 5–8 remaining delivery-package estimate is unchanged.

Todo summary lanes and pre-limit work counts now share `todos/summary_lanes.ts`.
Python's lane classification and hidden-work inference loops are removed; quota
recomputes counts after scope selection and carries incomplete source knowledge
through compaction/reprojection. Public canonical Todo lists retain the same
revision's acceptance guard. See [count semantics](../../reference/todo-work-counts.md).
This closes the summary-to-work-lane count consumer, not every T3 source or D1
projection delivery; legacy codecs/renderers and other summary policies remain.


Goal Channel ownership observation now reads a complete canonical Todo/lease revision and shares one TS batch policy with the legacy adapter. It retires display-layer lease time/generation/conflict decisions and local-file reads after promotion. Empty, unavailable and truncated observations remain distinct; see [coordination observation](../../reference/coordination-observation.md). This closes the Goal Channel ownership reader, not other channel panels or whole-Goal promotion.

The D1 document-ownership slice gives readers, editors and projection one visible-region
and Todo-block boundary. It fixes fenced examples becoming real tasks, narrative after
an archive end marker entering history, and sparse imported ordinals or archived
priority labels blocking readback. Projection reuses durable atomic state publication;
an equal-byte retry syncs file and directory before reporting `current`. Narrative and
canonical records stay intact. This converges the retained Python presentation/legacy
input adapter; it adds no RPC or business state machine and does not change TS authority
transactions, provider defaults, SQLite D2 or D3 promotion requirements.
Handoff mode now shares a typed quiescence policy between the legacy adapter and one provider-neutral CAS/receipt transaction. Promoted show/set consume canonical mode and complete Todo/lease facts; the old Python transition decision is removed. Legacy state/lease locks remain until their last writer retires. See [handoff-mode operation and replay](../../reference/handoff-mode.md).

Task-graph topology now shares `work_items/planning_relations.ts` with inventory
and horizon. One pure TS request owns relationship discovery, deterministic
bounded traversal, edge deduplication and missing/truncated completeness; the
Python predecessor indexes, condition parser and traversal are retired. Python
retains status source adaptation and public-safe node/evidence/handoff rendering.
This intentionally distinguishes successor lineage from completion dependencies,
corrects unblocks direction, includes Monitor generation conditions and preserves
parallel/diamond edges at the node cap. See the [graph contract](../../reference/protocols/task-graph-projection-v0.md#typed-todo-topology).
It does not change lifecycle admission, claim/lease semantics or default provider.
The status source can still be incomplete: this closes one T3 interpretation
boundary, not all graph source delivery or the remaining T1–T4 work.

Lease inspection now consumes one canonical Todo/lease/handoff-mode revision
after promotion; an absent canonical lease does not revive a local lease file,
and provider failure cannot fall back to Markdown. The read reports its provider
revision without repairing display or changing the lease. Both routes now use
`task_lease_inspection.ts` for time and eligibility interpretation; Python only
projects source-bound registration/legacy facts and transports the response.
Diagnostics reuse the TS rejection owner, including archived-Todo eligibility,
strict active expiry and bounded source-change retry. Unpromoted storage stays
unchanged; malformed active expiry intentionally becomes a visible error. See
[inspection semantics](../../reference/canonical-lease-renew.md#what-inspection-proves). The shared `task_lease_eligibility.ts` owner
also replaces the Python authority-core and three TS owner-eligibility copies
used by acquire, lifecycle and terminal fencing. Current-lease effectiveness is
derived inside acquire from the supplied owner/claim/exclusion/registration facts,
not from the old caller-provided `effective` hint. Other-Todo overlap facts still
come from the existing complete execution snapshot; release retains its separate
key/version cleanup fence. This closes one T3 reader and shared rule boundary,
not the remaining T1/T2 transactions or promotion. Goal Channel ownership display closes in the separate ownership-observation slice.

Capability resolution now shares `agents/capability_gate.ts`: missing prerequisites,
repair outputs, owner/agent resolution and blocked-Todo bindings have one typed
owner. Quota planning v1 passes normalized requirements, not Python-computed
missing lists; Monitor partitioning invokes the same rule in-process. The public
gate uses one batch; exact-target recovery callers retain a bounded value-only
cached bridge to that rule, not a second implementation. Python keeps legacy
codecs, candidate source/eligibility and the shared profile/rank adapter.
Disclosed corrections: a shared resolution binding names the highest-priority
blocked Todo, display variants deduplicate by Todo identity, and an authoritative
empty backlog never revives stale first-item diagnostics. Target capabilities
remain repair outputs, not permission or installed capabilities. No new provider,
source inventory, enablement or promotion is introduced; compact candidate-source
limits and the remaining T3 consumers still require their own closure.

Quota's scope/claim consumer now composes selection, bounded visibility and the
existing resume planner in one `todo.quota_planning.project` call per source.
`quota_selection.ts` replaces the Python claim-visibility module and the separate
Agent-scope User gate/action filters. Python retains legacy fact codecs, clock
and capability/profile adapters; the typed owner chooses lanes and ordering.
This deliberately corrects two semantics: explicit User gate applicability is
not cancelled by another Agent's claim or executor exclusion; active-next-action
rows obey the same scope and removed-continuation restrictions as ordinary rows.
User actions use `bound_agent` (legacy claim fallback), not execution ownership.
The User summary no longer presents an Agent execution `claim_scope`. Counts
precede display limits; claim priority, Monitor writeback/capability fences and
resume obligations remain unchanged. These are read decisions, not write grants.
This slice does not replace source adapters, add a second inventory, or claim
full T3 completion. Continue auditing the remaining consumers below; independent
standalone Todo summary display codecs remain until their callers migrate.

Current bounded delivery: shared-goal alignment and amendment admission use one
`shared_goal_work_source.py` snapshot per decision, reusing the canonical Todo
summary after promotion. The same provider read optionally supplies leases at
that revision; absent/empty/stale display and old lease files are not fallback
authority. `shared_goal_work.ts` owns their open-work, claim and exclusion
selection; the old Python selectors and amendment's second Markdown parse are
removed. Excluded work is not recommended to that Agent, but remains available
as amendment impact context. The source digest binds the canonical revision;
`canonical_todo_snapshot` has event sequence 0, not a fabricated Goal intent
revision, and a changed digest requires proposal rebase even without events.
Active malformed lease expiry now fails through the existing typed lease rule.
This independent consumer slice does not depend on open #4142, close T1/T2,
migrate all T3 consumers or grant amendment commit/whole-Goal promotion authority.

Standing-decision consumer closure: `todos/standing_decision.ts` owns reusable
receipt eligibility and chronology, shared by status/quota reads and archive
selection. Python decodes legacy metadata and submits one batch; its old receipt
selector and the archive-local TS eligibility copy are removed. Canonical reads
use the complete Todo snapshot, including retained archived decisions, before
assigning display indexes. Later rejection/cancellation is ordered by decision
time, not Todo ID; contradictory unresolved chronology yields a diagnostic and
no active receipt. Explicit user-gate metadata replaces notification heuristics
for this authority surface. These corrections are disclosed in the
[decision-scope contract](../../reference/protocols/decision-scope-v0.md#decision-chronology-not-display-order).
Legacy all-undated source-order compatibility remains; native display order is
not authority.

Decision dependency consumer closure: `todos/decision_scope.ts` now owns scope
coverage, exact-target relations, standing-receipt scoping and consistency
diagnostics. Quota selection shares its explicit gate-recipient predicate:
`global_gate` / `blocks_agent` take precedence over claim attribution. An exact
link to a different Todo cannot silently satisfy a broad scope dependency; it
produces a repair diagnostic, not approval or automatic retargeting. Python keeps
legacy decoding and repair presentation, not a second rule implementation.
Agent fallback, global Todo and summary consumers batch their candidate relations
to avoid one RPC per pair. Legacy completion still uses the shared coverage rule.
Validation covers the production-scale fixture, complete provider reads beyond
display limits, stale/missing display, and isolated real-state snapshot parity.
Scoped fallback selection now shares that TS owner for eligibility, priority,
deduplication and gate relations. The Python action-token gate matcher and
selection loop are retired. Explicit dependencies/global gates take precedence;
equal legacy action labels retain blocking compatibility, not word-overlap
authority. Different or missing labels cannot certify safe independence. This
deliberately removes inferred overlap dependencies and unjustified safe bypass;
see the [fallback contract](../../reference/protocols/decision-scope-v0.md#scoped-fallback-selection).
Python retains lane source adaptation and compact presentation; no new provider
read or resume evaluation is added. Remaining T3 work includes consumers that
reconstruct diagnostics from compact summaries; do not call those migrated.
This does not close T1/T2, all T3 consumers, or any durability/promotion hold.

Runtime capability re-entry now uses that same TS owner for verification-target selection,
owner-authority exclusion, advisory versus bound Turn handling, and the recovery contract
without durable grants. Python removes the former target lookup/filter rules and only adapts
host/scheduler facts, calls one typed reducer, and renders shell argv. One interaction packet
reuses the result; re-entry projection itself adds no runtime call on a healthy path. The intentional correction is that an
eligible fallback recommendation cannot hide a blocked task's real capability check before
explicit selection. Success re-enters the same Turn; failure still allows explicit fallback
selection. A committed receipt's Todo remains bound.

`agents/capability_memory.ts` now owns host-local runtime declarations, validation,
atomic merge and idempotent persistence through the existing mutation lock and durable
JSON writer. `capability_gate.ts` owns Goal/Agent/invocation precedence and local
unavailable overrides; the former Python union is removed. Live quota and executing
Turn entry remember the five explicitly typed runtime capabilities through the admitted
turn-start capability hook; quota core stays read-only and hook failure is isolated. Planning remains
read-only. Quota preparation reads fresh Agent state even when the status projection
is cached; all selection and settlement recomputations share that boundary. Python
retains registry/host adaptation, hook composition and CLI rendering. This adds one availability reducer call per decision, one memory read per
registered scoped decision, and one observation call for an explicit live declaration. No duplicate Python state reducer or new storage framework is added.

The observation scope is host/runtime, registry, Goal and registered Agent; observations
do not become a Goal-wide declaration, grant, capability lease, shared-authority head
or provider write. Inspect/correct/forget through `agent-capabilities`; the generated
`/loopx` skill explains negative observations and recovery. Existing frontend capability
editors govern optional feature configuration, not host tool observations, so their
configuration owner and fields are unchanged. See [operating semantics](../../quota-allocation.md).


Advancement-frontier checkpoint closure: `todos/frontier_revision.ts` owns
agent selection, completeness, material hashing, long-chain thresholds,
checkpoint construction and ACK/rearm classification. Observation, semantic
writeback and runnable-successor receipts now use the same typed checkpoint
constructor. Successor projection resolves the complete source, owned identity
and replacement checkpoint list in one request; Python no longer assembles
receipts or fetches the same frontier separately for each identity field.
Python retains the v0 field manifest, legacy JSON/metadata codecs, successor
eligibility and the existing obligation-id derivation. TS owns timestamp
ordering and reconstructs only a unique fresh successor insertion against the
complete current source; Python verifies its predecessor obligation id.
Compaction preserves the material `done` field, and history retains successor
lineage. Ambiguous, stale, truncated or unrelated material changes cannot close
the current obligation. This closes one T3 rule group, not
the remaining consumers or T1/T2/D1–D3.

Long-chain scope correction (#4667): Agent lanes count 15 claimed advancement
Todos or 20 claimed open Todos with claimed advancement work. Shared candidates
remain selectable but no longer impose this duty; unscoped Goal observations
retain the selectable-pool thresholds. Full material revisions include terminal advancement rows;
timestamp-only maintenance does not rearm them. A complete agent-owned identity
also keeps an accepted long-chain ACK valid when peers change shared unclaimed
work. Owned material edits still rearm; an entirely unclaimed lane has no
long-chain duty. Historical revision-only ACKs keep exact-revision matching.
Intentional corrections: semantic writeback now preserves the owned identity;
an identity without a revision or an explicitly incomplete checkpoint cannot
suppress replanning. Other trigger kinds cannot borrow long-chain identity
matching. The same TS owner now supplies `obligation_identity_revision` from the
owned basis for the existing Python identity codec and predecessor proof: peer
churn cannot invalidate an open Turn before its ACK. Numeric thresholds and
write authority remain unchanged. `replan_semantics.ts` accepts and projects an
evidence-linked vision path for long-chain review, preserving existing progress
exits and stricter vision obligations. The real CLI regression follows the
projected binding through durable ACK, satisfied checkpoint, one spend and next
Turn readback; maintenance stays quiet and an owned material edit rearms.
This advances overall-roadmap S2/S3's existing T3 owner, with no new provider,
store migration, frontend setting or claim of broader RFC acceptance.

The canonical index is built before display truncation. Exclusions, duplicate
ids/index lanes, incomplete timestamps and authoritative incomplete indexes
retain fail-closed behavior. Real CLI tests cover both ACK routes through
persisted run/history readback, a peer claim and an owned material edit. The
complex fixture also exercises revision-only and owned ACKs through the real
File provider with stale/missing display. Frontend/Lark configuration is
unchanged: this is the shared quota/recovery checkpoint path, not a new control
or user confirmation. No provider promotion is implied.

Large source facts use lossless deflate/base64 transport above 512 KiB, retaining
the exact v0 material bytes and the shared 2 MiB request boundary. The TS decoder
rejects malformed payloads and inflation beyond 64 MiB; it never truncates rows
or silently falls back to Python decisions. Real completed-history HTTP reads
and complete-checkpoint tail edits guard against transport-size regressions.

The list-filter consumer now uses `compact_evaluated_todo_group` instead of
re-running resume evaluation on active-only rows. Initial parsing/canonical reads
still evaluate against the full source through the TS owner; filtering requires
matching evaluated conditions and cannot make archived prerequisites disappear.
The shared synthetic fixture adds scoped-but-undecided gates and linked approvals;
a separate long-history CLI regression covers thousands of archived records.

Bootstrap and writer-outbox capture now include referenced archived resume targets
and their transitive dependency records. `archive_capture.ts` selects actual records,
rejects duplicate identities and contradictory role/class facts, and never imports
saved readiness as evidence. Legacy archive moves now retain the source role without
reserializing the original receipt. For older role-less rows, only an explicit
agent-only task class permits agent reconstruction; user decision authority requires
a recorded user role. Captured history does not become an active work/lease lane.
Unidentifiable referenced history still needs explicit repair, not a post-promotion
Markdown fallback. This closes the demonstrated dependency omission, not all history
import, provider qualification, soak, or D3 cutover requirements.

- Audit Turn/quota, Dashboard, standing decisions, shared-goal alignment and
  amendment revision inputs. Reuse #4117's canonical source adapter and pass
  one snapshot through a decision; do not build another Todo inventory.
- For each migrated caller, delete its post-promotion Markdown/event fallback in that
  PR. Prove absent/stale/malformed display, empty canonical state, unavailable
  provider, terminal/archive ordering, claim scope and data beyond UI limits.
  Canonical absence must not revive legacy data or become successful completion.
- Keep outcome history supervision, canonical obligations and settlement
  authority separate. Unknown observations cannot settle Todo/replan work.
  Explicitly disclose any semantic correction; do not label it full parity.

The retained-journal read boundary now shares one TS owner for scan admission,
checkpoint range, contiguous page coverage, lookahead and final-head agreement.
File and NoKV also share retained-history validation and append construction;
provider revision hashes, physical locks/CAS and backend headers remain local.
This retires duplicated storage-protocol knowledge without a new RPC, Python
bridge, capability or provider. The existing coordination internal owner is
sufficient; built-in File and optional NoKV/SQLite/PostgreSQL implementations
retain their deployment boundaries.

Intentional corrections: a positive checkpoint against an empty store is
`scan_cursor_out_of_range`; non-string cursors are `invalid_scan_request`;
a missing/reordered retained row or contradictory final head cannot produce a
successful page. PostgreSQL read operations use one repeatable-read snapshot,
so a concurrent commit appears on the next call instead of mixing newer rows
with an older head. The scan proves its requested interval, not an audit of
history before that checkpoint. Successful schemas, File/NoKV persisted bytes,
request identity and revision algorithms remain compatible. This supports T3/D1
readers but does not finish Todo writers, retention/compaction or promotion.

Continuation readback now shares one typed succession resolver, handoff state
machine and summary closure decision. The legacy adapter no longer owns those
rules. Full-source evaluations survive display selection; nonexistent/self
successors cannot certify closure and archived continuation evidence survives
capture. The existing archive-capture request advances to v1 so older runtimes
cannot silently omit the expanded graph. Query subsets do not emit whole-source
closure proofs, and bounded handoff views preserve their state and exclusions.
See [continuation readback](../../reference/todo-continuation-readback.md).
This closes that T3/L5 consumer family and its bounded L7 dependency, not D1–D3
or every T3 consumer. Python retains codecs, IO and the documented legacy route
prose hint until its remaining writers emit explicit replan flags; no new
capability/provider or parallel business authority is introduced.

The quota admission/settlement consumer now resolves explicit Todo selection
from the complete unified Todo reader before display compaction. It removes the
direct Markdown candidate append while preserving the pre-promotion event
adapter. Post-promotion empty or unavailable authority cannot revive display
rows. Settlement progress is reduced from the existing TS receipt chain;
Python renders identity-complete commands and both JSON/Markdown readbacks.
The existing idempotent writer repairs a missing spend receipt without a second
debit. This closes the demonstrated T3 consumer gap, not D1–D3, provider
promotion, or the remaining Python transaction adapters. See the
[operating contract](../../quota-allocation.md#receipt-backed-settlement-progress).

**Long-history transport boundary.** Replan history still has one TS decision
owner. Small requests retain the inline codec; larger complete fact snapshots
travel through a private, digest-bound local file reference. The same reducer
checks all rows, lane-scoped ACKs and retry identities; neither the RPC budget
nor a display window permits history truncation. Missing, altered or malformed
snapshots fail before a decision, and the adapter removes temporary files on
return or rejection. This repairs a T3/S2 settlement blocker and supplies S7/R7
wire-growth evidence; it does not make full-history parsing constant-memory or
qualify distributed execution. Cursor/checkpoint reduction remains a measured
follow-up with complete-source parity, not a second Python policy. See the
[history decision evidence](ledger/typescript-control-plane-migration-v0/2026-09-22-replan-history-policy.md).

**Recovery boundary (2026-09-22).** The
[authority archive command](../../reference/authority-archive.md) places retained
history validation, delta reconstruction and resumable restore in the existing
TS coordination owner. Python only resolves CLI paths and transports compact
requests/results; it does not reconstruct domain state or duplicate a provider
migration rule. The state-log codec is reused rather than creating independent
File/SQLite/PostgreSQL export formats. This enables an independently verified
recovery copy for D3/L8, while authority adoption, executor fencing and actual
Python writer retirement remain separate exits.

**T4 — collect full-writer retirement after durability cutover.**

- The 2026-09-19 command audit retires two already-typed but unconsumed
  execution surfaces: `coordination.local_authority.todo_compatibility_edit`
  and `coordination.local_authority.mutate`. Shared projection reduction and
  commit preparation remain because live claim, lease, update, archive,
  monitor, and team-plan transactions use them. The same audit retires the
  unused public `todo capture-followups` batch command instead of migrating it;
  ordinary `todo add` remains available but is not claimed to preserve the
  retired command's atomic batch, deduplication, or replay contract. The
  standalone `todo suggest` prompt command is also retired. Candidate analysis
  stays with the current agent and existing Todo read/authoring paths; it does
  not need a renamed command or a new wrapper protocol. See the
  [discovery and compatibility boundary](../../reference/protocols/long-horizon-agent-state-protocol-v0.md#candidate-discovery-and-command-retirement).
- Depends on T1–T3 and the shared RFC's [D1–D3](shared-goal-authority-state-provider-v0.md#durability-execution-cards), including owner approval
  and the explicit legacy migration window. Search remaining imports and
  public command routes before deleting old Markdown business writers,
  capture-only adapters and duplicate reference aggregates.
- Keep permanent Markdown rendering, validated import/export and external
  effect adapters. Every retained bridge names its live caller and exit
  condition. No full TS CLI, daemon or remote service is required.

**Validation and stop rules for every card**

Use `tests/fixtures/control_plane/coordination_production_scale_v0.json`,
`tests/control_plane/canonical_authority_fixture.py` and the existing provider
conformance suite when their semantic dimensions are affected. Verify their
current schema before reuse; never silently shrink a complex fixture to pass.
Run `npm run typecheck:control-plane`, `npm run test:control-plane`, the
affected public CLI tests and risk-based canary coverage. Shared transaction
changes require affected File/NoKV arms and an isolated real PostgreSQL run;
new local-store claims require that actual backend, not an in-memory substitute.

Before moving code, assert intended legal and illegal behavior independently.
After moving it, report baseline/head parity, intentional differences,
production versus bridge LOC, and crossings separately from tests/generated
code. Stop on an unknown writer, missing real environment, unexplained
difference, private-data dependency or failed required gate. Do not waive
authority, evidence, fixture or payload budgets to complete a card.
A read-only snapshot or disposable synthetic Goal is allowed; active Goal
promotion, new models/jobs, soak automation, release or merge need their
respective explicit authority.

Stacked schema-identifier cleanup is independent maintenance, not a prerequisite
for this sequence. Absorb a downstream change only when the selected complete
transaction actually needs it; rebase the remaining work after its base merges.

### Manager collaboration integration checkpoint (2026-09-13)

At `7eb4b7bb1661bd5eff63a8725a33169792d5964b`, #4152 is the merged
lease-fenced text/note update slice; #4121's SQLite candidate is also merged,
without provider promotion. These actual heads supersede the earlier execution
card's pending-code implication, not its qualification holds.

The [manager/handoff RFC](capable-manager-semantic-handoff-v0.md) follows this
RFC's transaction-payoff rule. Its proposed collaboration owner replaces one
complete request transaction and old semantic callers; it does not introduce
a leaf RPC per field, a new TS daemon, or another Todo/Vision/lease authority.
Existing `coordination/todo_continuation.ts` is a promoted-local, same-machine,
registered-agent, lease-free Todo path, not a general pre-Todo/cross-Goal
handoff. Retain its actual compatibility semantics while integrating it.
M2 reports the migration economics receipt and cross-commit recovery evidence;
M1 normal host tools need not wait for full TS or provider migration.
Shared Goal amendments retain their own proposal/commit boundary, and
shared-authority D1–D3/T4 conditions remain applicable to any affected storage
or full-writer retirement. No new runtime behavior is delivered by this note.

## 0. Decision in one example

During migration, the Python `loopx` CLI sends one coarse typed transaction to
a LoopX-managed TypeScript runtime. For example, Turn settlement first asks
TypeScript to validate the journal and authorize any still-Python providers;
after Python checkpoints those external outcomes, TypeScript performs the final
reduction and returns one typed result. A replay with no pending provider needs
only the reduction call. Python translates the result into the legacy CLI shape.
It does not call a series of TypeScript leaf helpers or retain parallel enums
and reducers.

The same PR must delete the Python semantic path it replaces. A new TypeScript
module is not migration progress by itself: the payoff is fewer semantic
owners, fewer cross-runtime round trips, and a facade with a credible deletion
condition.

After the CLI itself migrates to TypeScript, CLI-only use imports the same
kernel in-process and the Python-to-TypeScript bridge disappears. When the App,
CLI, scheduler, or several hosts need one shared writer, the same kernel may run
inside one optional managed daemon. This is one kernel with two deployment
forms, not one server per control-plane family.

## 1. Problem

LoopX already has TypeScript host and dashboard surfaces. The Effect Program,
Turn-journal effects, several Todo/quota decisions, and scheduler state now
have TypeScript owners, while much of the CLI composition and compatibility
surface remains in Python. A big-bang rewrite is too risky, but continuing to
translate leaf helpers would leave a chatty bridge and duplicate DTO knowledge:
code would move without simplifying the product.

The migration therefore needs intermediate states that satisfy all of these
constraints:

- one semantic owner for every migrated rule;
- no user-visible CLI split and no manual daemon lifecycle;
- real side effects can migrate, not only pure projections;
- correctness is qualified against a pinned pre-migration baseline and
  independently stated invariants;
- latency, packaging, upgrade, rollback, and crash recovery are measured at
  every cutover;
- each PR is a complete, reviewable replacement slice;
- migration economics improve: old semantic code and temporary scaffolding
  leave faster than bridge code accumulates.

## 2. Architecture decision

### 2.1 One TypeScript kernel

`@loopx/control-plane` is the intended semantic kernel. Domain modules own
typed state, interpretation, transition rules, and the internal effects that
belong to those rules. A transport shell must not become a second business
owner.

```text
Python CLI during migration ─┐
LoopX App / scheduler ───────┼─> one typed runtime boundary ─> TS kernel
future TS CLI ───────────────┘
```

The boundary uses coarse, versioned requests such as “settle this Turn” or
“commit this journal”, not chatty property getters. The runtime has a static
typed handler registry. Adding a domain handler does not create another
server.

### 2.2 Two deployment forms, one implementation

| Product topology | Execution form |
| --- | --- |
| CLI-only after the TS CLI cutover | Import and execute the TS kernel in the CLI process; no daemon |
| App-only | Embed the same kernel in the App runtime |
| App + CLI + scheduler, or concurrent clients | One managed local authority daemon; clients connect to the active writer |
| Migration while Python remains the CLI | One idle-exiting loopback runtime bridges Python to the migrated TS kernel |

If an authority daemon owns a registry/workspace, a CLI process must connect
to it instead of opening a second direct writer. Runtime discovery and startup
are automatic; users do not configure ports or supervise processes.

### 2.3 TypeScript owns migrated effects

The target is not “TypeScript decides, Python always executes”. TypeScript may
own internal LoopX effects such as atomic state checkpoints, event appends,
receipt commits, and idempotent reducer writes. Each effect has a typed request,
stable idempotency identity, typed receipt, and retry policy.

Asynchronous execution does not weaken settlement ordering: an effect receipt
is emitted only after the awaited durability boundary succeeds. It does,
however, permit concurrent requests, so the authority that owns a migrated
write must also own its per-key serialization or compare-and-swap contract.
Caller-side locking is acceptable only as an explicitly transitional guard; a
native TypeScript caller must not bypass the invariant after cutover. Retry
identity is operation-specific: when one Turn effect checkpoints several
successive journal states, the broad Turn effect id alone is not proof that two
write payloads are the same operation.

External authorities remain explicit adapters: model calls, human gates, host
schedulers, credentials, and third-party mutations are not hidden behind a
universal executor. Their receipts return to the Effect Program for
settlement.

### 2.4 Replacement, not production dual-running

Characterization may execute the old and new implementations offline against
the same pinned corpus. Production does not keep two rule engines or dual-write
semantic state. Once a slice passes its gates, callers flip to TypeScript and
the replaced Python rule is removed. A narrow compatibility facade may remain
only for a real public import, persisted schema, or unmigrated callback.

### 2.5 Validate once at every trust boundary

TypeScript types are erased at runtime. Network/RPC payloads, parsed JSON,
persisted state, extension input, and adapter responses therefore enter the
system as `unknown`; a static annotation or `as T` assertion does not prove
that those bytes satisfy the contract. Each migrated domain must decode these
values through a typed decoder or an explicit versioned schema parser before a
domain handler or Effect interpreter consumes them.

After successful decoding, the TypeScript kernel owns the typed value and may
rely on the compiler instead of repeating ad hoc field checks throughout the
domain. Transport checks such as framing, authentication, and size limits stay
separate from schema validation and semantic invariants. An unchecked
`JSON.parse(...) as T` must not establish control-plane authority.

`as unknown as T` is permitted only as a named migration seam: its exact call
site, upstream validator, negative boundary coverage, and removal owner must be
visible in the cutover PR. A migrated domain cannot pass its promotion gate
while public, persisted, RPC, or extension input still reaches its semantic
core through an unvalidated assertion. TypeScript complements runtime
validation; it does not replace it.

## 3. Current baseline and phase transition

Effect Program moved first because it joins ordered steps, identity,
short-circuit failure, replay, receipts, and settlement. That architectural
choice is now implemented rather than hypothetical.

### 3.1 Shipped baseline

| Slice | Canonical TypeScript ownership now shipped | Remaining migration debt |
| --- | --- | --- |
| Effect runtime and Turn journal ([#3416](https://github.com/huangruiteng/loopx/pull/3416)) | Effect algebra, settlement rules, runtime lifecycle, typed Turn-journal interpretation, and durable checkpoint effects | Python settlement facades still expose fine-grained calls and duplicate DTO/enum shapes |
| Todo, quota, and scheduler proof slices ([#3431](https://github.com/huangruiteng/loopx/pull/3431)–[#3434](https://github.com/huangruiteng/loopx/pull/3434)) | Completion fence/state, workspace causality, and scheduler transitions each have one TS rule owner | The cuts are mostly leaf-shaped; Python still composes several product transactions |
| Scheduler durable state ([#3440](https://github.com/huangruiteng/loopx/pull/3440)) | State normalization, persistence, replay, and one coarse transition are TS-owned | The Python compatibility path still pays a cross-runtime transport tax |
| Scheduler heartbeat/state transaction | TypeScript owns receipt freshness, ACK and host-failure validation, state construction, failure-cache transitions, replay/CAS fencing, atomic writes, and the public JSON/Markdown projection | Generated, receipt-bound host follow-up runs through the native TS CLI; Python remains only for unbound/manual compatibility calls and external host mutation |
| Quota spend commit transaction | TypeScript owns final spend-transition validation, typed event construction, effect replay/CAS fencing, crash repair, and the JSON/Markdown/index write set | Python still projects `should-run` and settlement readback facts, and holds the legacy cross-writer index lock until the CLI/index writers move in-process |
| Quota void commit transaction | TypeScript owns spend-target resolution, before/after reduction, canonical correction construction, effect replay/index CAS, prepared-receipt repair, and the JSON/Markdown/index write set | Python retains `should-run` facts, clock/effect identity, the legacy cross-writer index lock, one transport call, and compatibility entry points |
| Quota monitor-poll commit transaction | TypeScript owns monitor admission revalidation, target/event/result construction, effect replay/index CAS, provider intent, and repairable JSON/Markdown/index persistence | Python projects compact `should-run` facts, invokes the real Todo provider between at most two reductions, reloads legacy status, and holds the cross-writer index lock |
| Runtime decoders ([#3443](https://github.com/huangruiteng/loopx/pull/3443)) | Stable primitive decoding has one small shared module; domain decoders remain local | No larger schema framework is justified |
| Transaction payoff ([#3464](https://github.com/huangruiteng/loopx/pull/3464), [#3481](https://github.com/huangruiteng/loopx/pull/3481), and Todo completion) | Turn settlement, quota delivery routing, and Todo completion each cross one coarse TS boundary; the Todo transaction owns identity, replay fencing, validation planning/result reduction, continuation/recovery, and completion metadata | Python still executes explicitly external providers and materializes legacy Markdown/event results; other domains still need their own bounded cutovers |
| Promoted-authority Todo claim | TypeScript owns the provider-head read, lifecycle validation, complete-record update, hard-lease check, CAS, receipt, and readback-safe result for claims after authority promotion | Default local Markdown mode remains on the legacy writer; other Todo mutations and Markdown regeneration remain bounded follow-ups |

The scheduler facade exit now includes its first bounded Stage 3 route. A
versioned `heartbeat_followup_cli.ts` accepts bounded compact host facts from
the generated ACK/failure hint, verifies the originating heartbeat receipt,
and runs state validation, replay/CAS fencing, the locked write, and public
JSON/Markdown projection in one Node process. The Unix, Windows, and installed
console launchers select this route only for exact receipt-bound commands, so
the recurring host path no longer starts Python or pays a Python-to-Node
request/response. The deleted Python ACK rule and adapter-only tests no longer
form a second semantic owner. A decision-free Python compatibility adapter
remains for explicit in-process and manually constructed unbound calls. It can
be deleted after those callers consume generated receipt-bound hints. The host
automation adapter and its TOML/SQLite writes intentionally remain Python and
external to this transaction.

These slices proved correctness, packaging, Windows lifecycle, crash recovery,
real TS-owned writes, and acceptable warm primitive-call latency. They also
revealed the migration boundary: leaf-by-leaf translation grows TypeScript,
facades, parity fixtures, and bridge traffic before enough Python composition
can be deleted.

### 3.2 Payoff-phase decision

The migration therefore enters a **transaction-payoff phase**. New leaf
migrations are rejected unless they directly unlock a complete transaction
cutover and deletion in the same PR or the immediately stated bounded follow-up.
The unit of progress is now an operator-visible transaction, not a helper,
enum, dataclass, or source file.

A transaction cutover must:

1. move validation, state transition, migrated internal effects, and result
   construction behind one domain-owned TS request/response boundary;
2. delete the replaced Python rule composition, fine-grained API, duplicate
   enums/dataclasses, and implementation-specific tests;
3. leave Python as transport, legacy response projection, and explicit adapter
   for still-external authorities only;
4. avoid leaf-level bridge chatter. A transaction whose effect providers have
   migrated to TypeScript, or a replay with no pending provider, uses one
   request/response. While a real provider remains in Python, use at most two:
   one fail-closed preflight that authorizes named effects and one final
   reduction over their checkpointed outcomes. A model call, human gate, or
   third-party mutation starts a new receipt-bearing transaction rather than an
   implicit callback tunnel;
5. name the exact condition under which its Python facade and bridge operation
   can be removed.

Domain invariants remain with their bounded owner. “Coarser” does not mean one
universal control-plane command or one mega-reducer.

## 4. Migration sequence

### Stage 0 — Pin behavior and authority (complete, repeated per transaction)

For each selected transaction, record authoritative schemas and independently
reviewed legal/illegal transitions, production callers and side effects,
matched latency/install baselines, and rollback/state-compatibility boundaries.
Characterization fixtures are temporary migration evidence, not permanent
specification.

### Stage 1 — Effect Program and managed runtime foundation (shipped)

The TypeScript Effect algebra, settlement semantics, Turn-journal
interpretation, durable checkpoint effect, runtime lifecycle, packaging,
upgrade fingerprint, and boundary decoder foundation are on `main`. The Stage 1
settlement-facade cleanup is complete: Python fine-grained settlement readers
are removed, while coarse readback/projection remains bounded Stage 2B work.

### Stage 2A — Bounded rule-owner proofs (shipped; do not repeat as a pattern)

Todo completion, quota workspace causality, scheduler transitions, and
scheduler durable state established that a Python caller can safely switch to
a single TS semantic owner. Their characterization and facade layers were
appropriate migration evidence, but copying the same leaf pattern across more
domains would now increase total complexity.

### Stage 2B — Complete transaction cutovers (active)

Select by deletion leverage and runtime traffic, not by ease of translation.
The shipped Turn settlement, quota delivery-routing, Todo-completion,
scheduler-heartbeat, quota-spend commit, quota-void commit, and task-lease acquire cutovers
establish the pattern.
Subsequent candidates must name a remaining transaction and its deletion
leverage; remaining quota settlement readback is eligible only when it can
retire or materially shrink the facade rather than add another leaf handler.

The prior-host-Turn recovery boundary remains a transaction-level follow-up:
receipt selection, exact Todo lifecycle observation, settlement validation, and
recovery/continuation selection must move together before its Python coordinator
can be retired. The current source-boundary repair reuses `todo list --todo-id`
for lifecycle evidence so display truncation cannot keep a closed Turn in
recovery. It preserves closeout policy and adds no leaf RPC; it is not a completed
Stage 2B cutover. Future migration must retain crowded-inventory, provider-failure,
identity-conflict, and same-Turn no-spend recovery coverage.

For each completed transaction, replace migration-only characterization workers
and Python implementation fixtures with native TS semantic/invariant tests plus
one durable end-to-end adapter contract. Retain a characterization corpus only
while an old authority remains executable or a versioned compatibility window
requires differential proof; record its deletion trigger when introduced.

Current implementation status: Stage 1, the bounded Stage 2A proofs, and the
shipped Stage 2B cutovers are in place:

- Turn settlement/commit: TypeScript owns preflight authorization,
  ordered-prefix and replay validation, provider failure classification,
  receipt construction, terminal closeout joining, and the canonical result.
  A real Python provider uses two coarse reductions; completed replay uses one.
- Quota delivery routing: TypeScript owns continuity-versus-fallback selection
  and the selected Todo's settlement boundary. The in-flight path moved from
  two cross-runtime calls to one; the empty candidate short circuit remains
  zero.
- Todo completion: TypeScript owns completion identity, terminal replay fence,
  validation declaration/effect planning, validation-receipt reduction,
  continuation/recovery, completion metadata, registered-agent admission,
  successor ownership/exclusion, and existing-successor selection in one
  transaction. Python projects registry and Todo-source facts without deciding
  policy. A Todo without declared validation, including a replay, uses one
  reduction. A real caller-approved validation command remains an explicit
  Python provider between two reductions. Todo and policy-source snapshots are
  compared after the mutation lock so a receipt for one declaration or agent
  registry cannot authorize changed facts. Policy admission failures are
  returned as typed data by that same reduction and consumed only after Python
  actor/lease admission, preserving legacy error priority without a leaf
  runtime call inside the writer critical section. Materialized and
  event-projected writes consume the same typed result.
- Scheduler heartbeat/state: TypeScript owns receipt freshness, ACK and
  host-failure validation, identity-aware progression, failure-cache
  retention/counting, replay and CAS fencing, preview reduction, the locked
  atomic write, and the legacy-compatible JSON/Markdown result. Generated
  receipt-bound ACK/failure hints carry a versioned bounded fact packet and
  enter that transaction directly through the native CLI. Python no longer
  participates in the recurring path. Its decision-free compatibility adapter
  remains only for explicit in-process and unbound manual callers and exits
  when those callers adopt the generated route. Host automation mutation stays
  an external Python effect.
- Quota spend commit: TypeScript revalidates the compact before/after transition,
  constructs the canonical public-safe spend event, fences the effect with a
  locked index CAS, and commits JSON, Markdown, index, and transaction receipt
  as one repairable operation. Same-effect retries are idempotent, cross-effect
  drift conflicts, and a prepared transaction repairs a partial artifact set.
  The receipt binds the pre-append index digest and byte offset, so a retry can
  repair only its own truncated final JSONL row while unrelated corruption
  still fails closed.
  Python retains `should-run`/settlement fact projection plus one coarse
  transport call and the legacy kernel index lock; it no longer constructs or
  writes the spend event.
- Quota void commit: TypeScript finds the referenced spend under the mutation
  lock, reduces the before/after accounting decision, constructs the canonical
  correction, and commits its JSON, Markdown, index row, and prepared receipt
  through the closed spend/void accounting-artifact kernel. Same-effect retry
  replays or repairs one transaction; a fresh CLI invocation remains a fresh
  effect and therefore preserves the existing ability to append another
  correction for the same spend target. Malformed index rows now fail closed
  instead of being skipped. Void artifact names include an effect digest and
  JSONL rows use compact JSON; public payload semantics remain stable. The
  shared kernel also validates persisted receipt/path identity for spend
  recovery. Python retains `should-run` facts, UUID/clock ownership, one coarse
  transport call, and the legacy cross-writer index lock.
- Local task-lease lifecycle: native TypeScript transactions now own acquire,
  renew, transfer, release, terminal verification, holder verification, and
  fence close. They own boundary decode, handoff and owner/Todo eligibility,
  same-Todo and overlapping-write-scope conflicts, compare-and-swap,
  generation/idempotency rules, operation and fence receipts, the per-goal
  mutation lock, atomic lease persistence, and canonical results. Python
  projects compact registry, active-state, event-log, and rollout-log facts
  with before/after source digests, then makes one native transaction call.
  TypeScript revalidates those sources under the lease lock before decisions
  and immediately before writes. Closed fence replay is generation-bound: a
  non-required receipt is reusable only while no lease record exists, a
  committed release must still match the exact retired generation, and an
  aborted close can re-verify only the same active generation under a new lock.
  The provider-neutral coordination executor reaches the same pure TypeScript
  decisions for acquire, renew, transfer, and release through typed Python
  adapters. Shared provider execution, CAS, and authority receipts tracked by
  #3669 remain outside this cutover.
- Quota monitor-poll commit: TypeScript revalidates quiet, due, external, and
  exact-blocked-wait admission; constructs the canonical monitor target and
  event; journals a Todo-provider intent before mutation; and owns effect
  replay, index CAS, artifact-path fencing, and prepared/committed repair. A
  no-Todo poll and every completed replay use one reduction. A real Todo
  writeback remains an explicit idempotent Python provider between one
  preflight and one final reduction. Provider retry is bound to a persisted
  monitor effect identity, and stale older effects cannot overwrite a newer
  observation.
- Task-lease acquire: TypeScript owns identity normalization, settlement-plan
  projection, provider failure classification, ordered receipt construction,
  and the canonical result. Python invokes the existing atomic provider between
  one preflight and one final reduction; the provider retains the per-goal lock,
  owner eligibility, conflict, compare-and-swap, idempotency, and lease-file
  durability checks. Invalid identities stop before the provider, while a
  crash/retry after the provider re-enters its same-key idempotent path.

The quota-accounting cutovers remove the Python spend and void event builders
and their three-file writers. Their bounded facades exit when quota decision
and the top-level CLI execute in-process TypeScript, all run-index writers use
the native lock, and the legacy Python void API compatibility window closes.
Until then Python supplies compact projection facts, clock/effect identity,
result validation, and the shared legacy index lock. The Todo cutover removes
the Python state-evaluation dataclass, local identity
projection, replay helper, and public runtime handlers for those implementation
leaves. The remaining Python Todo facade owns fact projection, transport,
external command execution, source compare-and-swap, legacy response
projection, and the actual Markdown/event write. It exits when those writers
and the CLI move into the native TS transaction. The remaining fine-grained
Turn facade exits after quota and host-adapter callers move to their own coarse
transactions. The
task-lease semantic facade, atomic Python providers, settlement bridge
operation, and lifecycle rule engine are deleted. Python retains compact source
projection, one process transport, context-manager plumbing that carries the
opaque fence token/receipt id, legacy response projection, and compatibility
imports for existing Python callers. Those surfaces exit when the top-level
LoopX CLI, Todo writers, and authority-source adapters call the TypeScript
transactions in-process. The shared Python/TypeScript lock protocol remains for
the Python handoff-mode transition and other cross-runtime holders; it exits
when no Python writer acquires the per-goal lease lock. Vision checkpointing
remains a separate refresh/writeback transaction because it does not share the
delivery-selection lifecycle phase.

Lifecycle receipts recover a completed mutation or a held/closed fence after a
transport response is lost or the owning caller exits. Long-lived fence locks
record the Python caller PID rather than the managed Node server PID, and stale
reclaim uses token claims plus replacement-resistant file identity before
retiring a lock. This is not an exactly-once guarantee for a timed-out handler
that is still executing concurrently inside the same Node process; callers must
not start a second independent operation while that handler may still be live.

#### Quota void commit migration economics

| Field | Receipt |
| --- | --- |
| Canonical owner | Before: Python `slot_accounting.py` owned spend-target lookup, correction reduction, event/result construction, artifact allocation, and JSON/Markdown/index persistence. After: versioned TypeScript `quota.void.commit` owns those semantics plus effect fencing, index CAS, receipts, replay, and repair through the closed spend/void accounting kernel. |
| Legacy semantic code deleted | 212 Python product LOC covering the prior void lookup, transition, event/projection, path-allocation, and JSON/Markdown/index writer path. |
| Bridge code added | 263 Python diff LOC: the 243-line bounded `void_commit.py` transport/compatibility facade plus 20 import, re-export, normalization, and route-wiring lines in `loopx/quota.py` and the legacy `slot_accounting.py` surface. |
| Cross-runtime calls | The public execute and dry-run paths move from zero crossings to one coarse request/response. Exact-effect replay or repair also uses one request/response. Distinct CLI invocations remain distinct effects; the legacy two-step preview-plus-record compatibility surface uses one call per entry point. |
| Product-code net change | Product code is +2,210/−898 LOC, net +1,312. Tests/examples are +1,416/−3, net +1,413; build configuration is +3 and docs are excluded. The production shared kernel is already used by spend and void, replacing 671 lines in `spend_commit.ts` rather than creating a speculative framework. |
| Migration scaffolding | No migration-only worker, parity corpus, or temporary schema framework is added. Native boundary/invariant/replay/CAS/repair tests remain as shipped and persisted contracts; Python bridge tests exit with the compatibility facade. |
| Facade exit | Delete the Python void facade when quota decision and the top-level CLI run in-process TypeScript, all run-index writers use the native lock, and the legacy `build_*void*`/`record_*void*` Python API compatibility window closes. |
| Correctness and performance | Typed-decoder negatives, legacy target compatibility, effect isolation, index CAS, malformed receipts and paths, exact index-row identity, supported duplicate-index repair, concurrent mutation, truncated-tail repair, public CLI behavior, and clean wheel/sdist semantic probes pass. Across 16 cold starts, p50/p95 is 230.88/260.92 ms; 128 warm typed pings are 1.07/1.29 ms and warm void previews are 1.93/2.34 ms. Across 64 durable facade transactions, commit is 30.64/37.49 ms and exact-effect replay is 8.05/9.86 ms. Daemon RSS is 108.38 MiB idle and 109.80 MiB after 256 requests. In 64 interleaved full-CLI pairs, baseline/candidate p50/p95 is 736.51/828.68 versus 779.52/856.49 ms: p95 +27.81 ms (+3.36%). The absolute delta is the measured cost of one new managed-runtime fingerprint/request plus prepared-receipt durability; the percentage stays below the 5% material-regression gate, and Stage 3 removes that crossing. |

#### Task-lease acquire migration economics

| Field | Receipt |
| --- | --- |
| Canonical owner | Before: Python owned the atomic acquire provider and TypeScript reduced settlement around it. After: `task_lease_acquire.ts` owns the complete locked transaction and canonical result. |
| Legacy semantic code deleted | 973 product LOC: the Python provider/acquire composition and conflict path, the Python↔TS settlement bridge/reducer and handler, and legacy CLI settlement projection. |
| Bridge code added | About 641 gross product LOC are bounded compatibility code: compact Python authority projection plus one managed-runtime request, the compatibility import, the shared Python/TypeScript lock protocol, and the typed NoKV/coordination decision adapter. The local projection/import exit with the top-level Node CLI; the dual lock exits with the remaining lease writers and fences; the coordination adapter exits when that executor moves to the native runtime. |
| Cross-runtime calls | Public acquire and replay paths move from two request/response reductions to one native transaction request/response. |
| Product-code net change | Product code is +2,130/−1,122 LOC, net +1,008. Tests and fixtures are reported separately at +898/−1,081; build configuration is +4. |
| Migration scaffolding | The task-lease settlement characterization, fault-matrix, incident-replay, and fixture slices were deleted. Native invariant, crash/retry, direct-CLI, adapter, and cross-runtime lock tests replace them; no migration-only worker remains. |
| Facade exit | The semantic facade, atomic provider, settlement operation, and legacy CLI projection exit in this cutover. Only source/transport compatibility and cross-runtime serialization remain, with the deletion triggers above. |
| Correctness and performance | The public CLI matched the prior implementation in five acquire/replay/failure scenarios; 20 focused native tests, the 207-test Node suite, 4,615 Python tests (12 skipped), crash/retry and packaged-wheel smokes pass. In a matched 16-sample full-CLI run, happy-path p95 moved from 1,593.7 ms to 1,167.8 ms and replay p95 from 513.3 ms to 445.4 ms; medians were 364.6→425.6 ms and 343.3→351.9 ms respectively. |

#### Task-lease lifecycle migration economics

| Field | Receipt |
| --- | --- |
| Canonical owner | Before: Python owned renew, transfer, release, terminal/holder verification, and fence close around the native acquire transaction. After: `task_lease_lifecycle.ts` owns all six operations, their locked persistence, and their canonical receipts/results. |
| Legacy semantic code deleted | The Python lifecycle decision, CAS, lease-write, and in-process fence rule paths are removed; Python keeps only authority/source projection, managed-runtime transport, context-manager adaptation, and legacy public payload projection. |
| Cross-runtime calls | Each lifecycle verb uses one coarse native request/response. A held fence intentionally spans two calls, verify then close, because the caller's Todo mutation occurs between them while the same lock token remains authoritative. |
| Recovery contract | Operation receipts fence retry identity and expected generation. Fence receipts distinguish acquired, held, and closed states; replay revalidates current authority and the current or retired lease generation before returning an idempotent result. |
| Locking debt | PID liveness, token claims, stale reclaim, and replacement-resistant file identity make the shared lock safe across Python and Node. This bounded protocol is deleted after the handoff-mode transition and every remaining Python lease-lock holder move in-process. |
| Out of scope | This cutover shares the ordinary lifecycle decision but does not implement #3669's shared-provider execution, CAS, or authority receipts, and does not promise exactly-once execution for a second request issued while the original Node handler is still running after a client timeout. |

#### Todo terminal lifecycle migration economics

| Field | Receipt |
| --- | --- |
| Canonical owner | Before: Python owned terminal admission, successor derivation, and archive retention, while completion reduction and lease operations crossed narrower TS boundaries. After: `todo_lifecycle_decision.ts`, `todo_successor_derivation.ts`, `todo_terminal_lifecycle.ts`, and `todo_archive_selection.ts` are the typed owners of terminal admission, successor defaults/inheritance/bindings, lease release, completion reduction, CAS, receipt replay, and archive selection. The terminal transaction imports the successor and archive owners directly; legacy Markdown/event writers call their strict wire handlers and only materialize the returned proposal. |
| Legacy semantic code deleted | 284 Python product LOC are removed from semantic ownership: 74 lines for terminal decision plus archive eligibility/order/standing-receipt selection, and 210 lines of duplicated successor priority, capability/binding, exclusion, continuation, and predecessor-link derivation across Markdown complete/supersede and event completion. The remaining Python complete/supersede bodies are unpromoted compatibility writers, not a second terminal decision owner. Other deleted lines are adapter reshaping and moves and are not counted as payoff. |
| Bridge code added | 937 gross product LOC are classified as bounded transport/compatibility: the 538-line `provider_terminal_lifecycle.py`, 135-line successor intent/result adapter, 173-line local TS request decoder/router delta, 33-line legacy archive result adapter, 10 handler-registration lines, 6 projection-settlement lines, and 42 lines that route Turn durable readback to canonical authority after promotion. The 29-line `resolve_todo_state_path` extraction is a move, not payoff. Host-local validation declaration storage/execution is a retained external effect and is not mislabeled as bridge deletion. |
| Successor ownership | The public caller owns requested successor text and options. Python serializes that intent and adapts the typed proposal to the legacy writer. TypeScript alone derives inherited priority, default task class, capability binding, user binding, exclusions, same-agent continuity, and `unblocks_todo_id`; the promoted lifecycle derives and validates these facts inside the same provider transaction before atomically committing the target, successors, lease, and receipt. The legacy and event paths invoke the same pure TS decision through one effect-runtime call. |
| Cross-runtime calls | Measured at the public facade: promoted complete without validation, supersede, and archive use three request/responses (`todo_list`, terminal/archive transaction, projection readback). A validated complete uses four (`todo_list`, terminal preflight, terminal finalization, projection readback) plus one declared host-local validation effect. After an injected post-commit projection crash, the first attempt uses two calls and the receipt replay uses three. The promoted happy path did not exist before this cutover; a legacy terminal call uses the existing TS admission decision and adds one coarse successor-derivation call only when it has generated successor intent. |
| Product-code net change | Final merge-base classification is +4,051/−364 product LOC, net +3,687; tests/fixtures/examples are +3,594/−156, net +3,438; generated contracts are +3/−0 and docs are excluded. The increase delivers a complete provider-neutral transaction, one successor semantic owner, real File/PostgreSQL conformance, public facade parity, and durable mutation gates; it is not counted as deletion payoff. |
| Migration scaffolding | The production-scale fixture, three-arm rehearsal, provider conformance, public legacy/promoted parity matrix, and mutation cases remain because they express durable migration contracts. The compatibility facade and its call-count assertions exit with the facade; provider-neutral transaction and archive-order mutation coverage remain. |
| Facade exit | Retire the legacy successor-derivation/archive-selection crossings and their call-count tests when the last Markdown/event business writer migrates. Remove terminal facade portions as registry/lifecycle inputs and journal consumers converge; a native CLI permits full transport removal but is not required to delete duplicated decisions. Keep only caller-required input, private validation execution and projection-delivery adapters, even if they remain Python. Delete `resolve_todo_state_path` only after its concrete path consumers disappear; keep permanent Markdown rendering and qualified import/export. |
| Correctness evidence | Independent archive-order/standing-receipt semantics kill an oldest-selection mutant; optional `note`/`evidence`/`reason` cover `None`, empty, ordinary, Python-Unicode-whitespace-only, and whitespace compaction before/after promotion. Public-entry tests prove canonical commit followed by Turn-journal crash/retry settles once, logical retry tolerates prose changes but rejects different successor intent, rejected/concurrent/crash-recovered validated create publishes only the accepted digest sidecar, and legacy/promoted illegal actors retain a domain-rejection class. Independent successor tests pin priority, binding, exclusion, continuation, and predecessor-link inheritance. Stage 2C proves provider-first fence routing, live-lease import, orphan-history filtering, management-lock exclusion, replay, and zero-write previews. File and real PostgreSQL providers execute the same terminal conformance, while the independent legacy arm remains mandatory compatibility evidence. |

The monitor-poll cutover removes the Python admission-policy and monitor-target
modules and the Python event/replay/artifact writer. Its bounded facade exits
when quota `should-run`, Todo monitor persistence, status projection, and the
remaining run-index writers execute in the native TypeScript process; until
then it carries compact facts, the named Todo provider, legacy after-projection,
and the shared Python index lock only.

The final merge-base migration economics receipt for this cutover is:

| Field | Evidence |
| --- | --- |
| Canonical owner | Before: Python `monitor_poll.py`, `monitor_poll_policy.py`, and `monitor_target.py`. After: the versioned TypeScript `quota.monitor_poll.commit` transaction owns admission, target/event/result construction, replay/CAS, provider intent, and durable artifacts; Python retains compact fact projection, the named Todo provider, transport, and legacy after-projection only. |
| Legacy semantic code deleted | 826 Python product LOC: 601 replaced lines in `monitor_poll.py`, the 161-line policy module, and the 64-line target module. |
| Bridge code added | 495 Python diff LOC used only by the bounded bridge: 455 lines across `_NativeMonitorPollRejected`, `_mapping`, `_monitor_candidate`, `_due_monitor_candidates`, `_vision_wait_state`, `_registry_due_monitor`, `_decision_packet`, `_observation_packet`, `_index_digest`, `_native_result`, `_request`, `build_quota_monitor_poll_event`, `find_quota_monitor_poll_turn`, `_status_with_monitor_poll`, `_reload_status_after_monitor_writeback`, `_monitor_poll_failure`, `_capability_declaration_retry`, and `record_quota_monitor_poll_for_decision`, plus 40 import/schema wiring lines. The 34-line `_provider_writeback` is excluded because it adapts the retained real provider. |
| Cross-runtime calls | Before: zero because Python owned the whole path. After: one request/response for a no-Todo write, exact replay, or recovery; one preflight plus one final reduction when the real Todo provider runs. |
| Product-code net change | 2,743 added minus 831 deleted product LOC, net +1,912; tests/examples are separately 1,045 added minus 242 deleted, net +803, and docs are excluded. The temporary increase buys one complete transaction and cannot repeat: the next deletion is the 495-line bridge when quota decision, Todo persistence, status projection, and remaining index writers are native. |
| Migration scaffolding | Deleted the 218-line implementation-specific policy smoke and 18 target-helper assertions. No temporary parity harness is committed; typed boundary, public CLI, replay/CAS, malformed-input, provider, and repair tests remain because they express shipped or persisted contracts. |
| Facade exit | The Python facade remains only for compact source facts, the Todo provider, one shared cross-writer lock, transport, and legacy result projection. Delete it when `should-run`, Todo monitor persistence, status projection, and every run-index writer execute in the native TypeScript process. |
| Correctness and performance | Identity/admission, effect isolation, provider fencing, malformed receipts, concurrent CAS, crash repair, packaging, and launcher coverage pass. Managed-runtime cold start is 274.35/450.44 ms p50/p95, warm event 1.13/1.72 ms, durable commit 2.06/2.27 ms, and memory is 126.0 MiB idle/after burst. After replacing the prepared-plus-staged receipt sequence with one conservative prepared WAL that retains the index as commit proof, and skipping Git subprocesses only when a registry is provably outside a worktree, the final 64 interleaved full-CLI pairs report Todo write baseline/candidate p50/p95 of 663.34/971.40 versus 631.96/878.10 ms (candidate p95 -93.31 ms, -9.61%) and replay of 598.23/910.75 versus 580.13/900.69 ms (candidate p95 -10.06 ms, -1.10%). Both p95 deltas stay within the 5% and 25 ms full-CLI limits, resolving the earlier owner-review hold. |

### Stage 3 — CLI and App convergence

Ship a native TS CLI that imports the kernel in-process. Keep one automatically
selected authority path: direct in-process execution for CLI-only use, or the
managed daemon when the App/scheduler already owns the workspace. Remove the
Python bridge and its protocol after no production caller needs them.

The receipt-bound scheduler ACK/failure route is the first bounded native-CLI
slice in this stage. It is an exact launcher dispatch, not a generic Node
router, and leaves `quota should-run`, host automation mutation, and broader
quota policy in their existing owners.

### Stage 4 — Distribution cleanup

Package the kernel for npm and LoopX release artifacts, remove the Python
runtime requirement, and decide whether the optional daemon ships as a normal
Node entry point or a LoopX-built single executable. Do not silently depend on
an unofficial third-party Node wheel.

## 5. Payoff-phase PR contract

Every later migration PR includes a **migration economics receipt** in its
description and validation comment:

| Field | Required evidence |
| --- | --- |
| Canonical owner | Owner before and after the cutover; no ambiguous dual authority |
| Legacy semantic code deleted | Product LOC of replaced Python rules, fine-grained APIs, enums/dataclasses, and implementation-only adapters removed |
| Bridge code added | Product LOC added solely for Python↔TS transport or compatibility |
| Cross-runtime calls | Happy-path and recovery-path request/response counts before and after; target one request/response when effects are TS-owned or no provider is pending, otherwise at most one preflight plus one final reduction while a real Python provider remains |
| Product-code net change | Added minus deleted product LOC, reported separately from tests, fixtures, generated files, and docs |
| Migration scaffolding | Characterization/parity helpers added, retained, or deleted, with a concrete removal trigger |
| Facade exit | Facade deleted now, or the exact remaining caller/compatibility contract and deletion condition |
| Correctness and performance | Invariants, negative cases, matched end-to-end baseline, packaging, crash/retry, and host coverage relevant to the changed transaction |

LOC uses the final merge-base diff and classifies production code separately
from tests, fixtures, generated files, and docs. Moved code counts as deletion
plus addition; bridge LOC must name the functions whose only purpose is
cross-runtime transport or compatibility. Round trips are counted on one named
public happy path and its retry/recovery path, not inferred from handler count.

A PR that only relocates code, adds a handler, or increases bridge surface
without deleting authority does not pass this phase. A temporary net increase
may be accepted for one cohesive transaction only when the receipt shows why
the bridge is bounded and which next deletion realizes the gain. That exception
cannot be chained across open-ended leaf migrations.

Stable primitive decoders may be shared through the existing small runtime
decoder module. Domain decoders stay in their bounded contexts; this RFC does
not authorize a generic schema framework.

## 6. Correctness and performance gates

### Correctness

- Independently stated algebra properties: identity, associativity where
  applicable, ordering, short-circuit, replay, and effect-id isolation.
- Exact output parity for the pinned characterization corpus.
- Negative cases for malformed state, cross-effect overwrite, partial commit,
  cancellation, permission denial, and budget rejection.
- Boundary decoders reject missing fields, wrong types, unsupported schema
  versions, and oversized or malformed payloads before domain dispatch. The
  cutover inventory lists any remaining `as unknown as T` seam and proves that
  it is guarded; promotion requires removing unvalidated assertions from the
  migrated domain's authority inputs.
- Awaited writes emit receipts only after their declared durability point;
  concurrent same-key mutations are serialized or use a tested CAS contract,
  and retry identity distinguishes successive checkpoints within one Turn.
- Process crash and retry cannot duplicate a committed internal effect.
- Wheel and sdist are installed into fresh environments and execute deep
  semantic probes from packaged files.

#### Caller-observable semantic parity is a promotion gate

Every Python-to-TypeScript cutover inventories the behavior of every production
caller branch before implementation. The inventory covers accepted input and
default normalization; supplied, omitted, empty, and explicit-clear arguments;
eligibility and overlapping-rejection precedence; complete diagnostics and
remediation; dispatch-to-persistence readback; authority, ownership, receipt,
and no-effect outcomes; and replay or concurrent updates when the transaction
supports them. Equal reason codes or successful provider conformance do not
establish parity.

The cutover PR records machine-replayable execution receipts for an immutable
baseline revision and the exact reviewed head. Both runs use the same bounded
script, synthetic fixture fingerprint, public production entrypoint, and real
affected backend unless an intentional delta is declared and independently
approved. Each receipt names the revision, command, backend, exit status,
normalized observation fingerprint, and public-safe evidence pointer or inline
observation. Normalization may remove documented nondeterminism such as a
temporary path or timestamp, but never diagnostics, field presence, precedence,
persisted state, identity, ownership, or effects.

The same harness must demonstrate regression sensitivity: it fails an
independently stated invariant on the historical defect or a deliberate
semantic mutation, such as dropping a field or diagnostic detail or adding a
stronger precondition, and passes on the fixed head. A unit test that bypasses
the production entrypoint, or a suite in which every provider already shares
the candidate rule, is supporting coverage rather than baseline/head proof. If
the real backend or immutable baseline cannot be exercised safely, promotion is
held as `not_yet_proven`; prose cannot waive the gap.

This qualification is offline evidence, not a second authority. Production
does not dual-run Python and TypeScript, derive expected results from the
candidate, or retain the legacy rule after cutover. Intentional behavior changes
are separated from parity rows, justified against the public contract, and
approved explicitly. After promotion, only fixtures that express durable public
or persisted semantics remain.

Characterization output is evidence, not specification. If a pinned behavior
contradicts an independently reviewed invariant, the PR must disclose and
separately approve the behavior change. Once the old authority is removed,
promotion also requires deleting characterization machinery that serves only
that implementation comparison; durable regression fixtures may remain when
they express a public or persisted compatibility contract.

### Performance

Measure cold startup separately from steady-state execution. Every transaction
cutover reports:

- managed runtime cold-start p50/p95;
- warm typed request p50/p95;
- representative complete transaction p50/p95 and cross-runtime round trips;
- full CLI p50/p95 versus the pinned Python baseline;
- daemon memory after idle and under a bounded request burst.

The default acceptance target remains warm, non-durable internal transitions
below 2 ms p95 and no material full-CLI regression (greater than 5% or an
unexplained 25 ms additive p95). Durable transactions are compared with a
matched durability baseline rather than the 2 ms kernel budget. A miss, or a
tail regression hidden by a faster microbenchmark, is an owner review gate and
cannot be silently relaxed.

## 7. Install, upgrade, and rollback

The migration must not ask users to manage a service. The Python-transition
release requires Node.js 22.18.0 or newer, but installer and `loopx doctor`
must detect it before normal control-plane work and provide exact remediation.
The current source checkout raises that floor to Node.js 22.22.3 because this
release embeds the WAL-reset-fixed SQLite driver.
The wheel and sdist carry the TS source and versioned schemas.

The runtime is healthy while idle-exited: `stopped` means the next
control-plane request will start it automatically, not that the user must run a
daemon command. CLI and App surfaces consume the same lifecycle projection
(`running`, `stopped`, or `unavailable`) and stable diagnostic code. Raw stderr,
tokens, local paths, and private runtime metadata are not projected.

The runtime fingerprint includes every executed TS module and contract. An
upgrade starts a runtime for the new fingerprint; an old process can finish
in-flight work and exits on idle. Requests carry stable effect identities, so a
transport retry is safe only for handlers that are explicitly idempotent.

Rollback restores the previous artifact and fingerprint. Persisted state is
not rewritten into a TS-only format until a separately qualified state-schema
cutover.

## 8. Non-goals and stop conditions

- No permanent Python and TS semantic twins.
- No server per domain and no generic arbitrary-command executor.
- No big-bang CLI rewrite.
- No dual-write of production semantic state as a migration strategy.
- No performance claim from microbenchmarks alone.
- No more flat migration of leaf helpers merely because the bridge exists.
- No duplicate Python enum/dataclass retained without a named public import,
  persisted wire contract, or unmigrated caller.
- No permanent characterization harness for an implementation that no longer
  exists.

Stop or replan if the bridge becomes user-managed, a migrated rule still has a
Python semantic owner, the handler boundary becomes chatty, two consecutive PRs
increase bridge/scaffolding without retiring a facade, or a transaction cannot
meet its invariant/recovery/performance gates without weakening existing
behavior.

## Appendix A: Execution ledger

Measured delivery records live in the [per-entry ledger](ledger/typescript-control-plane-migration-v0/).
Each entry names its delivered boundary and remaining acceptance gaps; the T1–T4
checkpoints above remain the current migration plan.

### T2 Agent-addressed read checkpoint

Todo list selection now composes with the existing typed summary-lanes batch.
The Python role/status/id/Agent predicates and independent User scope rule are
removed; legacy and promoted consumers share `todos/agent_scope.ts` with quota
and decision scope. Explicit gate scope retains precedence over execution claim,
while retained User claims now correctly restrict scoped list visibility.
Full-source resume/succession stays evaluated before selection; original array
ordinals survive filters and display limits. No extra selection runtime crossing,
new capability/provider, or Python storage migration is introduced. Python keeps
input normalization and rendering until their actual host consumers migrate.
See [the read contract](../../reference/todo-work-counts.md); broader L5/D1 and
local-default qualifications remain open.

### T2 canonical read and display confirmation boundary

Canonical single-Todo and full-source reads now have one read-only TypeScript
module, separate from mutation orchestration and sharing the provider opening
boundary. Projection delivery composes a revision confirmation with the existing
full-source read; ordinary callers retain their response shape. Python owns
physical Markdown durability/retry, not the current-head comparison. Three-attempt
recovery and pinned-intent preservation use the existing journal-backed path;
no new RPC method, durable ACK or provider default. The stronger confirmation
costs one additional read on a stable delivery. Full L5/D1 qualification, D2 and
cutover remain open; see the [projection contract](../../reference/protocols/active-state-structured-projection-v0.md).

### Reviewed coordination cutover ownership

Saved-plan execution and fenced recovery now share the TypeScript promotion
owner. Fresh-source qualification wraps durable lineage qualification; recovery
uses that same lineage rule after exact fence verification. The Python CLI loads
a reviewed JSON carrier and transports fresh observations, without recreating
plan hashes, recovery decisions or receipt proof. Both commit paths share one
receipt/first-transaction readback contract.

This is a migration orchestration checkpoint, not completion of Stage 3 or a
default-provider flip. Integrate claim-preserving migration separately, retain
real-backend and captured-source qualification, and retire Python only where its
actual callers have moved. [Operator contract](../../reference/reviewed-coordination-promotion.md).
