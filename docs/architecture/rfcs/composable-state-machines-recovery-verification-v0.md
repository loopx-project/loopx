# RFC: Composable State Machines and Recovery Verification (v0)

- **RFC status:** Accepted
- **Supersedes / closes:** none
- **Delivery maturity:** Partial implementation; bounded Turn recovery coverage, full M2 remains open
- **Authors / owners:** Control-plane domain maintainers and testing maintainers
- **Created:** 2026-10-01
- **Last normative revision:** 2026-10-08
- **Implementation baseline:** `98acf52e7e41c193959bd45db622c65298273714`
- **Related contracts:** [Effect Interpreter](agent-loop-effect-interpreter-v0.md), [TS migration](typescript-control-plane-migration-v0.md), [shared authority](shared-goal-authority-state-provider-v0.md), [quality layers](../../development/testing-and-quality.md), [overall roadmap](loopx-overall-roadmap-v0.md)
- **Language mirror:** [中文版](composable-state-machines-recovery-verification-v0.zh-CN.md)

## Document map and maintenance contract

Sections 1–10 define the design and acceptance contract; section 11 defines
delivery and section 12 records open decisions. Acceptance of this design does
not qualify an implementation, change defaults or authorize promotion. Keep
both language versions synchronized; the Chinese document is a semantic mirror.
Add dated evidence to the existing
domain RFC ledgers when a slice ships; do not create a second task ledger.

## 1. Decision summary

Qualify one complete recovery journey across existing domain owners. Pure
TypeScript decisions, typed effects and durable receipts are the implementation
seams; a bounded executable model supplies independent expectations. The model
is test-only and never becomes a second production scheduler or state writer.

Start with one work item's ownership, execution, writeback and settlement.
Expand to successor scheduling and result delivery only with their real callers.
Reuse existing conformance fixtures, quality catalog and PR evidence. This RFC
adds no product capability, provider, wire schema, runtime switch or approval flow.

## 2. Problem and motivation

An individually valid Todo transition can leave an unsettled Turn. A committed
writeback followed by a lost response can invite a duplicate effect. A cleared
gate can leave its successor permanently quiet. Local tests of each owner do
not establish the causal relationship across these boundaries.

A valid Host result can contain an incorrect artifact. Its failure evidence must
reach the owning admission and recovery path before more work relies on it.
A resumed process, advanced journal phase or accepted handover alone does not
prove that the business error was corrected.

### Invariants

- One owner decides each domain transition; projections and receipts cannot
  manufacture new authority.
- Todo completion, Turn settlement, Goal completion and result delivery retain
  distinct meanings. An in-flight writeback may settle a Turn without completing
  its Todo; monitor closeout may require no quota debit.
- A retry preserves its logical operation and payload binding. A new intent
  does not inherit a historical receipt's authority.
- An expired or transferred executor cannot commit new protected work. Historical
  readback remains distinguishable from current permission to execute.
- Commit uncertainty remains explicit until the owning readback resolves it.
- A projection failure cannot undo a committed fact or authorize repeating its
  external effect.

## 3. Scope and non-goals

In scope: domain composition contracts, bounded sequence exploration, fault
injection, independent oracles and production-entrypoint refinement checks.
This is a quality contract for roadmap S2/S3/S10 and existing R1 recovery work.

Out of scope: a global state enum, universal workflow DSL, generic effect monad,
full-model reasoning verification, global event sourcing, a new authority store,
live Goal migration, automatic provider activation and unconditional liveness.

## 4. Current-system contract

`effect_program.ts` owns settlement identity, ordered reduction and receipts;
`turn_driver/settlement.ts` owns Turn settlement interpretation. Coordination
owns Todo/lease transactions; quota, scheduler and delivery keep their own
ledgers. The shared-authority RFC already distinguishes their composition from
a single coordination aggregate.

Existing `tests/control_plane_ts` conformance families and the quality guide
cover meaningful failures, replay and real backends. They are reusable evidence,
not proof that all interleavings or end-to-end liveness have been checked. The
semantic vocabulary registry checks bounded source properties; its declaration
of an invariant is not an executed temporal proof.

## 5. Proposed architecture

