# Agent Instructions

## What LoopX Does

LoopX is a local-first, runtime-neutral control plane for long-horizon agents
and personal agent teams. The Agent host supplies models and tools; LoopX keeps
goals, decisions, work ownership and evidence durable across sessions and hosts.
The core objective is better long-horizon effectiveness and efficiency: more
independently verifiable outcome value within comparable budgets, or comparable
accepted outcomes with less total cost and human attention. Structured state,
closed-loop feedback and evidence-driven replanning provide process supervision
across sessions, recovery and collaboration; their value depends on actual
decisions, execution and outcomes.
核心目标：通过结构化状态、闭环反馈和基于证据的重规划过程监督，提升 Agent 与
Agent 团队的长程效果和长程效率：在可比预算下取得更好的可验证成果，或以更低的
完整成本与人工介入交付同等验收成果。

- **Goals and work:** durable Goals, Todos, dependencies, acceptance and recovery.
- **Governed continuation:** heartbeat, scheduling, quota and explicit stop/wait
  conditions decide when authorized work can continue.
- **Peer collaboration:** claims, leases, scoped handoffs, dependent artifacts,
  receiver adoption and result return; a coordinator gains no implicit authority.
- **Personal Workspace:** App/frontend, Lark and CLI expose conversations,
  settings, work, decisions, artifacts and readback over the same typed owners.
- **Capabilities and extensions:** reusable engineering, research, knowledge,
  content and office workflows with explicit provider and permission boundaries.

Start with [README](README.md), the [overall roadmap](docs/architecture/rfcs/loopx-overall-roadmap-v0.md),
the [capability catalog](loopx/capabilities/README.md) and [stable protocols](docs/reference/protocols/README.md).
The roadmap distinguishes shipped boundaries from acceptance still to qualify.
Core rules live in `loopx/control_plane/`; capability contracts in
`loopx/capabilities/`; extension lifecycle in `loopx/extensions/`; optional
distributions in `packages/`; product surfaces in `apps/`.

