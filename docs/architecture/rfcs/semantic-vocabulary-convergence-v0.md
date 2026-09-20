# RFC: Semantic Vocabulary Convergence and Commit-Time Drift Checks (v0)

- **RFC status:** Draft
- **Delivery maturity:** Partial (M0 registry, computed inventory, and drift smoke ship with this RFC)
- **Authors / owners:** LoopX contributors; control-plane kernel maintainers own approval
- **Created:** 2026-09-15
- **Last normative revision:** 2026-09-17
- **Implementation baseline:** `1dc6ad8d8`
- **Related contracts:** `loopx/semantics/vocabulary_v0.json`,
  `loopx/semantics/inventory.py`,
  `loopx/control_plane/turn_transaction_contract.json`,
  `loopx/control_plane/coordination/coordination_state_contract_v0.json`,
  [Turn Envelope v0](../../reference/protocols/turn-envelope-v0.md),
  [Turn Loop Controller v0](../../reference/protocols/turn-loop-controller-v0.md),
  [TypeScript Control-Plane Migration v0](typescript-control-plane-migration-v0.md)
- **Language mirror:** [中文版](https://github.com/huangruiteng/loopx/blob/main/docs/architecture/rfcs/semantic-vocabulary-convergence-v0.zh-CN.md)

## Document map and maintenance contract

This RFC ships an English document and a `semantic-vocabulary-convergence-v0.zh-CN.md`
semantic mirror; both carry a language link and must be revised together when a
normative section changes.

- Sections 1-10 are the durable design and acceptance contract.
- Section 11 is the normative delivery plan.
- Section 12 contains unresolved decisions; proposed answers are not approval.
- Appendices hold the non-normative execution ledger, decision log, evidence
  registry, and rejected alternatives.

RFC maturity and delivery maturity are independent. Dated progress entries do
not amend normative sections.

---

## 1. Decision summary

1. **What becomes authoritative.** A curated registry and a computed inventory under `loopx/semantics/`. The
   curated registry `vocabulary_v0.json` names each kernel and cross-runtime
   vocabulary, the exact `module::Symbol` allowed to define it, the relations
   between vocabularies (same concept, shared field name, subset), the total
   projections, and the budgets the repository ratchets down. The generated
   inventory maps every closed-set carrier under `loopx/`:
   string enums, `Literal` aliases, named closed sets, TypeScript `as const`
   arrays, and every constant name defined in more than one module. A public
   smoke, `examples/semantic-vocabulary-drift-smoke.py`, checks the code against
   both inside the default `pytest` sweep on every pull request; premerge and
   the full-public fleet are additional surfaces (Section 10). A change that widens a
   vocabulary, forks a constant, or changes a budget must carry the required
   owner/registry edits in the same diff. Ordinary new carriers are discovered
   automatically and do not require a generated snapshot commit (Q9).
2. **Authority and generation.** Each enum lives in its owner module; the
   registry is checked against code by AST and text scan, and product code never
   imports it. The M1 generator derives the TypeScript effective-action binding
   from the Python owner after checking registry parity. This does not change
   wire values or move value authority to the registry; M2's shared contract
   generation remains a later milestone.
3. **Default and opt-in boundary.** The check is always on for the repository.
   It has no runtime flag because it never runs inside the product.
4. **Principal constraint.** Fail closed, deterministic, and not weakenable by
   a data edit alone. An unregistered literal in either runtime, a second
   defining module, a budget overrun, a registry value no module carries, a
   stale generated binding, an owner declared without a symbol, or a coverage count
   below the recorded floor each fails the smoke. The dispatch forms the scan
   recognises live in the smoke, not in the registry. The smoke reads only
   tracked sources and prints no private data.
5. **Not approved by this RFC.** Merging the three Turn outcome enums into one,
   splitting the three `effective_action` slots, deleting any legacy should-run
   field, deleting any Python twin module, or renaming any existing value.
   Those are later milestones with their own gates under the schema-reduction
   rule in `AGENTS.md`.

## 2. Problem and motivation

LoopX has grown by many small agent-driven PRs. Each PR added the vocabulary it
needed where it needed it. The result is not wrong behavior but drift: the same
concept spelled several ways, the same constant defined in several files, the
same field name carrying different vocabularies, and open string sets that any
module may widen without anyone noticing. Reviewers cannot tell from a diff
whether a new literal is a new state or a typo, and documentation cannot stay
in step with a set nobody enumerates.

The semantic surface is repository-wide, not a Turn-kernel problem. Measured on
the baseline by the inventory generator over 1169 source files under `loopx/`:

| Carrier | Count | Notes |
| --- | --- | --- |
| Python string enums | 102 | 29 in the control plane, 17 in capabilities, 6 in extensions |
| Named closed sets (`NAME = frozenset/tuple` of strings) | 490 | 66 are field lists, 31 kinds, 31 states, 29 statuses |
| `Literal[...]` aliases | 8 | |
| TypeScript `as const` arrays | 40 | 21 have an equal Python set; 14 have none |
| Named string constants | 2002 | 754 are `*_SCHEMA_VERSION` |
| Same name, same value, two runtimes | 166 | legitimate py/ts twins |
| Same name, same value, one runtime | 25 names / 58 definitions | forks; 7 are schema versions |
| Same name, different values | 18 names / 59 definitions | see below |

Concrete failures audited on the baseline:

- `TURN_ENVELOPE_SCHEMA_VERSION` was defined three times:
  `loopx/control_plane/quota/turn_envelope.py:16`,
  `loopx/control_plane/quota/turn_envelope.ts:13`, and a private copy in
  `loopx/control_plane/turn_driver/driver.py:31`. M0 removes the copy.
- `HANDOFF_MODES` was defined by the TypeScript owner and again as a literal
  tuple in `control_plane/testing/authority_e2e_fixtures.py:33`. M0 derives the
  fixture tuple from the `HandoffMode` enum.
- The same value set carries two names across runtimes:
  `MATERIAL_DELIVERY_OUTCOMES` in `work_items/delivery_outcome.ts` equals
  `VISION_OUTCOME_CHECKPOINT_MATERIAL_OUTCOMES` in
  `goals/goal_frontier/outcome_continuity.py`. Both equal `DeliveryOutcome`
  minus `surface_only`; nothing said so.
- Conflicting definitions with the same name: `DECISION_CONTEXT_CAPABILITY_ID`
  is `decision_context` in `capabilities/decision_context/packets.py:18` and
  `decision-context` in `extension_provider.py:24`; `MCP_REQUIREMENT` is
  `mcp==1.28.1` in `kunluncode_goal_mode/cli.py:28` and `mcp<2` in
  `claude_goal_mode/scripts/install.py:83`. Sixteen further names are generic
  module-local constants (`SCHEMA_VERSION`, `COMMAND`, `CAPABILITY_ID`) whose
  collision is harmless today and invisible tomorrow.
- The Turn result kinds exist twice by hand: `LoopXTurnResultKind` in
  `transaction.py:28` and `TURN_RESULT_KINDS` in `settlement.ts:49`. They
  match; no test asserted it. The same holds for eleven other py/ts pairs
  (settlement step, binding and failure kinds, receipt-bound phases, scheduler
  transitions, Todo completion continuation and recovery, delivery outcome,
  delivery workspace kinds, Goal amendment classes, Todo decision scopes).
- `effective_action` is an open string set with no enum on either runtime.
  Thirty-one distinct literals are dispatched on by string comparison in
  Python and TypeScript. Two of them (`observe_replay`, `block_replay`) are
  written by `turn_journal.ts:656` into the replay observation slot of the
  Turn Envelope and are not should-run verdicts at all. Two more
  (`quota_action_selection_deferred`, `quota_action_selection_rejected`) are
  quota error codes that `cli_commands/quota.py:279` copies into the slot.
  `AgentScopeFrontierAction` values are written into the
  `agent_scope_frontier.effective_action` slot of the same envelope. One field
  name, three vocabularies. `user_gate.py:162` compares the slot against
  `skip`, which no producer writes.
- Three near-isomorphic Turn outcome vocabularies coexist:
  `LoopXTurnResultKind` (12), `LoopXTurnRoute` (8), `LoopDisposition` (8),
  with `repair`/`repair_required` and `replan`/`replan_required` as different
  spellings of one verdict. The route-to-disposition projection is a private
  dictionary in `loop_controller.py:126`; nothing declared that it is total.
  The load-bearing table in `decide_loop_disposition` (result kind, retryable,
  attempt budget, decision user action, durable no-follow-up) exists only as
  prose in the controller protocol document.
- Six should-run decision fields the documentation already calls legacy are
  still mentioned by 7 to 35 Python modules each, with no ratchet stopping new
  consumers.
- 43 same-basename `.py`/`.ts` module pairs exist under `loopx/control_plane`
  while the migration RFC is replacement-first. The count had no guard.
- The repository already runs an AST-backed control-plane debt ratchet
  (`loopx/canary/maintainability_ratchet.py`) with a reviewed exception
  lifecycle, but it measures module metrics and dependency direction, not
  vocabulary shape. Vocabulary drift had no ratchet.

The current owners cannot solve this locally because every fix is
cross-module by definition: the Turn driver, quota, todos, capabilities, and
the TypeScript runtime each own one spelling of the same idea.

### Invariants

- **I1 Single owner.** Every registered vocabulary or constant has exactly the
  defining modules the registry lists, and the registered symbol name is
  defined nowhere else under `loopx/`. Everyone else imports.
- **I2 Closed sets.** Every value a registered vocabulary may carry is listed.
  The code carries no unregistered value in either runtime and the registry
  lists no value the code does not carry. Stage `m0`, delivered, and blocking
  today: the fixed literal scan exits the smoke non-zero on an unregistered
  comparison. Its evidence is bounded to the dispatch forms the scanner
  recognises, so a value that reaches the code by any other form is unverified,
  not shown to be absent.
- **I3 Cross-runtime parity.** When a vocabulary has a Python and a TypeScript
  owner, both carry the identical set.
- **I4 Total projections.** A registered projection names every source value
  exactly once, either mapping it or declaring it rejected.
- **I5 Ratchets only fall.** Retirement, twin, and inventory budgets may be
  lowered in any PR. Each budget is additionally pinned by a `BUDGET_ANCHOR`
  (or `RETIREMENT_ANCHOR`) literal inside the smoke, and each floor by a
  `COVERAGE_ANCHOR`, following the `RFC_MODULE_BUDGETS` anchor pattern in
  `tests/control_plane/test_m6_quality_gates.py`, with one deliberate
  difference: the registry value must **equal** the anchor. The precedent
  compares with `<=`, which lets a budget tightened below the anchor be raised
  back to the anchor later without any code edit. Equality makes every
  tightening a two-file diff and every loosening a code edit a reviewer sees.
- **I6 Same-diff visibility.** A semantic change and its required owner, registry or budget
  edits land in one reviewable diff. Computed inventory reports are evidence,
  not committed authority.
- **I7 Deterministic and public-safe.** The check reads tracked sources only,
  needs no network or credentials, and its failure text names files and
  values, never private data.
- **I8 Coverage only grows.** The number of registered vocabularies, owner
  symbols, projections, relations, schema versions, and scanned suffixes is
  recorded as a floor. An owner is `module::Symbol` or `null`; a bare module
  path is rejected, and a null owner requires a literal scan. The dispatch
  forms the scan recognises are fixed in the scanner code. A registry edit therefore
  cannot silently narrow what the guard sees.
- **I9 Both carrier shapes are measured.** A vocabulary reaches the code either
  as a string constant (`NAME = "value"`) or as a multi-value carrier (an enum,
  a named closed set, a `Literal` alias, a TypeScript `as const` array). Both
  get the same collision rule: one name defined in two modules with identical
  values is a twin, with different values a fork. Collision budgets count the
  shared-vocabulary subset only; module-local convention names such as
  `SCHEMA_VERSION`, `COMMAND`, or `*_LABEL` stay visible in the inventory totals
  but are not drift.
- **I10 On the pull-request path.** The drift smoke runs inside the default
  `pytest` sweep through `tests/architecture/test_semantic_vocabulary_drift.py`,
  so it fails closed on every pull request that runs the Python tests. Fleet
  discovery under `examples/` and the `repo-architecture-budget` premerge
  profile are additional surfaces, not the obligation: the fleet runs after
  merge and on a schedule, and premerge selects by changed-path tokens.
- **I11 Roles are distinct.** A vocabulary has one owner, some producers, some
  interpreters, and some pass-throughs (Section 5, "Roles of a vocabulary").
  Only the owner defines the set and only producers write values. Mentioning,
  comparing, serializing, or displaying a value confers no ownership. An
  interpreter or pass-through that starts writing a value has become a
  producer and must be registered as one. Stage `m0_5`, delivered. Blocking
  today for the producer half: a site that writes a kernel value without being
  registered as a producer exits the smoke non-zero (`undeclared producer
  sites`). Not blocking for the consumer half: interpreters and pass-throughs
  are deliberately unregistered, so their only evidence is the advisory F3
  inventory and no check can fail on them.
- **I12 Every kernel value is produced.** For a `kernel` vocabulary, every
  value not listed under `compatibility_only` has at least one production site
  the fixed production forms recognise or an executable witness at a registered input decoder. A variable-source note alone is not production evidence. A
  value that is only compared is dead or compatibility-only, never canonical.
  `skip` in `effective_action` is the first expected failure. Stage `m0_5`,
  delivered. Blocking today over the kernel tier: a registered kernel value with
  no observed producer exits the smoke non-zero. Evidence is bounded to the
  production forms inside the scan reach, which is F2's `verified / registered`
  of 6 / 26, so the `cross_runtime` tier is unverified rather than passed. At M0
  the literal scan accepted a compared value as carried.
- **I13 Producers write registered values only.** A production site that
  writes a value outside the registered set fails closed, independently of
  whether any consumer compares it. Production is stricter than comparison: a
  consumer comparing an unregistered value is dead code, a producer writing one
  is protocol drift. Stage `m0_5`, delivered. Blocking today: a recognised
  producer writing a value outside the registered set exits the smoke non-zero,
  over the same 6 / 26 domain as F1. Unresolved dynamic sites are reported and
  counted, never treated as proven safe. At M0 the literal scan covered both
  forms together.
- **I14 Scope is declared, not inferred.** A name defined in several modules
  is a fork unless the registry declares it `bounded_context` and lists the
  contexts and one owner symbol per context. Declared names leave the fork
  budget; a rename does not change the budget's meaning and is not a fix.
  Stage `m0_5`, delivered. Blocking today: a declaration that does not name every
  defining module exactly once exits the smoke non-zero, over 4 / 4 declared
  contexts. At M0 `SOURCE_SURFACES` was counted as a fork and noted.

## 3. Scope and non-goals

### In scope

- The registry file, its schema, and the ownership rule for editing it.
- The computed inventory, optional report export/check commands, and their tests.
- The drift smoke and its placement in the premerge and full-public fleets.
- The vocabularies registered at M0: the four Turn-kernel sets
  (`turn_result_kind`, `turn_route`, `loop_disposition`, `effective_action`),
  `agent_scope_frontier_action` and `lease_action`, and twenty cross-runtime
  sets whose Python and TypeScript owners carry equal values on the baseline;
  the route-to-disposition projection; nine relations; the Turn Envelope schema
  version; the six legacy should-run fields; the control-plane twin count; and
  the inventory fork and conflict budgets.
- The later milestones that turn `effective_action` into a typed enum, split
  its three slots, publish the projection through the contract, and retire
  legacy fields and twins under existing repository rules.
- The scan root is the `loopx/` package. "Repository-wide" in this RFC means
  every carrier under `loopx/`, in both runtimes, not every file in the git
  tree. The inventory `root` and every `literal_scan.roots` entry say `loopx`
  and the smoke reads nothing else.

### Non-goals

- Changing any runtime decision, payload shape, or wire format.
- Scanning `apps/` (about 90 TypeScript files on the baseline) or `examples/`
  (a dozen `effective_action` assertions in smokes). Those are consumers and
  test doubles, not producers; a smoke that asserts an unregistered value is
  invisible to M0 and is accepted as such until a milestone widens the root,
  which would also raise the merge-order cost in Section 10.
- Curating every closed set by hand. The inventory maps all of them; only
  vocabularies that cross a module or runtime boundary and are dispatched on
  are curated with owners, values, and relations.
- Replacing `turn_transaction_contract.json` or
  `coordination_state_contract_v0.json`. Those remain the owners of their
  phases and records; this registry may reference them, not restate them.
- Replacing `maintainability_ratchet.py`. It owns module metrics and dependency
  direction; this registry owns vocabulary shape. Whether their exception
  lifecycles merge is Section 12, Q7.
- A prose glossary as the enforcement mechanism. A glossary is a useful
  companion and is tracked in Section 12, but it cannot fail a build.

## 4. Current-system contract

Facts on baseline `1dc6ad8d8`:

- `turn_transaction_contract.json` is the one contract read by both runtimes:
  `effect_program.py:160-166` loads the phase tuple and
  `turn_journal.ts:1` imports the JSON. This is the template the registry
  follows for a shared source of truth.
- `coordination_state_contract_v0.json` goes further and generates
  `coordination_state_contract_generated.py` and
  `coordination_state_contract.generated.ts` through
  `scripts/generate_coordination_state_contract.py --check`, guarded by
  `tests/control_plane/test_coordination_state_contract.py`. This is the
  template the inventory generator follows now and the generation stage
  proposed in M2 follows later.
- The canary runner discovers every tracked `examples/**/*-smoke.py`
  (`loopx/canary/runner.py:392`), so a smoke at `examples/` needs no
  registration in `planner.py` or `premerge.py`.
- `loopx/canary/maintainability_ratchet.py` is the existing AST-backed
  control-plane debt ratchet. It carries reviewed exceptions with a
  `retirement_plan` and detects stale exception ids. Its subject is module
  size, `Any` density, decision-point counts, and forbidden dependency
  direction; it does not read enum or constant values.
- `AGENTS.md` already requires typed enums for state classification, forbids a
  second source of truth in Python for control-plane authority, requires a
  scope-fit review before adding a module, and requires maintainer approval for
  any schema reduction. This RFC adds the check that makes those rules
  observable in a diff; it does not change them.
- Package data for `loopx.control_plane` already ships `*.json`;
  `pyproject.toml` gains one line so `loopx.semantics` ships its two JSON files
  the same way.

## 5. Proposed architecture

### Ownership and authority

The registry is owned by the control-plane kernel maintainers. Any contributor
may lower a budget or add a value together with the code that carries it. Only
a maintainer may approve raising a budget, removing a value, or moving an owner
module, and the approval is recorded in Appendix B.

Forbidden alternate authorities: a second registry, a per-module list that
restates registered values, or a prose table that claims to be normative for a
registered vocabulary.

**Scope of a name (planned for M0.5, not in M0).** The collision rules are
keyed by name, so they cannot tell a fork from four bounded contexts that
happen to reuse one identifier. `SOURCE_SURFACES` is the first case: its four
definitions in `global_risks.py`, `global_todos.py`, `summary_all.py`, and
`pr_review.py` each list the data sources of that one CLI command, and the
value sets are meant to differ. It is counted in `multi_value_forks` today and
must not be "fixed" by renaming, because a rename lowers the number without
changing the code's meaning. The M0.5 scope slice adds top-level `scope_declarations` with at
least `global` and `bounded_context`; a bounded-context name is declared once
with its owning contexts, and declared names are removed from the semantic
fork budget while the raw inventory count remains visible (I14, the schema rows below, and the M0.5 row in Section 11). Until
then the fork budget is a ceiling that contains this one known
misclassification, recorded in the registry's `inventory_ratchets` note.

### Roles of a vocabulary

A module that mentions a value is not its owner, and a vocabulary has more
than one kind of participant. Consumer is the umbrella role for code that reads
or accepts a value; interpreter and pass-through are its two tracked subroles.
The registry distinguishes these roles because the check that makes sense differs
by role:

| Role | What it does | Registered | Check |
| --- | --- | --- | --- |
| Owner | Defines the closed set as one `module::Symbol` per runtime | Yes, since M0 | I1 to I3 |
| Producer | Writes a value into the field: assignment, dict or object literal, constructor keyword, `return` of a literal inside a listed deciding function, enum member on the owner | Yes for `kernel` vocabularies, from M0.5 | I12, I13 |
| Consumer | Reads or accepts a vocabulary value; this is the umbrella role for interpreters and pass-throughs | Usually no; relation is reported rather than curated | F3 |
| Interpreter | Consumer that branches on or maps the value: `if`, `match`, `switch`, membership test | No; found by the dispatch scan, ranked by `--report` | I2, F3 |
| Pass-through | Consumer that serializes, persists, forwards, or displays the value without changing its meaning | No | F3; persistence also needs F6 evidence |

Two rules follow. A value with no producer is dead or compatibility-only:
`skip` is compared in `todos/user_gate.py` and written nowhere, so M0 passes
it and M0.5 fails it until it is removed or listed under `compatibility_only`.
Production is stricter than comparison: M0.5 scans production forms on their
own and fails on an unregistered produced value (I13), while the M0 literal
scan keeps catching unregistered comparisons (I2). Interpreters and
pass-throughs are deliberately not registered; otherwise every consumer edit
would touch the registry, the churn Section 6 rejected for consumer counts.
Their relations to a vocabulary are advisory output of `--report`.

Production forms are fixed in the smoke at M0.5, like the dispatch forms:
Python `x["f"] = "v"`, `f="v"` as a constructor keyword of the envelope or
packet type, `return "v"` inside a function the registry lists as a producer,
and member access on the owner enum; TypeScript `f: "v"` in an object
literal, `x.f = "v"`, and the conditional expression. `variable_sourced_values`
stays for the values a producer builds from a variable the scan cannot follow.
Which vocabularies must list producers: `kernel` at M0.5; `cross_runtime` only
when a value is added or removed after M0.5; `cross_module` only if promoted
(Q8). Persistence is a property the production scan can answer: a producer
whose listed symbol is a journal or receipt writer marks the vocabulary
`persisted`, which is the fact Q2 and Q10 wait on.

### Executable production evidence during M0.5/M1

The producer guard and the owner-carrier check have separate evidence. Defining
an enum member proves membership, not production. For each vocabulary with
producer metadata, the guard compares observed result values against `values`,
rejects undeclared **function sites**, and checks that every non-compatibility
value has an observed producer. A variable-source note is not liveness evidence.
`return_producers` lists the registered functions whose scalar return expressions
belong to this vocabulary; packet builders' unrelated return text is excluded.
`return_paths` selects an explicit field/index path from a returned packet.
`call_producers` names reviewed builder parameters; their module binding and
actual signature are checked against tracked source. These declarations and
their code anchors move together. Selectors equal the code-owned map, with an
empty map for every other vocabulary, so additions also require a code change.
An unconfirmed field-named keyword argument retains closedness/unknown evidence
but cannot establish producer liveness or require producer registration. These
are reviewed output contracts, not automatic proofs of arbitrary helper-body
semantics. Local enum containers and
arguments to arbitrary predicates do not establish production; a selected
scalar must reach an observed output. Mutated or escaped mutable aliases remain
unknown. Generation uses strict enum extraction and rejects unsupported members
before writing any artifact, including when the second owner is invalid.

Python field assignments (including subscript/attribute and annotated writes),
dictionaries, call keywords, owner-member results and declared scalar returns
are parsed with AST. Imported enum aliases resolve only to the registered
owner, including one unrenamed re-export hop through a tracked module (a
second hop, a renamed re-export or a rebinding stays unknown); shadowed
names and unbindable calls remain unknown. Conditional
results exclude the condition's literals. Three further local forms are bound,
each only under a stated condition: a call to an undecorated, non-generator,
plainly-defined top-level `def` **of the same module** resolves to the union of
that function's own returns, with arguments never bound to parameters, so a
returned parameter stays unknown and the result is independent of the call site;
a local written more than once resolves to the union of the writes that
textually precede the read, and only when every store of that name is a plain
`name = expression` **and** no write shares an enclosing loop with the read;
and a container mutated only through direct literal-key subscript writes keeps
its untouched keys, with a written key carrying the union of its initializer and
every write. Anything outside those conditions — a decorator, `async def`, a
generator, recursion, an imported or attribute call, a back edge that carries a
later write to the read, a `with`, `except`, walrus, augmented, unpacking,
`global` or `del` rebinding, an alias, a method call, a computed, negative or
deeper store, an escape into a call, or an unknown `**` spread — leaves the site
unknown rather than admitting a value. TypeScript object writes, assignments
and declared returns use the repository's TypeScript parser rather than regex,
and report the same blocker vocabulary as the Python scanner, so one residue
taxonomy covers both runtimes.
Neither parser executes inspected source. These are syntactic result witnesses,
not a proof of reachability or whole-program data flow.

`uv run python examples/semantic-vocabulary-drift-smoke.py --report` lists unresolved
production locations. Unresolved parts cannot supply missing value evidence; known
conditional branches remain structural witnesses, not reachability proofs.
The producer guard covers all six kernel entries using distinct evidence lanes:
`effective_action`, `turn_route`, `loop_disposition`, and
`agent_scope_frontier_action` have source witnesses; `turn_result_kind` also has
executable input witnesses at the fixed `transaction._result_kind` decoder.
For each registered value the real decoder must return the matching typed member;
invalid probes must report rejection. This proves a permitted production path,
not that a Host has emitted every member or that every host execution is valid.
`input_producer` cannot select arbitrary code: the verifier is fixed in the smoke.

`lease_action` is explicitly legacy/compatibility-only: in-repository runtime
callers use separate acquire/renew/transfer/release command classes. Its four
members remain available to the existing typed `LeaseModeGateCommand` input
interface until M4 caller/migration review. No persisted usage is asserted.
The producer list is empty only because every value carries an explicit reason
and retirement milestone. A newly observed producer invalidates that declaration. Kernel families without producer metadata are printed as coverage pending; their
owner parity must not be reported as I12/I13 completion. M0.5 remains incomplete
until all required families meet its acceptance rows.

The decision owner includes five existing results previously missed by the
literal scanner: `blocked_health`, `blocked_wait`, `control_plane_repair`,
`operator_gate_notify`, and `throttled_skip`. Registering them preserves the
existing quota behavior. M1 removes the unproduced `skip` and synthetic
`operator_gate` admission. The fallback consumer now recognizes the actual
`quota_skip` action; a runnable scoped fallback must not keep a skip action.
Legacy field retirement remains a separate acceptance obligation.

Preparation for the TypeScript parser: `npm ci --ignore-scripts` from the
repository root, using its lockfile. The scan itself needs no network or
credentials. Python 3.11+ and the repository-supported Node runtime are required.

### M1 action domains and compatibility

#### Why this stage is necessary

M1 makes callers read the appropriate field and lets later PRs distinguish a
new decision from a new diagnostic. Expanding one string set cannot do this:
copying `result_kind` or an arbitrary host action into the quota action field
feeds different meanings into the same dispatch surface. Seeing an enum in a
comparison also does not prove that the system produces its values. M1 separates
these evidence roles and fixes the scoped fallback mismatch that recognized
unproduced `skip` while the actual output was `quota_skip`.

The boundary is deliberately limited: the root action remains the distinguishable
`D ⊔ F` union without adding wire tags to every string; only load-bearing producers
require registration, not every consumer; historical signed data remains readable.
The checks cover finite vocabularies, supported output forms and generated artifact
consistency. They do not prove whole-program semantic completeness, reachability of
all branches or arbitrary variable-flow safety.

The cost is regenerating bindings after owner changes and installing the locked
TypeScript parser for local scans. Reusing existing CI jobs still adds job work and
contributor repair effort; no new required job does not mean no new obligation.
First classify a failure: replace bare actions with owner references; accompany a
new decision with owner, producer and consumer validation; keep diagnostics in
`error_code` and Turn results in `decision`. Use Section 10 regeneration commands
for stale artifacts. Repair scanner false positives with a regression example,
rather than widening the vocabulary, reducing coverage or relaxing budgets.

#### Output contracts and compatibility boundaries

Let `D` be the 32 decision values owned by `EffectiveAction`, and `F` the four
values owned by `AgentScopeFrontierAction`. The root should-run and its envelope
projection retain the existing action strings through `A = D ⊔ F`. The registry
anchors the two member vocabularies and checks `D ∩ F = ∅`; hence the value
identifies its domain without a new wire tag. The TypeScript bindings and union
type derive from these owners, not an independently maintained third value list.
This is the registered-union option in Q6. A union arm cannot establish another
owner's producer liveness, and a canonical decision function's scalar return
domain remains `D`.

| Surface | Current contract | Compatibility |
| --- | --- | --- |
| Root should-run / Turn Envelope `effective_action` | Decision/frontier union `A` | Frontier verdicts retain their meaning and spelling |
| Nested `agent_scope_frontier_v1.action` | Frontier domain `F`; one emitted action field | Readers prefer `action` and retain the old v0 alias as fallback |
| Internal journal replay observation | Existing `decision=replay_legal\|replay_blocked` | Public inspection and stored journal shapes do not change |
| Turn-result Effect observation | Turn verdict in `decision`; `effective_action=null` | Intentional projection change: read `decision` for the verdict; host action fields cannot author a quota decision |
| Action-selection rejection or deferral | `effective_action=quota_skip`; diagnostic in `error_code` | Intentional CLI change: readers distinguish reasons using the unchanged diagnostic code |

New frontier writes remove the redundant nested `effective_action` and advance
the nested schema to v1. They do not normalize historical signed v0 documents:
the envelope capsule still preserves both legacy keys when present, and journal
resume returns the stored plan unchanged. Compatibility tests characterize old
signatures before the migration, mutate signed fields to prove coverage, and use
the real filesystem journal writer and resume reader. New v1 signatures change
only for the declared nested schema/field reduction. No frontend setting owns
this alias; the quota CLI and Markdown reader are covered by the live tests.

The transient `effect.interpret_turn_result` projection previously copied either
an arbitrary host action or `result_kind` into the quota action field. It now
emits JSON null, preserved as `None` by the Python adapter. Its TypeScript return
type fixes the action to null; quota observations retain their existing string
action. The executor reads the result's `decision` and persists the normalized
host result and plan, not this transient observation. Real host validation still
rejects unsupported action fields, and executor/journal replay tests cover the
unchanged no-spend wait path. This projection change does not migrate stored
result, receipt or journal schema versions.

The literal guard uses Python AST and the TypeScript compiler parser for bounded
field writes, comparisons, membership and match/switch cases. It rejects bare
action literals even when registered: import the owner instead. Conditions,
unrelated fields, comments and source examples inside strings do not count as
action values. This is a syntax boundary, not a whole-program data-flow proof;
dynamic keys, aliases and unresolved expressions retain their declared limits.
The generated bindings/glossary freshness check reuses the existing PR pytest
and smoke path; this milestone adds no required CI job.

### Formal model and proof boundary

The registry is a finite specification of a larger program semantics. Let
`V` be the set of registered vocabularies, `L` the source sites, `U(v)` the
ambient runtime values, and `S(v)` the registered admitted values of vocabulary
`v`. Production and consumption range over `U(v)` before validation. The model
records relations, not just names:

```text
D ⊆ L × V                         defines
P ⊆ L × V × U(v)                  produces
C ⊆ L × V × U(v)                  consumes or branches on
I ⊆ L × V × V                     interprets one vocabulary as another
T ⊆ L × V                         passes through without changing meaning
G ⊆ V × V × (S(v_source) ⇀ S(v_target) ∪ {reject}) projects
R ⊆ L × V × Version               persists a value durably
```

Each obligation is stated over the domain it is actually checked on, not over
`V`. `Producers(V) ⊆ V` is the subset that declares producers — every
`tier: kernel` vocabulary, plus any vocabulary of another tier that carries
executed production evidence; `Produced_scan(v)` is the production the fixed
forms observe inside the code-owned scan reach; `ScopeDeclarations` are the
forked names the registry declares as bounded contexts.

The machine-readable universe and F1/F2 statement/evidence fields are canonical
projections of `ProducerDomain`: the walked vocabulary names, kernel membership,
and outside counts by tier. The drift smoke requires exact agreement with that
projection, including the valid `Kernel(V) ⊆ Producers(V)` comparison. These
fields are generated statements, not free-form prose checked for forbidden
phrases; arbitrary paraphrases are not interpreted as formal evidence. The RFC
explanation remains subject to human review.

1. **Producer closedness (vocabularies declaring producers):** `∀v ∈
   Producers(V): Produced_scan(v) ⊆ S(v) ⊆ U(v)`. A recognised producer cannot
   write a value outside the registered set. `Producers(V)` is currently the six
   `tier: kernel` vocabularies plus `settlement_binding_kind`, the one
   `cross_runtime` vocabulary carrying an executed witness. Production outside
   the scan reach, and the 19 `cross_runtime` vocabularies still outside
   `Producers(V)`, are unverified rather than proven closed.
2. **Canonical liveness (vocabularies declaring producers):** `∀v ∈
   Producers(V): Canonical(v) ⊆ Produced_scan(v) ∪ CompatibilityOnly(v)`. A
   value that is only compared is dead or compatibility-only, never canonical.
   The 19 `cross_runtime` vocabularies outside `Producers(V)` are never walked,
   so liveness there is unverified.
3. **Consumer domain closedness:** `Accepted(c) ⊆ S(v)`, unless the consumer
   explicitly declares an external or partial domain.
4. **Scope enumeration completeness:** `∀n ∈ ScopeDeclarations`, the declared
   context owner modules are exactly the modules defining `n`, one context per
   module, and every context owner symbol is `n`. Scope is declared and never
   inferred, so "a collision is a conflict only when the declared scopes
   overlap" is the *definition* of a semantic conflict and cannot be violated;
   the checkable obligation is that a declaration enumerates every defining
   module. Spelling alone still cannot establish equivalence.
5. **Projection totality:** for every source value, a projection maps to a
   target value or explicit `reject`.
6. **Persistence compatibility:** a persisted vocabulary change preserves all
   readers or declares a versioned migration.

Each obligation records the set it quantifies over in
`formal_model.invariants[].domain`, and the smoke derives both sizes from the
registry rather than trusting the declared numbers:

| Obligation | Quantifies over | Verified / registered | Evidence bound | Stage | Blocks today |
| --- | --- | --- | --- | --- | --- |
| F1, F2 | `vocabularies[tier=kernel].producers` | 6 / 26 | producer scan reach | `m0_5` | Yes, within the 6 |
| F3 | `vocabularies[*]` | 0 / 26 | inventory only | `advisory` | No |
| F4 | `scope_declarations[*].contexts` | 4 / 4 | declared defining modules | `m0_5` | Yes |
| F5 | `projections[*]` | 1 / 1 | executable owner function | `m0` | Yes |
| F6 | `persists_edges[*]` | 0 / 0 | unmodelled | `unproved` | No |

`verified` is the sub-domain the enforcement stage walks; `registered` is the
whole population of the same unit. An advisory or unproved stage walks nothing,
so its `verified` count must be zero, and an enforced stage may not declare an
empty domain. The selector and the evidence bound of each obligation are pinned
by `FORMAL_DOMAIN_ANCHOR` in the smoke on the `COVERAGE_ANCHOR` pattern (I5), so
an invariant cannot widen the set it claims through a registry edit alone. F5's
`verified` count is the number of projections the smoke actually imports and
executes, taken from `EXECUTED_PROJECTIONS` in code, not the registry's own row
count; a registered projection with no executed check raises `registered`
without raising `verified`, the way F1 reports 6 of 26. The
producer scan reach is measured on every run instead of pinned, because its
denominator moves with any new module; the smoke prints the current ratio, the
unresolved-site total, and the share of that total no wider scan could ever
resolve (E21).

#### Four separate readings of one obligation row

A `formal_model.invariants[]` row is read four ways. This RFC states each of them
separately, because collapsing them is exactly how validated metadata comes to
read as an executed proof:

| Reading | Where it lives | What it can say, and what it cannot |
| --- | --- | --- |
| Schema validation | `check_formal_model` in the drift smoke | The block has the exact key set, the five roles, the consumer hierarchy, the seven relation kinds, each of F1 to F6 stated exactly once with a non-empty statement and evidence boundary, a lane agreeing with its stage, and a domain whose two sizes the smoke recomputes from the registry. It says the claim is *well formed*. It never evaluates the claim |
| Implementation stage | `invariants[].enforcement` and the lane name | Which milestone owns the check: `m0`, `m0_5`, `advisory`, `unproved`. A stage is a position in the delivery plan, not a result |
| Evidence status | `invariants[].evidence`, `invariants[].domain`, and `proof_boundary` | What the check rests on and how much of the population it walks: `verified / registered` under a named `evidence_bound`, classified `established`, `bounded`, `unknown` or `unproved`. Bounded evidence over a sub-domain is not proof over the whole |
| Blocking behaviour | whether a violation makes `examples/semantic-vocabulary-drift-smoke.py` exit non-zero | The only reading that answers "will this stop a merge". It is a property of the calls in the smoke's `main()`, not of any field in the registry |

The four do not move together, and the current tree is the proof of that. F1, F2
and F4 carry implementation stage `m0_5` and sit in the `blocking_next` lane, yet
they block a merge today over the kernel tier and the declared scopes. F3 is
schema-valid, carries an evidence string, and walks nothing. F6 is schema-valid
and has no check at all. A row that validates therefore establishes exactly one
thing: the claim is well formed. Reading a discharged proof, a delivered check or
a merge blocker out of that validation is the failure mode this subsection
exists to prevent, and the regressions in
`tests/architecture/test_semantic_formal_model.py` pin the distinction in code.

These are different proof obligations. M0 establishes owner-set equality,
cross-runtime parity, the declared executable projection, and inventory
computed from the current tracked tree. Fixed literal forms and closed-set carriers provide bounded evidence,
not whole-program proof. M0.5 adds bounded producer and scope checks. Producer
discovery over dynamic code, behavioural equivalence of `same_concept`, and
persisted-reader compatibility remain unproved until their source-to-sink
edges are modelled. The registry stores this proof boundary in
`formal_model`; an `unproved` property is an explicit limitation, never an
implicit pass.


### Soundness, relative completeness, and candidate decisions

The word *complete* is scoped here. Let `U(v)` be the ambient runtime value
space for a vocabulary, `S(v)` its registered admitted set, `P(v)` the values
actually produced, and `O(v)` the values observed by the scanner. The producer
obligation is meaningful only when production is defined over `U(v)`:

```text
P(v) ⊆ S(v) ⊆ U(v)
```

Defining `P(v)` as a subset of `S(v)` in advance would make the first
inclusion tautological. M0 currently establishes only bounded claims about
`O(v)` and registered structural carriers.

For a recognised language fragment `L0` and an exact analyser `A0`, define:

```text
Sound(A0, property, L0)    := A0 accepts c ⇒ property(c)
Complete(A0, property, L0) := property(c) ⇒ A0 accepts c
```

The M0 guard can aim at both properties for its fixed carrier and dispatch
forms. It cannot claim either property for arbitrary dynamic Python or
TypeScript. A value flowing through an alias, configuration, reflection,
external input, or unrecognised syntax belongs to `unknown` until a bounded
analysis accounts for it. Unknown is an evidence result, not proof of absence.

Advisory candidate triage uses one finite disposition:

```text
reuse_existing | extend_vocabulary | create_vocabulary | local_only
external_input | compatibility_only | unknown
```

This makes the *workflow classification* exhaustive even though the program
analysis is not. The registry stores the allowed labels and default, not
per-candidate decisions; this metadata does not enforce candidate handling in
product code. The drift smoke validates the label contract only.
`reuse_existing` requires the same slot, compatible scope, and an equivalent
contract. `extend_vocabulary` requires a witness that
reusing an existing value would collapse two states with different required
behaviour. `create_vocabulary` requires a new semantic domain or independently
owned lifecycle. If the evidence cannot decide among these cases, the default
is `unknown`; the agent must not silently treat an unresolved candidate as a
reuse.

General behavioural equivalence remains undecidable for arbitrary programs, so
`same_concept` is not promoted to a theorem by this schema. It becomes a
blocking property only for a restricted contract with explicit inputs,
outputs, transitions, persistence version and finite test domain. This is the
boundary between a useful proof skeleton and an uncheckable claim of
whole-program semantic convergence.


### State model and schema

`loopx/semantics/vocabulary_v0.json`, `schema_version`
`loopx_semantic_vocabulary_v0`. The key set is closed; an unknown top-level or
vocabulary key fails the smoke.

| Key | Content | Check |
| --- | --- | --- |
| `coverage_floor` | counts of vocabularies, owner symbols, literal-scan fields, projections, relations, schema versions; the scanned suffix set | Actual counts are at or above the floor, declared suffixes cover the floor set, and each floor equals its `COVERAGE_ANCHOR` (I8) |
| `vocabularies.<name>.owners` | `python` and `typescript`, each `path::Symbol` or `null` | Enum members, closed-set members, `Literal` alias, or `as const` array equal `values`; the symbol is defined only in owner modules (I1, I2, I3) |
| `vocabularies.<name>.tier`, `status` | `kernel`, `cross_runtime`, `cross_module`; `canonical`, `legacy`, `merge_candidate` | Closed enumerations |
| `vocabularies.<name>.literal_scan` | `field`, roots, suffixes | Every literal the fixed dispatch forms capture is registered; every registered value is captured or variable-sourced (I2) |
| `vocabularies.<name>.variable_sourced_values` | value to producer module | The producer still contains the quoted value |
| `scope_declarations.<name>` (M0.5a) | `bounded_context` and its context IDs, each with one `module::Symbol` owner | Every declared name resolves to one inventory fork, names every defining module exactly once, and is excluded only from `multi_value_forks_semantic`; undeclared forks remain visible (I14) |
| `vocabularies.<name>.input_producer` | Fixed executable decoder witness, currently `turn_result_kind` only | Every registered input produces the matching typed member and invalid probes reject; arbitrary callable selection is forbidden |
| `vocabularies.<name>.producers` (M0.5) | `path::Symbol` sites that write the field, required for `kernel` | Every site writes registered values only; every value not under `compatibility_only` has at least one source site or executable input witness (I12, I13) |
| `vocabularies.<name>.compatibility_only` (M0.5) | values retained for persisted readers or a legacy typed caller interface | Subset of `values`; zero production sites; each carries a `value_notes` reason and a retirement milestone |
| `formal_model` | finite universes, role relations and hierarchy, semantic obligations, candidate decisions, and established/bounded/unknown/unproved claims | Schema validation only. The drift smoke checks the exact key set, the role hierarchy, the candidate decisions, and that each of F1 to F6 is stated exactly once with a non-empty statement, evidence boundary and derived domain; `tests/architecture/test_semantic_formal_model.py` mutates each of those rules. A validated block is a well-formed claim, never an executed proof, and what blocks a merge is the code in the smoke's `main()`, not this field (Section 5, "Four separate readings of one obligation row") |
| `formal_model.invariants[].domain` | the set the obligation quantifies over: `quantifies_over` selector, `verified` and `registered` sizes, `evidence_bound` | Selector and bound are code-owned names pinned per invariant by `FORMAL_DOMAIN_ANCHOR`; both sizes are derived from the registry and must equal the declared ones; an advisory or unproved stage must declare `verified: 0`, an enforced stage a non-empty domain |
| `formal_model.enforcement_policy` | blocking-now, blocking-next, advisory, and unproved lanes | Every formal invariant appears in exactly one lane and its lane agrees with its `enforcement` stage. The lane records the implementation stage that owns the check, not whether a violation blocks a merge today; the two are tabulated separately in Section 11 |
| `vocabularies.<name>.value_notes`, `deprecated_values` | per-value review notes; values slated for removal | Names must be registered values |
| `relations.same_concept` | groups of `vocabulary.value` members | Every member resolves |
| `relations.shared_field_names` | one field name, its slots and the vocabulary or values each carries | Every slot resolves |
| `relations.subsets` | superset vocabulary, excluded values, owners of the subset symbol | Owner symbols equal superset minus excluded |
| `projections.<name>.mapping` | source value to target value or `null` | Keys equal the source vocabulary; mapped values match the owner function; `null` routes raise (I4) |
| `schema_versions.<name>` | constant name, value, owner modules | The only defining modules are the listed owners and all carry the value (I1) |
| `retirement_ledger.<group>.fields` | per-field Python and TypeScript budgets under two metrics: `*_module_budget` counts modules carrying the field token, `*_migration_surface` counts the modules that read, write or bind it | Actual counts are at or below both budgets; the field set and every token budget match `RETIREMENT_ANCHOR`, every surface budget matches `MIGRATION_SURFACE_ANCHOR`, and the five syntactic roles partition the token count exactly (I5) |
| `dual_runtime_twins` | root and module budget | Tracked same-basename `.py`/`.ts` pair count is at or below budget; root and budget equal their code anchors (I5) |
| `inventory_ratchets` | budgets for same-runtime fork names and definitions, conflicting names and definitions, schema-version forks, multi-value twins and forks, and the shared-vocabulary conflict and fork subsets | Inventory summary counts are at or below budget, and each budget equals its `BUDGET_ANCHOR` entry (I5, I9) |

The inventory retains `schema_version=loopx_semantic_inventory_v0`.
The guard builds it in memory from the complete tracked `loopx/` tree, once per
run, and uses that result for owner, scope and budget checks. No report file is
read. `scripts/generate_semantic_inventory.py` exports the same map on demand. It
lists Python enums, closed sets, `Literal` aliases, TypeScript `as const`
arrays, and duplicate definitions split into cross-runtime twins, same-runtime
forks, conflicting values, and multi-value twins and forks, one entry per line.
Every multi-value collision carries each defining module and its value set, so
the divergence itself is reviewable rather than only its count. Consumer counts
and merge-candidate groups are both printed by `--report`;
`merge_candidate_groups` returns the groups; all inventory output is
uncommitted.
Merge candidates are advisory
because an equal value set is not proof of one concept. The printed list drops
the groups whose names are exactly one registered vocabulary's own owner
symbols: `EffectiveAction` and `EFFECTIVE_ACTIONS` are two runtimes spelling one
registered concept, not two concepts to merge. Calling `merge_candidate_groups`
without the registry keeps the unfiltered list. A dropped pair is already
ruled on, so dropping it retires nothing and classifies nothing; each remaining
group is printed with its names, values, modules, and whether its modules span
both runtimes, which is the shape of a registry gap. Single-module string
constants are counted, not listed.

Values are additive. Removing a value, a field, an owner, or a relation is a
schema reduction and follows the `AGENTS.md` rule: enumerate the affected
surfaces, research producers and readers, lower the floor in the same diff, and
record maintainer approval.

### Command or event lifecycle

The check has one command: run the smoke. It is idempotent and has no side
effects. Failure text names the vocabulary, the offending files, and the values
so the fix is mechanical: register the value, import the constant, or lower the
scope of the change.

### Provider or extension contract

New vocabularies are added by a PR that adds the registry entry, raises the
coverage floor, and, where a TypeScript owner exists, names its `as const`
array. A vocabulary qualifies for curation when it is dispatched on by more
than one module or crosses the Python/TypeScript boundary; everything else is
mapped by the inventory without curation. Adding a carrier is discovered
on the next full-tree scan; genuine shared-contract changes still need review.

## 6. Alternatives and design choices

| Alternative | Why not now |
| --- | --- |
| Unify the three Turn enums into one in a single PR | Breaks I5 style incrementalism; the three enums have different owners and change reasons (settlement, route, controller). Register and project first, then merge only where a projection proves identity (Section 12, Q2). |
| Rely on `mypy` `Literal` types | Does not cover TypeScript, JSON payloads, or the CLI; the drift here lives at exactly those boundaries. |
| Documentation glossary only | Cannot fail a build; the repository already has eleven documents calling themselves a mental model and no glossary, which is the symptom. |
| Generate bindings from the registry immediately | Premature until owners are settled. Generation is M2 and follows the coordination contract precedent. |
| Grep-based lint in CI without a registry | Encodes the allowed set in the linter, which becomes a second registry with no review trail. |
| Extend `maintainability_ratchet.py` instead of a new registry | Its subject is module metrics and dependency direction with per-module ceilings; vocabulary shape needs values, owners, and relations. The two share the ratchet idea, not the data model. Merging exception lifecycles is Q7. |
| Put the scan regex in the registry | A regex in data can be narrowed in the same edit that widens a vocabulary; the M0 review showed the first pattern missed every TypeScript `===` site. Forms are fixed in the smoke and the suffix set is floored. |
| Commit the computed inventory or consumer counts | Structural churn adds merge conflicts without new authority. Compute the full tree and expose optional reports; consumer counts remain advisory. |

## 7. Safety, privacy, and compatibility

- No runtime path imports the registry at M0; product behavior is unchanged
  with the check present or absent.
- The scanner uses `git ls-files --cached -z` and reads the indexed source paths
  from the working tree. Untracked and ignored files are excluded; stage a new
  source path before running the inventory scan. Tracked symlinks and invalid
  Python syntax fail closed. A checkout with Git metadata is required.
- Literal and TypeScript carrier scans recognize both single and double quotes.
  They remain structural text scans, not complete parsers or data-flow analysis.
- Literal-scan roots and suffixes, and the twin root and budget, are anchored in
  code. JSON alone cannot narrow these scopes or raise the twin budget.
- Failure text uses repository-relative source paths and registered identifiers.
- Legacy readers and writers are untouched. Budgets freeze their current spread
  without removing a single reference.
- Mixed versions are not a concern for a build-time check. When M2 introduces
  generated bindings, the generator's `--check` mode and the smoke both run so
  a stale generated file cannot merge.

## 8. Migration and rollback

- **Admission.** M0 lands with the registry and inventory matching the
  baseline exactly, plus two behavior-preserving edits so the owner check is
  green: the duplicate `TURN_ENVELOPE_SCHEMA_VERSION` in `driver.py` becomes an
  import, and the `HANDOFF_MODES` tuple in the authority e2e fixtures is
  derived from the `HandoffMode` enum.
- **Rollback.** Deleting the smoke, the `loopx/semantics/` package, the
  generator, its test, and the `pyproject.toml` line restores the previous
  state with no runtime effect. Later milestones each carry their own rollback
  in Section 11.
- **Point of no return.** None in M0. M3 field removals are the first
  irreversible step and are gated individually.

## 9. Validation and acceptance

| Claim | Test or evidence | Required result | Boundary / exclusions |
| --- | --- | --- | --- |
| Registry and inventory match the code at baseline | `uv run --extra test loopx canary smoke-suite --script semantic-vocabulary-drift-smoke.py` | `ok` with coverage, ratchet, budget, and twin report | Proves parity for registered vocabularies and mapped carriers only |
| Inventory is computed on demand | `uv run python scripts/generate_semantic_inventory.py` | valid JSON on stdout, no repository writes | Full tracked tree, not only the PR diff |
| Scanner classification rules | `uv run --extra test python -m pytest tests/architecture/test_semantic_inventory.py` | pass | Fixture repository; rules from this RFC, not from output |
| A widened `effective_action` set fails closed in Python | Add an unregistered literal via `==`, membership, or conditional expression | Failure names the value and file | Mutation exercise; not a committed test |
| A widened `effective_action` set fails closed in TypeScript | Add an unregistered literal via `===` or a ternary | Same | Same |
| A forked constant fails closed | Redefine `TURN_ENVELOPE_SCHEMA_VERSION` or `HANDOFF_MODES` in a non-owner module, run the smoke | Failure lists the extra defining module or the fork budget | Same |
| Python and TypeScript owners cannot diverge | Remove one entry from a registered `as const` array, or widen a registered enum | Failure names the missing or unregistered value | Same |
| The registry cannot be weakened by data alone | Declare a bare-module owner; drop an owner; narrow suffixes to `.py`; rename a vocabulary another relation references; add an unknown key | Each fails naming the rule | Same |
| A new carrier is visible | Add a tracked enum without exporting a report | Current scan includes it; no freshness-only failure | A duplicate spanning changed and unchanged files still fails its budget |
| Conflicting spellings cannot grow | Add a third value for an already-conflicting name, regenerate | Failure names the definitions budget | Same |
| A multi-value collision cannot grow | Define one closed-set name in two modules with divergent values, or with equal values, and regenerate | `multi_value_forks` or `multi_value_twins` fails naming the new name | Mutation exercise; not a committed test |
| The registry cannot relax its own ratchet | Lower any `coverage_floor` count, raise any `inventory_ratchets` budget, or raise a retirement budget, in the same diff that removes the coverage it counts | `COVERAGE_ANCHOR`, `BUDGET_ANCHOR`, or `RETIREMENT_ANCHOR` fails naming the anchored value | Mutation exercise; moving an anchor is a code edit a reviewer sees |
| A tightened budget cannot drift back to a stale anchor | Lower a registry budget without touching the anchor | Failure says the registry value and the anchor differ | Equality, not `<=`; the fix is to lower the anchor in the same diff |
| The smoke is on the pull-request path | `uv run --extra test python -m pytest tests/architecture/test_semantic_vocabulary_drift.py` | pass; the test is collected by the default `pytest -q` sweep in `python-tests.yml` | The fleet and premerge surfaces are not the obligation (I10) |
| Premerge selects the smoke for a `loopx/` diff | `uv run --extra test loopx canary premerge --changed-file loopx/control_plane/turn_driver/loop_controller.py` | the plan lists `examples/semantic-vocabulary-drift-smoke.py` under `repo-architecture-budget` | Selection is by trigger hint; the pytest wrapper is the guarantee |
| Measurement covers both carrier shapes and filters local naming | `uv run --extra test python -m pytest tests/architecture/test_semantic_inventory.py` | pass, including the collision and module-local-convention fixtures | Rules come from this RFC, not from scanner output |
| No behavior change from the two owner fixes | `uv run --extra test python -m pytest tests/test_loopx_turn_transaction.py tests/test_loop_turn_loop_controller.py tests/test_turn_loop_disposition.py tests/test_loopx_turn_managed_step.py tests/control_plane -k authority` and `uv run --extra test loopx canary premerge --from-git-diff` | pass | Environment failures already present on `main` are excluded when reproduced on a clean tree |
| Docs governance accepts the RFC pair | `python3 examples/docs-governance-smoke.py` | pass | Checks mirror, links, index |
| Consumer roles are reported per site for vocabularies that declare a slot, with the unknown stated (B5, optional) | `uv run python scripts/generate_semantic_inventory.py --report --consumer-evidence` and `uv run --extra test python -m pytest tests/architecture/test_semantic_consumer_report.py` | the report names its coverage and the vocabularies it refused to analyse, then prints read/interpret/pass-through/unknown counts, both unknown shares, and every unknown reason with its site count; the tests pass | Advisory only, and optional in #4447: syntactic use over a two-root scan reach, never data flow and never a gate. Coverage is the 1 of 26 registered vocabularies that declares `literal_scan.field`; the other 25 are reported as `missing_slot_identity` and are not analysed |
| Retirement budgets use standalone field tokens | `count_identifier_modules()` uses identifier boundaries for the six fields | `goal_boundary`: 30 Python modules under the new metric; the old substring metric was 35 | Conservative lexical measure; it removes compound-name false positives but does not prove semantic reader absence |
| The retirement metric separates readers from mentions (B3) | `check_reader_metric()` records every fact that holds of each module carrying one of the six field tokens -- reads, writes, binds, unresolved, mention only -- beside a single-label role partition | `goal_boundary`: 30 token modules resolve to 8 reading, 7 writing, 9 binding, 1 unresolved and 14 mention-only, so its migration surface is 16, not 30; `work_lane_contract` is 29 of 29 | Syntactic use, not data flow. The facts overlap, so they are asserted to *cover* the token population; the role partition is asserted to sum to it and is for ordering and printing only |
| A new reader of a legacy field fails the pull-request path | Add a module reading `payload["protocol_action_packet"]` beyond the budget | `check_reader_metric` fails naming the field and the count | Committed fixture in `tests/architecture/test_semantic_vocabulary_drift.py`; the anchor equality check is the same pattern as `RETIREMENT_ANCHOR` |
| A computed key stays unresolved rather than absent | Count Python mapping accessors whose first argument is not a literal, and TypeScript computed member accesses | 1711 Python sites and 400 TypeScript sites under `loopx/`; a field measured at zero readers is measured against that standing unknown | This is why zero readers cannot by itself authorize a removal (Q11). It is attributable to no field, so unlike a field-specific unknown it never enters a surface. Python subscripts with a computed key are excluded: `rows[index]` and `payload[key]` are the same syntax |
| The module-local convention filter is a code edit | Widen `MODULE_LOCAL_CONVENTION` in `inventory.py` and scan | `*_semantic` budgets fall with no code change elsewhere | Known boundary; the regex is in code so the widening is a reviewed diff, and the unfiltered totals stay budgeted |
| A registered value nobody produces fails (M0.5) | Run the production-form scan on the baseline | Fails naming `effective_action` and `skip`; passes after `skip` is removed or listed `compatibility_only` | First expected I12 failure; a compared-only value is not carried |
| A producer of an unregistered value fails (M0.5) | Write `effective_action: "brand_new"` in a listed producer site | Fails naming the site and the value even though no consumer compares it | I13; production is stricter than comparison |
| A bounded-context name leaves only the semantic fork budget by declaration (M0.5a) | Declare `SOURCE_SURFACES` with its four contexts; separately, rename one definition without declaring | Raw `multi_value_forks` stays 4, `multi_value_forks_semantic` is 3; a rename alone changes neither semantic accounting nor declaration | I14; the honest fix is a registry edit a reviewer sees, the rename is not a repair |
| Historical committed snapshots could become stale across merges | Replay the scanner over the first parent and the merge of the last twenty `upstream/main` merge commits | 8 of 20 merges change at least one carrier | Historical cost motivating Q9; current checks compute the combined tree without a committed snapshot |
| The formal model cannot silently lose a proof obligation | Remove an invariant, role, relation, candidate decision, or proof-boundary category from `formal_model` | The drift smoke fails on the exact formal-model shape | The model is a finite contract and proof ledger; it does not prove the listed properties by itself |
| An obligation cannot claim a domain nobody counts | `uv run --extra test python -m pytest tests/architecture/test_semantic_vocabulary_drift.py -k domain` | Dropping `domain`, inflating `verified` or `registered`, inventing a selector, claiming an unanchored selector or an out-of-stage evidence bound, and an advisory invariant claiming verified members each fail closed | The sizes are derived from the registry, so the check grounds the declared domain in registry data; it does not prove the obligation over that domain |
| Bounded binding forms cannot be loosened into false evidence | `uv run --extra test python -m pytest tests/architecture/test_semantic_producer_binding.py` | pass; every recognized form has a negative twin — a decorated, `async`, generator, rebound, imported or recursive callee, an unordered store, an aliased or escaped container, and an unknown `**` spread each keep the site unresolved | Fixture repository; a site the scan cannot bind stays unresolved with its recorded reason, never dead |
| F1/F2 quantify over exactly what the producer check walks | Same test module: compare `check_producers`' predicate with the declared F1/F2 domain | The vocabularies with `producers` are exactly the `kernel` tier, 6 of 26; the other 20 are all `cross_runtime` | The scan reach bounds the claim further and is reported, not pinned |

Known limits, stated so the check is not over-trusted:

- **Renames launder a collision.** Collisions are keyed by name, so renaming one
  side of a fork lowers the count without removing the drift. The advisory merge
  report is the review aid here; value-set equality cannot be a hard budget
  because `CONFIDENCE_LEVELS` and `EDGE_CASE_COMPLEXITIES` share `high/low/medium`
  while meaning different things. The merge-candidate report alone does not cover
  this limit: it groups *different* names carrying *identical* value sets, while a
  fork is *one* name whose modules disagree, so the grouping never lists a fork.
  `divergent_value_sets(inventory)`, printed by `--report`, is the name-keyed
  companion that lists surviving forks with their count of disagreeing value sets,
  where they were visible only as a number before. It is **not** a rename detector:
  measured, a one-sided rename leaves the name with a single definition, so it
  stops being a fork and drops out of both the budget and this report. The one
  case that does fail closed is a **declared** name, because `scope_declarations`
  names every defining module and a renamed side no longer matches. An undeclared
  one-sided rename, and renaming every side at once, both lower the budget with
  nothing reporting it. That residue is accepted for M0 along with the rest of
  this entry.
- **Single-element carriers are invisible.** A closed set with one string member
  is not a vocabulary, so reducing a two-value set to one removes it from the
  inventory entirely.
- **The literal scan can misread unrelated comparisons on the same line.** A form
  such as `log("effective_action", kind === "repair_required")` is captured as an
  `effective_action` value. Registering the reported value to clear the failure
  would widen the vocabulary, so the correct fix is to register the field name
  and the literal together or restructure the line; the failure text names the
  file so this is visible in review.
- **Anchors are code, not history.** A PR can still move an anchor; it cannot do
  so without editing a named literal next to the registry change. Because the
  check is equality, a stale anchor is impossible, but the anchor also carries
  no memory of the lowest value ever reached; that history is the git log.

## 10. Operational contract

The standard premerge catalog limit increases from 9 to 10 checks so the new
vocabulary check does not displace the existing heartbeat/quota coverage. Quick
and deep tier limits are unchanged.

The check cannot affect a running system: it executes only in tests, premerge,
and CI. Its operator surface is the failure text. No observability, capacity,
or on-call contract applies.

Where it runs, and which surface is the obligation:

| Surface | Trigger | Selection | Role |
| --- | --- | --- | --- |
| `pytest` sweep, `python-tests.yml` | every pull request whose classification runs the Python tests | always collected via `tests/architecture/test_semantic_vocabulary_drift.py` | **The commit-time obligation (I10)** |
| `loopx canary premerge` | local, before opening a PR | `repo-architecture-budget` profile, trigger hints include `loopx/`, `examples/`, `scripts/`, `refactor` | Early local signal |
| Full public smoke fleet | push to `main`, daily schedule, manual dispatch | `examples/**/*-smoke.py` discovery | Post-merge confirmation; not a PR-required check by design |

Before this table existed the RFC said the smoke ran "in premerge and CI". On
the baseline that was true only after merge: premerge did not select the smoke
for a diff touching `loopx/control_plane/` alone, and the fleet workflow is
deliberately not a PR-required check. A fleet-discovered smoke is not a
commit-time check until a required PR job collects it.

**On-demand inventory (Q9).** The former committed snapshot imposed a second
synchronization obligation on otherwise valid PRs. It is removed. Let `f(T)` be
the full tracked-tree inventory and `G(f(T), R)` the existing registry, owner,
scope and budget predicates. Checks still evaluate `G(f(T), R)`; only the extra
condition `I_committed = f(T)` disappears. The same computed map feeds the
checks, so stale or missing local reports cannot hide a new fork. This does not
prove that independently valid branches cannot introduce a semantic conflict
when combined: validate the combined tree normally. Never replace the full-tree
scan with a diff-only scan.

For inspection, run `uv run python scripts/generate_semantic_inventory.py` for
JSON stdout or append `--output .local/semantic-inventory.json` for an optional
report. `--output <path> --check` compares that explicit report without writing;
`--check` alone fails with migration guidance. Reports may be attached to CI
artifacts, but are neither committed nor required to run semantic checks.
Bindings and the glossary remain committed generated contracts with freshness
checks; this decision concerns only the repository census. No new CI job is added.

**Interpreter and checkout.** Run the commands above from the target worktree
with `uv run`; Python compatibility comes from `pyproject.toml` (`>=3.11`),
and the imported LoopX must come from this checkout. Canary normalizes displayed
`python3` commands to `sys.executable`, the interpreter that launched LoopX.
A global installation may scan a different release snapshot even when its Python
is compatible. See [local validation](../../development/testing-and-quality.md#local-validation-environment--本地验证环境)
for setup, interpreter/source readback, and lockfile boundaries. Historical
receipts below retain the commands actually executed.

For an already provisioned environment, `bash scripts/loopx-python.sh --exec
<python arguments>` selects a compatible installed interpreter, including
`.venv/bin/python`, or honors `LOOPX_PYTHON`. That selector installs neither
Python nor dependencies. Fleet and premerge child commands can retain `python3`
because the selected project or CI environment supplies it on `PATH`.

The TypeScript effective-action binding and the [glossary](../../reference/glossary.md)
are generated with `uv run python scripts/generate_semantic_bindings.py`.
Run it after changing the Python owner or registry. Carrier changes are scanned
automatically; exporting an inventory report is optional. The existing drift smoke and PR pytest sweep check freshness;
no additional required CI job is introduced. Install the locked Node dependencies
with `npm ci --ignore-scripts` before running the TypeScript production scan.

## 11. Normative delivery plan

| Milestone | Shipped behavior | Entry gate | Exit evidence | Rollback |
| --- | --- | --- | --- | --- |
| M0 | Registry with 26 vocabularies and 9 relations, computed inventory with optional export, drift smoke with fixed dispatch forms and coverage floor, two owner forks removed, RFC index entry | This RFC opened | Section 9 rows green; 20 mutation classes fail closed | Delete the smoke, `loopx/semantics/`, the generator, and its test |
| M0.5a | `scope_declarations` with `bounded_context` and per-context owners; semantic fork count separated from raw inventory count | M0 merged | Smoke checks every declared context owner; raw `multi_value_forks` remains 4 and `multi_value_forks_semantic` is 3; undeclared forks still fail the budget | Remove the scope declarations and semantic-fork budget |
| M0.5b | `producers` and `compatibility_only` on `kernel` vocabularies; production-form scan with the two role checks (I12, I13); retirement budgets counted by identifier with all six anchors lowered in one diff (Q11); merge-order rule from Q9 written into Section 10 | M0.5a complete; Q9 decided or its interim rule accepted | Smoke green with I11 to I14 enforced; `skip` resolved; Section 9 producer rows green; `turn_route` persistence answered for Q2 | Remove producer fields and role checks; budgets return to the pre-M0.5b anchors |
| M1 | `EffectiveAction` typed enum in one owner module; the replay observation and frontier slots split off (Q6); producers and consumers import it; registry `literal_scan` tightened to the enum | M0.5 merged; owner module chosen (Q3); slot split decided (Q6) | Smoke green; zero bare `effective_action` literals outside the owner; parity fixtures for status/should-run unchanged | Revert to literals; registry keeps the set |
| M2 | Route-to-disposition projection, the `decide_loop_disposition` decision table, and the cross-runtime sets published through a shared contract with generated Python and TypeScript bindings, following the coordination contract generator | M1 merged; Q2 and Q7 decided | Generator `--check` and smoke green; `settlement.ts` and `transaction.py` read the generated set | Regenerate from prior contract |
| M3 | Per-field retirement of legacy should-run fields, one field per PR, budgets lowered to zero and the field removed | Field's migration surface is emptied module by module, and the residual unresolved and dynamic-key evidence is reviewed; a zero count is not by itself the gate | Schema-reduction record per `AGENTS.md`; Appendix B entry | Restore field from the last writer |
| M4 | Twin budget lowered with each replacement-first cutover from the migration RFC | Each cutover PR | Budget edit in the same diff | None needed; budget follows code |

A ratchet without a target is a direction, not a plan. The table below is the
state at which this RFC is complete; each row is a registry budget or a
vocabulary property the smoke can check. Rows marked *open* wait on a Section
12 decision and are the reason the plan is a skeleton until those are recorded.

| Surface | Baseline (`1dc6ad8d8`) | Measured by | Target when this RFC closes | Reached by |
| --- | --- | --- | --- | --- |
| `effective_action` values | 33 literals, no owner symbol | registry `vocabularies.effective_action.values`; `semantic-vocabulary-drift-smoke.py` fails on an unregistered literal | one enum owner; `skip`, `observe_replay`, `block_replay`, and the two `quota_action_selection_*` codes gone from the decision slot; 32 decision values after accounting for the five previously missed producers and retiring the synthetic operator_gate value | M1 |
| `effective_action` slots in one envelope | 3 vocabularies under one field name | no counter: the slot split is a Q6 decision, not a number. Read `relations.shared_field_names` | 1, or a registered union if Q6 keeps the field | M1 (Q6) |
| Turn vocabularies | 3 sets, 28 values, 21 distinct, 7 redundant spellings | registry `vocabularies`; spelling overlap is `relations.same_concept` | 3 sets kept; projection and decision table generated and checked; spellings unchanged unless Q10 sets a merge | M2 (Q2, Q10 *open*) |
| Same-runtime forks, semantic | 18 names | `semantic-vocabulary-drift-smoke.py`: `same_runtime_forks_semantic` | 0 | baseline PRs |
| Conflicting values, semantic | 2 names | `semantic-vocabulary-drift-smoke.py`: `conflicting_values_semantic` | 0 | baseline PRs |
| Multi-value forks | 4 (1 misclassified) | `semantic-vocabulary-drift-smoke.py`: `multi_value_forks` and `multi_value_forks_semantic`. Only the count is printed today; #4614 adds `divergent_value_sets` to name the surviving forks | 0 after `scope` declares bounded-context names | M0.5 + baseline PRs |
| Multi-value twins | 19 | `semantic-vocabulary-drift-smoke.py`: `multi_value_twins` | 0 | baseline PRs |
| Legacy should-run fields | 6 fields, 124 py / 10 ts module mentions | `semantic-vocabulary-drift-smoke.py`: one `<field>.py` / `<field>.ts` pair per field for the token count; one `retirement_role:` line per field and runtime under `--report` for the migration surface and its five roles | 0 fields | M3, gated on emptying the B3 migration surface; the token count stays budgeted until Q11 |
| Merge-candidate groups | 32 unreviewed | `merge_candidate_groups()` in `loopx/semantics/inventory.py`; no command prints it today, and #4630 adds the CLI line. Read the reviewable count, not the raw one -- a registered cross-runtime vocabulary owns both its Python and TypeScript symbols, so those pairs are required by I3 rather than debt | every group classified; only `same_semantics` groups merged | classification PR, then per-group PRs |
| Control-plane py/ts twins | 43 | `semantic-vocabulary-drift-smoke.py`: `independently_maintained` | follows the TypeScript migration RFC; no target here | M4 |

*Measured by* names the command and field that print each surface today, the
way Section 9 names a test for each claim. It deliberately does not carry the
values: a transcribed number is stale on the next merge, and a reader who wants
the current state runs the command rather than trusting a date. Dated values
belong to the delivery tracker, issue #4447, which owns delivery status; this
table stays a contract about where the truth is measured.

Read the reviewable merge-candidate count rather than the raw one. The raw
grouping pairs any two names carrying identical values, which includes the
Python and TypeScript symbols a registered cross-runtime vocabulary is required
by I3 to have. Treating those as debt is a measurement artifact, not drift.

### Two-track execution and enforcement lanes

The roadmap separates repairing existing semantic debt from improving the
measuring apparatus. Track A can proceed without waiting for a design decision:
remove real forks, conflicts, twins, and legacy readers one narrow PR at a time.
Track B improves what the guard can know: scope declarations, bounded producer
analysis, identifier counting, and merge-order handling. Track A reduces the
measured debt; Track B makes that measurement more faithful. M1 and later depend
on Track B where the current measurement is known to be incomplete.

```text
Track A: baseline debt repairs ───────────────────────────────┐
                                                               ├─> M1 typed slots
Track B: scope + producer model + metric boundaries ──────────┘       │
                                                                      ├─> M2 generated projections
                                                                      ├─> M3 legacy retirement
                                                                      └─> M4 runtime twin migration
```

The formal model uses four enforcement lanes so a difficult property does not
become an accidental merge blocker. A lane name records the **implementation
stage** that owns the check. It is not a statement about what blocks a merge,
and the two are listed in separate columns because they have diverged:

| Lane | Properties | Implementation stage | Blocks a pull request today |
| --- | --- | --- | --- |
| `blocking_now` | F5 projection totality | `m0`, delivered | Yes. A source value the projection neither maps nor rejects exits the smoke non-zero |
| `blocking_next` | F1 producer closedness, F2 canonical liveness, F4 scope separation | `m0_5`, delivered for the kernel tier and for declared scopes | Yes, inside their declared domains. An unregistered produced value, a registered kernel value with no observed producer, and a scope declaration that does not name every defining module each exit the smoke non-zero. Outside those domains nothing is walked, which is unverified, not passed |
| `advisory` | F3 consumer domain closedness | `advisory`, no analysis written | No. Consumer and interpreter edges are inventory output; nothing can fail on them |
| `unproved` | F6 persistence/version compatibility | `unproved`, not modelled | No, and it cannot be reported as passed either |

The `blocking_next` row previously read *planned blocking checks after M0.5; not
claimed by M0*. That was true when the lane was named and false once M0.5b
shipped: `check_producers` and `check_scope_declarations` are called from the
drift smoke's `main()`, and by I10 that smoke fails closed on every pull request
that runs the Python tests. The lane name is deliberately unchanged -- it still
records which milestone owns the check -- and the blocking claim has moved to
its own column. A lane is a schedule position; only the code in `main()` decides
what stops a merge.

The exit condition for a phase is its evidence row, not the existence of a
formula or a registry entry. A property moves from `unproved` to `advisory` only
when a bounded source-to-sink analysis exists, and moves to a blocking lane only
after its false-negative boundary is documented and mutation tests cover the
recognised forms. This keeps the contract strict about silent corruption while
allowing incomplete analyses to remain useful without blocking unrelated work.

PR review preserves these lanes. Ordinary changes record their checked scope and
reason, then exit semantic review when no shared contract is affected. Detailed
evidence is limited to affected contracts and may reference existing review
evidence. A scanner blind spot is advisory; missing required validation for a
contract affected by this PR, or a concrete violation, blocks approval with the
contract, triggering change, observed evidence, minimum repair and rerun command.
The global F6 proof gap does not itself block unrelated work or excuse a missing
compatibility check required by the changed contract. See the
[review evidence contract](../../../loopx/capabilities/pr_review_queue/README.md#semantic-alignment-and-ci-constraint-recovery)
for the executable verdict shapes. Model performance remains an empirical
question: compare matched tasks/model/budgets, counting tokens, time, independently
accepted completions, false blocks and missed defects before claiming a benefit.

The phases are therefore:

1. **M0:** keep the current structural guard and make its proof boundary
   explicit.
2. **M0.5:** implement `scope`, producer forms for the four Turn kernel
   vocabularies, and identifier-based retirement counts.
3. **M1:** split the overloaded `effective_action` slots and introduce one typed
   owner after Q3 and Q6 are decided.
4. **M2:** publish the full decision table and both projection hops through a
   generated cross-runtime contract.
5. **M3/M4:** retire legacy fields and reduce Python/TypeScript twins only when
   their reader and migration evidence is complete.

This roadmap is normative for dependencies and exit evidence. Issue #4447 may
carry owners, suggested dates, and operational checklists, but it must not
introduce a competing target state.


## 12. Open decisions

1. **Registry location.** Owner: kernel maintainers. M0 implements
   `loopx/semantics/` because the scope is repository-wide and neither
   `loopx/control_plane/` nor `docs/reference/` is; the package holds only the
   two JSON files and the scanner and is imported by no product code. This is
   a proposal until recorded in Appendix B. Needed before M1.
2. **Merge `LoopXTurnRoute` and `LoopDisposition`?** Owner: Turn driver owner.
   The projection is total but not injective (`blocked` and `wait` both map to
   `wait`), and `stop`, `terminal`, `contract_error` exist on one side only.
   The `same_concept` relations record the four shared verdicts.
   Recommendation: keep both, publish the projection in M2, revisit after the
   managed-step consumer matures. The persistence premise is now established:
   `run_loopx_turn_once` writes `plan: dict(plan)` through the TypeScript journal
   writer, including `plan.route.kind`; `load_loopx_turn_plan_from_journal`
   restores that route. The executor replay regression checks an actual journal
   on disk and the resume reader. Keep the three vocabularies and publish the
   non-injective projection in M2; any later renaming needs a persisted-plan
   migration, not just an in-process enum refactor. This evidence does not prove
   compatibility of every external reader or every other persisted field.
3. **Owner module for `EffectiveAction`.** The implementation uses
   `quota/effective_action.py`, matching the pre-generation option. Its runtime
   callers serialize `.value` to preserve existing strings. TypeScript consumers
   import `quota/effective_action.generated.ts`, generated from that enum with
   member/value parity checked. M2 may generate both bindings from the shared
   contract, retaining the existing import paths. No
   second independent value list may be introduced into a runtime module.
4. **Companion glossary.** `docs/reference/glossary.md` is generated from the
   registry's curated meaning, owner, value and compatibility metadata. It covers
   registered vocabularies; the inventory remains the wider structural map.
   The existing smoke rejects stale output. Edit the owner/registry and regenerate
   rather than maintaining a second prose authority. Owner: docs maintainers.
5. **Term-family naming rule.** Whether new identifiers in the `gate`,
   `scope`, `packet`, `handoff`, `settlement` families must cite a glossary row
   in review. This is a review rule, not a smoke; recommendation is to adopt it
   in the first-review roster once the glossary exists.
6. **Action slot decision (Q6).** Keep the root should-run/Turn Envelope field
   as the anchored, disjoint decision/frontier union. Nested frontier v1 uses
   its existing `action`; journal replay uses its existing `observation.decision`.
   No redundant replacement field is introduced. Preserve historical v0 signed
   capsule fields on read; new writes use the versioned reduced shape. See the
   M1 compatibility table and tests above. This decision does not authorize
   unrelated legacy-field retirement or Turn outcome enum merging.
7. **Relation to `maintainability_ratchet.py`.** Whether the inventory
   ratchets adopt its reviewed exception lifecycle (`retirement_plan`, stale
   exception detection) or stay plain budgets. Recommendation: adopt it in M2
   when generation lands, so a fork with a documented reason can be excepted
   instead of budgeted. Owner: canary maintainers.
8. **Promotion rule from inventory to registry.** Whether a mapped carrier
   with three or more external consumer modules or a cross-runtime twin must be
   curated. Recommendation: yes as a review rule now, enforced by the smoke
   only after a quarter of inventory history exists. Owner: kernel maintainers.
9. **Inventory freshness across merges (Q9).** Adopt on-demand full-tree
   computation and optional untracked reports. Retire the committed census and
   its equality obligation; preserve semantic predicates, roots, floors and
   budgets. This supersedes regenerate-after-merge and explicitly rejects the
   old option's diff-only scan. Section 10 defines commands and proof limits.
10. **Target state for the Turn vocabularies.** Section 11's target table
   keeps three sets and seven redundant spellings by default because Q2
   recommends keeping both. Q2's writer/readback evidence shows that `turn_route`
   is persisted. The implementation therefore retains three distinct value sets
   and generates their projection; it does not merge spellings. A future proposal
   to merge them must provide a dual-read/versioned migration and reader proof.
   Owner: Turn driver owner. Needed before M2 closes.
11. **Retirement budgets by identifier.** The six legacy-field budgets use
   `count_identifier_modules()`, so `goal_boundary_repair` is not counted as
   `goal_boundary`. This is a conservative lexical metric, not proof of zero
   semantic readers. B3 adds `check_reader_metric()` beside it, which records
   every fact that holds of each module -- reads, writes, binds, unresolved,
   mention only -- and budgets as the migration surface every module that is not
   mention-only. That includes the field-specific unknowns: a module holding the
   field name as data, or one the scan could not parse, is work someone must do
   before this field can go, and its shape rather than its existence is what is
   unknown. Both metrics are now checked. What stays open is whether the token
   budget is retired once the surface budget has ordered a removal, and what
   residual evidence a field at zero surface still owes given 1711 Python
   computed-key sites and 400 TypeScript computed members, which belong to no
   field and so can never be retired by emptying one. Owner: kernel maintainers.

## Appendix A: Execution ledger (non-normative)

New entries are files, not sections here. Each one lives in
[`ledger/<rfc-slug>/`](ledger/README.md) as `YYYY-MM-DD-slug.md` with a Chinese mirror
beside it, and nothing enumerates them — the directory listing is the index.

The reason is measured, not stylistic. This section was a single append cluster:
every branch adding an entry inserted at the same position, so concurrent work
conflicted here by construction — eight times in one afternoon during the
#4447 repair round, each resolved by hand as "both sides are disjoint, keep
both". That is mechanical work whose failure mode is silent: one careless
resolution drops an entry nobody notices is gone. A file per entry removes the
shared line, and `examples/docs-governance-smoke.py` checks the naming and the
mirror pairing so the convention cannot rot back.

The entries below predate that split and stay where they are. They are
append-only history, never edited, so they were never the thing that conflicted.

### 2026-09-17 — B3: the retirement metric separates readers from mentions

The six legacy should-run fields were budgeted by a token count: modules whose
text contains the standalone field name. That number answers "does this name
appear here", which is not the question a retirement asks. `check_reader_metric`
classifies the same modules by syntactic use and budgets every module that is
not a bare mention as the work owed before a field can be removed. The role
column below is a single label in a fixed precedence, for ordering and
printing; the counts a retirement reads are the overlapping facts beside it.

| Field | Python token | reader | writer | binding | unresolved | mention | surface | TS token | surface |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `protocol_action_packet` | 5 | 1 | 4 | 0 | 0 | 0 | 5 | 2 | 2 |
| `external_evidence_observation` | 8 | 4 | 1 | 1 | 1 | 1 | 7 | 1 | 1 |
| `heartbeat_recommendation` | 17 | 8 | 4 | 1 | 0 | 4 | 13 | 1 | 1 |
| `execution_obligation` | 20 | 8 | 7 | 0 | 0 | 5 | 15 | 1 | 1 |
| `work_lane_contract` | 29 | 11 | 8 | 9 | 1 | 0 | 29 | 3 | 3 |
| `goal_boundary` | 30 | 8 | 4 | 3 | 1 | 14 | 16 | 2 | 1 |

Three results the token count had hidden:

- `goal_boundary` and `work_lane_contract` were within one module of each other
  at 30 and 29, so the plan ordered them as equally expensive. Their real
  surfaces are 16 and 29. Fourteen of `goal_boundary`'s modules are prompt prose
  and module-path imports that no migration touches; `work_lane_contract` has no
  such module at all.
- `protocol_action_packet` has one Python reader and four writers. It is the
  cheapest first M3 removal, and the token count did not say so.
- 1711 mapping accessors under `loopx/` take a computed key, and 400 TypeScript
  computed member accesses are that runtime's equivalent. No name-keyed scan,
  lexical or syntactic, can attribute them to a field, so neither is part of any
  field's surface and the smoke prints both numbers beside the per-field counts.
  This is the measured form of "a zero count does not authorize a deletion";
  the residual obligation is Q11's.

Five corrections were measured after the first implementation, all of which had
made the surface smaller than the work:

- The TypeScript scan recognized a member access and nothing else, so the
  constructs TypeScript actually uses were classified as prose: a destructuring
  read, an object-literal write, and a declared property signature. Of twelve
  equivalent accesses written for both runtimes, eight disagreed. `{field: x}`
  was a writer in Python and a mention in TypeScript, so porting a dict literal
  across the boundary shrank the surface with nothing migrated, and every one of
  the six fields measured zero TypeScript writers. `execution_obligation.ts` read
  as 0 modules to migrate while `turn_envelope.ts` declared its truncation
  limits. Both runtimes are now asserted to classify the same access the same
  way, which is the property that makes the surface safe to plan against.
- The TypeScript half of the standing unknown was drawn only from modules that
  spelled a field, 4 of 145. Widening it to every tracked module raised the
  count from 82 sites to 395 and cost 0.52s.
- The summary counted role labels. The label is single-valued, so a module that
  both read and wrote the field was counted only as a reader and read/write
  overlap could not appear at all. Each of the five facts -- reads, writes,
  binds, unresolved, mention only -- is now counted separately and a module is
  in every set it belongs to. The sets overlap, so the smoke asserts they
  *cover* the token population rather than sum to it, and the partition keeps
  its own sum-to-token assertion beside them. The overlap is large:
  `heartbeat_recommendation` has 8 modules reading and 8 writing within a
  surface of 13, which the labels reported as 8 readers and 4 writers.
- A module holding the field name as data was reported `unresolved` and then
  excluded from the migration surface, on the reasoning that it was not *known*
  to need migration. It is known to need investigation: nobody can say the field
  is absent from that module without opening it, and that is field-specific work
  either way. Excluding it also let the surface fall when a reader was rewritten
  into a form the scan could not resolve. Field-specific unknowns now count;
  `external_evidence_observation` 6 → 7, `goal_boundary` 15 → 16 and
  `work_lane_contract` 28 → 29 follow, each one module. The repository-wide
  computed-key totals stay outside every surface, because they belong to no
  field and emptying one can never retire them.
- An unparseable tracked Python module was credited as a mention. It may hold
  readers, so calling it prose shrank the surface on the strength of a parse
  failure. It is now that field's unknown and stays in the surface. Raising
  instead was considered and rejected: it fails the scan for a direct caller
  working on a half-written tree, and unknown is the honest classification
  rather than a louder one.

The roles are asserted to partition the token count exactly, per field and per
runtime, on every run. The new metric therefore reclassifies one population
rather than measuring a smaller one, and this slice repays no debt: both
budgets are pinned at their measured values in the same diff.

The partition assigns each module its first matching role, which answers "what
is this module mainly" and not "who writes this field". `work_lane_contract`
has 8 modules whose role is `writer` and 13 that write it; the other 5 also
read it and the partition calls them readers. A retirement looking for every
producer reads the overlapping `reads`/`writes`/`binds` counts printed beside
the partition, not the partition itself.

The check costs 8.5s of a 36.9s guard, the median of five runs that time the
function inside the guard rather than subtracting two whole-guard runs; the five
spanned 8.4-9.2s of 35.8-39.8s, or 21-25% of the guard. Subtraction was tried
first and abandoned: on a shared machine it put the same cost anywhere
between 4s and 32s, because it differences two numbers that both move with
whatever else is running. The scan
must walk every tracked Python module, because the computed-key total is
repository-wide and a module that never names a field still contributes to it.
`parse_python` was factored out of `python_facts` so both scans raise the same
error on an unparseable source, and is deliberately left uncached: holding
roughly two million AST nodes for the rest of the run measured 0.7s worse
overall than parsing twice, and it slowed `check_inventory` from 2.4s to 5.4s,
which is the pass #4628 had just made cheaper.

TypeScript reuses `scripts/semantic_production_scan.mjs` and its TypeScript
parser in one batch. AST property and literal-subscript accesses distinguish
reads, writes and compound updates, including optional access and template
interpolation; comments, quoted examples and regex literals cannot become
accesses or mask later code. Bare object/type keys remain mentions: syntax
alone does not establish that the object carries the retired payload field.
Computed-key data flow and external consumers remain outside this metric;
M3 must inspect those paths before removal. The token budgets are unchanged.

### 2026-09-18 — Three producer-scan answers that were confidently wrong

Normative for what the producer scan may report as complete. No budget, floor or
anchor moved: `unresolved_producer_sites` is 40 before and after, because no
site in the tree exercises these shapes today. The fixes remove a way for a
future edit to pass the gate, not a current violation.

- **Why the direction matters.** F1 proves `Produced_scan(v) ⊆ S(v)`, so the
  dangerous error is a value the scan does not see: an unregistered value then
  passes. Over-reporting can only raise a false alarm. Each case below was a
  *missing* value reported inside a complete-looking set.
- **A `global` rebinding was invisible.** `_module_functions` walks `tree.body`
  and skips into no function, so `global pick; pick = other` inside another
  function never counted as a second binding of `pick`, and a same-module call
  still resolved to the original `def`'s returns for a name the module swaps at
  runtime. A name some scope declares `global` and then stores is now
  disqualified, and the call keeps `call_result`.
- **A `**` spread replayed a stale initializer.** `bound` follows plain
  `name = expression` writes, so a dict mutated afterwards through a subscript
  still resolved to its initializer. `{**overrides}` therefore reported the
  key's original value as the produced one. The per-key union that `lookup`
  applies is unreachable through a spread, which contributes every key at once,
  so a spread of a mutated container now takes the unknown-key answer.
- **The TypeScript scanner had no scope model.** Any identifier spelled `String`
  was read as the builtin conversion and any `undefined` as the literal, so
  `function emit(String)` — a caller-supplied function that can return anything
  — produced a confident value. Shadowing is now detected per file, which is
  coarser than per scope and deliberately so: file granularity can only withhold
  a builtin reading, never invent one.
- **One counterexample was relaxed, not removed.** A literal non-negative
  subscript write *is* modelled, and the read resolves to the union of the
  initializer and the write. Demanding `unresolved` there pinned a weaker scan
  in place, so the assertion now states the property that matters: the reported
  set may over-approximate but must never omit the written value.
- **What this does not establish.** The scan still models no cross-module data
  flow, and file-granular shadow detection will withhold the builtin reading
  from a file that shadows `String` in an unrelated function. Both are
  conservative failures, and both stay measured rather than assumed.

### 2026-09-17 — Two measurements that contradicted themselves, corrected

Normative for what F5's `verified` count means and for closed-set collision
identity. No budget, floor or anchor was relaxed; both changes were reproduced
against `d8e7af141` before being written.

- **A declaration was counting as its own evidence (F5).** The
  `projections[*]` selector returned `len(registry["projections"])` for *both*
  `verified` and `registered`, while `check_projections` imported exactly one
  hardcoded projection. Adding a projection whose owner module and function do
  not exist anywhere in the tree, and declaring the domain as 2/2, passed the
  whole smoke and printed `F5:2/2` under the evidence bound
  `executable_owner_function`. `verified` now counts only the projections named
  in `EXECUTED_PROJECTIONS`, which is code, and each one's registry `owner` must
  equal the function the check imports. The same injection now either fails
  (`claims 2 verified members ... the m0 check walks 1`) or is declared honestly
  and prints `F5:1/2`.
- **Reordering a `set` counted as a semantic fork.** Collision identity used
  `tuple(item["values"])`, which is source order. Swapping three lines inside a
  `set` literal — membership unchanged — turned a twin into a fork and failed
  the gate on three budgets at once, while `divergent_value_sets`, computed from
  the same inventory, correctly reported no divergence: one scan, two outputs,
  and the wrong one held the gate. Identity is now membership when *every*
  definition of a name is a `set`/`frozenset`, and source order otherwise.
- **The normalization is deliberately narrow.** `tuple`, `list` and `as const`
  carriers keep order-sensitive identity, because `LIFECYCLE_PRIORITY` is a
  tuple defined in two modules whose order *is* the priority; normalizing every
  carrier would have replaced a false positive with a false negative. A name
  carried by mixed containers also stays order-sensitive, which makes the rule a
  pure relaxation: it can only merge definitions the old rule split, so no
  untouched tree starts failing.
- **What this does not do.** It does not establish the ordering semantics of
  enums or `Literal` aliases, which carry no container and are left
  order-sensitive; and it does not add a second executed projection. F5 still
  walks one.

### 2026-09-17 — B2: three bounded binding forms, and the residue that stays unresolved

- **Trigger:** [#4447](https://github.com/huangruiteng/loopx/issues/4447) recorded
  the B2 residue as "34 total minus the 15 unprovable by design". Re-measured on
  `9003577f9` the total is **41**, and the breakdown is across every scanned
  vocabulary rather than `effective_action` alone: `annotation_only=5,
  argument_name_only=10, attribute_read=2, call_result=11, other=1,
  typescript_dynamic=8, unstable_local=4`. The issue's number was stale; this
  entry records the measured split.
- **Delivered:** three bounded local forms in `python_production`, each with
  positive and negative fixtures in
  `tests/architecture/test_semantic_producer_binding.py`.
  1. **Same-module call results.** A call to an undecorated, non-generator,
     plainly-defined top-level `def` of the same module resolves to the union of
     that function's own returns. Arguments are never bound to parameters, so a
     returned parameter stays unknown and the answer does not depend on the call
     site; it is memoised per module scan. A decorator, `async def`, a generator,
     a second top-level binding of the name, an imported or attribute call, a
     local rebinding and recursion all keep the `call_result` blocker.
  2. **Ordered rebinding of a local.** A local written more than once resolves to
     the union of the writes that textually precede the read, and only when every
     store of that name is a plain `name = expression` and no write shares an
     enclosing loop with the read. `with`, `except`, walrus, augmented,
     unpacking, `global` and `del` rebindings are not ordered by this scan and
     erase the local.

     Textual position is execution order only where no back edge crosses it. A
     write later in a loop body reaches the read at the top of the next
     iteration, so the preceding-writes filter dropped a live value and reported
     a closed value set that was not closed: a producer emitting an unregistered
     value on every iteration after the first read as fully resolved, which is
     the one failure mode that turns an unknown into wrong evidence rather than
     into a smaller residue. Four shapes are pinned as regressions — a `for`
     back edge, a `while` back edge, a write carried by an outer loop, and a
     `finally` that rebinds — beside two positives that keep the ordering this
     form was built for.
  3. **Key-precise container writes.** A local container mutated only through
     direct literal-key subscript writes keeps its untouched keys, and a written
     key carries the union of its initializer and every write. An alias, a method
     call, a computed or deeper store, a `del`, a negative index, or passing the
     container to any call still discards the container. A negative index names
     the same slot as a non-negative one whose number depends on the container's
     length, so recording it against the key `-1` left a read of `table[0]`
     looking at an initializer the write had already replaced. A `**` spread of statically
     known dict literals is flattened, so an optional spread no longer hides a
     sibling key; an unknown spread still makes every key dynamic.

  The TypeScript parser gains the two sound forms the Python scanner already had
  (`||` and `??` arms, a transparent `String(x)`, and `undefined` read as no
  value) and, more importantly, reports the **same blocker vocabulary**: the
  single `typescript_dynamic` catch-all is replaced by `attribute_read`,
  `call_result`, `unstable_local` and `dynamic_key`, with `typescript_dynamic`
  kept only as the fallback for a form it cannot classify. An owner-member
  result (`enum_result`) now carries its reason too; an unlabelled unknown was
  invisible in the report breakdown.
- **Result:** unresolved sites **41 → 40**, split
  `annotation_only=5, argument_name_only=10, attribute_read=7, call_result=14,
  other=1, unstable_local=3`. The back-edge and negative-index rules were added
  after that measurement and left every number in it unchanged, so no site on
  the tree was resolving through the unsound path: the generality had bought
  nothing that the soundness fix takes away.
- **Three non-blocking review findings closed afterwards, none of which moves a
  number.** `_MODULE_FUNCTIONS` was keyed on `id(tree)`, so its correctness
  depended on `_TREES` never evicting; give that cache a bound and a reused id
  would hand back another file's functions, binding a call to the wrong callee
  with no symptom. It is keyed by path and text hash now, as `_TREES` is.
  `_is_generator` used `ast.walk`, which descends into nested scopes, so a plain
  function that merely defined a generator inside itself read as a generator and
  lost its binding -- safe in direction, but it withheld evidence this slice
  exists to make actionable. And `blockerFor` labelled an object literal, an
  array literal and a template expression `dynamic_key`, which the shared
  vocabulary defines as a computed or non-literal subscript; Python answers
  `other` for the same shapes, so both runtimes now agree. The residue stays at
  40 sites with the same split. All eight TypeScript sites are reclassified (five
  `attribute_read`, three `call_result`); none was resolvable, so that part is a
  taxonomy, not a shrink. The one site that closes is
  `driver.py::build_loopx_turn_plan:500`, which needed all three forms and the
  spread flattening at once. Evidence improves further than the count shows:
  unresolved rows carrying at least one known value go **2 → 7**. Registry
  values, budgets and the producer site list are unchanged, and no site becomes
  newly visible or unregistered.
- **Deliberately not bound, with the reason recorded:**
  - `annotation_only` (5) — all five are bare `effective_action: str` field
    declarations carrying **no value node at all**. Unprovable by design;
    the issue's classification is confirmed.
  - `argument_name_only` (10) — confirmed unprovable by design, with one
    sharpening: these are unprovable as a *production role*, not unresolvable as
    an expression. Four of the ten now carry a fully resolved value set and are
    still correctly unresolved, because the callee (`_execution_obligation` and
    its peers) reads the field rather than emitting it. The honest way to shrink
    this bucket is a registry `call_producers` declaration naming a reviewed
    output builder — a data edit a reviewer sees — never a scanner change.
    Counting any field-named keyword as production would make the obligation
    tautological, which Section 5 forbids.
  - `attribute_read` (7), `call_result` (14), `unstable_local` (3) and `other`
    (1) — every remaining site bottoms out in one of four things outside this
    scan's bound: a read off a caller-supplied mapping or object
    (`decision.get("effective_action")`, `run_decision.effective_action`), a call
    into another module, a returned parameter, or a method chain. Binding any of
    them needs cross-module or object-field resolution, a separate bounded form
    with its own blast radius; it is not attempted here. **A site this scan
    cannot bind stays `unresolved` with its recorded reason — it is never
    treated as dead.**
- **Cost:** the producer scan runs on every pull request touching `loopx/`. Over
  the 319 Python files it reaches and the 5 producer vocabularies, best of three
  runs on one tree: **9.20 s → 7.17 s**. The deepened scan is net faster because
  it now reuses the memoised parse and the module-function table across
  vocabularies instead of re-parsing once per scan.
- **Effect on normative design:** Section 5's bounded producer model names the
  three forms and the shared blocker taxonomy; no invariant or milestone changes.

### 2026-09-17 — B5: consumer roles for the vocabularies that declare a slot

Non-normative; advisory evidence only, and B5 is an optional item of #4447. No
check changes its pass/fail result on the current tree, and nothing added here
gates a merge.

- `consumer_ranking` counts modules that *mention* a symbol. Section 5 already
  says that number "does not classify roles or prove data flow", so B5 adds
  `loopx/semantics/consumer_report.py`: a bounded AST scan that classifies each
  consuming site as `read`, `interpret`, `pass_through` or `unknown` and carries
  per row the location (`module::symbol` and line), the source SHA the scan ran
  against, the value domain, and the limitation that applies to that row.
- **Coverage is the registry's declaration, not a guess, and it is 1 of 26.**
  B5 is gated on concrete slot identity, and only `effective_action` declares
  `literal_scan.field` — the same `literal_scan_fields:1/1` the drift smoke
  already reports. The first implementation fell back to the vocabulary id when
  no field was declared, which analysed 25 vocabularies against a field name
  nobody had claimed exists and let every downstream row inherit the guess. The
  fallback is removed: a vocabulary with no declared slot is reported by name
  under `missing_slot_identity`, counted in the header, and not analysed at all
  — not partially, and not by owner class alone.
- Anchors are identities the registry already carries, which is why B2 was the
  precondition: the declared slot name and the registered owner class, bound
  through the same one-unrenamed-hop import discipline the producer scanner
  uses, and only where no nearer binding has taken the owner's name over. The
  scan reach is code owned exactly as `PRODUCER_ROOTS` is, so registry data
  cannot widen it.
- **A role is claimed only from syntax that establishes it.** A call, an
  f-string and a further attribute all end the climb as `unknown` with the
  construct named. `Kind(value)` and `sink(value)` are the same syntax, so
  neither may be read as proof that the value came out unchanged; the earlier
  implementation reported both as `pass_through`, and `.value` as a
  meaning-preserving enum unwrap on an object it had not established was an
  enum member. `pass_through` now means the AST shows the value itself
  relocated — returned, stored, placed in a structure — with nothing applied.
  An observed branch still wins over an unresolved sibling use, because
  interpreting is the top of the rank and nothing stronger could be hidden.
- Measured on `d8e7af141` across `loopx/control_plane` and `loopx/cli_commands`,
  485 of 1213 tracked sources: **713 rows — 0 `read`, 61 `interpret`,
  9 `pass_through`, 643 `unknown`, a 90.2% unknown share.** Restricted to the
  111 rows that belong to `effective_action`, the unknown share is 36.9%.
  Against the guessing implementation this is 921 rows down to 713 and a 78.9%
  unknown share up to 90.2%: the report became smaller and less confident, which
  is the direction the evidence supports.
- The unknown is most of the measurement, not a residue to tidy away. 602 sites
  read a mapping under a computed key and are therefore unresolved for the
  covered vocabulary; 18 modules spell the slot with no recognized anchor;
  13 sites hand the value to a callee this scan does not follow; 9 tracked
  TypeScript sources carrying the slot are not walked because there is no
  TypeScript parser on this path; 1 is an unstable local. Each is a row with a
  location and a recorded reason, following the pattern B2 set for unresolved
  producer sites, and neither population is filtered out of the printed listing
  any more: the classified rows and the unattributable ones now get the same
  `--top` budget in their own blocks, because a table that showed only what the
  grammar resolved read as a complete census of the slot's readers -- and a
  single location-ordered list would have buried the classified rows under the
  602, which is the same concealment spelled differently.
- What the report establishes is **syntactic use, not data flow**. It does not
  prove the value came from a registered producer, that the branch is reachable,
  or that a vocabulary with no rows has no reader — the computed-key population
  is precisely why that last claim cannot be made from a name-keyed scan.
- Exposed through the existing report surface as
  `scripts/generate_semantic_inventory.py --report --consumer-evidence`, which
  refuses to run without `--report` because it is advisory evidence, not a
  check. The per-site scan costs 4.1-4.4s over three runs on the full tree,
  against roughly 217s for the ranking that `--report` already prints. The drift
  smoke does not call it.
- **What this leaves B5 worth.** One vocabulary, 111 attributed rows, 41 of them
  unknown, and no second vocabulary can be covered until an M1/M3 migration
  declares a slot for it. The honest reading is that the machinery is ahead of
  the registry it reads; the value arrives when the first migration needs it,
  not before.
- Not addressed here: the reach is two roots rather than the tree; TypeScript is
  counted but not parsed; and following a local is one hop, so a value moving
  through two aliases is unknown rather than traced. Widening any of the three
  is a separate change carrying its own risk.

### 2026-09-17 — Per-value meaning for the `cross_runtime` tier

Non-normative for the model; it adds no invariant and changes no existing
check's verdict. What changes is that every registered value now says what
produces it.

- Coverage moves from 68 of 149 values to 149 of 149. The 68 were the whole
  kernel tier, documented by #4625 (the four canonical Turn vocabularies) and
  #4626 (`effective_action` and `lease_action`). The 81 added here are the
  whole `cross_runtime` tier, 20 vocabularies.
- The tracking issue described this remainder as "117 values". That count was
  taken before #4626 merged: 117 is everything #4625 did not cover, which then
  still included `effective_action` (32) and `lease_action` (4). Both are
  kernel-tier and already documented, so the work actually outstanding was
  81 values. The registry is the measurement, not the issue text.
- **What a note is required to say:** which condition produces the value — what
  has to be true at runtime for the code to choose it. Not a restatement of the
  identifier, and not only the disposition that follows. The three sets of notes
  that existed at M0 recorded disposition, which is why a reader still had to
  reconstruct control flow from the generated rule table; that is the failure
  mode being closed. That is the bar review holds a note to; it is not a bar a
  test can decide, and the ratchet below does not claim to.
- Where the producing condition cannot be established, the note says so and names
  the evidence that would settle it, in the form `Unresolved: … Missing
  evidence: …`. Two of the 81 are in that state as measured here, and neither is
  guessed at: `settlement_failure_kind.cancelled` is declared in both owners and
  admitted by the decoders but selected by no branch under `loopx/`, exercised
  only by tests that fabricate it, and carries no `compatibility_only`
  declaration saying it is reserved; `todo_decision_scope_kind.other` is an
  accepted member with no producer, no fallback — a kind outside the set is
  rejected, not coerced to it — and no documented rule for when an author should
  choose it.
- A related boundary the notes now state rather than hide: several
  `cross_runtime` values are **author-declared and only membership-validated**,
  not selected by any branch. All four `goal_amendment_class` values, all of
  `todo_decision_scope_kind` and `todo_decision_scope_granularity`, and
  `delivery_outcome.primary_goal_outcome` are in this class. Their notes say who
  declares the value and against what criterion, cite where that criterion is
  normative, and say plainly that no code branch selects it. This is a real
  property of the tier, and it is the reason `cross_runtime` declares no
  producers and sits outside F1/F2.
- The ratchet is a new file, `tests/architecture/test_cross_runtime_value_notes.py`,
  rather than an addition to the end of `test_semantic_vocabulary_drift.py`,
  where the kernel-tier ratchet lives and where several open branches already
  collide. It derives its population from the registry, so a new `cross_runtime`
  vocabulary is covered without editing the test. It fails a value with no
  `value_notes` entry, an entry that is blank or whitespace, and an unresolved
  marker that does not name its missing evidence; a further test fails if a
  vocabulary is ever registered under a tier neither ratchet walks.
- **Two gates the first revision of that file carried were removed under
  review**, and the review is right. A character floor plus a count of
  non-stopword words claimed to catch a note that only restates its own
  identifier: a word count cannot show that a note names the producing
  condition, and what it reliably changes is to reward padding. A budget pinning
  the unresolved count at 2 claimed to stop "unresolved" becoming the cheap
  default: a cap on honesty buys the smaller count by pressuring the next author
  to invent a producing condition rather than record that the evidence is
  missing, which is the outcome the evidence rules exist to prevent. Both
  obligations remain real and both stay with review; the test now asserts only
  what it can decide from the registry.
- Not addressed here: the notes are prose, and nothing checks them for truth.
  Nothing verifies that a stated producing condition was ever right, or still
  matches the code after the code moves. For the `cross_runtime` tier there is
  no producer scan to check it against, which is the same gap F1/F2's domain
  bounds already disclose.
### 2026-09-17 — Formula, role and enforcement claims separated; formal signature mutated

Normative for the enforcement-lane wording; the checks are unchanged except for
one added rule. Track B slice B0 of #4447.

- **One measured inconsistency, fixed in the prose.** The Section 11 lane table
  glossed `blocking_next` as *planned blocking checks after M0.5; not claimed by
  M0*. F1, F2 and F4 sit in that lane and all three fail closed today: dropping a
  registered value that `executor.py::_run_turn` writes raises `producer writes
  unregistered values`; adding a kernel value nobody produces raises `decoder
  does not produce registered input`; removing one context from the
  `SOURCE_SURFACES` declaration raises `contexts must name every defining module
  exactly once`. Each exits the smoke non-zero, and by I10 the smoke runs on the
  pull-request path. The lane name is a milestone label, so it was kept and the
  blocking claim moved to a column of its own.
- **Four readings now stated separately** wherever I2 and I11 to I14 and the
  lanes are described: schema validation, implementation stage, evidence status,
  and actual blocking behaviour. A validated `formal_model` row establishes only
  that the claim is well formed; it is not a delivered check, not an executed
  proof, and not a merge blocker.
- **The formal signature was almost untested.** One test touched
  `check_formal_model`, and it read two fields of `candidate_decisions`. The key
  set, the five roles, the consumer hierarchy, the six invariant ids, the
  per-invariant shape and the four-lane partition were unmutated.
  `tests/architecture/test_semantic_formal_model.py` adds 26 single-mutation
  regressions, each asserting the checker fails closed naming its own rule.
- **One mutation escaped and the check was tightened.** An exactly duplicated
  invariant entry passed: the id set and the lane partition are both sets, so a
  repeat leaves them unchanged, and every dict `check_formal_model` builds by id
  keeps the last occurrence only. A second `F1_producer_closedness` carrying a
  weaker statement validated, and nothing recorded which of the two the smoke had
  walked. The list must now state each id exactly once.
- **Not addressed here.** The grounding gap the 2026-09-17 domain entry named is
  still open: moving an invariant's `enforcement` and its policy lane together
  stays internally consistent, so a coordinated two-field edit can still
  downgrade a check without any test failing. Closing it needs the lane to be
  derived from the code that runs, not declared beside it. B0 narrows the gap to
  a coordinated edit and documents the residue; it does not close it.

### 2026-09-17 — Invariant statements bounded to their verified domains

Normative; requires kernel-maintainer approval. No check changes its pass/fail
result on the current tree; what changes is what the invariants claim.

- F1 and F2 were unconditional over `V` while `check_producers` skipped every
  vocabulary without `producers` — 20 of 26, the entire `cross_runtime` tier.
  Both are now stated over `Kernel(V)` and over `Produced_scan(v)`, the
  production observed inside the code-owned scan reach, which is 432 of 1203
  tracked `loopx/**/*.{py,ts}` files. `validate_production`'s own docstring
  already disclaimed whole-program closedness; the statements now agree with it.
- F4 was `conflict := collision ∧ scope_overlap`, a definition that cannot be
  violated because scope is declared and never inferred. It is restated as the
  enumeration-completeness property `check_scope_declarations` really enforces.
- Every obligation gains `domain` (`quantifies_over`, `verified`, `registered`,
  `evidence_bound`). Both sizes are derived from the registry on each run, and
  the selector/bound pair is pinned per invariant by `FORMAL_DOMAIN_ANCHOR`, so
  an invariant cannot widen the set it claims through a data-only edit.
- The report prints the domain sizes, the scan reach, and how many unresolved
  producer sites can never become evidence (15 of 41).
- The `continue` comment in `check_producers` said the skipped vocabularies were
  "other kernel families". They are not kernel at all; the comment is corrected.
- Not addressed here: `check_formal_model` still accepts an internally
  consistent false claim, because moving an invariant's `enforcement` and its
  policy lane together stays self-consistent. That grounding gap is separate.

### 2026-09-16 — B2 pilot: one re-export hop bound in the Python producer scanner

- **Trigger:** after M2 moved the three Turn owners into
  `turn_contract_generated.py`, every production site that still imported an
  owner through the `transaction.py` / `driver.py` compatibility re-exports
  became `unknown_producer` (43 → 52 unresolved sites) with no code change in
  those modules and no failing check, because the scanner bound an owner only
  when imported from the owner's own module.
- **Delivered:** `python_production` binds one unrenamed re-export hop
  through a tracked module; a second hop, a renamed re-export, a same-name
  class or assignment, or a later `import` leaves the consumer unknown, with
  positive and negative fixtures. Only names that are owner symbols are
  followed, so the full smoke keeps its runtime. The two executable input
  witnesses are selected from one code-owned table keyed by the registered
  `input_producer` site, and the smoke keeps one anchor for both instead of
  four literal copies. Unresolved sites: 52 → 41; no site becomes newly
  visible or unregistered; registry values and budgets are unchanged.
- **Effect on normative design:** the bounded producer model in Section 5
  names the one-hop rule explicitly; no invariant or milestone changes.

### 2026-09-16 — Review consistency repair

- Keep one candidate-decision section per language.
- Use `L` for source sites, `U(v)` for ambient values and `S(v)` for admitted
  values throughout the registry and narrative. Production is not admitted by definition.
- Clarify candidate dispositions as advisory metadata; no per-candidate runtime
  store or enforcement is delivered by this schema.

### 2026-09-15 — M0 opened with the RFC

- **Baseline:** `1dc6ad8d8`
- **Delivered:** registry with four vocabularies, one projection, one schema
  version, six legacy-field budgets, one twin budget; drift smoke; duplicate
  `TURN_ENVELOPE_SCHEMA_VERSION` in `driver.py` replaced by an import.
- **Evidence:** Section 9 rows; see Appendix C.
- **Known gaps:** the projection check imports the private
  `_route_to_disposition` until M2 publishes it.
- **Effect on normative design:** none.

### 2026-09-15 — M0 revised after review; scope made repository-wide

- **Baseline:** `1dc6ad8d8`
- **Trigger:** a review found the first literal scan blind to TypeScript
  (`===` never matched), two unregistered values already on the baseline
  (`observe_replay`, `block_replay`), and an owner check that silently skipped
  any owner written without a symbol.
- **Delivered:** registry moved to `loopx/semantics/vocabulary_v0.json` and
  widened to 26 vocabularies, 46 owner symbols, 9 relations, and a coverage
  floor; generated inventory `inventory_v0.json` with generator `--check` and
  unit test; smoke rewritten with fixed dispatch forms (comparison, assignment,
  ternary, membership, conditional expression), AST-based owner resolution,
  owner exclusivity, inventory freshness and fork/conflict budgets; the
  `HANDOFF_MODES` fixture fork derived from the enum.
- **Evidence:** Appendix C, E6 to E10.
- **Known gaps:** the load-bearing `decide_loop_disposition` table is still
  prose only (M2); the literal scan cannot attribute a literal to one of the
  three `effective_action` slots (Q6); the scan cannot follow values through
  variables, so two quota error codes are registered as variable-sourced with
  a producer check rather than proven.
- **Effect on normative design:** Sections 1 to 5, 8, 9, 11, 12 revised;
  I8 added. Recorded as the same-day revision of an unmerged draft.

### 2026-09-15 — M0 measurement repaired after a second review

- **Baseline:** `1dc6ad8d8`
- **Trigger:** a second review ran 14 attacks against the smoke. Seven escaped:
  lowering `coverage_floor` (individually or all at once), raising
  `inventory_ratchets` or a retirement budget, and — decisively — dropping an
  owner *and* lowering the matching floor in one diff. The floor lived in the
  same file it guarded and was compared with `>=`, so the registry could relax
  its own ratchet. I5 and I8 were prose, not machine-enforced.
- **Also found:** collision detection ran only over string constants, so the 599
  multi-value carriers were listed but never compared. Four same-name forks were
  already on the baseline, including `SOURCE_SURFACES` defined four times with
  four different value sets, plus 19 invisible twins. Separately, 16 of the 18
  `conflicting_values` names were module-local conventions (`SCHEMA_VERSION`
  sixteen times, `COMMAND`, `REQUEST_SCHEMA`, `SURFACE`), so the budget was
  mostly measuring local naming.
- **Delivered:** anchors `COVERAGE_ANCHOR`, `COVERAGE_SUFFIX_ANCHOR`,
  `BUDGET_ANCHOR`, `RETIREMENT_ANCHOR` in the smoke, closing all seven escapes;
  `multi_value_name_collisions` giving enums, closed sets, `Literal` aliases, and
  `as const` arrays the string-constant collision rule, with the four forks and
  19 twins budgeted at today's count; `MODULE_LOCAL_CONVENTION` keeping
  module-local names in the visible totals but out of the semantic budgets
  (`conflicting_values_semantic` 2, `same_runtime_forks_semantic` 18); advisory
  merge-candidate report over the 32 groups of distinct names sharing a value
  set; two scanner tests on a dedicated collision fixture; I9 added and the
  Section 9 limits stated.
- **Evidence:** Appendix C, E11 to E13.
- **Known gaps:** collisions are keyed by name, so a rename still launders one;
  single-element carriers are invisible; the literal scan can misread an
  unrelated comparison sharing a line.
- **Effect on normative design:** I5 and I8 restated as enforced rather than
  intended; I9 added; the Section 5 table and Section 9 rows updated. Moving an
  anchor is now the only way to relax a budget, and it is a code edit.
- **Open question sharpened:** Q7 may now collapse from "adopt the
  `maintainability_ratchet` exception lifecycle" to "share its anchor pattern",
  because this smoke already uses that pattern.

### 2026-09-15 — M0 placed on the pull-request path after a third review

- **Baseline:** `1dc6ad8d8`
- **Trigger:** a third review asked where the smoke actually runs. A premerge
  plan for a diff touching only `loop_controller.py` and `turn_envelope.ts`
  listed 32 commands and not this smoke; `full-public-smokes.yml` triggers on
  push to `main` and a daily schedule and is documented as intentionally not a
  PR-required check. The only surface that runs on every pull request is the
  `pytest` sweep, and the committed test covered the scanner on fixtures only.
  The RFC's "commit-time" claim therefore held only after merge.
- **Also found:** every anchor compared with `<=` (or `>=` for floors), copied
  faithfully from the `RFC_MODULE_BUDGETS` precedent. A budget tightened below
  its anchor in one PR could be raised back to the anchor in a later PR with no
  code edit, so the ratchet stalled at whatever value the anchor last held.
- **Delivered:** `tests/architecture/test_semantic_vocabulary_drift.py` runs
  the smoke as a subprocess inside the default sweep; the smoke is added to the
  `repo-architecture-budget` premerge profile beside the maintainability
  ratchet; all three anchor comparisons become equality; I10 added; Section 10
  gains the surface table.
- **Evidence:** Appendix C, E14 to E16.
- **Known gaps:** the pytest wrapper costs about three seconds per sweep;
  premerge selection still depends on a trigger hint matching the changed path.
- **Effect on normative design:** I5 restated as equality with the reason for
  departing from the precedent; I10 added; Section 9 gains three rows; Section
  10 rewritten from one sentence to a surface table.

### 2026-09-15 — M0 reviewed a fourth time: scope, merge order, target state

- **Baseline:** `503991dd2` merged; `upstream/main` at `2f84af990`, twelve
  commits ahead of the branch.
- **Trigger:** a fourth review asked what the guard's inputs depend on and
  what "repository-wide" covers. Merging the twelve upstream commits into a
  scratch tree staled the inventory (one enum, three closed sets); replaying
  the scanner over the last twenty upstream merges showed eight would have
  done the same. The RFC said repository-wide while the inventory root and
  every literal scan said `loopx/`; `examples/` holds a dozen
  `effective_action` assertions and `apps/` about ninety TypeScript files the
  smoke never reads.
- **Also found:** `SOURCE_SURFACES` is four CLI commands each listing its own
  data sources, not a fork; the name-keyed rule cannot express that. Retirement
  budgets count substrings (35 vs 30 identifier modules for `goal_boundary`).
  The plan had budgets but no target state, and its four entry decisions had
  no owner deadline.
- **Delivered:** Section 3 fixes the scan root to `loopx/` and names `apps/`
  and `examples/` as non-goals; Section 5 previews the M0.5 `scope` field with
  `SOURCE_SURFACES` as the first case; Section 9 gains three known-boundary
  rows; Section 10 gains the merge-order hazard and interpreter paragraphs;
  Section 11 gains the target-state table; Section 12 gains Q9 to Q11 and a
  verification note on Q2; the registry's `inventory_ratchets` gains a note on
  the misclassified fork. No code or budget changed.
- **Not done on purpose:** the premerge planner keeps `python3`, because every
  fleet command is spelled that way and the runner smoke asserts the text; the
  interpreter requirement is documented instead.
- **Evidence:** Appendix C, E17 to E20.
- **Effect on normative design:** Section 3 scope narrowed to match the code;
  Section 11 now has a definition of done; Section 12 gains three decisions.

### 2026-09-15 — Role and scope models written into the contract

- **Trigger:** the RFC used "producer" and "consumer" nineteen times without
  defining either, Q2 and Q10 depended on "a producer check" the document
  never specified, `scope` existed only as a preview paragraph, and Section 1
  still said the smoke ran "on every premerge and full-public run" after
  Section 10 had made the pytest sweep the obligation.
- **Delivered:** Section 5 gains "Roles of a vocabulary" (owner, producer,
  interpreter, pass-through) and three schema rows (`scope`, `producers`,
  `compatibility_only`); Section 2 gains I11 to I14, each marked as enforced
  from M0.5; Section 9 gains three M0.5 rows; Section 11 gains the M0.5
  milestone and M1 now gates on it; Q2 and Q10 point at I12 instead of an
  undefined check; Section 1 matches Section 10. No code, registry value, or
  budget changed; the M0 smoke does not yet enforce I11 to I14.
- **Effect on normative design:** four invariants added with an explicit
  enforcement milestone; the plan gains a definition of "produced" that M3's
  zero-reader gate and Q2's persistence question can both use.

## Appendix B: Decision log

**What the "Owner / approval" column records, and when it is updated.** The
column records **acts that can be verified from the repository** -- the merge
commit, and the approving review where one exists -- not a status at the time the
row was written. Two rules follow, both learned from this table going wrong in
both directions:

1. **A row is updated in the PR that lands the change**, in the same diff. Every
   row here once said "PR review pending" or "approval not yet given" while its
   change was already on `main`; nothing updated the column at merge, so the
   ledger stated the opposite of the tree. A row that freezes at authoring time is
   worse than no row, because a reader treats a closed-looking record as settled.
2. **A merge and an approving review are different acts and are named
   differently.** Where Section 5 requires kernel-maintainer approval, record
   which one actually happened. Do not write "approved" from an instruction given
   outside the repository, and do not leave "not yet given" standing after the
   change has landed -- both have happened on this table, and each misleads a
   reader who cannot see what was said elsewhere.

| Date | Decision | Owner / approval | Alternatives | Normative sections changed |
| --- | --- | --- | --- | --- |
| 2026-09-16 | Q9: compute the full inventory on demand; retire the committed census | Implementation for [maintainer feedback](https://github.com/huangruiteng/loopx/pull/4360#issuecomment-5692062394); landed in [#4494](https://github.com/huangruiteng/loopx/pull/4494), merge `75fcd5556` | Committed snapshot with post-merge regeneration; diff-only scan rejected | 1, I6, 3, 5, 9, 10, 12 |
| 2026-09-16 | B2: bind one unrenamed re-export hop in the Python producer scanner | Implementation, Refs [#4447](https://github.com/huangruiteng/loopx/issues/4447) B2; landed in [#4573](https://github.com/huangruiteng/loopx/pull/4573), merge `6979d528b`, approved by @huangruiteng | Require every consumer to import the owner module (fragile; failed silently in M2); unbounded multi-hop resolution rejected | 5, Appendix A |
| 2026-09-17 | B3 repair: count all five orthogonal use facts rather than the role labels, and put field-specific unknowns inside the migration surface | Implementation, Refs [#4447](https://github.com/huangruiteng/loopx/issues/4447) B3; raises `goal_boundary` 15 to 16, `work_lane_contract` 28 to 29 and `external_evidence_observation` 6 to 7, each being one module that carries the field name as data, with token budgets and carrier counts unmoved; per Section 5 this needs kernel-maintainer approval. **What is on record is a merge, not an approving review:** [#4651](https://github.com/huangruiteng/loopx/pull/4651) was merged by @huangruiteng in `02cc53bd5` on 2026-09-18 while a `CHANGES_REQUESTED` review still stood, and no approving review exists. Whether a maintainer merge satisfies Section 5 for a budget raise is the maintainer's call; this row records the act, not a conclusion about it | Keep counting the role labels and add an overlap column (rejected: the label is single-valued, so any count built from it under-reports whichever fact sorted second, and one more column would not change that); leave `unresolved` outside the surface as "not known to need migration" (rejected: it is known to need investigation, and a budget that excludes it falls when a reader is rewritten into a form the scan cannot resolve); raise on an unparseable module (rejected: it fails the scan for a direct caller working on a half-written tree, and unknown is the honest classification, not a louder one); rely on the cross-runtime equivalence test alone (rejected: it asserts agreement, and two runtimes that both read a destructuring as prose agree) | 11, Appendix A, Appendix B |
| 2026-09-17 | B3: budget the migration surface beside the token count; keep both until Q11 | Implementation, Refs [#4447](https://github.com/huangruiteng/loopx/issues/4447) B3; landed in [#4651](https://github.com/huangruiteng/loopx/pull/4651), merge `02cc53bd5` | Replacing the token budget outright (rejected: the token count is the anchor that proves the new roles partition the same population, and dropping it in the same diff that introduces them would make the smaller number unauditable); counting Python computed-key subscripts as unresolved reads (rejected: `rows[index]` and `payload[key]` are one syntax, and the unknown would stop carrying information; TypeScript has no mapping-accessor convention, so its computed member access is counted separately and stated as an upper bound) | 5, 9, 11, 12 |
| 2026-09-16 | B1 rename invariance: add the name-keyed divergence advisory; state the limit it does not close | Implementation, Refs [#4447](https://github.com/huangruiteng/loopx/issues/4447) B1; landed in [#4614](https://github.com/huangruiteng/loopx/pull/4614), merge `0a4917956`, approved by @huangruiteng | Keying the budget on value sets (rejected: `CONFIDENCE_LEVELS` and `EDGE_CASE_COMPLEXITIES` share `high/low/medium` with different meanings); a committed name ledger (rejected at M0: Q9 retired the committed census). The advisory lists surviving forks by name; it was first described as catching a one-sided rename, which measurement disproved, so both mirrors state the limit as it behaves | 9 |
| 2026-09-17 | B2: bind same-module call results, ordered local rebinding and key-precise container writes; reclassify the TypeScript residue rather than shrink it | Implementation, Refs [#4447](https://github.com/huangruiteng/loopx/issues/4447) B2; landed in [#4682](https://github.com/huangruiteng/loopx/pull/4682), merge `14a766e50`, approved by @huangruiteng | Bind cross-module calls and object fields (rejected: a separate bounded form with its own blast radius, not this slice); count a field-named keyword as production (rejected: it makes the obligation tautological, Section 5); leave `typescript_dynamic` as one catch-all (rejected: eight sites shared one reason, so the residue was not actionable); bind the callee's parameters to the call-site arguments (rejected: the answer would depend on the caller and could not be memoised, and a wrong binding would invent evidence) | 5, 9, Appendix A |
| 2026-09-17 | B5 (optional): report consumer roles per site only for vocabularies that declare `literal_scan.field`, and state the unknown share instead of classifying everything | Implementation, Refs [#4447](https://github.com/huangruiteng/loopx/issues/4447) B5; landed in [#4663](https://github.com/huangruiteng/loopx/pull/4663), merge `736299027`, approved by @huangruiteng | Fall back to the vocabulary id when no slot is declared (rejected on review: it analysed 25 of 26 vocabularies against a field name nobody declared, and every row downstream inherited the guess; they are now named under `missing_slot_identity` and not analysed); register consumers in the registry (rejected: the tracking issue forbids blanket consumer registration, and a declared list is a claim rather than evidence); make the report a merge gate (rejected: F3 is the advisory lane, and a 90.2% unknown share cannot gate anything); report only the sites the grammar resolves (rejected: the tables would read as complete, so an unrecognized mention and a computed-key read are printed rows with reasons) | 9, Appendix A, Appendix B, Appendix C |
| 2026-09-17 | B0: state schema validation, implementation stage, evidence status and blocking behaviour separately for I2/I11-I14 and the enforcement lanes; require each formal invariant id exactly once | Implementation, Refs [#4447](https://github.com/huangruiteng/loopx/issues/4447) B0; landed in [#4661](https://github.com/huangruiteng/loopx/pull/4661), merge `2303ff033`, approved by @huangruiteng | Rename the `blocking_next` lane to match its behaviour (rejected: the lane name is the milestone that owns the check, and renaming it would lose that and collapse the two readings the other way); add a `blocks_today` boolean to `formal_model` (rejected: it would be one more declared field a reader could mistake for a measurement, and the fact is a property of the smoke's `main()`, which no registry edit can change); leave the lane gloss and note the gap in the ledger only (rejected: the gloss is the sentence a reviewer quotes) | 2, 5, 11, Appendix A, Appendix B |
| 2026-09-17 | Bound F1/F2 to the kernel tier and the scan reach, restate F4 as scope enumeration completeness, and give every obligation a derived `domain` | Implementation, Refs [#4447](https://github.com/huangruiteng/loopx/issues/4447); **approved** by @huangruiteng on [#4631](https://github.com/huangruiteng/loopx/pull/4631), merge `440b002fb` (2026-09-17); the approving review is the recorded authorization | Leave the unconditional statements and record the gap in prose only (rejected: the statement was stronger than `validate_production`'s own docstring); restate F4 as per-context value-set disjointness (rejected: refuted by the repo's own data, since `scope_declarations` exists to permit legitimate same-name reuse); widen the scan so the unconditional claim becomes true (rejected: a separate change with its own risk) | 5, 9, Appendix B, Appendix C |
| 2026-09-17 | Document every `cross_runtime` value with the condition that produces it, taking per-value coverage from 68/149 to 149/149, and ratchet it in a separate test file | Implementation, Refs [#4447](https://github.com/huangruiteng/loopx/issues/4447) Track A; landed in [#4662](https://github.com/huangruiteng/loopx/pull/4662), merge `11b857dec`, approved by @huangruiteng | Append to the kernel ratchet at the end of `test_semantic_vocabulary_drift.py` (rejected: three open PRs already collide on that tail, and a same-diff rule is exactly what a merge there loses); infer a meaning for the two values with no producer (rejected by the evidence rules: a guessed note is indistinguishable from a verified one once it is in the table); document only the values a branch selects (rejected: it would leave the author-declared values looking undocumented rather than declared, which is the more useful fact); enforce the "not a restatement" bar with a character floor plus a non-stopword word count, and cap the unresolved count at 2 (both rejected under review: a word count cannot show that a note names the producing condition and only rewards padding, and a budget on honesty pressures the next author to invent a condition rather than record missing evidence) | Appendix A, Appendix B |

## Appendix C: Evidence registry

| Evidence id | Claim | Baseline / environment | Artifact or command | Result | Privacy / validity boundary |
| --- | --- | --- | --- | --- | --- |
| E1 | Three definitions of the envelope schema constant | `1dc6ad8d8` | `rg -n 'TURN_ENVELOPE_SCHEMA_VERSION\s*=' loopx` | 3 files | Source only |
| E2 | 28 distinct `effective_action` literals across `loopx/` | `1dc6ad8d8` | the smoke's `literal_scan` | 28 | Pattern-bound; prose mentions excluded |
| E3 | 43 py/ts twins under the control plane | `1dc6ad8d8` | smoke twin report | 43 | Same-basename rule only |
| E4 | Legacy field spread | `1dc6ad8d8` | smoke budget report | see registry | Module mentions, not call sites |
| E6 | Global census of closed-set carriers | `1dc6ad8d8` | `python3.11 scripts/generate_semantic_inventory.py` summary | 102 enums, 490 closed sets, 8 aliases, 40 arrays, 2002 named constants, 166 twins, 25/58 forks, 18/59 conflicts | AST and `as const` text scan; module-level only |
| E7 | First scan pattern captured zero TypeScript sites | `1dc6ad8d8` | pattern applied to every `.ts` line containing `effective_action` | 0 of 7 dispatching files matched; `===` always failed | Pattern-bound |
| E8 | Two `effective_action` values unregistered on baseline while the first smoke was green | `1dc6ad8d8` | `turn_journal.ts:656` ternary | `observe_replay`, `block_replay` | Same |
| E9 | Owner check skipped a bare-module owner | `1dc6ad8d8` | first smoke's `if "::" in python_owner` | `effective_action` owner never checked | Code reading plus mutation |
| E10 | Twenty drift mutations fail closed (TS `===`, TS ternary, Python membership, Python `==` through `or ""`, bare owner, dropped owner, narrowed suffixes, renamed vocabulary, forked symbol, TS value removed, enum widened, projection changed, legacy field regrown, stale inventory, third conflicting spelling, dead value, lost variable producer, broken subset, forked schema version, unknown registry key) | `1dc6ad8d8` + local edit, inventory regenerated where the edit adds a carrier, restored after each run | temporary edit then the smoke with `python3 -B` | 20/20 exit 1 naming the rule, value, or file | Local exercise, not a committed test |
| E5 | Nine drift mutations fail closed (unregistered literal with and without digits, forked constant, TS kind removed, Python enum widened, projection changed, legacy field regrown, dead registry value, new py/ts twin) | `1dc6ad8d8` + local edit, restored after each run | temporary edit then the smoke, run with `python3 -B` | 9/9 exit 1 with the offending value or file named | Local exercise, not a committed test; a same-size same-second edit needs `-B` to defeat stale bytecode |
| E11 | The registry could relax its own ratchet in one diff | `1dc6ad8d8` + local edit | fourteen registry mutations: lower one floor, lower all floors, lower a floor while dropping the owner it counts, raise every `inventory_ratchets` entry, raise one entry, raise a retirement budget | 7 escaped before the anchors, 0 escape after; each caught failure names the anchored value | Local exercise, not a committed test |
| E12 | 599 multi-value carriers were listed but never compared | `1dc6ad8d8` | collision rule applied to enums, closed sets, `Literal` aliases, and `as const` arrays | 4 same-name forks (10 definitions) and 19 twins already on the baseline, none budgeted; `SOURCE_SURFACES` alone has four divergent value sets | Name-keyed; a rename removes a name from the comparison |
| E14 | The smoke was not on the pull-request path | `1dc6ad8d8` + M0 | `loopx canary premerge --changed-file loopx/control_plane/turn_driver/loop_controller.py --changed-file loopx/control_plane/quota/turn_envelope.ts`; `.github/workflows/full-public-smokes.yml` triggers | 32 commands planned, smoke absent; fleet runs on push to `main` and schedule only | Selection by path token; CI wiring read from the workflow files |
| E15 | A tightened budget could drift back to its anchor | `1dc6ad8d8` + M0 | `ratchets[key] <= BUDGET_ANCHOR[key]` and `floor[key] >= anchored` in the smoke | any value between the tightened budget and the anchor passed | Code reading; the precedent uses the same comparison |
| E16 | Equality closes the stall and the wrapper reaches the sweep | `1dc6ad8d8` + M0 | lower one `inventory_ratchets` entry with the anchor untouched, then `pytest tests/architecture/test_semantic_vocabulary_drift.py` on the clean tree | the mutation fails naming both values; the wrapper passes in about three seconds | Local exercise plus committed test |
| E17 | Upstream merges stale the committed inventory | `upstream/main` `2f84af990`, last 20 first-parent merges | scanner facts of every changed `loopx/**/*.{py,ts}` compared between first parent and merge | 8 of 20 merges change at least one carrier; the branch's own upstream sync added 1 enum and 3 closed sets | Facts-level comparison, equivalent to a full regenerate |
| E18 | Declared scope exceeded the scan root | `503991dd2` + M0 | `literal_scan.roots` and inventory `root` read from the registry; `grep` for `effective_action` dispatch literals under `examples/`; count of `.ts`/`.tsx` under `apps/` | roots are `loopx` only; 12+ assertions in `examples/`; 90 files in `apps/` | Consumers and test doubles, not producers |
| E19 | `SOURCE_SURFACES` is four bounded contexts, not a fork | `503991dd2` | the four `multi_value_forks` definitions read from the inventory | each module lists the data sources of its own CLI command with disjoint values | Judgement from reading the values; the rule cannot make it |
| E20 | Retirement budgets over-count by substring | `503991dd2` | `'goal_boundary' in text` vs `\bgoal_boundary\b` over `loopx/**/*.py` | 35 vs 30 modules | Identifier count is the M3 gate's measure |
| E21 | F1/F2 were unconditional but verified over one tier | `3ca868193` | `check_producers`' skip predicate, and the producer scan roots, read from the tree | 6 of 26 vocabularies declare `producers`, exactly the `tier: kernel` ones; the 20 skipped are all `cross_runtime`; the scan reaches 432 of 1203 tracked `loopx/**/*.{py,ts}` files (35.9%), the uncovered bulk being capabilities 285, other control-plane 192, extensions 83 | Counts from the registry and the tracked tree; the reach denominator moves with any new module, so it is reported, not pinned |
| E22 | Fifteen reported unresolved sites can never become evidence | `3ca868193` | smoke report `unresolved_producer_blockers` | 41 unresolved sites, of which `argument_name_only` 10 and `annotation_only` 5 are a field-named keyword argument and a bare declaration; the other 26 are dynamic or interprocedural | Label-keyed; the two labels are code-owned in the scanner, so the floor moves only by a code edit |
| E23 | F4 as written could not be violated | `3ca868193` | read `check_scope_declarations` against the F4 statement | Scope is declared and never inferred, so `conflict := collision ∧ scope_overlap` is a definition; what is enforced is that a declaration names every defining module exactly once, over 1 declaration and 4 contexts | Judgement from reading the check; value-set disjointness across contexts is deliberately *not* the property, because `SOURCE_SURFACES` legitimately reuses one name in four contexts (E19) |
| E24 | The retirement budget counted mentions as readers | B3 integration tree | `check_reader_metric()` over the six legacy fields; the five facts asserted to cover `count_identifier_modules()` and the role labels to partition it; both runtimes asserted to classify one access identically | 109 py token modules resolve to 85 surface modules; `goal_boundary` 30 → 16, `work_lane_contract` 29 → 29, `protocol_action_packet` 5 → 5 with one module reading it; 8 of 12 equivalent accesses had disagreed across the runtimes, and all six fields had measured zero TypeScript writers | Syntactic use, not data flow; 1711 Python computed-key accessors and 400 TypeScript computed members stay unattributable and belong to no field, so zero surface is not zero readers; the role partition is not a producer count |
| E27 | Agreement between runtimes does not by itself keep an access in the surface | B3 repair tree | Seven spellings of one TypeScript access, each scanned alone and each required to land in a named class that is inside the migration surface: dotted read, subscript read, shorthand and aliased destructuring, object-literal key, type property, name carried to a subscript | All seven are in the surface; none is a mention. The cross-runtime equivalence test alone would have passed with both runtimes reading a destructuring as prose, which is the shape the first implementation actually had | A counterexample set, not a proof of completeness: an eighth spelling nobody wrote down is still unmeasured, which is why an unprovable use lands in `unresolved` rather than in a confident default |

| E26 | B5 consumer evidence covers 1 of 26 registered vocabularies | `d8e7af141` | `scripts/generate_semantic_inventory.py --report --consumer-evidence`, cross-read against the drift smoke's `literal_scan_fields` coverage | 1 vocabulary declares `literal_scan.field` (`effective_action`) and is analysed; 25 are reported `missing_slot_identity` and are not analysed. 713 rows over 485 of 1213 tracked sources: 0 `read`, 61 `interpret`, 9 `pass_through`, 643 `unknown` (90.2%); of the 111 rows attributed to `effective_action`, 41 are unknown (36.9%). The removed vocabulary-id fallback had reported 921 rows at a 78.9% unknown share | Coverage is a registry property, not a code one: it moves only when a vocabulary declares a slot. Rows are syntactic use over a two-root reach, never data flow, and the scan is advisory — it refuses to run without `--report` |
| E13 | The conflict budget mostly measured local naming | `1dc6ad8d8` | `MODULE_LOCAL_CONVENTION` applied to `conflicting_values` and `same_runtime_forks` names | 16 of 18 conflicts and 7 of 25 forks are module-local conventions; the semantic subsets are 2 and 18 | Classification is a name pattern, documented in the scanner and pinned by a fixture test |

## Appendix D: Rejected or superseded alternatives

See Section 6. A single-PR enum unification was rejected because the three
enums have distinct change reasons; the evidence that could reopen it is a
projection proven to be a bijection after M2.

## Appendix E: Incident and review lessons

- Hand-synchronized parallel constant lists across runtimes pass review until
  the day one side changes; a parity check must exist before the second copy is
  accepted.
- A private extraction of a shared constant looks harmless in a large module
  and is the most common way a schema version forks. Test fixtures are the
  second most common: `HANDOFF_MODES` was copied into an e2e fixture.
- A literal scan that only accepts `[a-z_]` silently skipped a `_v2` spelling
  during the M0 mutation exercise. Capture every quoted string and validate the
  shape separately, so a malformed value is reported instead of ignored.
- A scan pattern written from Python examples matched no TypeScript at all,
  and a guard that is green on a baseline containing violations proves only
  that the guard is blind. Mutation-test every runtime the registry claims to
  cover before declaring an invariant.
- When the registry is both the specification and the validator's input, a
  data edit can weaken the validator. Keep the recognised forms in code, floor
  the coverage counts, and reject owners that are not `module::Symbol`.
- A ratchet on names alone lets an already-conflicting name gain a third
  spelling. Budget definitions as well as names.
- A smoke the fleet discovers is not a commit-time check. Ask on which
  required PR job it is collected, and plan a diff that touches only the
  guarded code to see whether selection finds it. If the answer is "after
  merge", the invariant is a report, not a gate.
- An anchor compared with `<=` pins only the value it held when written. Every
  tightening below it is unprotected until someone remembers to move the
  anchor. Compare with equality so the two values cannot separate.
- One field name can carry several vocabularies inside one envelope; a scan
  that sees the field cannot see the slot. Record the slots as a relation so
  the ambiguity is a registered fact, not an accident the registry blesses.
- A committed snapshot of the whole tree makes the guard's input depend on
  other people's merges. Measure how often the tree changes under it before
  committing it, and write down who regenerates when `main` goes red.
- A name-keyed collision rule needs a way to say "these are different things
  that share a name". Without it the honest fix and the dishonest fix (a
  rename) lower the same number, and reviewers cannot tell them apart.
- When a document widens its scope faster than the code, the two must be
  reconciled in whichever direction is cheaper, but they must match. A scope
  claim the scanner does not implement is a false invariant.
- Budgets that only go down describe a direction. Write the target table
  before the second milestone, or nobody can say when the work is done.
- A decision that waits on "a check" the RFC never defines is a dangling
  reference dressed as prudence. Name the invariant and the milestone that
  delivers the check, or the decision has no input and never closes.
- Using a role word (producer, consumer) nineteen times is not defining it.
  Until the roles are a table with a check per role, "who writes this value"
  is a question every reviewer answers differently.
