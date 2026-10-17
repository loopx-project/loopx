# Agent Engineering Rules

These are the detailed repository instructions linked from [AGENTS.md](../../AGENTS.md).
Read the applicable sections before the corresponding action; their requirements,
exceptions and authority boundaries remain mandatory. Commands and backticked
paths are relative to the repository root unless stated otherwise.

## Engineering Quality And Right-Sized Scope

### TypeScript-First Implementation

Prefer TypeScript for new or refactored domain-neutral LoopX capabilities,
control-plane and orchestration logic, capability composition, context/memory
composition, and shared projections. Follow the accepted
[TypeScript migration RFC](../../docs/architecture/rfcs/typescript-control-plane-migration-v0.md)
and reuse the existing typed owner. Do not introduce a parallel Python decision
owner. Stable domain providers, specialized computation and transport adapters
may remain Python with an explicit placement rationale; this preference does
not require unrelated language-only rewrites or make a full migration a
prerequisite for a bounded user outcome.

尽量用 TypeScript 实现新增或重构的 LoopX 通用能力、控制面、编排、能力组合、上下文/记忆组合及共享投影，遵循 TS 重构 RFC 并复用既有类型化 owner，不新增平行的 Python 决策源。稳定领域 provider、专门计算与传输适配器可保留 Python，但须说明归属理由；不因语言偏好扩大无关重写，也不把全量迁移设为有界用户结果的前置条件。

### Source-Checkout Python Entry Points

Run source development and validation from the intended worktree root with
`uv run --extra test python ...` or `uv run --extra test loopx ...`. Use
`uv sync --extra test` to prepare the project environment. An explicitly
activated compatible environment with the checkout installed remains valid.
Check `sys.executable` and `loopx.__file__` when interpreter or source provenance
is uncertain; a global `loopx` may point to another release snapshot.

Keep Python subprocesses on the selected interpreter (`sys.executable`), and
keep bootstrap interpreter discovery, supported-version declarations, CI
version coverage, and version-specific fixtures intact. Do not replace those
with a nested `uv run`, rewrite historical execution receipts, or commit a
generated `uv.lock` as part of an unrelated change. See the testing and quality
guide for the validation layers and the source-checkout environment boundary.

### Evidence-Based Budget Decisions