### Ownership and authority

Each selected domain names its current state/decision owner, accepted commands
or observations, preconditions, effects, commit point, receipt and next consumer.
Record these in the owning contract and existing validation matrix. Do not copy
the production transition implementation into the model as expected truth.

The implementation separates three responsibilities:

1. Decode external and historical data into legal domain values.
2. Decide from an immutable snapshot, command and explicit authority-supplied
   facts; return a typed decision or effect description without performing IO.
3. Execute in the existing authority boundary, revalidate relevant preconditions,
   commit durably and return receipts to the next decision.

Schema validity and a pure decision do not establish that a snapshot is current.
Locks, CAS, source witnesses and provider acceptance remain owned by their
existing execution boundaries.

### State model and schema

Use the [state taxonomy](../../product/core-control-plane/state-definitions.md):
persistent domain state, derived decisions, execution/settlement phase and read
projection. Model independent dimensions as products and constrained alternatives
as sums. Do not persist a derivable flag to simplify a test.

A test model abstracts only the identities, states and dependencies needed by
the chosen invariants. It states the finite actor/resource counts, trace bound,
faults and omitted behavior. Bind model actions to public commands or the
production transaction entrypoint, with independent readback after transitions.
No new production record or persisted field is required by this RFC.

### Command and recovery lifecycle

Exercise at least these orderings where the selected journey supports them:
reject before dispatch; interruption before commit; commit then lost response;
exact retry; conflicting retry; lease transfer/expiry; cancellation during an
in-flight effect; restart; and delayed projection delivery.

Cancellation stops future authorized dispatch as its domain contract specifies;
it does not erase a possibly committed external result. Recover from the same
operation identity. Unknown outcomes require reconciliation, not a fresh identity
or blind retry. Pure decision replay, receipt recovery and counterfactual
simulation follow the separate [Effect Interpreter](agent-loop-effect-interpreter-v0.md)
contracts.

### Detection, containment and recovery evidence

