# Agent Delivery Workflows

These are the detailed repository instructions linked from [AGENTS.md](../../AGENTS.md).
Read the applicable sections before the corresponding action; their requirements,
exceptions and authority boundaries remain mandatory. Commands and backticked
paths are relative to the repository root unless stated otherwise.

## Instruction Loading And Maintenance

`AGENTS.md` is the shared repository entry point. Root `CLAUDE.md` contains
only `@AGENTS.md`, so Claude Code imports the same instructions without a
second policy copy. This also supports Claude Code sessions without native
AGENTS discovery. Do not replace the import with a prose request to read the
file; an import makes loading independent of the model's tool choice.

Codex discovers project instructions from the repository root through the
launch working directory, subject to its combined project-document budget
(32 KiB by default). Keep root `AGENTS.md` below 200 lines and 12 KiB to leave
headroom for other discovered instructions. A larger user/global instruction
set can still exhaust the combined budget; verify the actual loaded content
when diagnosing a host. Do not make a larger personal limit a prerequisite for
this repository, or change someone's global configuration to hide root growth.

Claude Code loads `CLAUDE.md` and expands its import at session start. Current
versions also support native AGENTS discovery, but the import keeps this
repository compatible with both paths. The detailed documents are ordinary
Markdown links, not `@` imports: read the applicable section before its action.
This preserves mandatory workflow details without loading every release,
benchmark and operational procedure into every conversation. Moving text into
an automatically imported file would retain the original context cost.

When checking host adoption, use a fresh session rooted in the intended
checkout. In Codex, inspect the loaded repository-instruction message or session
record, including the final Benchmark Smoke Classification section. In Claude
Code, use `/memory` and `/context` to verify `CLAUDE.md` and its AGENTS import.
An existing conversation may retain older instructions: explicitly re-read
changed instructions or start a fresh session before relying on an update.
Loading evidence does not prove model compliance or grant execution authority.

For instruction edits, check bytes, lines, local links/anchors and the Claude
import. Compare moved obligations, exceptions, evidence provenance, modality,
ordering, continuation and stop conditions against the base revision. Preserve
existing root heading anchors used by skills or public links. Keep universal
constraints in the root; move task details to their existing owner or the
applicable delivery, engineering or operations section. Validate representative
routes (development, UI, review/merge, release and monitoring) and an unrelated
task so moving a procedure neither hides a required read nor applies it globally.

