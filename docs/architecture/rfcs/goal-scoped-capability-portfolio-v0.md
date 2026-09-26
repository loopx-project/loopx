# RFC: Goal-scoped Capability Portfolio and Connector Lifecycle (v0)

- **RFC status:** Draft
- **Supersedes / closes:** none
- **Delivery maturity:** Proposal; existing catalog, hooks and external-evidence slices are partial prerequisites
- **Authors / owners:** LoopX capability and control-plane maintainers
- **Created:** 2026-09-21
- **Last normative revision:** 2026-09-26
- **Implementation baseline:** `65afc4872db67d36f74625a9e53ae63da2bc619c`
- **Related contracts:** [overall roadmap](loopx-overall-roadmap-v0.md),
  [research exploration](research-exploration-control-plane-v0.md),
  [agent loop effects](agent-loop-effect-interpreter-v0.md),
  [post-outcome memory utility](post-outcome-memory-utility-attribution-v0.md),
  [extension reference](../../reference/extensions.md), and
  [external-evidence lifecycle PR #4813](https://github.com/loopx-project/loopx/pull/4813)
- **Language mirror:** [中文版](goal-scoped-capability-portfolio-v0.zh-CN.md)

## Document map and maintenance contract

Sections 1–10 are the durable design and acceptance contract. Section 11 is
the normative delivery plan. Section 12 contains unresolved decisions.
Appendices are non-normative evidence and history. RFC maturity and delivery
maturity are independent. The English and Chinese documents are a semantic mirror
and must change together.

---

## 1. Decision summary

LoopX will add a **Goal-scoped Capability Portfolio** that lets an Agent reason
about, select, compose, evaluate, degrade and retire capabilities against the
Goal's outcome and acceptance gaps. It owns adoption, composition and lifecycle
transition decisions. It indexes immutable receipts from their existing owners;
it does not own or restate their effects. It does not copy capability
configuration, provider state, evidence, Todo state, authority grants or memory
into another source of truth.

The portfolio composes existing owners:

1. the capability catalog describes what can be considered;
2. existing Goal configuration and external-capability bindings select an exact
   operation, provider revision and profile digest;
3. `agent_context` projects a bounded plan at `before_plan`, freezes a selected
   route at `before_delegate`, and returns typed outcomes at
   `after_delegate_result`;
4. the external-evidence lifecycle qualifies repeatable source methods and
   connectors;
5. Decision Context, Explore and reward memory remain downstream consumers
   with their own admission rules.

**Goal enablement is sufficient to activate the capability's supported behavior.**
Once the existing configuration owner resolves a capability as enabled for a
Goal's current Agent/surface scope, its applicable hooks and normal execution
route participate automatically;
there is no second Portfolio switch, manual adoption step or per-Turn reminder.
Activation follows the capability's trigger, budget and authority contract. It
does not mean invoking every enabled capability on every Turn.

Use the least machinery that satisfies the work: no enabled capability adds no
Portfolio work; one capability or several independent capabilities use their
existing direct routes; a material dependency or selection tradeoff introduces
a bounded composition. Durable portfolio state is created only for an explicit
cross-Turn adoption/lifecycle decision that existing configuration cannot express.

Composition advice is fail-open; existing capability obligations are not.
An unavailable portfolio must not suppress enabled direct routes or bypass
Todo admission. Discovery cannot install software, enable a provider, enlarge
scope, change model authorization or approve a protected effect.

This RFC does not approve automatic capability installation, a connector
marketplace, domain-specific ranking in Core, or finance execution authority.

## 2. Problem and motivation

LoopX already has a catalog, extensions, readiness checks, Goal/Todo capability
requirements, three Agent-context hooks, external-evidence planning, Explore,
Decision Context and reward memory. A fresh Agent still has to infer how these
pieces fit together. A domain prompt or local strategy document often supplies
the missing organization, so adoption reasoning disappears across sessions and
another Agent may repeat the same discovery, select redundant sources or treat
provider readiness as evidence quality.

Connectors expose the same gap. The current connector registry is useful
inventory and usage telemetry. Registration, readiness and call counts do not
prove source coverage, freshness, rights, execution, parent admission or
decision value. A stable source should be promoted only after discovery and a
bounded trial, and should later degrade or retire when it becomes stale,
unreliable, costly or unused.

Concrete example: a research Goal needs current primary evidence, independent
counterevidence and a read-heavy worker. The Agent should discover an existing
external-research method, one qualified source connector and an eligible worker
route; explain why each was selected; freeze revisions and budget; record
partial coverage and failures; and tell whether the result changed the
decision. Today those facts live in separate projections and prose.

### Invariants

- Portfolio adoption never creates or enlarges authority.
- Configuration stays with its original owner; the portfolio stores only
  exact references, digests and bounded readback.
- `ready`, `executed`, `read`, `admitted`, `decision-changing` and
  `domain-eligible` remain distinct states.
- One source observed through multiple connectors or workers is not independent
  evidence.
- Unknown, stale, partial and unavailable are explicit; an empty result is not
  complete coverage.
- A model response, tool call, commit or connector invocation is not effect
  evidence by itself.
- Replays are idempotent and revision drift cannot silently reuse an old plan.
- CLI, managed Turn, frontend and Lark read the same public projection.
- Enablement, adoption policy and owner qualification are separate typed facts.
- No Portfolio record is required to use an already enabled capability.
- Feature-off and portfolio-failure paths preserve existing capability routes
  and their obligations, including explicit automation settings.

## 3. Scope and non-goals

### In scope

- a provider-neutral capability descriptor reference and Goal adoption record;
- direct activation first, with a bounded composition DAG only when needed;
- trial, adoption, degradation and retirement decisions, plus typed references
  to owner receipts;
- a connector qualification profile built on the external-evidence lifecycle;
- injection through `before_plan`, `before_delegate` and
  `after_delegate_result`;
- exact effective-configuration and provider-revision readback;
- shared CLI/frontend/Lark inspection and feedback;
- finance and one non-finance journey as qualification consumers.

### Non-goals

- replacing Goal, Todo, quota, claim, lease or shared-authority state;
- copying provider credentials, raw source bodies or private configuration;
- moving Decision Context, Explore or reward-memory state into the portfolio;
- inventing a universal score across unrelated capabilities;
- automatic installation, permission grant, payment, publishing, signing or
  trading;
- treating a portfolio recommendation as a runtime or domain authorization;
- requiring every Turn to scan every installed capability.

## 4. Current-system contract

At the implementation baseline:

- the capability catalog and extension manifests describe installed and enabled
  implementations, declared providers, hooks, permissions and readiness;
- `goal.external_capability_bindings` already owns durable Goal-scoped enablement
  for exact operations, provider revisions and profile digests; direct bound
  invocation admits read-only operations without creating a Turn or spending
  quota, while governed effects remain on their existing execution path;
- capability admission and capability memory expose bounded Goal/provider and
  host observations but do not grant authority;
- Todo capability gates answer whether a known task can execute; they do not
  discover a Goal's missing capability;
- `agent_context` supports `before_plan`, `before_delegate` and
  `after_delegate_result` with bounded guidance-only projections;
- the connector registry stores inventory and simple usage telemetry, not
  source qualification;
- merged PR #4813 ships `external-evidence` discovery, plan, receipt observation,
  parent admission and evidence retirement, with provider execution outside Core;
  it does not ship a durable connector qualification state machine;
- Decision Context owns decision evidence, Explore owns research topology, and
  reward memory owns qualified reusable outcome lessons.

The existing configuration editor and Goal capability settings already share
preview/apply/readback. Periodic-report post-writeback hooks resolve their Goal
subscription at composition; reward-memory hooks preserve owner-defined
surface/automation settings. These are reuse boundaries, not proof that every
capability is already wired into every host. General automatic participation,
Portfolio state and cross-surface readback remain proposed here.

## 5. Proposed architecture

### Ownership and authority

**Placement:** proposed capability id `goal-capability-portfolio`; provider id
`builtin` (a design label, not a new registration in this PR). Its independent
caller outcome is inspecting and retaining a Goal's capability choices across
Agents. The small read model belongs with built-in capability policy because it
must understand existing Goal configuration without installing a domain package.
Catalog, configuration, Decision Context and extension lifecycle remain sufficient
for their own contracts; none owns cross-capability selection history.

Selection, demand detection and lifecycle policy belong to that capability.
Typed normalization, identities and transitions stay in its TypeScript owning
boundary; Python only adapts transport. The generic Kernel reuses registration,
bounded dispatch, schema validation, failure isolation and existing effect/Todo
admission. It must not learn capability names, connector stages, domain ranking
or portfolio adoption states. Registration occurs at the composition root;
shared quota, scheduler and Todo reducers do not import portfolio policy.
Provider execution and independently versioned domain integrations remain in
their existing capabilities/extensions/packages. No new worker, scheduler,
workflow DSL, binding store or generic effect ledger is required.

The portfolio owns only:

- why a Goal considered, trialed, adopted, degraded or retired a capability;
- the selected composition and its exact revision;
- portfolio lifecycle-transition receipts and review triggers.

Existing Goal configuration and external-capability binding remain the sole
runtime enablement owners. Their preview/apply/readback activates supported
behavior without an adoption record; the Portfolio cannot veto that direct
route. Conversely, an adoption record does not enable or invoke a capability:
`adopted` with a missing/stale required binding is visible but not runnable.
Degrade/retire changes Portfolio selection policy only; disabling a capability
still uses its existing configuration owner.

It references, without copying:

- catalog and extension declarations;
- effective configuration and provider readiness;
- Todo requirements and authorization decisions;
- external-evidence call/admission receipts;
- Decision Context, Explore and memory artifact identifiers.

No chat, UI, connector, worker or domain capability may become an alternate
portfolio writer. Mutations pass through one typed reducer and the configured
Goal authority provider.

### Activation and proportional execution

The configuration owner resolves inheritance, explicit disable, Agent/surface
scope and supported operation/profile settings once. An Agent-scoped activation
(such as reward memory) never enables other Agents in the same Goal. Portfolio consumes that exact effective result;
it must not infer enablement from catalog presence or reinterpret each owner's
legacy defaults. Existing explicit manual-only or disabled automation settings
remain effective. New supported automatic surfaces need no Portfolio opt-in;
any change to an existing capability's defaults must be disclosed and qualified
by that owner before release.

| Effective state and current work | Automatic behavior | Additional Portfolio work |
| --- | --- | --- |
| No enabled capability | Existing Agent path | No provider/model calls, hook contribution or durable Portfolio write |
| Enabled but trigger not applicable | Keep capability ready; do not invoke | Empty hot-path contribution; optional inspect reason |
| Enabled and applicable; direct or independent work | Run the existing supported hook/route within its own admission | No trial, DAG, adoption receipt or extra model call required |
| Enabled with a material dependency or tradeoff | Produce the smallest bounded plan; execute through existing owners | Only the selected dependency closure and necessary decision refs |
| Missing binding/readiness/authority/budget | Preserve the owner's unavailable, blocked or deferred result | No implicit repair, provider replacement or grant |

The host must actually dispatch registered applicable hooks, not merely print
that an enabled capability exists. Commands that have no automatic hook remain
available through the normal Agent/tool route; unsupported host integration is
reported as unsupported, not as successful activation. Automatic protected-effect
execution still requires its existing exact admission. Optional ranking advice
cannot turn a machine-enforced capability obligation into a suggestion.

Composition is justified by a typed input/output dependency, shared constrained
resource, alternative-provider choice or a named acceptance gap requiring joint
results. Capability count and keyword matches alone are insufficient. Use known
effective configuration first; broaden discovery only for an unresolved gap.
Two independent capabilities stay direct. One operation choosing between costly
providers may warrant a plan. Existing native hooks continue while planning fails.

### State model and schema

These are proposed contract sketches, not five mandatory new stores. Direct
activation reads existing owners and creates none of these durable records.
Materialize adoption/lifecycle state only when an explicit cross-Turn policy
cannot be derived from configuration or existing owner receipts; a plan is
needed only for the composed route. A new field must have a real consumer.

#### `capability_catalog_entry_v1`

This is a normalized reference to an existing capability declaration:

```text
capability_id, capability_revision, owner_ref, declaration_ref, declaration_digest
effective_config_ref?, readiness_ref?
```

The portfolio does not edit or persist a second catalog. Outcome, phase, schema,
authority, privacy and cost declarations are resolved from their original owners
only when selection needs them.

#### `goal_capability_adoption_v1`

```text
goal_id, adoption_id, portfolio_revision
gap_ref, capability_id, capability_revision
status = candidate | trial | adopted | degraded | retired
reason, alternatives[], expected_effects[]
effective_config_ref, effective_config_revision, config_digest
binding_ref?, binding_digest?
trial_budget?, trial_window?, authority_refs[]
owner_observation_refs[], lifecycle_receipt_refs[]
review_after, degradation_conditions[], retirement_conditions[]
created_at, updated_at
```

`gap_ref` points to an outcome or acceptance gap; it does not create a second
Todo. A transition needs an expected current revision. Omitting a field preserves
it; explicit clear semantics are defined per optional field. `binding_ref`
resolves the existing Goal binding when execution requires one; it is not a
portfolio-owned copy of provider configuration. Binding readiness/status is a
read-time owner join. An enabled direct capability may have no adoption record;
inspection reports `adoption_status=null`, not a fabricated `adopted` transition.
The read model separates effective enablement, execution mode
(`disabled | direct | composed`) and optional adoption status, with provenance.

#### `capability_composition_plan_v1`

```text
goal_id, todo_id?, turn_id?, composition_id, portfolio_revision?
gap_refs[], nodes[], edges[], selected_at, expires_at
node: capability/provider/connector/worker/reducer reference,
      phase, input/output schema, exact revision, budget,
      required authority, required read/write scope,
      disposition, reason
```

`composition_id` is a canonical digest of all normalized decision-relevant
fields. The graph must be acyclic. Each considered candidate receives `selected`,
`skipped`, `unavailable` or `incompatible` with a reason. A plan is guidance,
not execution authority.

#### `capability_owner_receipt_observation_v1`

```text
observation_id, composition_id?, node_id?, phase
goal/todo/turn identity
owner_kind, owner_revision
owner_receipt_ref, owner_receipt_digest
observed_at, owner_receipt_status
lineage_digest, review_trigger, next_lifecycle_proposal
```

This is a read-only index over an immutable receipt from the capability,
provider, delegation, Decision Context, external-evidence or outcome owner. It
must not copy provider/model/connector revisions, coverage, source families,
cost, failure details, parent disposition, decision effects or utility. Those
facts remain authoritative only in the referenced owner record.

An owner correction, revocation or retirement creates or selects a new owner
receipt according to that owner's protocol. The portfolio observes the new
reference and marks the old observation superseded at read time; it never
rewrites the owner fact. If owner readback conflicts with the index, the owner
wins and a portfolio lifecycle transition cannot consume the stale observation.

Each successful portfolio mutation returns a
`capability_lifecycle_transition_receipt_v1` containing only the adoption id,
operation id, expected and committed portfolio revisions, previous and next
adoption status, reason, optional composition id, owner-observation references and next
review trigger. `adopted` means the Goal's capability-adoption policy selected
the capability. It never means evidence was admitted, an outcome succeeded or
new authority was granted.

Two examples preserve the boundary:

- **External evidence.** The external-evidence owner alone records provider
  execution observation, parent admission, coverage and retirement. If that
  owner corrects or retires a receipt, the portfolio follows the superseding
  owner reference and may propose `degraded`; it does not retain a competing
  coverage or admission fact.
- **Non-evidence capability.** A delegation owner records worker route and
  result receipts, while the relevant evaluation owner records outcome quality.
  The portfolio references those receipts when reviewing adoption; it does not
  translate them into generic `admitted`, `refuted` or `decision_effects` facts.

### Command and event lifecycle

```text
Existing Goal enablement read back → capability-owned applicability check
  ├─ independent work → native hook/direct route
  └─ material dependency/tradeoff → bounded plan → existing execution owners
Both routes → owner result/readback
  → only if cross-Turn policy is needed: adoption/lifecycle transition
```

Discovery/trial is for a missing method or uncertain selection, not a mandatory
entry gate for already enabled work. Composed delegation freezes its route at
`before_delegate` and observes owner receipts at `after_delegate_result`;
non-delegated work keeps its native execution and result boundary.

The mutation identity is `(goal_id, adoption_id, expected_revision,
operation_id)`. Replay with the same intent returns the original receipt;
identity drift fails closed. Provider/config revision drift invalidates the
plan. Lost responses reconcile through receipt readback before retry.

If a portfolio cannot be read, planning continues without portfolio guidance
and reports Portfolio availability as unknown; it does not invent an evidence
coverage fact. If a Todo explicitly requires the missing capability, normal capability admission blocks that Todo; the portfolio does
not weaken it.

### Connector qualification profile

Connector qualification belongs to `external-evidence-research`, consuming its
existing plan/receipt/admission/retirement references. The following is a proposed
connector-owner profile, not the evidence-retirement state machine shipped by
#4813 and not Portfolio adoption vocabulary:

```text
external-research discovery
  → connector candidate
  → bounded trial
  → parent qualification
  → active
  → degraded | retired
```

A connector descriptor adds source family, supported operations, coverage
domain, publication/observation-time semantics, rights, cost, failure and
fallback declarations. A call receipt binds the exact plan/provider revision,
source references, coverage interval, freshness, rights snapshot, cost,
latency, failure and output digest. Registration and readiness remain
inventory facts. Parent qualification remains distinct from finance evidence
eligibility or another domain's admission.

Portfolio `adopted` describes selection policy only. Connector `active`, if
supported by that qualification owner, is a separately labeled owner-joined fact.
For example, `adoption_status=adopted` and `connector_qualification.status=active`
can coexist with different owner refs; neither maps to the other. An adopted
entry can reference a degraded connector, and an active connector need not have
any Portfolio adoption. Until the connector owner supplies that typed fact,
qualification is unknown; registry readiness or evidence admission cannot mint it.
Installation,
enablement, doctor status, provider revision and rollback continue to come from
the extension runtime and existing Goal binding. Disable, uninstall, doctor
failure or binding revision drift makes the composition stale; the portfolio
must not silently resolve a replacement provider.

### Runtime injection

- **`before_plan`:** read enabled applicable capabilities; emit no Portfolio
  contribution for independent direct work. For composed work, project only the
  current gap, selected dependencies and stale/unavailable nodes.
- **`before_delegate`:** freeze worker/connector/provider revisions, budget,
  schemas, authority references and composition digest. Domain capabilities
  describe the question and acceptance criteria; the generic delegation owner
  controls capacity, route and result receipts.
- **`after_delegate_result`:** index typed owner-receipt references and propose
  a portfolio lifecycle review. Cost, coverage, failure, admission, decision
  effect and utility remain in their owning receipts. Raw worker prose is not
  an adoption or lifecycle receipt.

These phases apply when their corresponding lifecycle event exists. They do
not force delegation or a governed Turn for a bound read-only call. Existing
turn-start/post-writeback hooks and pending-intent execution retain their owners;
Portfolio neither duplicates dispatch nor journals an effect twice.

An optional Turn-start summary contains only a relevant composition reference,
stale/unavailable dependencies and next review trigger. Direct work adds no
Portfolio prompt section. Full catalog/history and owner joins stay behind
on-demand inspect. Cache bounded declarations by their existing revision;
configuration/provider/receipt drift invalidates only affected entries.

### Long-running responsibility, disclosure and memory evolution

**Decision:** unify when context is disclosed and how an update is verified,
not all storage behind a universal memory database. A durable Agent is a
Goal-scoped identity with responsibilities and recoverable work, not an
infinitely growing host transcript. Managed Codex workers and short-lived
native subagents remain different execution modes; neither gains authority
from a memory entry or functional role.

The following integration is planned; existing owners are not a claim that the
whole journey already works:

| Layer | Existing owner and disclosure boundary | Update and invalidation |
| --- | --- | --- |
| Responsibility and constraints | Goal vision/direction, registered Agent profile and actual grants; small references at planning/resume | Owner-authorized configuration retains its revision; profile is advisory and does not grant permissions |
| Working continuity | Todo, checkpoint, collaboration request/result, explicit continuation; load selected work and unresolved corrections at resume | Re-read task/acceptance/source state; consume a handoff explicitly, never infer acceptance from delivery |
| Facts and episodic evidence | Material/source registry, Explore, Decision Context and Turn Recall; retrieve for the concrete question and evidence gap | Preserve source, known-at time, subject, scope, revision and correction/supersession references; uncertain time remains unknown |
| Reusable lessons and methods | Reward Memory candidate/review/recall/application; skill or capability owner for procedures | Review a scoped lesson, verify write and destination recall; only validated procedure changes become a versioned PR/configuration proposal |

Progressive disclosure is a read policy, not the storage format. Planning gets
bounded responsibility/working references; task-specific retrieval gets only
relevant evidence/lessons; full source is fetched on demand. Do not inject the
entire capability catalog, transcript or all financial/research memories at
every `before_plan`. A negative search result is scoped to the query and source,
not proof that an asset or capability does not exist. Missing/expired/unreadable
context is explicit; ordinary independent work can continue unless its own
required evidence or authority is missing.

Memory updates follow the existing owner's path:

1. A material correction or outcome references the exact source and affected
   work/capability revision; conversational intent is not a completed write.
2. Separate a current fact correction from a reusable lesson. Correct the
   original state through its owner; submit only decision-relevant learning to
   the existing candidate/review seam. Do not overwrite history with a summary.
3. Preserve conflict, scope, provenance, replacement/expiry and deletion policy.
   A provider's asynchronous acceptance is not completion: verify the write,
   read back the exact item, then test destination recall at a fresh boundary.
4. Apply a recalled lesson explicitly to a decision. Record use separately from
   task outcome and utility. Usage count or a model's self-rating is not effect.
5. Repeated, transferable evidence may propose a skill/configuration/capability
   change. Validate against held-out failures and a same-workload baseline;
   route through existing review, version and rollback owners. No self-issued
   authority, silent installation, or universal rule from one successful task.

Capability **composition** uses these scoped inputs to fill an acceptance gap:
prefer a direct call; add only necessary dependencies. Capability **evolution**
uses reviewed outcome evidence to change a version or adoption policy. Facts,
temporary work state, lessons and executable procedures must not be collapsed
into one “memory updated” claim. The proposed Portfolio references owner
receipts; it does not reproduce the learning database or outcome state machine.

OpenViking is an optional context provider behind existing Reward Memory or
Decision Context bindings. Do not make it the Goal/Todo store or an install-time
dependency. Local source-backed recall and existing continuation must remain
usable without it. Hermes and LingTai are design references, not additional
required runtimes. New adapters are justified by a measured retrieval/update
gap, not by the number of integrations offered.

### Product path and first delivery boundary

Start with the user's Goal and the registered Agent's job, not a choice among
memory frameworks. Reuse the existing Goal settings/capability editor, request
timeline, artifact/detail drawer and Lark return path. Normal conversation shows
what changed, the result, and any necessary decision. On-demand details show
which source/version was used, why it applies, what changed and how to correct
or retire it. Silence unchanged background state. A memory service outage must
not be represented as forgetting the Goal or losing execution authority.

The first implementation is the read-only M0 **inspection slice**:
`capability inspect --goal-id ... [--agent-id ... --phase ...]`. It reuses the
Dashboard configuration projection and the existing TS coordinator-context
owner. No new state, lifecycle reducer, enablement switch, provider or automatic
hook is introduced. Reads report coverage and independent-read consistency;
configuration, projected guidance, native readiness, execution, adoption and
utility remain distinct. It does **not** ship all M0 automatic participation or
M1–M5. No additional frontend control is needed for this slice: the existing
settings editor already owns these configurations and their readback. Live
memory/usage/correction controls and Lark evidence returns remain delivery work,
not acceptance supplied by CLI tests.

Next, qualify one **correction → fresh-session decision** journey before adding
a composition planner: persist an authorized correction, show the affected
source and superseded assertion, resume a different session, retrieve the
correct version, apply it and return evidence to the original request. Test both
an engineering and a research question; no host-session-file copying. The
current continuation and Decision Context contracts own the work; missing
provenance/update fields belong to their owners, not a new continuity store.

The optional [query-ready Reward Memory caller](../../reference/reward-memory-decision-consumption.md)
provides a bounded prerequisite: TS admission/completion, existing Python
provider/applier adaptation, context delivery distinct from semantic assessment,
and exact caller-retained replay. It does not complete the fresh-session journey,
automatically compose capabilities, or establish memory utility; those remain
subject to real caller and held-out outcome qualification.

可选的 query-ready Reward Memory 调用方提供有界前置切片：TS 准入/完成、原
Python provider/applier 适配、上下文交付与语义判断分开、调用方保留的精确复用。
它不宣称已完成跨会话纠正、自动组合或记忆效果；真实调用和留出结果仍须验收。

## 6. Alternatives and design choices

### Domain skills organize capabilities

This is useful for early experiments but loses adoption history across Agents,
duplicates runtime discovery and makes each domain implement failure and
authority rules. Domain capabilities will keep domain semantics and acceptance
criteria, while the portfolio owns generic organization.

### Connector registry becomes the quality authority

Rejected. A registry is inventory and telemetry. Source quality and parent
admission require exact call evidence, time, coverage, rights and domain rules.

### Decision Context owns capability planning

Rejected. Decision Context assembles decision evidence; it must not become a
configuration, provider or authorization owner.

### Mandatory portfolio adoption and a universal planner

Rejected. Existing enablement already answers whether a capability participates.
Requiring another switch, trial, adoption record or graph for independent work
creates a second gate and unnecessary tokens, writes and failure dependencies.
The direct path remains primary; composition is a demand-driven capability
policy, not a Kernel prerequisite for every Goal or Turn.

### Fully automatic self-installation

Rejected for v0. It collapses recommendation, configuration and authority. The
portfolio may propose installation or enablement through existing governed
owners, but cannot perform it implicitly.

## 7. Safety, privacy, and compatibility

- Automatic activation is an integration obligation; composition ranking is
  advice. Existing admission, required validation and authority remain enforced.
  Portfolio stores no secrets or raw private payloads.
- Public projections redact private source, account and paid-data details. They
  may join authorized, bounded coverage or failure fields from owner
  projections at read time, but the portfolio does not persist another copy.
- A readiness observation cannot become a durable grant. Existing authority
  scope and protected-effect confirmation remain authoritative.
- Mixed-version readers preserve unknown fields and reject unsupported semantic
  narrowing. Revision mismatch is visible and blocks reuse.
- A connector with expired rights, stale revision or ambiguous execution moves
  to unknown/degraded; it is never silently active.
- Source-family deduplication prevents multiple wrappers or workers from being
  counted as independent evidence.
- A feature-off installation retains the existing planning, delegation and
  evidence paths.

## 8. Migration and rollback

M0 first qualifies automatic direct participation and read-only inspection over
existing owners. Unconfigured/disabled capabilities retain their defaults;
enabled capabilities need no new Portfolio opt-in or bulk adoption migration.
Adding this integration must preserve each owner's explicit automation settings.
A connector becomes qualified only through its own exact-revision evidence.

Rollout proceeds Goal by Goal using existing capability configuration controls.
Before any durable Portfolio mutation, preflight verifies its typed owner,
authority provider and references. M0 ships without a new storage dependency;
M2 adds state only for irreducible policy. Rollback removes Portfolio planning
and writes while retaining receipts for read-only audit and preserving enabled
native routes. To stop a capability itself, disable it through its original
configuration/binding owner. No destructive registry migration is part of v0.

## 9. Validation and acceptance

| Claim | Test or evidence | Required result | Boundary / exclusions |
| --- | --- | --- | --- |
| Enabled means usable automatically | enable through existing Goal editor/CLI, then a fresh supported Agent session without Portfolio command or adoption | applicable native hook/route runs and returns owner readback; no second opt-in | required authority and explicit manual-only settings remain enforced |
| Small work stays small | zero, one, two independent capabilities, then one real dependency | direct cases add zero Portfolio model/provider calls, DAGs or durable writes; dependency produces only needed plan | ordinary owner execution cost remains visible |
| Off/failure parity | compare same base/head workload on CLI, managed Turn, context and post-writeback paths; inject Portfolio failure | unchanged native decisions/effects; no duplicated dispatch or new gate | an actual required-capability failure still blocks through its owner |
| State vocabularies keep their owners | adoption `adopted` + connector `active`, then connector degradation and no-adoption cases | separately labeled refs/statuses; no automatic mapping or fabricated adoption | connector qualification profile is not yet shipped |
| Core stays generic | caller/import audit plus unrelated capability execution | policy is capability-owned; no Portfolio branches in Todo/quota/scheduler rules | existing generic admission still applies |
| Portfolio does not grant authority | mutation and adversarial fixtures | requested scope expansion rejected; no grant written | does not qualify each external provider |
| Plan binds exact semantics | mutate gap, config, provider, route, budget and graph fields | digest mismatch fails closed | does not prove live execution |
| Replay is idempotent | lost-response and concurrent retry fixtures | one transition and one receipt | provider side effects remain provider-owned |
| Failure preserves useful work | portfolio/provider unavailable fixture | native route continues with Portfolio availability unknown; hard requirement blocks only that Todo | no availability SLO |
| Connector lifecycle is auditable | discovery→trial→qualification→degrade→retire fixture | every transition has exact revision and typed reason | domain eligibility tested separately |
| Provider binding keeps one owner | existing Goal binding preview/apply/readback plus disable/upgrade/rollback fixtures | portfolio references the exact binding and becomes stale on drift; it writes no parallel binding | extension runtime still proves provider readiness |
| Self-discovery is useful | fresh finance and non-finance Agents receive the same Goal only | both select a minimal defensible composition or explain empty selection | two cases do not prove universal uplift |
| Composition improves outcomes | frozen baseline versus portfolio-assisted trials | better first useful action, coverage or decision quality within declared cost; failures retained | no automatic production promotion |
| Three hooks agree | before-plan/delegate/result contract tests | same composition identity and exact route/result lineage | raw model quality excluded |
| Owner truth is not duplicated | correct and retire one external-evidence receipt and one non-evidence outcome receipt | portfolio follows immutable superseding refs; no copied admission/effect fact survives | each owner still validates its own semantics |
| Product surfaces agree | existing Goal settings preview/apply/readback in CLI, packaged frontend and Lark; new-session, stale, reconnect and repeated-action cases | same effective enablement, direct/composed mode, optional adoption status and owner refs; no second activation control | release each enabled entry point only with its usable readback; owner facts retain provenance |
| Domain boundaries hold | finance and another domain fixtures | Core remains domain-neutral; domain admission remains independent | no trading authorization |

Measure time to first useful action, evidence coverage, stale/duplicate-source
errors, manual intervention, token/monetary cost, decision changes and accepted
outcomes. Number of installed capabilities, calls or generated words is not a
success metric.

## 10. Operational contract

Operators can inspect effective enablement, direct/composed mode, optional
portfolio revision, adopted/trial/degraded
adoptions, exact config/provider revisions, owner-receipt references and next
review triggers. Authorized views may join current failures, cost and coverage
from their owners without persisting them in the portfolio. Alerts are
event-driven for revision drift, rights expiry, repeated failure, budget
exhaustion or a required capability becoming unavailable; routine successful
calls do not create noise.

Reuse the existing capability configuration editor and Goal settings entry.
Show effective behavior and actionable failures first; composition details and
owner receipts are on demand. Do not add an empty Portfolio panel, an adoption
wizard or a second enable button. Lark uses the same configuration/read model.

Capacity is bounded per Goal and relevant event. Candidate enumeration is
paginated on demand and prompt projection is size-limited. Measure same-workload
base/head latency, tokens, reads, writes and calls for off/direct/composed paths.
Off/direct add no Portfolio model or provider calls, durable writes or prompt
section. Any local projection overhead must fit an explicitly measured budget;
M0 records that budget using the repository budget-decision guide. No universal
SLO or performance win is claimed by this proposal. Failure classes distinguish
unavailable, incompatible, unauthorized, stale, rights-expired, budget-exhausted,
provider-failed and result-unqualified. Backup and recovery follow the selected
Goal authority provider; raw provider artifacts follow their original owners.

## 11. Normative delivery plan

| Milestone | Shipped behavior | Entry gate | Exit evidence | Rollback |
| --- | --- | --- | --- | --- |
| M0 · Automatic direct path and inspect | Resolve existing enablement, dispatch supported native hooks and expose shared readback; no new Portfolio store | existing config, hooks, admission and UI owners identified | zero/one/independent-capability cases; no second opt-in; off/failure parity and measured overhead; affected CLI/frontend/Lark journeys | remove integration; existing capability routes/config remain |
| M1 · External evidence and connector trial | Reuse merged #4813 evidence lifecycle; add only missing connector-owner qualification | real connector caller and exact-plan/provider boundary | one real host method and one connector trial with partial/failure receipts; no fabricated connector status | retain inventory/evidence; disable qualification writes |
| M2 · Optional durable adoption | candidate/trial/adopted/degraded/retired reducer and receipts only for irreducible cross-Turn policy | concrete caller needs policy beyond existing config/owner receipts; Goal authority provider selected | replay, concurrency, drift, recovery and no-adoption direct path | disable writer; retain read-only receipts |
| M3 · Demand-driven composition | Small dependency closure through existing planning/delegation/result hooks | M0; M2 only when durable policy needed; M1 only for connector qualification | direct→composed→direct, no-hint finance/non-finance trials, owner correction and same-workload overhead | remove planning; native hooks continue |
| M4 · Effect qualification | external-only, connector-only and hybrid trials; owner-backed review and retirement proposals | frozen metrics, budget and stop rules | retained denominators show benefit or explicit no-uplift without copied effect truth | revert Portfolio selection through its typed owner |
| M5 · Cross-surface consolidation | Common inspect/detail/recovery across CLI, packaged frontend and Lark | shared projection and preceding verticals available | cross-session, stale, reconnect and repeated-action acceptance for the combined journey | hide optional detail/mutation controls; native readback remains |

M0 is a useful outcome on its own and does not wait for connector qualification,
a new authority store or the complete M2–M5 design. Each milestone includes the
entry points it changes; M5 cannot defer a required M0/M2/M3 settings companion.
#4813 is merged evidence infrastructure, not proof of the Portfolio or live
connector qualification. The overall-roadmap owner tracks S8 ordering; canonical
Todos track implementation. This change delivers the revised RFC and the M0
inspection slice above, not a completed Portfolio runtime.

### Integration sequence after the inspection slice

1. **M0 remainder / continuity pilot:** source-revision-aware resume and
   correction readback; one CLI/managed Turn, packaged frontend and Lark journey.
   Prove no stale assertion is treated as current, provider-off parity, bounded
   context and no unauthorized cross-Goal disclosure.
2. **M1 + M3:** use external evidence plus one connector only when the same
   question needs both. Bind selection to existing responsibilities/context and
   receipt references. Test direct→composed→direct, partial failure, conflicting
   evidence, unavailable worker and return-to-requester. Do not require M2.
3. **M4 before autonomous evolution:** frozen cases, baseline, negative examples,
   correction retention, duplicate/stale-use errors, useful retrieval precision,
   cost/latency and actual decision changes. Then propose one reversible method
   change. “No uplift” is a valid result; keep denominators and failures.
4. **M2 only if necessary, then M5 consolidation:** persist irreducible adoption
   policy only after the caller cannot express it using existing configuration
   and receipts. Publish ready-to-use presets only for journeys proven in two
   domains; installation/configuration preview, source permission, off/uninstall,
   degraded fallback and recovery must work from the existing surfaces.

This order integrates S1/S3 continuity, S6 memory, S8 composition and S11
evaluation in the overall roadmap; it is not a second scheduler or roadmap.
Before implementation, reconcile active continuity, checkpoint, Decision Context
freshness and utility-attribution PRs. Reuse their owners and do not duplicate
their writers. The inspection slice has no dependency on an unmerged PR.

## 12. Open decisions

1. **Portfolio storage profile.** Owner: shared-authority and capability
   maintainers. Recommendation: use the configured Goal authority provider for
   adoption records, reuse existing Goal external-capability bindings for
   runtime enablement, and keep large receipts/artifacts with their owners. Do
   not create a portfolio-specific provider binding. Decide only when M2 has a
   concrete durable-policy caller; M0/direct use needs no new storage.
2. **Cross-capability comparison.** Owner: capability maintainers.
   Recommendation: compare only within a named Goal gap and report multiple
   dimensions; do not create one global score. Validate in M3/M4.
3. **Automatic degradation threshold.** Owner: capability plus domain owner.
   Recommendation: automatic proposal, explicit typed policy to apply; never
   infer retirement from low call count alone. Decide before M4.
4. **Installation proposal UX.** Owner: product and extension maintainers.
   Recommendation: show governed repair/install proposals only after M3 proves
   selection value. It is outside v0 execution authority.

---

## Appendix A: Execution ledger (non-normative)

### 2026-09-21 — research and contract synthesis

- **Baseline:** `0ef7ebd749ec97a698a8fc7f2a29844dd368689b`; PR #4813 inspected at
  `491c0bf3ccd4804091d7611bd85d73f5f466fdd9`.
- **Delivered:** RFC contract only.
- **Evidence:** repository audit of catalog, connector registry, capability
  admission/memory, Agent-context hooks, Decision Context, Explore, reward
  memory and external-evidence proposal; one finance connector inventory/use
  dogfood informed the lifecycle but is not public qualification evidence.
- **Known gaps:** no canonical portfolio reducer, frontend/Lark projection or
  two-domain effect trial.
- **Effect on normative design:** initial proposal.

## Appendix B: Decision log

| Date | Decision | Owner / approval | Alternatives | Normative sections changed |
| --- | --- | --- | --- | --- |
| 2026-09-21 | Initial proposal; no approval inferred | pending maintainer review | domain-only organization, registry-as-quality-owner, Decision Context owner | all |
| 2026-09-21 | Narrow generic use/effect state to owner-receipt observations and portfolio-only lifecycle receipts | maintainer review request on exact head `1a6b15c6` | duplicate generic effect/admission authority | Sections 1, 3, 5, 7, 9–11 |
| 2026-09-21 | Existing Goal enablement activates supported behavior; direct-first, composition on demand; adoption and connector qualification retain separate vocabularies | revised proposal pending exact-head review | second opt-in, mandatory DAG/adoption, Kernel-owned selection | Sections 1–12 |

## Appendix C: Evidence registry

| Evidence id | Claim | Baseline / environment | Artifact or command | Result | Privacy / validity boundary |
| --- | --- | --- | --- | --- | --- |
| E1 | Three generic hook phases exist | implementation baseline | `agent_context` and subagent-context tests/source | inspected | static inspection, not live uplift |
| E2 | Registry is inventory/telemetry rather than qualification | implementation baseline | connector-registry schema and CLI | inspected | no exhaustive provider audit |
| E3 | External-evidence typed lifecycle is a merged prerequisite | current implementation baseline | `external_research/README.md`, typed external-evidence owner and CLI | merged via #4813; source inspected | not connector qualification or live-provider evidence |
| E4 | Durable Goal binding already owns exact provider operation/revision/profile selection | implementation baseline | extension reference and capability-admission source | inspected | read-only binding contract; does not prove provider execution |
| E5 | Automatic participation and settings should reuse existing owners | current implementation baseline | `agent_context.ts`, `capability_hooks.ts`, periodic-report/reward-memory hooks, configuration editor and Goal settings | source inspected | general Portfolio activation and measured overhead remain unimplemented |

### 2026-09-26 — comparative evidence (source inspection, not live qualification)

- [LingTai kernel molting implementation](https://github.com/Lingtai-AI/lingtai-kernel/blob/5c65c3f9860de0addc0d2bb10d91b7d80a55930c/src/lingtai/tools/context/_molt.py):
  resume notification and explicit acknowledgement around context replacement.
  Transfer the recovery discipline, not character prose as execution authority.
- [Hermes memory documentation](https://github.com/NousResearch/hermes-agent/blob/v2026.9.24/website/docs/user-guide/features/memory.md)
  and [skill ledger](https://github.com/NousResearch/hermes-agent/blob/v2026.9.24/tools/skill_ledger.py):
  bounded session-start memory, on-demand history, versioned mutation evidence.
  A frozen snapshot also explains why a long-lived session may miss updates;
  LoopX should validate a new planning/resume boundary, not merely persistence.
- [OpenViking session lifecycle](https://github.com/volcengine/OpenViking/blob/v0.4.21/docs/en/concepts/08-session.md):
  synchronous archive and asynchronous extraction with change lineage. Reuse
  provider task/readback evidence; an accepted extraction is not usable memory.
- [Muse's product design](https://introducing.muse.ai/) and
  [Grok Bot's product design](https://x.ai/news/designing-grok-bot): vendor-stated
  persistent work, relevant notifications, and progressive detail in a primary
  conversation. These support a product hypothesis, not claims about internal
  implementation, reliability, or outcome uplift. No live accounts were tested.

The proposed LoopX decisions above are inferences from source inspection and
existing local owner boundaries, not reproduced benchmark results. Public
examples must not contain private conversations, account data or raw corpora.

## Appendix D: Rejected or superseded alternatives

The alternatives in Section 6 remain rejected until evidence shows they can
preserve the invariants with less state and equal product clarity.

## Appendix E: Incident and review lessons

- A capability being installed or a worker route being projected did not prove
  that a real call could execute. Exact authority/readiness readback belongs in
  each composition plan.
- Successful persistence or exact memory readback did not prove that an
  experience changed future behavior. Use and effect qualification remain
  separate.
- A portfolio that copies coverage, admission or decision effects would create
  a second truth that can outlive an owner correction. Portfolio state therefore
  records only its own adoption lifecycle and immutable references to owner
  receipts.
