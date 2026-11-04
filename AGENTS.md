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

Start with [README](README.md), the [roadmap](docs/architecture/rfcs/loopx-overall-roadmap-v0.md),
the [capabilities](loopx/capabilities/README.md) and [protocols](docs/reference/protocols/README.md).
The roadmap distinguishes shipped boundaries from acceptance still to qualify.
Core rules live in `loopx/control_plane/`; capability contracts in
`loopx/capabilities/`; extension lifecycle in `loopx/extensions/`; optional
distributions in `packages/`; product surfaces in `apps/`.

Codex and Antigravity load this file; `CLAUDE.md` imports the same policy for
Claude Code. Read a linked procedure when its trigger applies to the current
task; do not load every reference for every turn. Keep current work, evidence
and monitor targets in their canonical task/state, not in this file or a frozen
prompt. Keep the root within both the 24,000-byte (24 KB) Antigravity per-file
rule cap and the default 32 KiB Codex discovery budget, and do not eagerly
import linked detail documents. See
[loading](docs/development/README.md#agent-instruction-loading-and-maintenance)
for host-specific checks; delivery does not prove model compliance.

Prefer [context](docs/development/README.md#progressive-context-for-development):
current goals, constraints and acceptance criteria first; supporting evidence and
procedures on demand under their owning read/recovery contract.

## Goal-Oriented Development

Before selecting non-trivial work, resolve the requested outcome from user
direction, the linked issue/task, accepted contract or demonstrated bug. For
cross-cutting LoopX work, consult the [roadmap](docs/architecture/rfcs/loopx-overall-roadmap-v0.md)
and domain acceptance without making every fix wait for every RFC or inventing a
roadmap id. Check latest `main`, related PRs and canonical Todos so older task
prose cannot override corrected direction or duplicate work.

For recurring operational or performance failures, follow the
[optimization](docs/development/testing-and-quality.md#roadmap-aligned-optimization):
bind the failure to its owning acceptance before repair, separate caller, shared
typed transport/semantics and provider costs, and reuse the common contract. A
faster lookup, timeout increase or promotion is only interim until sustained
acceptance is proven; reconcile the existing checkpoint as evidence changes.

Carry one compact delivery brief from task to PR (goal/source, current gap,
observable result, owning boundary and decisive acceptance evidence), keeping
private Goal state out of public artifacts. Choose a complete, independently
reviewable and reversible outcome slice; small diffs, fields, receipts, test
counts and merged PR counts do not establish progress. Characterization,
prerequisites, research, docs and maintenance are valid when they remove an
evidenced gap or enable a named next step.

For long-horizon improvements, trace the accepted goal and structured state
through feedback, the replan decision, subsequent work and validated outcomes.
Check whether new evidence changed the action or justified continuing it, and
preserve negative results and uncertainty (`state survival`, graph edges, read
receipts or replan records alone do not prove adoption or benefit). Compare
outcomes and complete costs under comparable conditions using the
[measurement contract](docs/architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.md#6-measurement-model);
do not force a direction change merely to increase replan counts.

Continue through implementation, integration, negative cases and readback while
authorized work remains feasible. Do not stop at setup, a serializer, a mock or
an isolated smoke when the useful outcome is missing, and do not expand scope
merely to enlarge a PR. When a staged boundary is necessary, name the delivered
delta, remaining gap, next owner/dependency and verification/rollback benefit.
Reuse or update an existing successor without ceremonial follow-up tasks; real
authorization, cost and operational stop gates remain.

When adding or extending state/protocol vocabulary (Enum, `Literal`, named
closed set or TypeScript `as const`), answer whether it reuses an owner, extends
a registered vocabulary, remains local or creates a shared contract, and follow
the [diff advisory](CONTRIBUTING.md#check-new-semantic-vocabulary-while-developing)
before the full-tree check (including intentional untracked sources). For
multi-Agent changes, qualify dependency artifacts, receiver adoption, claim/lease
handling, independent acceptance and result return; sending a message or
registering workers does not prove collaboration, and missing frontend/Lark/CLI
companion work makes a product journey partial.

Before delivery, reconcile the result with the original/current goal, update its
task and RFC checkpoint when the boundary changes, and preserve passed, failed
and untested distinctions. If user feedback exposes the same missing outcome,
repair the owning rule, active task or projection through self-repair rather
than appending stronger prose. PR review must execute the capability-owned
`problem_context` delivery judgment; author declarations do not certify it.

## Commit And PR Hygiene

### Worktree And PR Gate

For any tracked repository change beyond a trivial typo fix, create or use a
dedicated clean `git worktree` on a `codex/` branch based on the verified target
remote's latest `main` (or the user-named integration/release branch), verifying
merge base and actual PR base before pushing so unrelated history cannot leak.
Do not implement changes directly in a dirty primary worktree; if a dirty tree
holds valuable work, classify it read-only, copy the valuable subset into the
clean worktree, and clean the original only after merge or explicit obsolescence
(leaving unrelated untracked local artifacts alone). Every tracked change must
go through a branch PR before reaching `main` unless explicitly marked local-only.

### Author Submission And Review Entry

Open a completed, independently reviewable proposal as a normal PR. Missing
review, pending/failed CI, change-quality holds and unavailable merge readiness
are not reasons to keep it Draft; record those limits in the PR and reserve
Draft for unfinished author work with a concrete remaining step. For an
authorized author delivery, mark a completed Draft ready, read back its state
and unchanged head, and refresh the review packet (Drafts are excluded from the
review queue). A reviewer must obey the packet's action authority and may not
mark another author's Draft ready without authorization. Keep exact-head review,
required validation, first-screen evidence and merge authorization separate.

### DCO Sign-Off

Every PR commit needs a `Signed-off-by` trailer: always use `git commit -s`.
Repair a missing trailer with `git commit --amend -s` or rebase before pushing
(see [DCO](CONTRIBUTING.md#license-and-dco-sign-off)).

### Bilingual RFC Diff Gate

For an existing English/Chinese RFC pair under `docs/architecture/rfcs/`,
including ledger entries, both files must change in the same PR. Check staged
files before committing against the verified PR target ref (`PR_BASE_REF`):

```bash
: "${PR_BASE_REF:?set the verified PR target ref}"
uv run --extra test python scripts/check_rfc_bilingual.py --base "$PR_BASE_REF" --staged
```

Before merge, run `uv run --extra test python scripts/check_rfc_bilingual.py --base "$PR_BASE_REF" --head HEAD`
against the PR's target branch and exact reviewed head (`uv run --extra test loopx canary premerge --from-git-diff --git-diff-base "$PR_BASE_REF"`
runs this as a direct blocking check, including in the quick tier). A failure holds merge even when CI waiting is
disabled or admin bypass is authorized; unstaged translations cannot satisfy it,
and paired diffs do not certify semantic equivalence.

### Staging And Public Boundary

Before staging non-trivial changes:

1. Read `git status --short --branch`, `git diff --stat`, `git diff --name-only`
   and `git ls-files --others --exclude-standard`.
2. Classify every path (product code, core docs, durable smoke, local/private
   state, or obsolete artifact) and scan for credentials, private state, local
   absolute paths, raw benchmark logs/trajectories/verifier output and internal links.
3. Stage explicit pathspecs only (`never git add .`), split by reviewer logic
   (runtime/API, public docs/protocols, validation/cleanup), and follow
   [capture retention](docs/development/documentation-layout.md#visual-evidence-retention)
   before staging media or capture manifests.

### Merge Authority And Validation

Before approval or merge, read the repository-kept
[merge workflow](skills/loopx-pr-merge/SKILL.md) and its
[self-merge rules](skills/loopx-pr-merge/SKILL.md#self-merge-eligibility-and-benchmark-exceptions);
installing it is optional and does not replace repository policy or the current
capability-owned review.

Control-plane, runtime and product surfaces (`loopx/**`, `apps/**`, `packages/**`,
permissions/authority, persisted state/receipts, quota/scheduler/todo rules, and
CLI/API contracts) require explicit maintainer merge authorization. Propose and
review the exact head, pass required validation, and run merge-readiness
(`uv run --extra test loopx pr-review --check-merge-readiness NUMBER@HEAD_OID`,
plus `--goal-id GOAL` for Goal-managed work) for that unchanged head. A
`ready=true` result permits the normal path. Explicit maintainer authorization
may use admin bypass to override a `ready=false` readiness or GitHub policy
hold, but never missing exact-head review, required validation, unresolved
findings or threads, a changed head, an unmergeable conflict, or absent merge
authority. Small diffs, green CI or published reviews do not grant merge
authority; without authorization, leave the merge to the maintainer.

For author-owned PRs, GitHub blocks formal self-approval: publish a `COMMENTED`
review on the exact head carrying the approval conclusion and an English verdict.
Maintainers may self-merge single-purpose, low-risk public-doc, contributor
metadata, test, example or narrow-cleanup PRs (and the bounded benchmark
seam/helper exceptions) only when all conditions in
[self-merge rules](skills/loopx-pr-merge/SKILL.md#self-merge-eligibility-and-benchmark-exceptions)
hold and `uv run --extra test loopx canary premerge --from-git-diff --git-diff-base "$PR_BASE_REF"`
(or equivalent risk-based validation) passes. After merging, sync local `main`, preserve unrelated untracked artifacts
and continue the next safe project batch.

### Named Public Releases

Before completing a named public release, follow the existing release owners:

- [Personal Feishu guide](docs/product/release-readiness.md#personal-feishu-release-guide):
  verified personal user ownership, illustrated released UI, upgrade/use guidance
  and ownership/text/image readback; no corporate/bot substitution, implied
  announcement authority, or private account/draft/raw evidence exposure.
- [Attribution](docs/product/release-readiness.md#release-note-checklist):
  derive eligible non-founder community contributions from the tag range and
  merged PR metadata with concrete bilingual attribution and product-first
  ordering; omit both sections when none qualifies.
- [Release body](docs/product/release-readiness.md#final-release-body-usage-gate):
  teach each changed optional surface's activation, validation, disable/rollback,
  authority/privacy boundary and versioned docs in both languages; validate the
  complete body before publishing and after remote readback.

## First-Screen Review Evidence

Treat public first screens as reviewable product presentation. Within the
requested outcome, existing design contract and scoped authorization, do not
add a confirmation round before commit, push or PR submission merely because
the viewport, hero, primary CTA or opening navigation changes; reuse prior
approval across routine refinements.

Before submitting/updating the PR, inspect the actual first viewport and put a
public-safe screenshot/browser capture directly in the PR description's visual
evidence (with useful before/after and desktop/narrow views, preview link when
available, tested revision, and synthetic/prototype/packaged/installed boundary).
Chat, PR comments, Todos or final reports alone do not satisfy this rule. Ask
only for a missing consequential decision, work outside accepted scope, or an
explicit user reservation of presentation approval; this rule grants no merge,
install, release, paid-execution or sensitive-publication authority.

## Product Delivery Completeness

For product changes, read [frontend delivery](docs/development/frontend-delivery.md#task-first-delivery--从用户任务开始)
before implementation. Identify affected frontend/Lark/CLI entrypoints and
inspect existing settings/capability editors before declaring a change backend-only.
Every new or materially extended caller-facing capability needs a shipped
frontend journey: discover purpose, perform the authorized operation, see
unavailable/error states and read back the result. Reuse the typed owner without
duplicate configuration or automatic execution of privileged tools. For
artifact-producing capabilities (HTML, games, mini-worlds, video), the requested
work, conversation preview/download and revision feedback satisfy the journey
without a dedicated editor unless an accepted workflow requires one. Before PR
handoff, validate the packaged journey with its real owning backend and a
failure/recovery case, distinguishing proposed, packaged and installed behavior.

## UI Design Standard

**Avoid unnecessary secondary labels and descriptions. Let the layout do the explaining.**
Use hierarchy, grouping, spacing and clear control names to make the interface
understandable. Remove slogans, heading restatements and implementation or Agent
instructions from product UI. Preserve accessible control names, input
constraints, permission/activation consequences, errors, recovery and action
outcomes; place optional help on demand.

Before changing or reproducing any UI, read [design system](docs/development/design.md),
especially [viewport attention](docs/development/design.md#earn-the-users-attention),
and carry the primary user question, default-versus-detail choices and the
[populated-page review](docs/development/design.md#populated-page-review)
in the task/PR evidence. Inspect the affected page with long, repeated content
and mixed states rather than quiet fixtures alone, and name the observed
composition and any remaining failure.

## Public And Private Boundary

Never publish private department/team/customer/meeting/reporting/strategy or
local operating context in repository changes, PR descriptions or review
artifacts (notes, fixtures, screenshots, examples, catalog rows). Keep private
planning/incident evidence in ignored `.local/` or another owner-approved
ignored location, and generalize public material by removing internal actors,
timelines, reporting needs, local paths, raw logs, private links and decisions.
Scan candidates before staging; if already pushed, stop normal delivery, use
self-repair, clean current public heads and report remaining PR-ref/cached-view
cleanup as an explicit user/support gate.

## PR Review Comments

For a requested GitHub PR review, publish validated actionable findings on the
PR by default (unless local-only or private/security-sensitive), returning the
published URL and compact summary. Use [review workflow](skills/loopx-pr-review/SKILL.md)
and execute the current capability-owned `pull_request_review_execution_contract_v2`.
Apply its typed-state, domain-neutrality, behavior-change, guidance-vs-obligation,
default-off-isolation and authority lenses; flag substring/prose-classification
risks, domain-specific generic contracts and silent default changes, and prove
feature-off parity on every changed shared surface.

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
`uv run --extra test python ...` / `uv run --extra test loopx ...` (or an
activated compatible environment with that checkout installed), following the
[environment](docs/development/testing-and-quality.md#local-validation-environment--本地验证环境).
Verify `sys.executable` and `loopx.__file__` when provenance is uncertain, keep
subprocesses on `sys.executable` (not nested `uv run`), preserve version coverage
and fixtures, and never rewrite historical receipts or commit an unrelated `uv.lock`.

### Evidence-Based Budget Decisions

When a size, structure or latency budget fails, follow the
[budget decisions](docs/development/testing-and-quality.md#budget-failure-decisions):
measure the same base/head workload, identify what the limit protects and
compare information value and real redundancy before compaction, retention or
an evidence-backed increase. Hard limits and frozen experiment/promotion criteria
retain their authority; never relabel an earlier failure or add an approval flow.
Agent-consumed prose is behavior: before shortening it, compare obligations
clause by clause (actor, ordering, modality, evidence provenance, scope,
continuation and stop conditions) using `observable_semantics.decision_text_assessment`
and deliver host-capped detail through reachable task-specific instructions
rather than silent truncation.

### Refactor Real-Path Validation

Before claiming refactor delivery, follow the [real-path gate](docs/development/testing-and-quality.md#refactor-real-path-gate--重构真实路径门)
and the [default provider](docs/development/testing-and-quality.md#default-provider-performance-qualification--默认-provider-性能资格):
validate the affected production entrypoint and real backend beyond mocks/unit
tests. PostgreSQL authority-store changes require its integration suite on an
isolated real server/tenant (unavailable environment is an evidence gap, not a
pass); never test by promoting, rewriting or corrupting active Goal/registry/
writer-fence/Todo/lease state. SQLite real-path performance gates qualify default
local delivery (a File-only performance miss must be disclosed but does not block
SQLite qualification), while correctness, durability, authority and recovery
checks remain required for every affected backend.

### Bounded Future-Facing Refactoring

During development and before approving or merging, apply the
[scope and refactoring](docs/development/README.md#engineering-scope-and-refactoring)
to the touched behavior and adjacent owner. Include a same-domain, reversible,
behavior-preserving improvement when locally reviewable and covered by parity;
report applied/deferred/unnecessary with the concrete boundary.

## Capability And Extension Placement

Before adding an ability, read [placement](docs/reference/extensions.md#placement-decision-for-agents).
Name public capabilities after caller outcomes; reuse the nearest capability
and typed owner. Record capability id, provider id, builtin/extension origin
and why that owner is or is not sufficient before editing. Before adding a
module, builder, protocol field, CLI option, fixture, smoke section or
abstraction, apply [scope fit](docs/development/README.md#scope-fit-review):
require an active caller, shipped behavior or explicit compatibility contract,
characterize before moving code, derive tests from independent semantics, keep
state/delivery classifications typed and generic obligations domain-neutral, and
disclose default-behavior changes.

## Automation And Monitor Todos

Keep generic heartbeat prompts free of one-off project/PR monitor policy.
Recurring watches belong in canonical `continuous_monitor` Todos with compact
`claimed_by`, `unblocks_todo_id` and evidence metadata; discover them through
status/quota/Todo projections. Change the saved prompt only for heartbeat
lifecycle contract changes or an explicit user scheduler request; see
[prompt layers](docs/heartbeat-automation-prompt.md#two-prompt-layers).

## Projection Sink Design

When adding a display sink (Lark Base, dashboard, chat summary or report), follow
[projection contract](docs/product/core-control-plane/state-machine.md#7-projection-sink-machine).
Use bounded public-safe state projections rather than project-private raw files,
retain source/evidence/gates and row lineage, redact public/multi-user sinks,
and require explicit user authorization for owner-only private planning boards.
A sink displays state; it does not become its authority.

## Smoke Retention Policy

Before adding, retaining or consolidating a smoke, read
[good smokes](docs/development/good-smokes.md). Keep shipped behavior, reusable
contracts, boundary checks, automation regressions and representative public-safe
fixtures with independent semantic oracles; do not freeze dated prose or
temporary builders (~500 lines triggers review). For benchmark changes, follow
[benchmark boundaries](docs/development/good-smokes.md#benchmark-boundaries)
and [archives](benchmark/README.md#archive-placement): active smokes
protect stable toolkit contracts without entering archives into CI or requiring
raw tasks, trajectories, logs, verifier tails, credentials, uploads,
leaderboard submissions or private local paths.

## LoopX Self-Repair

When LoopX behavior is surprising, too small, contradictory, or called out by
the user as likely wrong, use `skills/loopx-self-repair/SKILL.md`. Treat
recurring mistakes as product or process gaps: update the skill, interaction
docs, active-state projection, or focused smoke so the lesson is durable,
without lowering gates, guessing around contradictory payloads, or committing
private logs and local state.