Host behavior references:
[OpenAI Docs: AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md),
[Claude Code: project instructions](https://code.claude.com/docs/en/memory).

## Goal-Oriented Development

Before selecting non-trivial work, resolve the current requested outcome from
user direction, the linked issue/task, accepted contract or demonstrated bug.
For cross-cutting LoopX work, consult the [overall roadmap](../../docs/architecture/rfcs/loopx-overall-roadmap-v0.md)
and relevant domain acceptance; do not make every fix wait for every RFC or
invent a roadmap id. Check latest `main`, related PRs and canonical Todos so an
older task description cannot override corrected direction or duplicate work.

For recurring operational or performance problems, connect the demonstrated
failure to the owning roadmap/RFC acceptance before choosing a repair. Separate
caller overhead, shared typed semantics/transport and provider-specific costs;
prefer the existing common contract where behavior is shared. Follow the
[optimization evidence guide](../../docs/development/testing-and-quality.md#roadmap-aligned-optimization).
Distinguish an interim mitigation from closing the owning acceptance: faster
lookup, a larger timeout or successful promotion alone does not qualify sustained
operation. Reconcile the existing checkpoint when the evidence changes it;
do not add a parallel roadmap or require unrelated RFC work for a bounded fix.

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

When a change introduces or extends a state classification, protocol literal,
Enum, `Literal`, named closed set, or TypeScript `as const` vocabulary, answer
whether it reuses an existing owner, extends a registered vocabulary, remains
local, or creates a new shared contract. Before the full-tree semantic check,
run the development-time advisory over the current diff:

```bash
uv run python scripts/generate_semantic_inventory.py --changed-from HEAD
```

Pass each intentional untracked source with `--include-untracked loopx/path.py`;
the probe never scans untracked or ignored files automatically. Findings are
review prompts, not a gate or proof of semantic equivalence, and an empty result
does not cover dynamic construction or unsupported syntax.

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

Every commit in a pull-request branch must include a
`Signed-off-by: Your Name <your.email@example.com>` trailer, or the `DCO`
check will reject the PR. Always commit with `git commit -s`. If a commit is
already missing the trailer, amend it with `git commit --amend -s` (or an
interactive rebase for multiple commits) before pushing. See CONTRIBUTING.md
for the full DCO policy.

Only skip this worktree/PR gate when the user explicitly says the change is
local-only and must not be proposed for the repository.

### Bilingual RFC Diff Gate

For an existing English/Chinese RFC pair under `docs/architecture/rfcs/`,
including its ledger entries, both files must change in the same PR. Check the
staged files before committing:

```bash
python3 scripts/check_rfc_bilingual.py --base origin/main --staged
```

The staged check includes earlier branch commits. Before merge, run
`python3 scripts/check_rfc_bilingual.py --base origin/main --head HEAD` against
the PR's actual target branch and exact reviewed head. The source-checkout
`uv run --extra test loopx canary premerge --from-git-diff` runs this as a direct
blocking check, including in the quick tier. A failure holds merge even when CI
waiting is disabled or admin bypass is authorized. It checks the committed diff;
an unstaged translation cannot satisfy it. The check uses the merge base, covers
paired deletion/renaming, and leaves legacy single-language files alone. Review
the actual translation; paired diffs do not certify semantic equivalence.

### Staging And Publication

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

### Merge Authority And Self-Merge

Control-plane, runtime and product surfaces require explicit maintainer
merge authorization. A change that alters the behavior of `loopx/**`, `apps/**`
or `packages/**`, a permission or authority boundary, a persisted state or
receipt contract, a quota/scheduler/todo rule, or a CLI/API contract must be
proposed and reviewed on its exact head. After the required validation passes
and exact-head merge readiness returns ready, an agent may use admin bypass
when the maintainer has explicitly authorized that merge or delivery batch.
A small diff, successful validation, published review or green CI does not grant
merge authority by itself. Admin bypass never substitutes for exact-head review,
required validation or merge readiness. Without explicit authorization, leave
these changes for the maintainer to merge; when the affected boundary is unclear,
use the same authorization requirement.

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
  `loopx pr-review --goal-id GOAL --check-merge-readiness NUMBER@HEAD_OID`
  returned ready for
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

## Release Workflows

### Personal Feishu Release Guide

Every named public release must include an illustrated Feishu release guide
owned by the maintainer's verified personal user account. Follow the
[release guide procedure](../../docs/product/release-readiness.md#personal-feishu-release-guide)
before calling release communication complete. Verify account and document
ownership, use public-safe screenshots of the released UI, teach upgrade and
capability use, and read back the published text and images. Do not substitute a
corporate account or bot, claim a candidate is released, or infer authorization
for chat announcements or other social channels. Keep account identifiers,
private reference documents, drafts, and validation logs out of the repository.

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

## First-Screen Review Evidence

Treat the first visible screen of public product surfaces as reviewable product
presentation. For authorized work within the requested outcome and existing
product/design contract, do not add a separate user-confirmation round before commit,
push or PR submission merely because the first viewport, hero, primary CTA or
opening navigation changes. Reuse prior scoped approval; a new commit or a
routine refinement does not invalidate it.

Before submitting or updating the PR, inspect the actual first viewport and put
its screenshot or browser capture directly in the PR description's visual
evidence section. Include a useful before/after comparison for an existing
surface, affected desktop/narrow views, and a working preview link when one is
available. Identify the tested revision and whether the data and surface are
synthetic, a prototype, packaged or installed. Use public-safe evidence; keep
private messages, paths and live operating context out. A screenshot only in
chat, a PR comment, a Todo note or the final report does not satisfy this rule.
Refresh the evidence when the visible result materially changes, without
requesting another approval for the same authorized direction.

Ask only for a genuinely missing consequential decision, work outside the
accepted scope, or an explicit user instruction reserving presentation approval.
Do not infer such a reservation from a first-screen change alone. Name the
specific unresolved choice and continue independent authorized work. This
presentation evidence rule does not grant merge, installation, release, paid
execution or sensitive-data publication authority. Existing approval records
remain historical evidence; older prose that merely repeats the blanket
first-screen confirmation requirement follows this current rule.

## Product Delivery Completeness

For product changes, identify affected user entry points (frontend, Lark, CLI)
while planning. Inspect existing settings and capability editors before calling
a configuration change backend-only; include necessary companion work in the
same delivery plan, reusing the existing configuration owner and projection.

Every new or materially extended caller-facing capability must enter the
shipped frontend through an existing appropriate surface: users can discover
its purpose, perform the authorized user operation, see unavailable/error states,
and read back the result. A CLI command, catalog row, static description or
"copy this command" button alone does not complete frontend delivery. Reuse the
same typed owner; frontend presence does not require a duplicate configuration
switch or automatic execution of privileged tools. Internal helpers and providers
belong to their owning capability's interaction rather than a separate screen.

Before PR handoff, validate that journey in the packaged frontend alongside the
real owning rule/backend, including a relevant failure or recovery case. State
which frontend/Lark/CLI entrypoints changed and what was verified. A staged CLI
or backend prerequisite may be proposed as partial, with its remaining frontend
journey and linked owner; do not mark the capability complete or use "developer
only" as an automatic exemption. These are agent-owned completion checks, not
new approval gates.

## UI Design Standard

Apply the whole-viewport attention review in `docs/development/design.md`
("Earn The User's Attention") before implementation and in PR visual evidence.
For production-bound interactive UI changes, carry one ordinary user task through
`docs/development/frontend-delivery.md`: compare the shortest authorized journey
before coding, validate the packaged interaction and owning state, and distinguish
proposed changes from installed behavior. For documentation and prototypes, name
the simulated boundary without claiming packaged or installed readback.

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