These are composition cases, not a new shared error enum or production incident
ledger. Optional assessment follows the
[bounded detection contract](optional-semantic-assistance-jev-v0.md#detection-result-and-handoff-to-the-owning-rule);
current use of affected evidence follows
[alignment §3.8](shared-goal-alignment-and-governed-amendment-v0.md#38-invalid-evidence-and-affected-consumers).

| Observation and owning evidence | Permitted containment | Required recovery evidence |
| --- | --- | --- |
| Identity, permission or lease check fails | Refuse new protected execution/commit; stop an owned process only under its qualified Host contract | Current authorized owner and valid preconditions; old output grants no new authority |
| Typed transient Host failure | Existing bounded retry policy, same logical intent and checked attempt budget | Current admission and resolved effect uncertainty; backoff alone proves no repair |
| Independent task/acceptance check fails for a named criterion | Block successful acceptance/settlement in that scope; retain the failed candidate and original identities | Actual repair or permitted replan, then a current check of the criterion and declared inputs |
| Validator unavailable, inconclusive or necessary input unreadable | Preserve lack of evidence and the existing fail-closed gate | Obtain usable evidence; inability to check does not prove content false |
| Observer/model suspects drift | Advisory or an explicitly enabled existing replan path | Domain investigation/current verification; confidence or a non-triggering signal cannot clear a confirmed failure |
| Explicit consumer basis becomes invalid/unavailable | Refuse new current use at its dependency/acceptance boundary; preserve history and unrelated work | Current permitted source and consumer eligibility; confirmed semantic failure also requires criterion revalidation |
| Provider outcome is unknown | Retain intent and hold later effects | Same-operation authoritative readback; no replacement identity |

Each recovery trace retains the challenged criterion/precondition, source and
validator basis, affected Goal/Todo/Turn/artifact/effect identities, committed or
unknown effects, permitted next action and the check that releases the
restriction. Reuse existing records and fields. This is not a universal wire
packet; a necessary extension belongs to its real producer and consumer.

Correct JSON and hashes around a wrong calculation still fail a criterion that
checks the calculation. File existence cannot qualify that stronger claim. Bind
the declared verifier to its version, scope and current source basis; silently
weakening it is not repair. Non-executable criteria name the authorized review
and deciding evidence, rather than treating another model's confidence as proof.
Check before consequential acceptance/use and after relevant basis changes,
with explicit cost and coverage limits; per-step hidden-reasoning verification
is not required. Late detection distinguishes prevented, committed and unknown
effects. Local recovery cannot silently compensate another provider's effect.

```text
observation + declared criterion/source basis
  → owning check: failure, insufficient evidence, or bounded concern
  → scoped admission/acceptance decision with original identities
  → authorized repair, replan, takeover or same-operation reconciliation
  → current verification + unresolved-effect readback
  → resume eligible work, or retain a visible scoped restriction
```

These arrows compose existing owners, not a global state machine. Taking over
unfinished repair work does not require its completion validator to pass first.
It requires current ownership, honest failed/unknown facts and existing execution
admission. Before claiming recovery, the receiver independently satisfies the
affected completion/use condition.

The last trusted recovery point is a **named verified basis**: identities,
declared artifact/source versions, applicable validation and effect receipts.
If only the phase journal survives, claim phase recovery only. Missing artifacts,
versions or verification scope require an explicit limit and authorized
reconstruction/revalidation. This is not automatic workspace rollback or
arbitrary external-state restoration. Replan acceptance, context delivery,
process progress and business recovery remain distinct.

### Safety and conditional progress

Safety assertions hold for every explored prefix. Progress assertions explicitly
name assumptions: an eventually available provider, a current authorized owner,
sufficient budget, eventual delivery and a fair opportunity to run, as applicable.
A user-held gate or permanently unavailable provider invalidates those progress
assumptions; it must remain observable rather than be declared a successful run.

Bounded exploration can demonstrate completion within its declared schedule or
find a counterexample. It does not prove unbounded eventual progress. A formal
liveness claim requires a model and proof with stated fairness assumptions plus
an explicit implementation-refinement argument.

### Provider contract

Run the same semantic scenarios against each affected, claimed backend, using
its real durability boundary. Provider-specific crash and ambiguity behavior
may need different injection mechanisms. Follow the real-backend gate; an
in-memory store does not qualify PostgreSQL, SQLite or File. External effect
guarantees remain with [provider acceptance](provider-effect-acceptance-v0.md).

## 6. Alternatives and design choices

Keep example-based regressions and add sequence exploration around an evidenced
gap. A universal model would multiply state space and ownership; separate local
tests alone miss cross-domain obligations. A small model with a production
adapter provides a reviewable middle boundary. Adopt a model-testing library or
formal checker only when it reduces the selected slice's validation cost.

## 7. Safety, privacy and compatibility

Use synthetic isolated Goals, disposable stores and controlled providers. No
active Goal mutation, real external notification or paid model call is needed.
Public counterexamples contain bounded synthetic facts, not user transcripts.
Preserve wire versions, omitted/null/clear distinctions, receipt identity and
feature-off behavior. Changes to these semantics require their existing owner
review; the test model cannot authorize them.

## 8. Migration and rollback

Introduce independent invariants before replacement, compare pinned base/head
through the same real entrypoint, then cut over one cohesive owner and delete
its redundant rule. Preserve only required compatibility readers. Roll back the
slice through its existing protocol; this RFC authorizes no data migration.

## 9. Validation and acceptance

| Claim | Evidence | Required result | Boundary |
| --- | --- | --- | --- |
| Legal internal states | Typecheck negative cases and boundary decoder tests | Invalid combinations rejected; supported wire input preserved | Types are not authorization |
| Deterministic decision | Same trusted facts and command; input isolation check | Equal decisions and unchanged observable inputs | No IO or clock read in the selected pure core |
| Composition safety | Independently specified model over bounded action/fault sequences | No stale commit, repeated debit or invented receipt | State the explored domain and omitted cases |
| Recovery progress | Named fair schedules with available dependencies | Same work resumes or reaches its contractually visible gate | No unbounded liveness claim |
| Implementation agreement | Pinned base/head production-entrypoint traces and independent durable readback | Intended semantics preserved; disclosed changes independently justified | Real affected backend required |
| Test sensitivity | A historical defect or deliberate semantic mutation | The relevant invariant fails before/faulted and passes after repair | Do not derive expectations from candidate output |
| User-visible continuity | Affected CLI and packaged App/Lark journeys | Truthful state, next action and original-context result | Omitted entrypoints explicitly unqualified |
| Business error containment | Valid identity/hash with content violating the declared criterion | Independent failure blocks new successful acceptance/use in that scope | Malformed-packet detection alone is insufficient |
| Recovery basis | Artifact, verifier, criterion or source changes between check and recovery | Old success cannot qualify a new basis; unknown effects still require readback | No arbitrary filesystem/external-effect rollback claim |

Record seed or deterministic enumeration, trace bound, normalized observations,
failure/skipped counts and a minimal counterexample in existing PR evidence.
Shrink failures without dropping the causal prerequisite that made them fail.
Persist only counterexamples protecting durable behavior. Avoid keeping a
one-off base/head comparison framework after its compatibility purpose expires.

## 10. Operational contract

No new service or operator action. Existing state/projection contracts expose
pending work, unknown effects and delivery failures. Qualification reports name
the exact implementation and backend; a passing model never changes a runtime
profile's promotion status.

## 11. Normative delivery plan

| Milestone | Delivery | Entry | Exit | Rollback |
| --- | --- | --- | --- | --- |
| M1 | One typed decision/settlement boundary with illegal-state rejection | Real caller and independent invariant | Types, decoder compatibility and public-path evidence; no claim of full composition | Revert bounded replacement |
| M2 | Work ownership → writeback → settlement recovery sequence | M1 plus named real backend and fault seams | Bounded traces, sensitivity, replay/transfer/crash readback | Retain durable regression; revert changed rule |
| M3 | Successor scheduling and original-context delivery | Real scheduler/delivery callers and M2 contract | Conditional progress and affected packaged UI/CLI/Lark evidence | Existing delivery rollback |

Use existing domain tasks and roadmap checkpoints for execution; this plan does
not require unrelated migrations before a bounded repair can ship.

The [quiet-to-due Monitor checkpoint](ledger/typescript-control-plane-migration-v0/2026-10-02-monitor-quiet-due-recovery.md)
records bounded CLI replay/successor evidence and its explicit M2/M3 limits.

### Turn settlement qualification boundary

The Turn owner now admits provider returns and readback in TypeScript before
Python may checkpoint, abort a rejected attempt, or retry an absent effect.
`settlement_provider.ts` owns these pure rules within the existing Turn bounded
context. Python retains callback invocation and journal IO. This replaces its
commit/readback classification and completion-result wrappers; it does not add a capability, provider or
universal effect executor.

The bounded exploration covers three ordered effects (writeback, quota spend,
terminal closeout), one interruption before/after effect commit or checkpoint,
one unresolved readback hold, explicit failed-turn retry, resolution and exact
replay. `tests/test_loopx_turn_settlement_recovery.py` drives the production Turn
entrypoint and real File journal with a durable **synthetic** provider ledger.
The independent oracle requires one commit per logical effect, ordered phases,
retained prepared intent during uncertainty, no repeated host execution and
conditional progress after readback clears. Four invalid-completion/identity
cases fail on the pinned pre-change implementation before checkpoint admission.

`tests/test_loopx_turn_driver.py` also exercises actual CLI writeback and quota
providers against disposable File state, including lost journal checkpoints and
independent run-index readback. The TypeScript tests enumerate legal/illegal
provider evidence for each step. Exceptions model process interruption, not
power-loss durability. These checks qualify the Turn/provider/journal slice;
lease transfer, stale-owner races, PostgreSQL authority, successor scheduling
and App/Lark delivery remain outside it. M2 therefore remains open. Its next
owning work is the existing ownership-to-settlement acceptance with a real
lease/GoalRef fence, followed by M3's actual delivery callers.

### Source-grounded implementation sequence

The table tracks independent composition boundaries within M2/M3 and roadmap R2/R3/R4. Candidate checkpoints do not establish installed behavior or full M2 acceptance. The source baseline is `233cc76fd22760947d73e1501032b8b77e28148b`.

| Bounded outcome | Existing entry and owner | Current checkpoint and decisive exit |
| --- | --- | --- |
| Unavailable declared ancestry prevents current use | `Delegations.read/start`, `delegation_results.py`, `delegation_result_use.ts` | `current_use` separates historical completion from present eligibility. New dispatch, adoption and settlement share ancestry checks; same-operation replay only reads back. Real File/SQLite three-level counterexamples and the packaged team reader cover withdrawal, cause/input references and restored readback. |
| Independent check failure reaches an actionable original-task recovery journey | `executor._task_validation_stage`, `ValidatedTurnReceipt`, canonical `turn_loop_controller_contract_v0.json`, `turn_journal.ts` | Preserve qualified failure scope and repair/replan detail; complete bounded repair or verifier-only retry, current validation and original-effect settlement. Host assertions cannot become trusted validation. |
| Shadow review explains criteria and coverage | progress-review receipt/context, `progress_review_evidence.ts`, canonical acceptance inspect | Select current canonical task criteria; exact GoalRef preserves instance identity, while changed tasks or recreated instances withdraw old judgments. Independent older cores retain only the legacy manual-study shape; canonical scope never downgrades. Show separate dimensions, declared-file net change, missing evidence and unreadable storage. Default off and existing assist trigger rules retain their semantics. |

For the second outcome, `_task_validation_stage` already saves the independent
result and blocks settlement. `ValidatedTurnReceipt` omits that validation's
`recovery_kind`; the canonical controller intentionally maps legacy
`validation_failed` to generic repair. Preserving a trusted validator's replan
request is a disclosed contract extension, not a violation of today's rule.
Change the canonical contract, `scripts/generate_turn_contract.py` and the
existing `validation_failed`/`repair` definitions in
`loopx/semantics/vocabulary_v0.json`, retaining legacy omitted detail as generic
repair. Do not hand-edit generated code or add
a parallel Python decision source. Review missing, malformed and contradictory
detail and old/new reader compatibility before selecting a wire change.

Failed-Turn retry at `validation_stage=task_postcondition` already reuses the
cached Host result and reruns validation without invoking the Host again.
**Doing repair work** needs its own current bounded execution admission, followed
by revalidation. Reuse criterion/verifier pins where the acceptance owner supplies
them. A generic command without retained verification basis cannot become a
durable business checkpoint because its exit code was zero. A replacement check
cannot erase an old unknown effect.

Start with independent counterexamples in existing fixtures, then production
entrypoints with real validator commands and a disposable supported backend.
Cover missing evidence, stale verifier/source, same-operation replay, failure
during repair, stop/takeover and lost effect response. The Todo/Turn view must
expose the failed check and scope, support authorized repair/recheck, and read
back success or continued failure in the packaged App; CLI and affected Lark
entries share the owner. A command-copy button or backend receipt alone does not
complete the journey. Bound repeated verification and source-chain traversal
using measured workloads. Roll back code through its owner while retaining
receipts, completed effects and unresolved recovery obligations.

One ancestry admission reads at most 64 operations and 16 levels, merging
repeated source reads by operation. The 15-second elapsed budget stops starting
further checks; an already running validator retains its configured timeout.
This is not a 15-second HTTP deadline. Exhaustion is unavailable, never saved
success. Coverage is explicit local delegation inputs, not undeclared memory or
arbitrary reasoning; multiple sources are not an atomic snapshot.

An optional `acceptance_scope` refers only to registry/runtime paths inside the
selected workspace. Criteria come from the current owner; private commands and
paths stay out of model questions. Hashes, versions and declared coverage are
observations, not proof of model correctness, a durable business checkpoint or
whole-task completion. Existing team evidence and capability settings carry App
readback, and CLI/MCP share the owner; no Lark-specific protocol is introduced.
Live correction, remote exactly-once, takeover, observer quality and intervention
retain their existing RFC acceptance owners.

## 12. Open decisions

The first M2 implementation must choose its smallest sufficient exploration
technique and bounds with the testing and domain maintainers. Prefer existing
fixtures and deterministic enumeration; justify a new dependency with replayable
counterexamples and measured cost. No universal tool or mandatory whole-tree
state-space budget is selected here.
