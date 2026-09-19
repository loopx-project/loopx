# Agent Instructions

## Goal-Oriented Development

Before selecting non-trivial work, resolve the current requested outcome from
user direction, the linked issue/task, accepted contract or demonstrated bug.
For cross-cutting LoopX work, consult the [overall roadmap](docs/architecture/rfcs/loopx-overall-roadmap-v0.md)
and relevant domain acceptance; do not make every fix wait for every RFC or
invent a roadmap id. Check latest `main`, related PRs and canonical Todos so an
older task description cannot override corrected direction or duplicate work.

Carry one compact delivery brief from task to PR: goal/source, current gap,
observable result, owning boundary and decisive acceptance evidence. Reuse the
existing task/PR fields; keep private Goal state out of public artifacts.
Choose a complete, independently reviewable and reversible outcome slice.
Small diffs, fields, receipts, test counts and merged PR counts do not establish
progress. Characterization, prerequisites, research, docs and maintenance are
valid when they remove an evidenced gap or enable a named real next step.

Continue through the selected slice's implementation, integration, negative
cases and readback while authorized work remains feasible. Do not stop after
setup, a serializer, a mock or an isolated smoke when the useful outcome is
still missing. Do not expand scope merely to make a PR larger. When a staged
boundary is necessary, name the delivered delta, remaining gap, next owner/
dependency and why the boundary improves verification or rollback. Reuse or
update an existing successor; do not create ceremonial follow-up tasks for a
completed request. Real authorization, cost and operational stop gates remain.

For multi-Agent changes, qualify the relationship the user needs: dependency
artifacts, receiver adoption, claim/lease handling, independent acceptance and
result return as applicable. Sending a message or registering workers does not
prove collaboration. Missing frontend/Lark/CLI companion work makes a product
journey partial even when a backend slice is ready to merge.

Before delivery, reconcile the result with the original/current goal and
update its task and RFC checkpoint when the boundary changes. Preserve passed,
failed and untested distinctions. If user feedback exposes the same missing
outcome, repair the owning rule, active task or projection through self-repair;
do not merely append stronger instructions. PR review must execute the current
capability-owned `problem_context` delivery judgment; author declarations and
this prose do not certify it or settle a Goal.

## Commit And PR Hygiene

### Worktree And PR Gate

For any tracked repository change beyond a trivial typo fix, create or use a
dedicated clean `git worktree` on a `codex/` branch. Use latest `origin/main`
unless the user explicitly names an integration or release branch; in that
case, fetch that branch and use its latest remote head as both the worktree
baseline and pull-request base. Before pushing, verify the merge base and PR
base so unrelated `main` history cannot leak into a stacked integration PR.
Do not implement changes directly in a dirty primary worktree, even when the
task starts by inspecting that dirty tree.

When a dirty worktree contains potentially valuable changes, first classify it
read-only, then copy or reapply the valuable subset into the dedicated clean
worktree and open a PR from that branch. Reset or clean the original dirty
worktree only after the valuable subset has been merged or explicitly judged
obsolete. Leave unrelated untracked local artifacts alone.

Every tracked repository change must be pushed on a branch and reviewed through
a pull request before it reaches `main`. Do not push broad mixed commits or
direct commits to `main`.

### DCO Sign-Off

Every commit in a pull-request branch must include a
`Signed-off-by: Your Name <your.email@example.com>` trailer, or the `DCO`
check will reject the PR. Always commit with `git commit -s`. If a commit is
already missing the trailer, amend it with `git commit --amend -s` (or an
interactive rebase for multiple commits) before pushing. See CONTRIBUTING.md
for the full DCO policy.

Only skip this worktree/PR gate when the user explicitly says the change is
local-only and must not be proposed for the repository.

For non-trivial repository changes, especially anything that touches benchmark
adapters, smoke tests, public docs, or commit/push workflows, use the
`git-split-commit-pr` workflow before staging:

1. Establish ground truth with `git status --short --branch`,
   `git diff --stat`, `git diff --name-only`, and
   `git ls-files --others --exclude-standard`.
2. Classify every changed path before staging:
   - core product code;
   - core documentation;
   - durable validation smoke;
   - local/private state;
   - low-value or obsolete artifact.