Codex loads this file; `CLAUDE.md` imports the same policy for Claude Code.
These are standing repository constraints. Read a linked procedure when its
trigger applies to the current task; do not load every reference for every turn.
Keep current work, evidence and monitor targets in their canonical task/state,
not in this file or a frozen prompt. Keep the root within the default 32 KiB
Codex discovery budget, and do not eagerly import linked detail documents.
See [loading and maintenance](docs/development/README.md#agent-instruction-loading-and-maintenance)
for host-specific checks; delivery does not prove model compliance.

## Goal-Oriented Development

Before selecting non-trivial work, resolve the current requested outcome from
user direction, the linked issue/task, accepted contract or demonstrated bug.
For cross-cutting LoopX work, consult the [overall roadmap](docs/architecture/rfcs/loopx-overall-roadmap-v0.md)
and relevant domain acceptance; do not make every fix wait for every RFC or
invent a roadmap id. Check latest `main`, related PRs and canonical Todos so an
older task description cannot override corrected direction or duplicate work.

For recurring operational or performance failures, follow the
[roadmap-aligned optimization guide](docs/development/testing-and-quality.md#roadmap-aligned-optimization):
bind the demonstrated failure to its owning acceptance before repair, separate
caller, shared typed transport/semantics and provider costs, and reuse the common
contract. A faster lookup, timeout increase or successful promotion is only an
interim result until sustained acceptance is proven. Reconcile the existing
checkpoint as evidence changes; do not create a parallel roadmap or require
unrelated RFC work for a bounded fix.

Carry one compact delivery brief from task to PR: goal/source, current gap,
observable result, owning boundary and decisive acceptance evidence. Reuse the
existing task/PR fields; keep private Goal state out of public artifacts.
Choose a complete, independently reviewable and reversible outcome slice.
Small diffs, fields, receipts, test counts and merged PR counts do not establish
progress. Characterization, prerequisites, research, docs and maintenance are
valid when they remove an evidenced gap or enable a named real next step.

For changes intended to improve long-horizon work, trace the accepted goal and
structured state through feedback, the replan decision, actual subsequent work
and validated outcomes. Check whether new evidence changed the action or
justified continuing it; preserve useful negative results and uncertainty.
State survival, a graph edge, a read receipt or a replan record alone does not
prove adoption or benefit. Compare outcomes and complete costs under comparable
conditions using the [existing measurement contract](docs/architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.md#6-measurement-model);
do not force a direction change merely to increase replan counts.

Continue through the selected slice's implementation, integration, negative
cases and readback while authorized work remains feasible. Do not stop after
setup, a serializer, a mock or an isolated smoke when the useful outcome is
still missing. Do not expand scope merely to make a PR larger. When a staged
boundary is necessary, name the delivered delta, remaining gap, next owner/
dependency and why the boundary improves verification or rollback. Reuse or
update an existing successor; do not create ceremonial follow-up tasks for a
completed request. Real authorization, cost and operational stop gates remain.

When adding or extending state/protocol vocabulary (Enum, `Literal`, named
closed set or TypeScript `as const`), answer whether it reuses an owner, extends
a registered vocabulary, remains local or creates a shared contract. Follow the
[diff-scoped semantic advisory](CONTRIBUTING.md#check-new-semantic-vocabulary-while-developing)
before the full-tree check, including each intentional untracked source. Findings
are review prompts, not a gate or proof that all semantic forms were covered.

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
dedicated clean `git worktree` on a `codex/` branch. Verify the target repository
and remote first: a fork's `origin/main` may not be the PR target. Fetch and use
the latest target `main`, or the integration/release branch explicitly named by
the user, as both worktree baseline and PR base. Before pushing, verify the
merge base and actual PR base so unrelated `main` history cannot leak into a
stacked integration PR.
Do not implement changes directly in a dirty primary worktree, even when the
task starts by inspecting that dirty tree.

When a dirty worktree contains potentially valuable changes, first classify it
read-only, then copy or reapply the valuable subset into the dedicated clean
worktree and open a PR from that branch. Reset or clean the original dirty
worktree only after the valuable subset has been merged or explicitly judged
obsolete. Leave unrelated untracked local artifacts alone.

Every tracked repository change must be pushed on a branch and reviewed through
a pull request before it reaches `main`. Do not push broad mixed commits or
direct commits to `main`. Only skip this worktree/PR gate when the user explicitly
says the change is local-only and must not be proposed for the repository.

### Author Submission And Review Entry

Open a completed, independently reviewable proposal as a normal PR. Missing
review, pending or failed CI, change-quality holds, and unavailable merge
readiness are not reasons to keep it Draft. Record those limits in the PR;
entering review does not certify validation, adoption, approval or merge authority.
Reserve Draft for genuinely unfinished author work with a concrete remaining
implementation or integration step.

For an authorized author delivery, mark an existing completed Draft ready,
read back its state and unchanged head, and refresh the review packet. Drafts
are excluded from the review queue, so waiting for a review before marking one
ready creates a deadlock. A reviewer must still obey the packet's action
authority and may not mark another author's Draft ready without authorization.
Keep exact-head review, required validation, first-screen evidence and explicit
merge authorization as their separate gates.

### DCO Sign-Off

Every PR commit needs a `Signed-off-by` trailer: always use `git commit -s`.
Repair a missing trailer with `git commit --amend -s` or a multi-commit rebase
before pushing. See [DCO](CONTRIBUTING.md#license-and-dco-sign-off); otherwise the
DCO check rejects the PR.

### Bilingual RFC Diff Gate

For an existing English/Chinese RFC pair under `docs/architecture/rfcs/`,
including its ledger entries, both files must change in the same PR. Check the
staged files before committing. Set `PR_BASE_REF` to the verified PR target
remote-tracking ref (for example `upstream/main` for a fork PR):

```bash
: "${PR_BASE_REF:?set the verified PR target ref}"
uv run --extra test python scripts/check_rfc_bilingual.py --base "$PR_BASE_REF" --staged
```

The staged check includes earlier branch commits. Before merge, run
`uv run --extra test python scripts/check_rfc_bilingual.py --base "$PR_BASE_REF" --head HEAD`
against the PR's actual target branch and exact reviewed head. The source-checkout
`uv run --extra test loopx canary premerge --from-git-diff --git-diff-base "$PR_BASE_REF"`
runs this as a direct
blocking check, including in the quick tier. A failure holds merge even when CI
waiting is disabled or admin bypass is authorized. It checks the committed diff;
an unstaged translation cannot satisfy it. The check uses the merge base, covers
paired deletion/renaming, and leaves legacy single-language files alone. Review
the actual translation; paired diffs do not certify semantic equivalence.

### Staging And Public Boundary

Before staging non-trivial changes (including benchmark adapters, smokes,
public docs and commit/push workflows):

1. Read `git status --short --branch`, `git diff --stat`, `git diff --name-only`
   and `git ls-files --others --exclude-standard`.
2. Classify every changed path: product code, core docs, durable validation
   smoke, local/private state, or low-value/obsolete artifact.
3. Scan candidates for credentials, private state, local absolute paths, raw
   benchmark logs/trajectories/verifier output and internal links.
4. Stage explicit pathspecs only; never `git add .`.
5. Split by reviewer logic: runtime/API behavior, public docs/protocol notes,
   and focused validation/cleanup. Push reviewable batches as branch PRs.

### Merge Authority And Validation

Before approval or merge, read the repository-kept
[merge workflow](skills/loopx-pr-merge/SKILL.md); installing it is optional.
It does not replace repository policy or the current capability-owned review.

Control-plane, runtime and product surfaces require explicit maintainer merge
authorization. This includes behavior in `loopx/**`, `apps/**` or `packages/**`,
permissions or authority, persisted state/receipts, quota/scheduler/todo rules,
and CLI/API contracts. Propose and review
the exact head, pass required validation, and obtain ready merge-readiness for
that unchanged head. Only then may an explicitly authorized merge or delivery
batch use admin bypass. Small diffs, green CI or published reviews do not grant
merge authority. Unclear affected boundaries use the same authorization rule;
without it, leave the merge to the maintainer. Bypass never replaces review,
validation or readiness.

Self-merge means self-review/refinement, a published exact-head review, then
merge under the required validation and authorization. For author-owned PRs,
GitHub blocks formal self-approval: publish a `COMMENTED` review carrying the
approval conclusion and an English verdict. A diff read, green CI or the merge
itself is not this record. If a self-merge lacks it, publish the review for the
merged commit and repair the rule that allowed the gap.

Maintainers may self-merge single-purpose, low-risk public-doc, contributor
metadata, test, example or narrow-cleanup PRs when all these conditions hold:

- none of the protected behavior surfaces above is changed;
- the diff is easy to review and required checks/focused smokes passed;
- its exact head has the published review, and
  `uv run --extra test loopx pr-review --goal-id GOAL --check-merge-readiness NUMBER@HEAD_OID`
  returned ready for that unchanged head;
- no private state, raw benchmark evidence, credentials, local paths or
  generated logs are included;
- no runtime behavior, benchmark adapter, permission, destructive Git or public
  evidence-policy change requires separate review.

The existing benchmark exceptions retain these boundaries:

- Small seam/refactor PRs (as in #145) may add/clarify a reusable adapter or
  control-plane contract with focused public smokes; they do not launch jobs or
  change existing scoring/runner behavior, and exclude temporary probes, raw
  evidence, private state, credentials, local paths and generated logs.
- After owner authorization, public benchmark helper/runtime, status observation,
  reducer/closeout or developer-workflow PRs may self-merge when focused smokes
  or compile checks pass and public/private scans are clean. They must not alter
  scoring, task semantics, leaderboard/submission behavior, permission boundaries
  or launch jobs.

Before any non-trivial self-merge, run
`uv run --extra test loopx canary premerge --from-git-diff --git-diff-base "$PR_BASE_REF"`
or equivalent risk-based validation. The PR comment names changed surfaces,
checks, failures/skips, manual holds and why coverage is sufficient. One
hand-picked smoke is insufficient for runtime, quota/status, scheduler, todo,
install, dashboard, benchmark-boundary or public/private evidence changes.
After merging, sync local `main`, preserve unrelated untracked artifacts and
continue the next safe project batch.

### Named Public Releases

Before completing a named public release, follow the existing release owners:

- [Personal Feishu guide](docs/product/release-readiness.md#personal-feishu-release-guide):
  verified personal user ownership, illustrated released UI, upgrade/use guidance
  and ownership/text/image readback; no corporate/bot substitution or implied
  chat-announcement authority. Keep account ids, drafts and raw evidence private.
- [Contributor attribution](docs/product/release-readiness.md#release-note-checklist):
  derive eligible non-founder community contributions from the tag range and
  merged PR metadata, preserve concrete bilingual attribution and product-first
  ordering, and omit both contributor sections when none qualifies.
- [Final release body](docs/product/release-readiness.md#final-release-body-usage-gate):
  teach each changed optional surface's activation, validation, disable/rollback,
  authority/privacy boundary and versioned docs in both languages. Validate the
  complete final body before publishing and after remote readback; no-change
  releases require the validator's explicit bilingual declaration. A candidate,
  checklist or release PR draft does not prove a released, usable capability.

## First-Screen Review Evidence

Treat public first screens as reviewable product presentation. Within the
requested outcome, existing design contract and scoped authorization, do not
add a confirmation round before commit, push or PR submission merely because
the viewport, hero, primary CTA or opening navigation changes. Reuse prior
approval; a new commit or routine refinement does not invalidate it.

Before submitting/updating the PR, inspect the actual first viewport and put a
screenshot/browser capture directly in the PR description's visual evidence.
Include useful before/after views for existing surfaces, affected desktop/narrow
views and a working preview link when available. State the tested revision and
synthetic/prototype/packaged/installed boundary. Evidence must be public-safe:
no private messages, paths or live operating context. Chat, PR comments, Todos
or final reports alone do not satisfy this rule. Refresh evidence on material
visual changes without asking again for the same authorized direction.

Ask only for a missing consequential decision, work outside accepted scope, or
an explicit user reservation of presentation approval. A first-screen change
alone creates no such reservation. Name the unresolved choice and continue
independent authorized work. This rule grants no merge, install, release, paid
execution or sensitive-publication authority. Existing approvals remain historical
evidence; older blanket-confirmation prose follows this current rule.

## Product Delivery Completeness

For product changes, read [task-first frontend delivery](docs/development/frontend-delivery.md#task-first-delivery--从用户任务开始)
before implementation. Identify affected frontend/Lark/CLI entrypoints and
inspect existing settings/capability editors before declaring a change backend-only.
Every new or materially extended caller-facing capability needs a shipped
frontend journey: discover purpose, perform the authorized operation, see
unavailable/error states and read back the result. Catalog text, a CLI command
or a copy-command button alone is incomplete. Reuse the typed owner; do not add
duplicate configuration. Frontend presence does not require automatic execution
of privileged tools. Internal helpers/providers belong to their owning
capability's interaction, not an independent screen.

For artifact-producing capabilities, the requested work is the deliverable.
An existing conversation, artifact preview/download and revision feedback can
satisfy the frontend journey. HTML, games, mini-worlds and video are creation
formats; they do not by themselves require a dedicated LoopX editor, simulator
or settings screen. Add a product surface only for an accepted user workflow.
Qualify the capability through actual requested works and meaningful revisions;
rendering or interaction checks alone do not establish creative quality or
communication effectiveness.

Before PR handoff, validate the packaged journey with its real owning backend
and a relevant failure/recovery case. Name changed entrypoints and evidence.
A staged prerequisite may be proposed as partial with its remaining journey and
linked owner; "developer only" is not an exemption. Completion checks do not
create new approval gates. Distinguish proposed, packaged and installed behavior.

## UI Design Standard

Before changing or reproducing any UI (including websites, dashboards, desktop,
documentation, prototypes, screenshots or framework migrations), read
[the design system](docs/development/design.md), especially
[whole-viewport attention](docs/development/design.md#earn-the-users-attention).
Apply it before implementation and in PR visual evidence. Match an approved
design source and use the system for unspecified details. For production
interactions follow task-first delivery above; label simulations and prototypes
without claiming packaged or installed readback.

## Public And Private Boundary

Never publish private department/team/customer/meeting/reporting/strategy or
local operating context in repository changes, PR descriptions or review
artifacts, including notes, fixtures, screenshots, examples and catalog rows.
Keep private planning/incident evidence in ignored `.local/` or another explicitly
ignored owner-approved location. Generalize material into public-safe product,
maintainer or developer language before publication; remove internal actors,
timelines, reporting needs, local paths, raw logs, private links and decisions.

Before staging public docs, fixtures, examples, catalogs or metadata, scan
candidates for private wording and ignored-artifact references. If already
pushed, stop normal delivery, use self-repair, clean current public heads and
report remaining PR-ref/cached-view cleanup as an explicit user/support gate.

## PR Review Comments

For a requested GitHub PR review, publish validated actionable findings on the
PR by default, unless local-only was requested or the material is private or
security-sensitive. Do not leave blockers only in chat; return the published URL
and a compact summary. Use [the review workflow](skills/loopx-pr-review/SKILL.md)
and execute the current capability-owned `pull_request_review_execution_contract_v2`.

Before publication, apply its typed-state, domain-neutrality, behavior-change,
guidance-vs-obligation, default-off-isolation and authority lenses. Flag concrete
substring/prose-classification misclassification risks, domain-specific generic
contracts and silent default changes. Prove feature-off parity on every changed
shared surface. Do not call enforced obligations guidance or give a protocol a
name that claims broader actor lifecycle/authority than it implements.

## Engineering Quality And Right-Sized Scope

### TypeScript-First Implementation

Prefer TypeScript and the existing typed owner for new/refactored domain-neutral
capabilities, control plane, orchestration, capability/context/memory composition
and shared projections. Follow the accepted [migration RFC](docs/architecture/rfcs/typescript-control-plane-migration-v0.md);
do not create a parallel Python decision owner. Stable domain providers,
specialized computation and transport adapters may remain Python with a placement
rationale. Do not expand into unrelated language rewrites or make full migration
a prerequisite for a bounded outcome.

### Source-Checkout Python Entry Points

From the intended worktree root, prepare with `uv sync --extra test` and use
`uv run --extra test python ...` / `uv run --extra test loopx ...`; an activated
compatible environment with that checkout installed is also valid. Follow the
[environment guide](docs/development/testing-and-quality.md#local-validation-environment--本地验证环境).
If provenance is uncertain, check `sys.executable` and `loopx.__file__`; a global
CLI may use another snapshot. Keep subprocesses on `sys.executable`, not nested
`uv run`. Preserve bootstrap discovery, supported versions, CI version coverage
and version-specific fixtures. Do not rewrite historical receipts or commit an
unrelated generated `uv.lock`.

### Evidence-Based Budget Decisions

When a size, structure or latency budget fails, follow the
[budget decision guide](docs/development/testing-and-quality.md#budget-failure-decisions):
measure the same base/head workload, identify what the limit protects and
compare information value and real redundancy before compaction, retention or
an evidence-backed increase. Hard limits and frozen experiment/promotion criteria
retain their authority; never relabel an earlier failure or add an approval flow.

Agent-consumed prose is behavior. Before shortening it, compare obligations
clause by clause: actor, ordering, modality, evidence provenance, scope,
continuation and stop conditions. Stable fields/commands/enums do not prove
instruction parity. Use `observable_semantics.decision_text_assessment` from the
current review capability; prefer justified regression-budget growth over losing
useful meaning. A host's hard loading limit needs complete delivery through
reachable task-specific instructions, not silent truncation.

### Refactor Real-Path Validation

Before claiming refactor delivery, follow the [real-path gate](docs/development/testing-and-quality.md#refactor-real-path-gate--重构真实路径门):
validate the affected production entrypoint and real backend, beyond mocks or
unit tests. PostgreSQL authority-store changes require its integration suite on
an isolated real server; report exact source/results. Use a separate database or
tenant and disposable runtime with synthetic fixtures or owner-authorized
read-only snapshots. Never test by promoting, rewriting or corrupting active
Goal/registry/writer-fence/Todo/lease state. An unavailable environment is an
evidence gap that holds delivery qualification, not a passing skip. Keep code
volume a cost: refactors should localize the next change, test and rollback,
not introduce unused production structure.

Performance qualification follows the [default-provider rule](docs/development/testing-and-quality.md#default-provider-performance-qualification--默认-provider-性能资格).
The current default local authority is SQLite: its real-path performance gates
qualify default delivery. A File-only performance miss must be disclosed but
does not block that SQLite qualification. Explicit File performance work still
qualifies File separately. Keep correctness, durability, authority, recovery and
data-integrity checks for every affected supported backend, including File and
the required real PostgreSQL suite. Preserve historical failures and frozen
experiment criteria; changing the qualification scope is not a historical pass.

### Bounded Future-Facing Refactoring

During development and before approving or merging, apply the
[bounded refactoring and scope-fit review](docs/development/README.md#engineering-scope-and-refactoring)
to the touched behavior and adjacent owner. Include a same-domain, reversible,
behavior-preserving improvement when locally reviewable and covered by parity;
it need not be strictly required for the immediate fix. Keep larger work as a
focused follow-up. Report applied/deferred/unnecessary with the concrete boundary;
no speculative framework or unrelated migration.

## Capability And Extension Placement

Before adding an ability, read [placement decisions](docs/reference/extensions.md#placement-decision-for-agents).
Name public capabilities after caller outcomes; reuse the nearest capability
and typed owner. Record capability id, provider id, builtin/extension origin
and why that owner is or is not sufficient before editing. The guide owns
builtin capability, optional distribution, extension lifecycle and private-helper
placement; independent installation alone does not justify a new capability.

Before adding a module, builder, protocol field, CLI option, fixture, smoke section
or abstraction, apply [scope-fit review](docs/development/README.md#scope-fit-review).
Require an active caller, shipped behavior or explicit compatibility contract;
characterize before moving code and derive tests from independent semantics.
Keep state and delivery classifications typed, generic obligations domain-neutral,
and default-behavior changes disclosed. Prose heuristics need a documented reason,
a typed follow-up and review of misclassification risk; distinguish guidance from
machine-enforced obligations. Prefer complete, reversible slices, replace stale
canonical material, and avoid unused structure or duplicate authority.

## Automation And Monitor Todos

Keep generic heartbeat prompts free of one-off project/PR monitor policy.
Recurring watches belong in canonical `continuous_monitor` Todos with compact
`claimed_by`, `unblocks_todo_id` and evidence metadata; discover them through
status/quota/Todo projections. Change the saved prompt only for heartbeat
lifecycle contract changes or an explicit user request to change the scheduler.
See [prompt layers](docs/heartbeat-automation-prompt.md#two-prompt-layers).

## Projection Sink Design

When adding a display sink (Lark Base, dashboard, chat summary or report), follow
[the projection sink contract](docs/product/core-control-plane/state-machine.md#7-projection-sink-machine).
Use bounded public-safe state projections rather than project-private raw files;
retain source/evidence/gates. Sinks should preserve row lineage as data.
Public/multi-user sinks require redacted evidence. An owner-only private planning board requires explicit
user authorization for that boundary and retains the contract's agent scope and
credential restrictions. A sink displays state; it does not become its authority.

## Smoke Retention Policy

Before adding, retaining or consolidating a smoke, read
[good smokes](docs/development/good-smokes.md), including independent semantic
oracles, durable behavior, public-safe fixtures and preservation of unique
integration coverage. Keep shipped behavior, reusable contracts, boundary checks,
automation regressions and representative fixtures likely to catch future bugs;
do not freeze dated prose or temporary builders. Roughly 500 lines triggers a
review, not automatic deletion; thin smoke assertions should exercise product logic.

For benchmark changes, follow [benchmark smoke boundaries](docs/development/good-smokes.md#benchmark-boundaries)
and [archive placement](benchmark/README.md#archive-placement). Active smokes
protect stable toolkit contracts; experiment-specific validation stays with the
research workspace. Archives do not enter active CI benchmark execution. Never
require raw tasks, trajectories, logs, verifier tails, credentials, uploads,
leaderboard submissions or private local artifact paths.

## LoopX Self-Repair

When LoopX behavior is surprising, too small, contradictory, or called
out by the user as likely wrong, use the project skill
`skills/loopx-self-repair/SKILL.md`. Treat recurring mistakes as product
or process gaps: update the skill, interaction docs, active-state projection,
or focused smoke so the lesson is durable. Do not resolve self-repair by
lowering gates, guessing around contradictory payloads, or committing private
logs and local state.