When a size, structure, latency, or similar regression budget fails, follow the
[budget decision guide](../../docs/development/testing-and-quality.md#budget-failure-decisions).
Establish what the limit protects, measure the same base/head workload, and
inspect consumer value and true redundancy before choosing compaction, keeping
the limit, or an evidence-backed increase. Preserve decision semantics and
compatibility; neither a historical ceiling nor a green revised test is the
objective. Record the tradeoff in existing PR validation/review evidence, not a
new approval workflow. Hard limits and frozen experiment/promotion criteria
retain their owning authority and cannot be reclassified to erase a failure.

LoopX is a semantic control plane: agent-consumed prose is part of its behavior
contract. Before shortening packet guidance, compare obligations clause by
clause, including ordering, evidence provenance, modality, scope qualifiers,
continuation and stop conditions. Stable fields, commands and decision codes
do not prove that shorter instructions preserve those obligations. A regression
ceiling is not frozen authority: prefer an evidence-backed limit increase when
compression would discard useful meaning. Use the existing review capability's
`observable_semantics.decision_text_assessment`; do not optimize for a green
size test at the expense of the caller's decision.

### Refactor Real-Path Validation

Before delivering a refactor, validate the affected production entrypoint and
real backend, not only mocks, in-memory substitutes, or unit tests. Authority
store refactors that affect PostgreSQL must run the PostgreSQL integration
suite against an isolated real server; report the exact source and results.
Use a separate database/tenant and disposable runtime with synthetic fixtures
or an owner-authorized read-only snapshot. Never test by promoting, rewriting,
or corrupting an active goal, its registry, writer fence, Todo, or lease state.
If the required real environment is unavailable, report the evidence gap and
hold delivery; a skipped test does not satisfy this gate. See the testing and
quality guide for the same safety and evidence boundary.

Treat code volume as a cost, especially during refactors. A good LoopX change
should make the next change easier to localize, test, and revert; it should not
turn a design possibility into unused production structure.

### Bounded Future-Facing Refactoring

During development and again before approving or merging each PR, explicitly
ask whether the touched behavior or its adjacent owning boundary has a small,
related, behavior-preserving refactor that would make the next likely product
or control-plane change easier. Prefer removing duplicate authority,
strengthening typed contracts, narrowing module ownership, and retiring
obsolete compatibility seams over adding speculative frameworks. For
control-plane work, keep state-machine and effect authority in the typed
TypeScript boundary when that is the established owner; Python may adapt or
bridge that contract, but must not silently recreate a second source of truth.

Apply the refactor in the current PR when it shares the same domain or change
reason, remains locally reviewable and reversible, and is covered by
characterization or parity validation; it need not be strictly required for
the immediate fix. Do not use this principle to justify broad migration or
unrelated cleanup. When the valuable related refactor is larger than a bounded
companion change, record a focused follow-up instead. PR review and merge notes
should state whether this future-facing pass was applied, deferred, or found
unnecessary, with the concrete boundary considered.

## Capability And Extension Placement

Before adding an ability, decide its capability owner and provider boundary;
do not choose a directory from the feature name alone:

- name public capabilities after caller outcomes, not delivery mechanisms. A
  proposed `connector`, `provider`, `adapter`, or `sink` capability needs an
  independently useful caller contract; otherwise make it an extension
  provider or an internal part of the outcome capability it serves;
- extend `loopx/capabilities/<capability>/` when the change belongs to an
  existing product contract and shares that built-in capability's lifecycle;
- create a new built-in capability only when LoopX core must ship it by
  default and it has a stable caller contract, real entrypoint, and focused
  validation;
- put generic manifest, registration, compatibility, and lifecycle mechanics
  in `loopx/extensions/`;
- put an independently versioned or optional provider distribution in
  `packages/<package-id>/` when it is co-located, or in its own package or
  repository when it is distributed separately. Reserve `loopx/extensions/`
  for extension lifecycle code and providers bundled in the LoopX wheel;
- do not create a capability merely to make an extension installable. An
  extension-owned command or workflow may declare only its runtime and
  lifecycle when LoopX callers do not need a provider-neutral capability
  contract;
- when a provider introduces a new product contract, register the capability
  contract and implement it through the extension only when that contract is
  intentionally provider-neutral and belongs in LoopX's capability catalog;
- keep private helpers in the nearest owning module. A helper is not a new
  capability or extension merely because several files are involved.

Record the placement rationale before editing: capability id, provider id,
whether the provider is built-in or extension-delivered, and why the nearest
existing owner is or is not sufficient. See `docs/reference/extensions.md` for the full
decision guide.

Before adding a new module, builder, protocol field, CLI option, fixture, smoke
section, or abstraction, pass a scope-fit review:

- Identify the shipped behavior, active call site, or explicit compatibility
  contract that needs it. If the value is only an uncommitted future runner, a
  design note, or a hypothetical extension with no validation contract, keep the
  design in docs or todo state until the real call site appears.
- Prefer a cohesive behavior-preserving seam. For example, let the ledger first
  recognize one compact public-safe row shape before adding a dedicated
  benchmark-specific builder, arm constants, or wide field-level smoke. Do not
  split so narrowly that reviewers must reconstruct one logical behavior from
  several dependent PRs.
- Design tests from semantics, not observed output. Independently review the
  intended invariant and legal or illegal transitions before testing the
  implementation. Never derive expected results from the implementation under
  test or its current output; characterization fixtures are non-authoritative,
  and contradictions require rule repair plus negative or mutation coverage.
- Characterize before moving code. For status, quota, review-packet, scheduler,
  monitor, and handoff behavior, add or extend parity fixtures first, then
  extract the proven rule or cohesive rule group.
- Reuse existing repository patterns and bounded contexts. Add code where its
  change reason belongs, such as `control_plane/runtime`, `control_plane/quota`,
  or `control_plane/todos`; do not create generic sink directories or helper
  layers just because several files share a similar shape.
- Treat large or hot files as warning signals. When a change would grow an
  already oversized module, first look for a narrow read model, domain helper,
  or bounded-context home. For internal module moves, update active call sites
  and delete the old entry point; leave a compatibility wrapper only when a
  real external import, persisted state, CLI/API contract, or migration window
  requires it.
- Distinguish duplicate knowledge from duplicate-looking code. Collapse shared
  state rules, protocol semantics, serialization contracts, and lifecycle
  invariants; avoid a parameter-heavy abstraction when two callers merely look
  similar but will evolve for different reasons.
- Keep smokes thin and durable. They should prove shipped behavior, boundary
  enforcement, and regression contracts, not every incidental field produced by
  a temporary builder. Large smokes are a prompt to move reusable logic into
  product modules or to narrow the assertion surface.
- Make illegal states hard to express. For status, quota, scheduler, monitor,
  todo, and handoff flows, prefer explicit enums, schemas, and transition
  helpers over scattered booleans and prose-only assumptions.
- State-classification and delivery-semantics rules belong in typed enums,
  schemas, or transition helpers. Substring denylists and prose heuristics must
  carry a documented reason and a typed follow-up; PR review must flag them
  with the concrete false-positive/negative risk.
- Core control-plane obligations and error text must stay domain-neutral. Do
  not put product- or benchmark-specific wording (for example "product
  advancement") into generic work-lane, quota, todo, or settlement contracts;
  prefer goal-agnostic phrasing.
- Default behavior changes must be disclosed: rename the smoke that encoded the
  old default, update docs/release notes, and name the affected lanes.
  "Guidance" versus machine-enforced obligations (such as `must_attempt_work`)
  must be explicit in the contract, not inferred from prose.
- Fail fast with actionable context at input, config, permission, and state
  boundaries, but do not replace clear control flow with broad exception
  plumbing or silent fallback.
- Ship right-sized, reversible batches. A PR should be theme-unified, locally
  validated, and reviewable as a complete stage package. A few hundred to
  roughly one or two thousand lines can be appropriate when the diff is cohesive
  and avoids hidden future scaffolding; a 30-line PR can still be too small if it
  leaves behavior split across follow-up PRs. Separate characterization/parity
  fixtures, mechanical moves, behavior changes, and cleanup when that makes
  review and rollback clearer.
- Keep public PRs concise and current-purpose focused. Future extension points
  are allowed when they reduce near-term churn, preserve compatibility, or
  define a real contract that is documented and tested. Do not bundle
  private/local experiment scaffolding, diagnostic run dumps, unused speculative
  plumbing, or long background narratives with the code path needed by the
  current behavior.
- Compress rather than append. For docs, fixtures, dashboards, and examples,
  replace or retire stale material when adding new current truth; do not let
  canonical surfaces accumulate multiple versions of the same conclusion.

Use this checklist to delete, defer, or right-size code as actively as you add
it. A PR that removes an unused abstraction, narrows a smoke to the real
contract, or moves a rule into the right bounded context is often more valuable
than one that adds a larger framework around the same behavior.

## Smoke Retention Policy

Keep a smoke test only when it validates a durable public behavior:

- shipped CLI/runtime behavior;
- a reusable control-plane contract;
- public/private boundary enforcement;
- a regression that previously stranded automation;
- a representative fixture that is likely to catch future bugs.

Do not keep one-off smokes whose main purpose is to assert the exact text of a
dated research note, candidate ranking packet, temporary run review, or
transitional benchmark decision. Preserve that information in the research doc
itself, and cover shared invariants with a data-driven aggregate smoke.

When a smoke grows beyond roughly 500 lines, re-check whether it is really one
test. Prefer splitting reusable logic into product modules and keeping the
smoke as a thin public behavior check. Large integration smokes are acceptable
only when they cover a real end-to-end adapter contract that smaller unit tests
cannot cover.

Benchmark smokes must never require raw task text, raw trajectories, raw logs,
verifier output tails, credentials, uploads, leaderboard submissions, or local
private artifact paths.