3. Scan candidate paths for credentials, private state, local absolute paths,
   raw benchmark logs, trajectories, verifier output, and internal links.
4. Stage by explicit pathspecs only. Do not use `git add .`.
5. Split commits by reviewer logic:
   - runtime/API behavior;
   - public docs and protocol notes;
   - focused validation or cleanup.
6. Push a branch and open a PR for reviewable batches.

Control-plane, runtime and product surfaces are never self-merged. A change
that alters the behavior of `loopx/**`, `apps/**` or `packages/**`, a
permission or authority boundary, a persisted state or receipt contract, a
quota/scheduler/todo rule, or a CLI/API contract is proposed, reviewed on its
exact head, and then left for the maintainer to merge -- including when the
author has already validated it, its review is published, and its CI is green.
Admin bypass does not convert one of those changes into a self-mergeable one,
and neither does a small diff: a validated fix is still a control-plane change
when it changes control-plane behavior. When the author cannot tell whether a
change is in that set, it is not self-merged.

For small, low-risk PRs, maintainers may self-merge after validation when all
of the following are true:

Here, "自合并" means: 自己 review/refine, publish that review on the exact head,
then admin-bypass merge after the required validation and authorization. A
self-merge whose head carries no published review is a process gap, not a
smaller form of review: repair it by publishing the exact-head review for the
merged commit and correcting the rule that let it through.

- the PR only touches public docs, contributor metadata, tests, examples, or
  narrow cleanup, and none of the surfaces named above;
- the change is single-purpose and easy to review from the diff;
- required checks or focused smokes have passed;
- the exact head carries a published self-review, and
  `loopx pr-review --check-merge-readiness NUMBER@HEAD_OID` returned ready for
  that unchanged head. GitHub blocks formal self-approval, so on an
  author-owned PR the record is a `COMMENTED` review on the exact head that
  states the approval conclusion and an English verdict; a green CI run, a diff
  read, or the merge itself is not that record;
- private state, raw benchmark evidence, credentials, local paths, and
  generated logs are excluded;
- there is no runtime behavior, benchmark adapter, permission, destructive git,
  or public evidence-policy change that needs separate review.

Small benchmark seam/refactor PRs may also be self-merged when they are like
PR #145: they add or clarify a reusable adapter/control-plane contract, include
focused public smokes, do not launch benchmark jobs, do not change scoring or
runner behavior for an existing benchmark, and do not include temporary probes,
raw evidence, private state, credentials, local paths, or generated logs.

Benchmark helper/runtime PRs may also be self-merged after owner authorization
when they are limited to public benchmark helper code, status/runtime
observation, reducer/closeout plumbing, or benchmark developer workflow support;
focused smokes or compile checks pass; public/private boundary scans are clean;
and the PR does not change benchmark scoring, task semantics, leaderboard or
submission behavior, permission boundaries, or launch new benchmark jobs.

After self-merging, sync local `main`, leave unrelated untracked local artifacts
alone, and continue with the next safe project batch.

Before self-merging non-trivial LoopX changes, run
`loopx canary premerge --from-git-diff` or an equivalent risk-based validation
set. The PR comment must name the changed surfaces, checks run, failures/skips,
manual holds, and why the coverage is enough. One hand-picked smoke is not
enough for runtime, quota/status, scheduler, todo, install, dashboard,
benchmark-boundary, or public/private evidence changes.

### Release Contributor Attribution

Keep the shipped product changes as the primary release narrative. When the tag
range contains merged work from community contributors other than project
founder `@huangruiteng`, add a prominent `## Community Contributors` section
after the English product groups and a matching `### 社区贡献者` section after
the Chinese product groups. Place both before compatibility, validation, or
update material so the credit remains visible without replacing the release
summary.

Link each eligible contributor's GitHub handle and relevant pull requests, and
describe the concrete contribution instead of publishing an unannotated name
list. Explicitly highlight external and first-time contributors when
applicable. Do not list or thank `@huangruiteng` in contributor sections;
founder stewardship is implicit in LoopX releases. Omit both contributor
sections when the tag range has no eligible community contribution.

Derive attribution from the previous-tag-to-current-tag Git range plus merged
PR metadata. Do not guess from commit display names, omit contributors because
their work is summarized elsewhere, let contributor credit displace product
content, or invent community attribution for a founder-only release. The
release PR and final GitHub release body must preserve the same bilingual
attribution.

### Release Capability Usage Gate

When a release introduces or materially changes an optional capability,
workflow, managed skill, or host surface, the final GitHub release body must
teach the user how to operate it. For every affected surface, include matching
English and Chinese entries with:

- exact activation or per-command/profile opt-in;
- a minimum runnable validation or readback command;
- exact disable, uninstall, envelope-removal, or rollback guidance;
- the authority and privacy boundary that activation does not grant;
- a canonical versioned documentation link.

Save the complete final release body in an ignored or temporary Markdown file.
Run `examples/release/release-readiness-doc-smoke.py --release-notes ...` with
one `--surface` argument per affected surface before publication, then read the
remote body back and run the same check again. A release with no applicable
surface changes must use `--expect-no-optional-capability-changes` and include
the validator's explicit bilingual no-change declarations. Never treat the
existence of a checklist, a release PR draft, or architecture-only capability
copy as proof that the final release body is usable.

## First-Screen Review Gate

Treat the first visible screen of public product surfaces as owner-reviewed
presentation, not as ordinary copy. Before committing, pushing, or self-merging
changes that alter the first viewport, hero block, primary CTA, or opening
navigation of README, hosted frontstage, showcase index pages, product home
pages, or similarly prominent public entry points, show the user a preview
first and wait for approval.

The preview should be concrete enough to judge the presentation: provide the
local URL and, when the surface is visual HTML, a screenshot or browser view of
the first viewport. Do not move the review gate into a PR comment, todo note, or
final summary after the fact. It must happen before the public first-screen
change is finalized.

## Product Delivery Completeness

For product changes, identify affected user entry points (frontend, Lark, CLI)
while planning. Inspect existing settings and capability editors before calling
a configuration change backend-only; include necessary companion work in the
same delivery plan, reusing the existing configuration owner and projection.

Before PR handoff, verify the affected user interaction, state readback and
feedback, including the packaged frontend when shipped. State which entry
points changed and the validation performed. If no frontend change is needed,
give a concrete, verified reason; if companion work remains, label delivery
partial and link it. These are agent-owned completion checks, not new approval
gates.

## UI Design Standard

Apply the whole-viewport attention review in `docs/development/design.md`
("Earn The User's Attention") before implementation and in PR visual evidence.

Before changing or reproducing any LoopX UI, read and follow the repository-root
`docs/development/design.md`. This includes websites, dashboards, desktop applications,
documentation, prototypes, screenshots, and framework migrations. When an
approved design source is provided, match it and use `docs/development/design.md` for unspecified
details.

## Public And Private Boundary

Do not commit internal department, team, customer, meeting, reporting,
strategy, or local operating context into the public repository. This includes
planning notes, status narratives, rollout stories, fixtures, screenshots,
examples, catalog rows, PR descriptions, and review artifacts whose value
depends on private organizational context rather than reusable public product
behavior.

Keep private planning and incident evidence in ignored local state such as
`.local/`, or another explicitly ignored owner-approved location. Only commit
material after it has been generalized into public-safe product, maintainer, or
developer language and no longer reveals internal actors, timelines, reporting
needs, local paths, raw logs, private links, or private decision context.

Before staging public docs, fixtures, examples, catalogs, or metadata, scan the
candidate paths for internal/private wording and ignored local artifact
references. If such context was already pushed, stop normal delivery, run
LoopX self-repair, clean the current public heads, and record any remaining
PR-ref or cached-view cleanup as an explicit user/support gate.

## PR Review Comments

When the user asks the agent to review a GitHub PR, treat PR feedback as a
public collaboration artifact by default. After validating the findings,
publish actionable review findings directly on the PR as a comment or review,
unless the user explicitly asks for a local-only review or the finding contains
private/security-sensitive material that must not be posted publicly.

Do not leave actionable PR blockers only in chat memory. The final user report
should include the PR comment URL and a compact summary of the posted findings.

Before publishing a review, run the capability-owned review lenses for typed
state rules, domain neutrality, behavior-change disclosure, and
guidance-vs-obligation, plus default-off isolation and authority semantics
(defined in `pull_request_review_execution_contract_v2`).
Flag substring denylists or prose-only classification rules with the concrete
misclassification risk, product- or benchmark-specific wording in generic
control-plane contracts, silent default-behavior changes, and text that calls a
machine-enforced obligation "guidance". For opt-in behavior, prove feature-off
parity across every shared changed surface. Reject protocol names that imply a
broader actor lifecycle or authority model than the implementation provides.

## Engineering Quality And Right-Sized Scope

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
[budget decision guide](docs/development/testing-and-quality.md#budget-failure-decisions).
Establish what the limit protects, measure the same base/head workload, and
inspect consumer value and true redundancy before choosing compaction, keeping
the limit, or an evidence-backed increase. Preserve decision semantics and
compatibility; neither a historical ceiling nor a green revised test is the
objective. Record the tradeoff in existing PR validation/review evidence, not a
new approval workflow. Hard limits and frozen experiment/promotion criteria
retain their owning authority and cannot be reclassified to erase a failure.

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

## Automation And Monitor Todos

Do not hard-code one-off project or PR monitor logic into a generic heartbeat
automation prompt. Recurring project-specific watches, such as "monitor PR #532
until merge", belong in LoopX state as `continuous_monitor` todos with compact
metadata such as `claimed_by`, `unblocks_todo_id`, and evidence notes. The
heartbeat prompt should remain generic and discover monitor work through
status, quota, and todo projection. Only update an automation prompt when the
heartbeat lifecycle contract itself changes or the user explicitly asks to
change the scheduler.

## Projection Sink Design

When adding an operator-facing display sink such as Lark Base, dashboards,
chat summaries, or reports, build it from LoopX's public-safe state and
projection surfaces instead of parsing project-specific private source files.
Valid display inputs include todo projection, quota/status contracts,
frontstage projections, compact run-history events, public-safe evidence
pointers, and redacted source warnings.

Do not make a generic sink depend on the shape of one local document, private
planning file, non-public wiki, raw transcript, local path, or connector payload.
If a source is valuable, first convert it into a bounded LoopX projection with
stable ids, source labels, evidence, and explicit gates; then let the sink
render that projection. Public or multi-user sinks must consume redacted
public-safe evidence. An explicitly owner-only operator board may sync private
planning evidence when the user authorizes that boundary, but it should still
scope rows by `agent_id` and avoid credentials or secrets unless the user
explicitly asks for a credential-handling workflow.

Projection sinks should preserve row lineage as data, not as ad hoc prose. When
rows supersede, migrate, or retire earlier display rows, represent that through
projection lifecycle fields such as `row_lifecycle`, `supersedes`,
`superseded_by`, `source_id`, and compact migration audit evidence. A sink may
render the lineage in existing evidence/history fields, but should not require
reading a project-private source document to understand why a row changed.

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

## LoopX Self-Repair

When LoopX behavior is surprising, too small, contradictory, or called
out by the user as likely wrong, use the project skill
`skills/loopx-self-repair/SKILL.md`. Treat recurring mistakes as product
or process gaps: update the skill, interaction docs, active-state projection,
or focused smoke so the lesson is durable. Do not resolve self-repair by
lowering gates, guessing around contradictory payloads, or committing private
logs and local state.

## Benchmark Smoke Classification

Use this classification when cleaning or reviewing benchmark-related changes:

- Keep focused boundary smokes such as
  `examples/benchmark-candidate-source-boundary-smoke.py`; they guard a reusable
  public/private source contract.
- Keep toolkit permission and integrity smokes while they validate the shipped
  provider-neutral capability contract.
- Keep benchmark-native runners, adapters, ledgers, scoring reducers, and dated
  experiment packets outside the active product surface. Historical versions
  follow the canonical archive placement rules in `benchmark/README.md`:
  retired implementations and dated packets belong under
  `deprecate/benchmark-legacy/`; explicitly identified immutable experiment
  snapshots may remain under `benchmark/` only under that document's conditions.
  Neither category is part of active CI benchmark execution.
- Add a new active benchmark smoke only when it protects a stable toolkit
  behavior; experiment-specific validation belongs with the research workspace.
