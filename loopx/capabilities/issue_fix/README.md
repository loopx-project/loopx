# Issue-Fix Capability

[中文](README.zh-CN.md) · [Capability index](../README.md) ·
[State Kernel/domain-state case study (中文)](docs/state-kernel-domain-state-case-study.zh-CN.md) ·
[Workflow contract](docs/protocols/issue-fix-workflow-contract-v0.md) ·
[Discovered issue promotion](docs/protocols/issue-fix-discovered-issue-promotion-v0.md) ·
[Acceptance loop](docs/protocols/issue-fix-acceptance-loop-v0.md) ·
[Ark Managed Agent qualification](../../../docs/reference/protocols/ark-managed-agent-issue-fix-qualification-v0.md) ·
[Reviewer recommendation](docs/protocols/issue-fix-reviewer-recommendation-v0.md) ·
[Reviewer request](docs/protocols/issue-fix-reviewer-request-v0.md) ·
[Reviewer notification sinks](docs/protocols/issue-fix-reviewer-notification-sinks-v0.md) ·
[Lark feedback inbox](../../extensions/lark/docs/lark-event-inbox.md)

Issue-fix is LoopX's product path for turning a public repository issue into a
small, validated, reviewable pull request and then keeping that PR moving until
its lifecycle has a clear outcome. The capability is designed for a
long-running issue-to-PR employee, not for a one-shot code generator: LoopX
keeps goal state, todos, authority, repository evidence, validation, reviewer
routing, monitors, human gates, and terminal closeout outside any single chat
turn.

The core product outcome is a focused fix PR when the issue is suitable. A
public comment or justified triage remains useful for rejecting unsuitable
candidates or recording a concrete blocker, but it is not a substitute for the
fix-PR path when that path is feasible.

## What LoopX Provides Underneath

You do not need to know LoopX before using this capability. The shortest mental
model is: a coding agent can inspect and change a repository, while LoopX is the
local-first control plane that remembers what the agent is trying to achieve,
decides what may run next, exposes progress to people, and keeps the work alive
across chat turns and external waits.

GitHub remains the source of truth for issues, code, checks, reviews, and merge
state. LoopX adds the missing employee-control layer between a host agent and
GitHub:

| LoopX foundation | What it contributes to issue/PR fixing |
| --- | --- |
| Durable goal state | Keeps the objective, acceptance target, current status, next action, and compact outcome evidence after one model turn ends. |
| Todo ownership and routing | Separates agent work from concrete human decisions; records priority, `claimed_by`, blockers, successors, handoffs, and monitor work so two agents do not silently do the same task. |
| Kanban/status projection | Projects the same todo truth into a human-visible board or dashboard without making the board a second state machine. People can see who owns the issue, what was produced, and what is waiting. |
| Quota and scheduler policy | Uses `quota should-run` to decide whether a bounded work segment should run now, wait, repair state, or stay quiet. Unchanged polling backs off and does not count as delivery progress. |
| Authority and interaction gates | Separates technical capability from permission. Private material, public comments, push, PR creation, review requests, merge, and production actions can each require explicit recorded authority. |
| Evidence and repository context | Pins conclusions to a repository revision, source trust, freshness, repo-relative references, reproduction, and validation. Compact evidence survives; raw logs, credentials, and private bodies do not leak into public state. |
| Replan and handoff contracts | Converts CI failure, reviewer correction, missing information, or a stale branch into a runnable successor, a concrete blocker, or a scoped human question instead of losing the correction in chat. |
| Continuous monitors | Watches CI, review, mergeability, maintainer comments, stale branches, merged, and closed states; writes back only material transitions and terminates with an explicit outcome. |
| Event-backed wait and resume | Converts authoritative external transitions such as a merged PR into idempotent public-safe rollout events. Todos waiting on `resume_when=pr_merged:#123` become runnable through normal status/quota projection instead of relying on chat memory or directly executing code from a webhook. |
| Public/private boundary checks | Scans public artifacts and keeps local paths, credentials, runtime state, raw transcripts, tool logs, and private evidence out of commits and PRs. |

The issue-fix capability composes these generic foundations into domain packets
and CLI commands. The host agent still reads code, edits the worktree, runs
tests, and performs separately authorized GitHub actions. This division is what
turns “generate a patch once” into a visible, resumable issue-to-PR employee:

```text
public issue
  -> durable goal and claimed todo
  -> revision-pinned evidence and reproduction
  -> focused patch and validation
  -> explainable reviewer route and authority gate
  -> PR monitor and material-transition replan
  -> merged/closed evidence and idempotent rollout event
  -> resumed successor, next issue, or explicit no-follow-up
```

## Product Position

LoopX is the control plane, not the coding model or GitHub itself.

| Layer | Responsibility |
| --- | --- |
| Host agent/runtime | Read code, reproduce the bug, edit files, run tests, and perform explicitly authorized git/GitHub actions. |
| Issue-fix capability | Build public-safe workflow, feasibility, repository-context, reviewer, validation, and PR-lifecycle packets. |
| LoopX kernel | Persist goal/todo ownership, quota, authority, evidence, monitor, replan, and human-interaction state. |
| Repository/GitHub | Remain authoritative for code, policy, CI, review, mergeability, and terminal PR state. |
| Human maintainer | Own design judgment, repository policy, sensitive/private context, and any action outside recorded authority. |

The issue-fix packet builders do not silently publish. A host agent may create
or update a PR only when the current LoopX boundary records that authority and
repository policy allows it. Merge remains a separate decision unless it is
explicitly authorized.

## End-To-End Design

```mermaid
flowchart LR
  I["Public issue candidates"] --> S["Selection and feasibility"]
  S --> C["Revision-pinned repository context"]
  C --> R["Reproduction"]
  R --> F["Focused patch and regression test"]
  F --> V["Layered validation"]
  V --> O["Reviewer recommendation"]
  O --> P["Authority-gated PR publication"]
  P --> M["CI/review/mergeability monitor"]
  M --> T["Merged/closed terminal closeout"]
  T --> E["Idempotent rollout event"]
  E --> N["Resume successor, next issue, or no-follow-up"]
  H["Human judgment"] --> S
  H --> O
  H --> P
  H --> M
  LX["LoopX goal/todo/quota/evidence"] --> S
  LX --> C
  LX --> V
  LX --> M
  E --> LX
```

### 1. Candidate selection

The first round should select one issue. Prefer public open issues with a
traceback, failing test, minimal reproduction, bounded change scope, and a
repository-native focused validation surface. Avoid issues that require
private data, credentials, production systems, large design debates, or broad
semantic changes.

Every candidate should receive one explicit route:

- `fix_pr`: reproduction and validation are credible and scope is bounded;
- `comment_only`: a public clarification or diagnosis adds value, but a safe
  patch is not ready;
- `triage_only`: evidence is insufficient, scope is oversized, or following up
  would not add value.

The long-running employee's primary acceptance target is `fix_pr`; the other
routes protect quality and maintainer attention.

### 2. Repository-grounded understanding

The authority order is:

1. current checkout evidence;
2. repository-scoped historical memory;
3. external expert or bot advice.

Read repository policy, architecture, nearby source and tests, validation
commands, and recent related fixes at the pinned revision. Compact this into
`issue_fix_repository_context_input_v0`, including revision, repo-relative
source references, evidence aspect, source trust, and freshness. Memory and
expert conclusions are advisory until verified in the current checkout.

### 3. Reproduction before modification

Separate four outcomes instead of flattening every failure into a product bug:

- product bug reproduced;
- test or fixture bug;
- environment/dependency failure;
- report remains under-specified or cannot currently be reproduced.

When possible, make the existing focused test fail for the reported contract
before changing production code. Preserve compact pass/fail and command-label
evidence, not raw logs or local paths.

### 4. Focused patch and regression proof

Use a clean worktree and branch from the latest approved base revision. Keep
the patch small, explainable, and consistent with nearby repository patterns.
Add or adjust a focused test that would fail without the fix. Expand validation
only in proportion to risk.

### 5. Reviewer recommendation and default request

Reviewer selection is part of the control plane because a correct patch can
still stall when the wrong person is asked to review it. LoopX now provides:

```bash
loopx issue-fix reviewer-plan \
  --repo-path /path/to/approved/repo \
  --repo owner/repo \
  --base-ref origin/main \
  --exclude-reviewer @pull-request-author \
  --exclude-author-name "PR Author Git Name" \
  --reviewer-sources-json reviewer-sources.json \
  --execute \
  --format json
```

After the PR exists, a host with standing `external_review_request` or
`publish` authority should notify the default reviewer directly:

```bash
loopx issue-fix reviewer-request \
  --url https://github.com/owner/repo/pull/123 \
  --repo-path /path/to/approved/repo \
  --base-ref origin/main \
  --reviewer-sources-json reviewer-sources.json \
  --notification-sinks-json local-private-notification-sinks.json \
  --execute \
  --format json
```

The current evidence order is deliberately conservative:

1. repository `CODEOWNERS` matches for each changed path;
2. caller-verified public maintainer maps whose most-specific path route names
   a primary contact;
3. commit history for the exact changed path;
4. nearest module-directory history when a new file has no usable path
   history;
5. maintainer-map fallback or cross-module contacts when no scoped route
   applies or the primary contact is excluded.

The packet ranks candidates with source kinds, reason codes, changed-path
coverage, history counts, recency, confidence, public `source_refs`, compact
matched-route evidence, and whether a GitHub handle is actually requestable.
It never captures the maintainer-map body or commit email addresses, never
records the local repo path, and `reviewer-plan` never sends a review request.
`reviewer-request` fetches the live PR author, existing review requests,
completed reviews, LoopX-marked reviewer comments, and live comments that
explicitly mention a reviewer and ask for review; excludes them
automatically; and asks the top remaining requestable candidate. It first uses
a formal GitHub review request. Only when GitHub confirms that this action lacks
permission does it fall back to one concise PR comment mentioning the same
reviewer. The command reads the PR again and verifies either provider state or
the fallback comment's semantic review intent plus public URL before claiming
success. A retry recognizes either a legacy marker or a bounded explicit review-request comment and
sends no duplicate comment; ordinary mentions and discussion do not suppress a
request. Network and unknown provider errors remain blockers rather than
triggering comments. History is
read at the base revision so feature-branch commits do not recommend the
author; `--exclude-author-name` covers unresolved git-name aliases.
The permission fallback is reviewer-facing product copy, not an internal
receipt. It names the linked issue and compact PR-title change summary, points
to the PR description for motivation, validation, and risk, and does not expose
an idempotency marker.
The workflow plan also projects an `issue_fix_pr_description_contract_v0`
template adapted from the PR-review five-block structure. Code changes add two
reviewer-context sections: the smallest key-code or pseudocode slice, and a
post-fix reproduction using the repository CLI or focused code/test surface.
Motivation, approach, concrete changes, validation, and main-branch
risk/uncovered scope remain required. An infographic is optional only for a
complex change and never replaces textual evidence. The reviewer's verdict
section remains review-only and is not authored into the PR description.

Issue-backed PRs also carry an explicit `关联 Issue` / `Related Issues` block.
For a complete fix, the builder defaults to one standalone `Fixes #N` line per
issue (or `Fixes owner/repository#N` across repositories). For partial work it
uses `Related to #N`, which creates a normal reference without promising
automatic closure. GitHub accepts the `close`, `fix`, and `resolve` keyword
families, including their documented inflections, but LoopX normalizes them to
`Closes`, `Fixes`, or `Resolves` for stable output. Closing references require
an explicit assertion that the PR targets the default branch, because GitHub
ignores closing keywords on other base branches. The functional block is
applied after semantic preferences and PR lifecycle should verify it through
`closingIssuesReferences`. Closing keywords in commit messages can close an
issue, but GitHub does not then list the containing PR as the linked PR, so the
Issue Fix format keeps the keyword in the PR body rather than relying on commit
copy. Comments are not part of this closing contract. See GitHub's
[linked-issue contract](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue).
When a human confirms that an unresolved git display name belongs to a specific
GitHub account, `--identity-map-json` records that compact mapping as verified
identity evidence and reranks the same repository-native contribution evidence.

`--notification-sinks-json` optionally adds a parallel reviewer channel. A
configured GitHub request and configured Lark notification are independent
obligations: LoopX attempts both, while the permission-only GitHub comment
remains a fallback for the GitHub request itself. The first Lark adapter uses an explicitly
named, project-dedicated Lark/Feishu bot profile to mention the same reviewer in
an approved group and read the message back. It rejects default/shared bot
identities, never selects a different reviewer, and never copies the local bot
profile, destination, member mapping, or raw provider response into public
state. A stable hashed receipt prevents duplicate sends across retries.

When a connected goal explicitly enables the agent-scoped Reward Memory
experiment for `reviewer_artifact.summary` and its v1 config sets
`automation.automatic_recall=true`, `reviewer-request` invokes the shared
automatic hook and previews the application independently of secondary-sink
availability. With the flag off it performs zero provider calls. This
lets a fixer verify recall, current-artifact identity, and the proposed concise
Chinese summary with zero external writes while per-PR secondary notifications
are paused. A configured secondary notification additionally requires that
verified application receipt before sending. The caller must identify itself
with `--agent-id` and supply the summary plus its application reasoning:

```bash
loopx issue-fix reviewer-request \
  --url https://github.com/owner/repo/pull/123 \
  --repo-path /path/to/approved/repo \
  --goal-id example-goal \
  --project /path/to/connected/project \
  --agent-id registered-fixer \
  --reviewer-summary '修复请求生命周期中的重复通知，并保留回读验证。' \
  --reviewer-summary-reasoning '当前 PR 与召回的幂等通知经验作用域一致。' \
  --execute \
  --format json
```

This is a thin consumer of the shared Reward Memory core, not an Issue Fix
memory implementation. It verifies the exact surface, current PR identity,
current-artifact check, memory readback, attribution digests, and non-empty
summary before the secondary send. It never infers another peer's agent id and
does not hard-code OpenViking: the experiment resolves the surface's explicit
corpus set from the goal's ignored project provider config and uses that
surface's recall profile. Compatible corpora are attempted in declared order
until the first exact hit, with one query per corpus at this function boundary;
telemetry records the bounded provider calls. It never scans unrelated project
corpora. Read
authority combines the normalized corpus read-authority
kind with `standing_policy.authority_source_ref`; it is never inferred from a
provider or repository name. A successful no-sink preview is returned as
`reviewer_artifact_reward_memory_preview` and remains read-only. The canonical
GitHub reviewer request remains fail-open and runs first; only the explicitly
configured secondary notification fails closed when this receipt is missing or
stale.

A second, independent surface can be enabled at
`reviewer_notification.before_send`. Immediately before the configured Lark
CLI adapter would send, LoopX performs one bounded recall and accepts only one
exact, structured `hard_policy` delivery window. A passed application receipt
feeds that policy into the existing send-or-queue path; the sink continues to
own deduplication, restart-safe queue receipts, provider execution, and remote
readback. Disabled or unavailable recall, no compatible memory, and conflicting
policies all fail open to the caller's current sink policy. If neither source
provides a policy, delivery is unrestricted. This surface does not create a
generic preflight router or grant notification authority.

Long-running goals can register the local-private sink pointer once with
`configure-goal --issue-fix-reviewer-notification-config`. Subsequent
`reviewer-request --goal-id ... --project ...` calls discover it automatically,
use explicit reader/user and sender/bot profiles without changing the machine
default, verify mapped reviewer `open_id` values with the sender app before
sending, and persist only new
`sha256:` receipts in the PR lifecycle row. If that row is missing, execute
mode auto-materializes it from a fresh compact GitHub lifecycle read before
the external notification. Restart/retry returns `already_notified`; no
notification ledger or public config path is added.

The same local-private sink config may reference a generic
`lark_event_inbox_config_v0`. When a reviewer group is configured, issue-fix
auto-binds that inbox through `reviewer-feedback-inbox`; host collection runs
without an agent, and heartbeats periodically drain messages addressed to the
dedicated bot. A message is acknowledged only after its PR/todo/vision effect
or no-follow-up rationale is written back.

`--reviewer-sources-json` is the bridge for repository-specific public routing
knowledge. The host reads an approved public source, such as a maintainer-map
issue or repository document, and supplies only stable source id, public URL,
trust, freshness, observation time, path-prefix/glob routes, and
primary/fallback handles. LoopX
does not fetch or persist the raw page. The output keeps the URL beside each
candidate so a maintainer can audit why that person was selected.

`CODEOWNERS` remains the strongest repository-native signal. Commit volume is
only evidence of familiarity; it is not proof of maintainership, availability,
or review authority. See the [reviewer recommendation
contract](protocols/issue-fix-reviewer-recommendation-v0.md) for scoring,
identity, and future-signal details, and the [reviewer request
contract](protocols/issue-fix-reviewer-request-v0.md) for the external-write,
idempotency, and verification rules. Secondary delivery, dedicated-bot
isolation, local-private identity mapping, and readback are defined by the
[reviewer notification sink
contract](protocols/issue-fix-reviewer-notification-sinks-v0.md).

### 6. PR publication and public-write boundary

Before an external write, prepare a public-safe package containing:

- problem and root cause;
- bounded diff summary;
- focused and expanded validation;
- risk and omissions;
- reviewer evidence;
- PR body or comment draft.

PR creation, public comments, push, merge, and publish are external writes.
The host agent may perform only the actions covered by current boundary
authority. Reviewer notification is also an external write, but a standing
`external_review_request` or `publish` authority lets the agent perform the
formal request and its permission-only comment fallback automatically without
another user prompt. This does not authorize arbitrary comments.
Recommendation packets themselves remain read-only.

### 7. Continuous PR lifecycle

After a PR exists, create a `continuous_monitor` todo with a stable target and
cadence. `loopx issue-fix pr-lifecycle` projects compact public PR metadata
into one of four decisions:

- `runnable_successor`: CI failed, review requested changes, or the branch
  needs an actionable replan;
- `monitor_continuation`: checks/review are still pending or nothing material
  changed;
- `user_gate`: an explicit human decision is required;
- `no_followup`: the PR is merged or closed and the monitor can terminate.

With `--fetch-metadata`, a `CHANGES_REQUESTED` observation also reads a compact
review-response summary: thread counts, the latest changes-requested time, and
the head commit time. LoopX waits for re-review instead of creating another
patch successor only when at least one fetched thread exists, every fetched
thread is resolved, pagination is complete, and the head commit is newer than
the review. Missing, partial, or older evidence fails closed to the actionable
replan route. Review and thread bodies are never captured.

Identical polls should not create work, consume delivery quota, or spam the
maintainer. Material transitions must produce a successor, concrete blocker,
or structured no-follow-up; the agent must not stop silently in monitor-only
state.

When a review or public maintainer comment contains a concrete correction, the
host compacts it into `issue_fix_maintainer_correction_input_v0` and passes it
to `pr-lifecycle`. The compact input keeps only the correction kind, a public
source reference, a bounded summary, and one of: verification plus PR update
path, a concrete ambiguity question, or missing authority scopes. It never
copies the raw review/comment body.

With `--execute-transition`, an `actionable_patch` creates exactly one
`issue_fix_maintainer_correction_patch` todo claimed by the registered agent.
`semantic_ambiguity` and `missing_authority` create a concrete user gate that
blocks that same agent. `unchanged` creates no todo. The normalized correction
fingerprint and deterministic todo text make retries idempotent, while terminal
merged/closed state still takes precedence over late feedback.

When a connected lifecycle writeback observes `MERGED`, LoopX also appends one
repository-qualified, public-safe, idempotent `pr_merge` rollout event. This is
the event consumed by todo resume projection. A dependent todo such as
`resume_when=pr_merged:#123` then becomes `resume_ready=true` when its GitHub
`task_repository` matches the event repository; a cross-repository dependency
must use `resume_when=pr_merged:owner/repo#123`. Missing repository identity
fails closed with an ambiguity diagnostic. The next `status` / `quota
should-run` pass can select a matched todo as ordinary runnable work.
Replaying the same merged observation reuses the stable event id and creates no
second transition.

If GitHub redirects a renamed repository, lifecycle reconciliation treats the
provider-returned PR URL as the canonical repository identity and records the
requested repository as an explicit alias source reference. The alias is valid
only for the same observed PR number; resume evaluation remains repository-
qualified and never matches the same number in an unrelated repository.

This is deliberately event-backed rather than webhook-code coupling:

```text
GitHub reports MERGED
  -> issue-fix lifecycle persists terminal evidence
  -> LoopX emits idempotent pr_merge rollout event
  -> resume_when projection becomes ready
  -> quota selects the successor on a later bounded turn
```

The merged PR does not directly execute an arbitrary callback, and the rollout
event does not grant new write authority. [LoopX PR
#1883](https://github.com/huangruiteng/loopx/pull/1883) is the implementation
and regression evidence for this contract.

### 8. Terminal closeout and repeatability

At merged/closed state, persist compact lifecycle evidence, close the monitor,
sync the management surface, record residual risk, and choose one of:

- next issue selection;
- a concrete rollout/follow-up todo;
- a blocker or superseding route;
- structured no-follow-up.

One merged PR proves a delivery slice. Repeating the loop on independent issues
tests whether the system is a durable employee rather than a scripted demo.

## Public GitHub Signal Provider

Issue-fix owns the body-free public GitHub probe and reply-monitor provider in
`loopx.capabilities.issue_fix.github_public`. Existing CLI callers keep using
the compatibility commands:

```bash
loopx value-connectors github-public-probe \
  --url https://github.com/owner/repo/issues/1 \
  --fetch-metadata \
  --format json

loopx value-connectors github-reply-monitor \
  --issue-url https://github.com/owner/repo/issues/1 \
  --after-comment-url https://github.com/owner/repo/issues/1#issuecomment-123 \
  --fetch-metadata \
  --format json
```

The CLI name and packet schemas remain stable. Only implementation ownership
moved: probes and monitor signals now evolve with the issue-to-PR outcome they
feed, while connector installation and generic approval planning remain in the
compatibility facade.

## Implemented Surfaces

| Surface | Command or path | Current responsibility |
| --- | --- | --- |
| AgentLoop host entry | `/loopx Fix <issue-url>`, guided start/command pack | Hand the same objective to Codex, Claude Code, or another host agent; LoopX constrains the delivery protocol without binding one model. |
| State kernel | `loopx todo`, `quota`, `refresh-state`, scheduler/monitor | Persist ownership, authority, bounded compute, replan, wait/resume, and terminal closeout. |
| OpenViking memory hook | `--repository-memory-*`, `repository-memory-sync` | Retrieve bounded advisory evidence from a stable rolling default-branch index; allow decision influence only after current-checkout verification, and authorize manual resource sync and reusable-knowledge writeback separately. |
| Semantic preference hook | `loopx semantic-preference recall`, stateless receipt | Optionally recall workspace-scoped user/reviewer preferences before a configured surface; the domain applies them, while LoopX retains only compact application evidence rather than raw memory. |
| Reward-memory experiment | `loopx configure-goal --reward-memory-config ... --reward-memory-agent ...`, `loopx reward-memory experiment-status`, `run_issue_fix_patch_planning_reward_memory`, `run_issue_fix_reviewer_artifact_reward_memory`, `run_issue_fix_reviewer_notification_automatic_reward_memory` | Default off. Allow one registered fixer lane to use an ignored provider binding and exact reviewed surfaces such as `issue_fix.patch_planning`, `reviewer_artifact.summary`, and `reviewer_notification.before_send`; OpenViking is the current provider, not a global dependency. Planning stays fail-open. Reviewer-artifact application remains previewable with no sink and zero external writes. The pre-send surface accepts only a verified structured hard-policy receipt and reuses the existing send/queue/readback path; disabled, unavailable, or empty recall adds no time restriction. The canonical GitHub request remains unaffected. |
| Generic inbound feedback | `loopx lark-inbox` collector/install/status/drain | Run a project-configured host collector independently of the agent process, durably project bounded inbound events, and require domain writeback before ACK; outbound messages remain a separate configured authority. |
| Issue-fix domain state | `loopx/domain_packs/issue_fix.py`, `issue-fix outcome` | Retain candidate preflight, feasibility, PR lifecycle, compact delivery evidence, and stable outcomes inside the existing goal rather than a parallel workflow ledger. |
| Candidate preflight | `loopx issue-fix workflow-plan --fetch-candidate-evidence --goal-id <goal-id>` | Before patch planning, persist strict issue-specific evidence. Missing evidence projects `evidence_required`; cross-references, closed PRs, and maintainer comments project source-bound verification successors. Only `admitted + proceed` enters feasibility. `--candidate-resolution-json` binds compact outcomes to the current PR head or comment `updatedAt` revision; `--candidate-preflight-json` remains the provider-neutral adapter/test seam. |
| Workflow plan | `loopx issue-fix workflow-plan` | Compose body-free metadata, intake, branch plan, validation label, ordered todo previews, gates, and PR-readiness blockers. |
| Repository context | `--repository-context-json` | Pin policy, architecture, change-scope, reproduction, and validation evidence with trust and freshness. |
| Feasibility | `loopx issue-fix feasibility` | Select exactly one `fix_pr`, `comment_only`, or `triage_only` route and optionally persist compact domain state. |
| Discovered issue promotion | [`loopx issue-fix promote-discovered-issue`](docs/protocols/issue-fix-discovered-issue-promotion-v0.md) | After a real defect is reproduced during adjacent work, require open-and-closed duplicate-search evidence, create or reuse one canonical public issue under `publish` authority, verify the PR closing reference, and atomically replace the `discovered-*` placeholder so Kanban and metrics retain one case. |
| Reviewer plan | `loopx issue-fix reviewer-plan` | Rank explainable reviewer candidates from CODEOWNERS, caller-verified public maintainer maps, and changed-path/module history without requesting review. |
| Reviewer notification | `loopx issue-fix reviewer-request` | Under standing authority, exclude the live PR author and existing coverage, request the top candidate, fall back to one verified `@reviewer` comment only on permission denial, and avoid duplicates. |
| PR lifecycle | `loopx issue-fix pr-lifecycle` | Project CI, review, merge state, draft, merged, and closed signals into monitor transitions. |
| Merge-triggered resume | `pr_merge` rollout event + todo `resume_when` | Turn connected terminal merge evidence into one idempotent event so blocked/deferred successors become runnable through status/quota. |
| Maintainer correction | `loopx issue-fix pr-lifecycle --maintainer-correction-json ... --execute-transition` | Turn bounded public review feedback into one claimed patch successor, a concrete user gate, or a quiet unchanged poll. |
| Metrics projection | [`loopx issue-fix metrics`](docs/protocols/issue-fix-metrics-projection-v0.md) | Keep repository baseline separate from attributable agent output, combine existing feasibility/PR lifecycle rows with caller-supplied public snapshots, and report deltas, ratios, inventory, and missing data without another ledger. |
| Repository snapshot | `loopx issue-fix repository-snapshot` | Explicitly collect bounded public GitHub stock/flow and known issue/PR state; optionally retain only material daily changes in the existing issue-fix domain state. |
| Metrics supplement | `loopx issue-fix metrics-supplement` | Derive screened issues, triage outcomes, automatic terminal closeouts, complete-coverage first-push CI, and explicit memory evidence from existing issue-fix state; compose coverage-gated human interventions and typed capability-gap todo transitions from existing rollout evidence, while accepting a compact event batch for other lifecycle counts and preserving honest missing-data semantics. |
| Explore progress graph | `explore_graph.enabled` + material `refresh-state` | Idempotently project material issue selection, reproduction, PR publication/terminal state, capability-gap lifecycle, and todo supersession into the two delivery/capability graph lanes; update configured row/visual sinks only when their semantic digests change. |
| Acceptance fixture | `loopx issue-fix acceptance-fixture` | Prove failure-before, minimal patch, and pass-after in a deterministic fixture. |
| Git branch fixture | `loopx issue-fix repo-branch-fixture` | Exercise the same repair contract through a temporary git branch. |
| Caller repo branch | `loopx issue-fix caller-repo-branch` | Inspect an approved local repo, require a current local base snapshot before creating an issue branch, and run caller-declared validation without implicit remote refresh. |
| Content bridge | `loopx content-ops issue-fix-*` | Reuse body-free public metadata/intake boundaries. |
| Visible projection | `status`, `lark-kanban`, dashboard | Derive human-visible issue work, outcomes, gates, and Monthly Impact from the same kernel/domain state without becoming a second source of truth. |
| Projection source reconcile | `lark-kanban sync-projection --reconcile-source` | Keep normal sync non-destructive; only a caller-attested complete source snapshot may preview and explicitly retire remote orphan rows plus stale local record mappings within that exact namespace. |

The capability module lives at `loopx/capabilities/issue_fix/`; domain-state
rows live in the existing issue-fix domain pack rather than a parallel context
ledger.

### Automatic progress graph

When a goal enables `explore_graph.enabled`, each material `refresh-state`
transaction composes a public-safe Explore projection from issue-fix domain
state, todo metadata, and rollout events, then runs configured sinks. Stable
result ids make retries idempotent. Poll timestamps and unchanged monitor
observations are excluded from the semantic digest, so they do not rewrite the
graph. A configured row sink advances its digest only after row/result-id
readback verifies the write. An authorized refresh returns a failed delivery
postcondition when sync/readback fails, so the closeout cannot call the remote
board current.

If the current run is allowed to update local LoopX state but external writes
are temporarily forbidden, use `refresh-state --suppress-external-sinks`.
Canonical issue-fix/Explore projection still runs locally; configured row and
visual sink digests do not advance and remain retryable on a later authorized
refresh. The local refresh may succeed, but the unsatisfied postcondition must
become a concrete authorized-sync successor before final delivery.

`explore_graph.enabled` and `explore_harness.enabled` are independent switches.
The graph is an operator projection and may be on while the harness remains
off; the harness is a separate opt-in worker-planning facility. LoopX still
treats issue-fix domain state and rollout events as facts, while the graph
presents two connected stories even between PRs: repository delivery and
reusable agent capability improvement.

The canonical Base rows and owner-facing Docx stage boards are separate
configured sinks. A project may place the Docx as a root-level resource in the
same Base as the Kanban, then register its first whiteboard and Docx token with
`loopx explore feishu-visual-configure`. Every bounded Evidence Stage gets a
matching document section and independent whiteboard. The issue-fix projection
groups PR delivery and LoopX capability nodes into two lanes on each stage and
draws their real cross-lane relations; a single-lane project remains a natural
single-lane board. Capacity is configurable from 10 to 20, defaulting to 14.
Automatic sync records independent row and visual digests, so a successful row
update can never be reported as visual publication. A failed stage-board update
remains runnable and retries without rewriting unchanged Nodes, Edges, or
Findings.

## Truth And Evidence Model

### Revision-pinned repository context

Repository context should answer:

| Question | Required evidence |
| --- | --- |
| What revision is authoritative? | Full base revision and branch relationship. |
| What can change? | Repo-relative source/test references and nearby patterns. |
| How is the issue reproduced? | Focused command or compact observed contract. |
| How is the fix validated? | Repository-native focused validation and risk-based expansion. |
| Which source is trusted? | Repository policy/current code first; memory/expert sources marked advisory. |
| Is the evidence fresh? | Revision or timestamp tied to the current checkout. |

### Public-safe evidence

Packets preserve compact classifications and references. They do not preserve:

- raw issue/comment bodies by default;
- raw validation, git, provider, or expert output;
- local absolute paths;
- credentials or private material;
- transcript/tool capture or automatic memory writeback without an approved
  isolation boundary.

### Environment vs product attribution

An unavailable dependency, killed process, or missing service is environment
evidence. It may block a validation surface without refuting the product bug.
Conversely, a failing legacy test does not prove the new patch caused the
failure; compare the pinned base and changed hunks before attribution.

## Reviewer Routing Contract

The reviewer recommendation layer separates three concepts:

1. **ownership evidence**: CODEOWNERS, caller-verified public maintainer maps,
   and path/module contribution history;
2. **review recommendation**: explainable ranked candidates;
3. **review request**: a default post-PR action governed by repository policy
   and explicit or standing boundary authority.

Current scoring gives CODEOWNERS matches dominant weight. A current verified
maintainer-map primary contact ranks above history-only familiarity, while map
fallback contacts rank below primary routing. Trust and freshness reduce map
weight. A new file falls back to its nearest module directory only when no
non-excluded exact-path history is usable. The packet exposes matched routes,
source links, and reason codes instead of presenting a score as authority.

The default policy requests one top requestable candidate when authority is
active. Existing requested or completed review counts toward that limit. The
request is complete only after provider readback confirms it.

Important safeguards:

- fetch and exclude the live PR author, existing reviewers, and explicitly
  unavailable reviewers;
- do not expose commit email addresses;
- do not treat bots, anonymous identities, or unresolved names as requestable;
- cap candidates and show path coverage;
- retain public source references while rejecting local/private source URLs and
  raw maintainer-map bodies;
- keep team handles distinct from individual handles;
- respect required-review and branch-protection policy outside the ranking;
- never infer merge authority from reviewer familiarity.

Planned signals, added only with real call sites and public-safe evidence:

- automatic discovery of checked-in package/module maintainer metadata beyond
  caller-supplied source packets;
- recent review participation and accepted-review history;
- reviewer load, stale request detection, and fallback routing;
- bus-factor/risk hints when one person dominates a critical module;
- GitHub identity resolution for public git authors without noreply handles;
- explicit repository allow/deny lists and team membership verification.

## Human Interaction Model

Humans should be interrupted for decisions, not routine progress. Typical
concrete user gates are:

- private reproduction material or credentials are required;
- architecture or behavior scope is genuinely ambiguous;
- repository policy requires a specific reviewer or owner approval;
- public write authority is missing;
- maintainer feedback changes the intended behavior;
- merge or production authority is not recorded.

CI pending, unchanged monitor polls, routine reviewer evidence collection, and
repository-native focused validation remain agent work. A visible Kanban can
project todo ownership, status, evidence, blockers, and outputs without becoming
a second source of truth.

## Public OpenViking Usage And Evidence

OpenViking is both the public repository used for the first sustained issue-fix
pilot and an optional repository-memory provider behind the generic LoopX
context-provider boundary. The integration does not make OpenViking the source
of truth for a patch: current checkout source and tests still outrank retrieved
knowledge.

### Three OpenViking knowledge lanes

The integration keeps these lanes separate:

| Lane | What is stored and read | How Issue-Fix may use it |
| --- | --- | --- |
| Rolling repository resources | A low-frequency watched public default branch for architecture, modules, files, and current patterns. | Advisory navigation only; every used hit is re-read from the current checkout before it can influence reproduction, scope, patch, or validation. |
| Revision-stamped learning cards | Compact reusable knowledge learned while fixing a PR: symptom, root cause, violated invariant, repair pattern, validation, observed revision, and applicability boundary. | Historical hypotheses that may be stale; retrieval alone has zero authority, and decision influence is recorded only after current-checkout confirmation. |
| Workspace-scoped user memory | Stable reviewer/user preferences such as PR language, section structure, and response style. | Recalled only for configured surfaces such as `issue_fix.pr_description`; raw semantic content stays with the provider and LoopX writes a stateless hashed receipt through existing evidence/state. |

These lanes are not interchangeable. Repository resources answer “where and
how does current public `main` work?”, learning cards answer “what did a prior
fix teach at a named revision?”, and user memory answers “how should this
reviewer-facing artifact be presented?”.

Representative public issue/PR cases now cover independent code paths:

| Public case | Focused outcome | Capability evidence |
| --- | --- | --- |
| [issue #3102](https://github.com/volcengine/OpenViking/issues/3102) → [merged PR #3115](https://github.com/volcengine/OpenViking/pull/3115) | Send `peer_id` for OpenClaw session messages. | First end-to-end fix, focused validation, publication, review, merge monitor, terminal closeout, and Kanban outcome. |
| [issue #3090](https://github.com/volcengine/OpenViking/issues/3090) → [merged PR #3121](https://github.com/volcengine/OpenViking/pull/3121) | Accept sparse indexed rerank results. | A second independent module, repository-native reviewer routing, review notification fallback, and repeated lifecycle handling. |
| [issue #3124](https://github.com/volcengine/OpenViking/issues/3124) → [merged PR #3148](https://github.com/volcengine/OpenViking/pull/3148) | Show configured VLM identity before usage telemetry exists. | Reusable knowledge distilled from a validated outcome and recovered by a future-style symptom query without falsely claiming decision influence. |
| [issue #3152](https://github.com/volcengine/OpenViking/issues/3152) → [PR #3176](https://github.com/volcengine/OpenViking/pull/3176) | Anchor user-scoped nested resource writes at the direct parent. | First fresh-issue rolling-index dogfood with decision influence and staleness recorded separately from retrieval volume. |

An earlier learning-card validation used a revision-scoped public
`viking://resources/.../<git-revision>` namespace. After the #3148 delivery
commit was proven to be an ancestor of the pinned revision, LoopX wrote one
`issue_fix_reusable_knowledge_input_v0` fact containing symptom, reproduction,
root cause, violated invariant, repair pattern, focused validation, and
applicability boundaries. A query equivalent to “configured model missing from
status when usage telemetry is empty” returned the knowledge overview and body;
an exact read recovered the causal and boundary fields. That proves
discoverability, not future patch value, so decision influence remains zero
until a different issue actually uses the result.

The first fresh-issue rolling-index dogfood was deliberately mixed rather than
reported as a blanket success. For issue #3152, a symptom/module query located
the relevant source and nearby tests, and a later validation query recovered
the focused test surface. A causal query was weak and did not determine the
patch. Every used locator was re-read and confirmed in the current checkout at
revision `5bfa9b617ecff478f825ca435a35bc4222b30582`; the reproduction and code
change were derived from that checkout. The resulting accounting is therefore:
useful `change_scope` and `validation` influence, zero memory patch authority,
and no stale result allowed into the compact repository context. This measured
positive-but-mixed result keeps rolling-main retrieval optional and fail-open
until repeated independent issues show stronger value.

The pilot has also produced generic LoopX fixes: [PR
#1784](https://github.com/huangruiteng/loopx/pull/1784) established early
control-plane groundwork, [PR
#1883](https://github.com/huangruiteng/loopx/pull/1883) made merged PR evidence
resume dependent todos, and [PR
#1887](https://github.com/huangruiteng/loopx/pull/1887) separated reusable
repository knowledge from audit-only delivery outcomes. Later slices added the
provider-neutral semantic-preference hook in [PR
#1991](https://github.com/huangruiteng/loopx/pull/1991), independent automatic
Explore Graph activation in [PR
#1995](https://github.com/huangruiteng/loopx/pull/1995), and the generic
host-managed Lark event collector lifecycle in [PR
#2000](https://github.com/huangruiteng/loopx/pull/2000). The merged LoopX
revision and focused smokes, not the pilot narrative, remain authoritative.

## Roadmap

### Current stage

- public metadata and route selection;
- repository-context provenance;
- deterministic and caller-repo repair artifacts;
- focused validation evidence;
- reviewer recommendation from CODEOWNERS, public repository-declared routing
  sources, and repository-native contribution evidence;
- authority-gated, idempotent reviewer notification with formal-request-first,
  permission-only comment fallback, and PR readback;
- PR lifecycle projection and provider-neutral maintainer-correction succession;
- idempotent `pr_merge` event projection and todo `resume_when` recovery;
- issue/outcome Kanban projection, repository snapshots, attributable impact
  metrics, and `Monthly Impact` rows;
- rolling-default-branch OpenViking retrieval, one fresh-issue measured
  dogfood, and explicit reusable-knowledge writeback with honest
  decision-influence accounting;
- an explicit, default-off `build_issue_fix_pr_description()` boundary for
  reviewer-facing descriptions. When configured, it performs at most one
  `issue_fix.pr_description` recall, passes results only to a caller-supplied
  applier, preserves the base description on fail-open or unattributed changes,
  and returns a stateless compact receipt for existing evidence/state writeback.
  Independently, its deterministic issue-reference block runs after semantic
  prose: complete fixes use `Fixes`, partial work uses `Related to`, and closing
  metadata requires explicit default-branch targeting;
- goal-scoped `explore_graph.enabled` projection at material refresh boundaries,
  independent from `explore_harness.enabled`, with separate row and visual sink
  digests;
- generic host-managed Lark inbound collection with install/status/health and
  durable inbox drain/ACK; domains configure routing, while outbound remains a
  separate authority;
- LoopX todo/quota/monitor/Kanban integration through the host agent.

### Next stage

- trigger the goal-default reviewer request directly from PR-ready transitions;
- resolve public GitHub identities and repository teams without leaking email;
- make publication authority visible per external action;
- make unchanged lifecycle observations physically idempotent everywhere;
- repeat two-stage repository-memory retrieval on independent fresh issues and record
  confirmed/refuted/stale results plus concrete reproduction, scope, patch, or
  validation influence;
- add revision-lineage supersession and stale quarantine for reusable knowledge;
- add a reusable terminal acceptance report across repeated issues.

### Longer-term stage

- multi-repository issue portfolios with bounded concurrency;
- maintainer preference learning from public accepted/rejected outcomes;
- reviewer load balancing and bus-factor awareness;
- decide packaged-default memory behavior only after repeated fresh-issue
  decision influence with no harmful stale guidance;
- Open Knowledge Format interoperability after the repository-context contract
  stabilizes;
- bounded multi-repository reporting and portfolio rollups over the implemented
  daily snapshot and Monthly Impact projection.

## Success Metrics

Track outcomes, not agent activity:

- selected issues that reach a focused PR;
- focused PRs accepted or merged;
- failure-before/pass-after proof rate;
- unrelated regression rate;
- time from issue selection to review-ready and terminal state;
- number and type of human interventions;
- reviewer recommendation acceptance/override rate;
- unchanged monitor polls skipped;
- public/private boundary incidents;
- LoopX generic gaps fixed or converted into concrete claimed todos.

`loopx issue-fix metrics` is the read-only reporting seam for these measures.
The period-start repository snapshot describes repository stock only; agent
output starts at zero and is attributed from the goal's existing feasibility
and PR lifecycle rows. The current public snapshot supplies repository flow and
may refresh current PR/issue state without rewriting lifecycle history. Optional
supplement counts cover evidence that is not yet native to those rows, such as
human interventions, first-push CI, capability deltas, and memory leverage.
Absent evidence is emitted as `not_available` plus a reason code, never as zero.
Memory impact deliberately separates `memory_retrievals`,
`memory_verified_decision_influence`, `memory_verified_patch_influence`, and
`memory_stale_results`; retrieving or confirming a result does not by itself
prove that it changed an issue-fix decision.
The same packet exposes stable `impact_rows`; the generic Lark sink maps them to
the `Monthly Impact` view with baseline, current, delta, ratio lineage, source,
freshness, and missing-data columns. Capability impact keeps found, fixed, and
real-callsite-verified gaps as separate rows so delivery volume is not confused
with product-path proof.

## Conversational `/loopx` Entry

On a host with the LoopX slash entry, start the long-running goal directly:

```text
/loopx --capability-route issue-fix Fix https://github.com/owner/repo/issues/123
```

For a manually integrated host, run `loopx bootstrap-command-pack --project .`
and pass the same complete arguments once through
`loopx start-goal --guided --project . --slash-command-arguments="..."`.
Typed callers that already own separate fields may instead pass
`--capability-route issue-fix` with `--goal-text`; the CLI owns route parsing in
both forms.

The explicit route switch does not bypass issue selection, authority, or
validation. Without it, goal text never activates issue-fix. With it, the
guided transaction creates the goal/todo/host-loop route from which the
capability-owned admission commands below can be executed.

## Feasibility Decision

`loopx issue-fix feasibility` selects exactly one of `fix_pr`, `comment_only`,
or `triage_only`. A `fix_pr` decision requires bounded change scope plus a
named reproduction and validation surface. The compact decision belongs in the
existing issue-fix domain state before writing todos for the chosen route; it
does not create a parallel workflow ledger.

## Repository Context

Both workflow planning and feasibility accept
`--repository-context-json <compact-context.json>`. The input must pin the
current revision and keep source references repo-relative. Current checkout
evidence remains authoritative; memory and expert conclusions stay advisory
until verified. The public
[OpenViking pilot handoff](docs/openviking-pilot-handoff.md) shows how the real
pilot applies that evidence order without introducing a repository-specific
control path.

They also accept either `--repository-memory-json
<compact-search-read-result.json>` or a configured context provider. The
provider path is deliberately layered: the reusable LoopX context-provider
module owns OpenViking CLI/version/service preflight, bounded explicit
`search -> read`, time/result caps, fail-open errors, and authority-gated
resource sync. Issue-fix owns the domain query, stable repository scope,
mapping retrieved resources back to repo-relative files, and exact current
checkout verification. There is no repository-name special case.

Set `LOOPX_ISSUE_FIX_REPOSITORY_MEMORY_PROVIDER_CONFIG` to a local-private
`issue_fix_repository_memory_provider_config_v0` file, or pass
`--repository-memory-provider-json`. When the configured provider, public
scope, current revision, and caller-approved checkout are available,
`workflow-plan` and `feasibility` run the provider by default. An explicit
`--repository-memory-json` still overrides the environment default. LoopX
hashes provider references, keeps every memory source advisory, allows patch
influence only for canonical-text exact matches or parser chunks whose
non-empty lines match the current checkout at least 98% (transport line
endings and one terminal newline are normalised), and
persists only the compact hook projection in the existing repository context.
Unverified hits contribute counts only; their summaries are not persisted.
Provider unavailability, empty retrieval, or a missing checkout is fail-open;
raw memory bodies, automatic transcript capture, private namespaces,
credentials, and provider config paths are never retained.

Set `repository_identity` when the provider scope belongs to one canonical
repository. LoopX normalises that identity and the checkout's Git `origin`
before any provider call. A missing or different origin produces a compact
`repository_identity_unavailable` or `repository_identity_mismatch` result:
read-only Issue Fix delivery fails open without memory, while resource sync
and validated-outcome writeback remain blocked with zero provider writes.
Only identity digests enter provider receipts. A stale `pinned` revision is
handled the same way for retrieval (`provider_revision_mismatch`) instead of
turning the optional provider into a whole-workflow exception.

The default long-running setup uses one stable provider-managed index for the
public default branch. The current checkout revision is supplied by the
issue-fix caller for verification; it is not encoded into the provider scope:

```json
{
  "schema_version": "issue_fix_repository_memory_provider_config_v0",
  "enabled": true,
  "provider": "openviking",
  "namespace": "public-repository",
  "visibility": "public",
  "repository_identity": "git:github.com/owner/repo",
  "revision_policy": "rolling_default_branch",
  "scope_ref": "viking://resources/public-repository/owner-repo/main",
  "max_results": 3,
  "timeout_seconds": 15,
  "sync_timeout_seconds": 180,
  "resource_references": ["src/module.py", "tests/test_module.py"],
  "service_ownership_receipt_path": ".loopx/context-provider-service.json",
  "writeback_enabled": false,
  "writeback_scope_ref": "viking://resources/public-repository/owner-repo/outcomes/<git-revision>",
  "workspace_scope": "owner-repo",
  "peer_scope": "issue-fix-agent"
}
```

The provider owns refresh cadence. For OpenViking this can be a low-frequency
native full-repository watch on public `main`: the first import builds the
index, and later runs reconcile the same stable target. LoopX does not derive a
new resource scope per checkout, persist an active revision, or block retrieval
on an activation receipt. A hit from the rolling index remains advisory: LoopX
maps it to a repo-relative file and verifies it against the current checkout
before it can influence reproduction, change scope, patch, or validation.
Unverified or stale hits remain counts only.

Retrieval and a provider-owned watch do not need a LoopX process lease. A
LoopX-triggered rolling sync is different because it can start a long external
write from a short-lived agent host. Before that write, LoopX requires a local
`context_provider_service_ownership_receipt_v0` from a persistent external
service or supervisor. The receipt names the provider, an opaque service
identity, its generation, and a live process id. LoopX reads it before and
after the sync, never publishes its path or process id, and blocks with zero
writes when ownership is absent. If the generation or process changes during
the call, the result is `restart_detected_no_resume`: completed or pending
writes and elapsed time remain recorded as an additional attempt rather than
being reported as resumed progress. This contract is provider-neutral and
does not make LoopX a provider process manager.

`pinned` remains available for an intentionally immutable corpus. In that
compatibility mode, `repository_revision` must match the caller checkout and
the revision must appear in `scope_ref`:

```json
{
  "schema_version": "issue_fix_repository_memory_provider_config_v0",
  "enabled": true,
  "provider": "openviking",
  "namespace": "public-repository",
  "visibility": "public",
  "repository_identity": "git:github.com/owner/repo",
  "revision_policy": "pinned",
  "scope_ref": "viking://resources/public-repository/owner-repo/<git-revision>",
  "repository_revision": "<full-git-revision>",
  "resource_references": ["src/module.py", "tests/test_module.py"]
}
```

Resource indexing is intentionally separate from retrieval. Use
`loopx issue-fix repository-memory-sync` to preview a bounded set of
repo-relative public files only for an explicit manual sync; add `--execute`
only after the provider-resource write is authorized. The rolling default path
normally relies on the provider watch instead. A transport failure after an
explicit provider commit is reconciled by bounded target readback before any
retry. Retrieval and resource sync use separate bounded timeouts because
semantic indexing can legitimately take longer than read-only search.

Validated-outcome writeback is a separate, default-off hook. It runs only when
the caller explicitly adds `--write-repository-memory`, the local provider
config independently sets `writeback_enabled: true`, delivery evidence says
`completed`, validation says `passed`, the delivery evidence has a stable
`recorded_at`, the outcome revision matches the configured public resource
scope, and `--repo-path` proves with git that delivery `commit_ref` is an
ancestor of that pinned revision. Divergent, missing, or unresolved commits
block before the provider is called. Squash flows should record the final
merge/squash commit, not a superseded feature-branch commit. The checkout path
and raw git output are never retained. LoopX writes one distilled fact containing
revision, provenance, freshness, public outputs, risks, a stable supersession
key, and explicit workspace/peer scopes. A content hash selects the immutable
target, so an identical retry reads and accepts the existing fact without a
second write; conflicting content stops instead of overwriting. Raw
transcripts, tool logs/results, expert answers, credentials, private material,
and captured local paths are rejected. The provider packet retains only opaque
refs and compact receipts.

An outcome without `reusable_knowledge` remains an audit fact: it proves what
was delivered, but it is not promoted as patch guidance. The compatibility
`issue_fix_reusable_knowledge_input_v0` contract remains available for existing
callers. New terminal outcomes should use
`issue_fix_repository_learning_card_input_v0`; LoopX accepts it only after the
issue-fix stage is merged, comment-published, or triage-complete, and writes it
to the separate `repository-learning-cards` collection. Both contracts require:

- a searchable symptom signature and a focused reproduction contract;
- the checkout-verified root cause and violated invariant;
- the repair pattern, focused validation contract, and repository-relative
  verification references;
- explicit applicability and non-applicability boundaries.

A repository learning card additionally requires explicit confidence,
repo-relative affected modules, bounded invalidation conditions, a
revalidation contract, and `current_checkout_verification_required: true`.
The stored card combines those fields with the source revision, outcome
`observed_at`, public evidence URLs, validation result, commit, and provenance
already enforced by the writeback envelope. Writeback also stores SHA-256
digests of the cited verification references, never their raw contents. On
retrieval, LoopX exposes only bounded card metadata and marks the hit confirmed
when every cited file still has the same digest in the current checkout;
missing or changed references leave it unverified. The card is therefore
searchable as a historical hypothesis but never self-authorizing: retrieval
starts with zero decision influence, and a later issue must inspect the stated
invalidation conditions and complete the revalidation contract before
recording reproduction, scope, patch, or validation influence.

This distinction prevents PR titles, changed-file lists, and passing-test
labels from being mistaken for reusable diagnosis. Confirmation against the
current checkout also does not by itself prove value. A retrieval records
decision influence only when it names the concrete decision it changed
(`reproduction`, `change_scope`, `patch`, or `validation`); provider retrieval
alone records zero influence.

Use retrieval at three bounded points. Before diagnosis, search by symptom and
module to discover candidate incidents. After reproducing locally, search by
the observed causal path or invariant and confirm or refute each hit in the
current checkout. Before closeout, search the changed module and invariant for
prior validation surfaces and negative boundaries. Repository source, tests,
and current documentation remain authoritative throughout.

Do not write whole source files, raw issue or PR discussions, transcripts,
tool output, unverified hypotheses, reviewer identity mappings, or LoopX
control-plane state as reusable repository knowledge. Current source belongs
in the rolling repository resource index; reviewer routing comes from live
repository ownership signals; LoopX operating lessons remain in LoopX state.

The OpenViking adapter deliberately uses deterministic `viking://resources/`
writeback for this first contract. It does not call experimental `ov
add-memory`, because that command creates a fresh session and currently accepts
no idempotency key. Conversation/session capture therefore remains out of
scope and requires a separate owner decision even when validated-outcome
writeback is enabled.

Default enablement is an evidence decision rather than an installation side
effect. A project should first dogfood the hook across several independent
issue/context runs and a restart boundary. Make it a packaged default only
when it repeatedly changes a concrete issue-fix decision with novel,
checkout-verified evidence and without stale, misleading, or boundary-unsafe
retrieval. Retrieval count alone is not success; otherwise keep it explicit
opt-in with the same fail-open behavior.

## PR Lifecycle Monitor

After publication, `loopx issue-fix pr-lifecycle` and a `continuous_monitor`
todo keep CI, review, maintainer correction, mergeability, stale branch, and
terminal status visible. Publication, review requests, merge, and access to
private material remain explicit gates. Each material transition must yield a
`runnable_successor`, concrete blocker, or structured no-follow-up; unchanged
polls remain quiet and do not spend delivery quota.

Pass `--issue-ref` when persisting PR lifecycle state. This explicit public-safe
link lets the outcome read model join the PR to its issue without guessing from
branch names, titles, or text.

To convert bounded feedback into durable work, supply a compact correction and
explicitly execute the transition:

```bash
loopx issue-fix pr-lifecycle \
  --url https://github.com/owner/repo/pull/123 \
  --issue-ref issues_100 \
  --metadata-json pr-metadata.json \
  --maintainer-correction-json correction.json \
  --goal-id issue-fix-goal \
  --project /path/to/approved/repo \
  --claimed-by issue-fix-agent \
  --execute-transition \
  --format json
```

The correction source is provider-neutral: any public HTTPS or repo-relative
reference may be used, while the current PR monitor remains the authoritative
lifecycle source. Exact retries neither add another successor nor rewrite the
same lifecycle row.

## Status And Output View

Todo cards answer **what the agent should do next**. They do not, by
themselves, answer **what happened to one issue**. `loopx issue-fix outcome`
fills that read-model gap without creating another ledger or lifecycle state
machine. It derives one stable `issue_fix_outcome_projection_v0` case from the
existing feasibility row, revision-pinned repository context, optional compact
delivery evidence, and optional PR lifecycle row.

Compact delivery evidence uses `outcome_status=in_progress|completed|blocked`
and `validation_status=passed|failed|partial|not_run`. Terminal PR state still
takes precedence, while an explicit blocked delivery remains visible over a
non-terminal wait such as pending CI.

A delivery that names `commit_ref` cannot be projected as validated or
publication-ready merely because its JSON says `passed` or `completed`. For
those states, writeback requires `--repo-path` plus a full
`--repository-ref` under `refs/heads`, `refs/remotes`, or `refs/tags`. LoopX
checks that the checkout has a GitHub remote matching `--repo`, resolves the
pinned repository revision and commit objects, proves ancestry, and requires
the recovery ref to resolve exactly to the pinned revision. The persisted
`issue_fix_repository_commit_evidence_v0` keeps the full object ids, recovery
ref, and a clone-stable repository fingerprint, but no checkout path, remote
URL, or raw git output. Legacy evidence without that proof remains visible but
is downgraded to `validation=unverified` and
`stage=delivery_evidence_unverified` until it is re-resolved.

The case card exposes the selected route and current stage; issue and PR links;
repository revision and context fingerprint; reproduction and validation
status; repo-relative changed files and commit ref when explicitly supplied;
checks, review, mergeability, and terminal result; remaining risks; and the next
action. Missing delivery evidence remains `declared` or unknown—PR existence is
never treated as proof that focused validation passed.

The packet is directly consumable by `loopx lark-kanban sync-projection`.
Execution todos remain separate cards, while the stable outcome card is keyed
by repository and issue. A merged, closed, or triaged terminal card remains
visible by default so the board shows outputs instead of only active work.
Shared sinks continue to apply the existing local-path, private-link, and
private-reference redaction boundary.

The default `loopx lark-kanban sync-loopx-todos` path also derives all issue
outcomes from the goal's existing feasibility and PR lifecycle domain state and
upserts them beside todo rows. A feasibility row therefore appears as issue work
even before a PR exists; a PR enriches that row only when its lifecycle
observation carries the matching `repo` and explicit `issue_ref`. Numeric issue
aliases (`#123`, `issue_123`, `issues/123`) canonicalize to `issues_123` on
write and when reading legacy rows, so equivalent explicit links cannot silently
fall into the unlinked count. The command's `--limit` applies only to active todo
rows; all derived outcome rows remain in scope, and the receipt exposes the
split through `limit_policy`. This automatic closeout projection adds no outcome
ledger or second state machine.

Supplying `--delivery-evidence-json` alone is a read-only preview. Add
`--write-delivery-evidence` after focused validation to store its validated,
public-safe compact form inside the existing feasibility row. Later default
outcome and Kanban syncs then retain the validation, changed files, commit, output
links, and risks instead of falling back to the feasibility declaration. The
write flag rejects an ad hoc `--feasibility-json` source so the destination is
always the stable goal-scoped row.
Passed or completed evidence that includes a commit also needs the approved
checkout and recoverable ref described above. A missing, divergent, or stale
commit fails before ledger mutation. Repeating the same material proof is an
unchanged, no-write operation even when its verification timestamp is newer.

The Lark adapter renders this as a first-class issue dimension rather than
only flattening the packet into `Evidence`. Outcome rows set
`Work Item Type=Issue Fix` and populate `Repository`, `Issue`, `Pull Request`,
`Route`, `Stage`, `Validation`, `Outcome`, and `Context Tags`. The bounded
multi-select tags expose route, stage, reproduction/validation status, test
changes, multi-file scope, and grounded repository context without copying
free-form evidence. `Issue Fix Outcomes` provides the table view; `Issue Fix
Kanban` groups the same rows by `Stage`. Existing boards gain the missing fields
and views through idempotent `lark-kanban setup --execute` schema reconciliation.

## Commands

```bash
# Preview the complete issue-fix workflow.
loopx issue-fix workflow-plan \
  --url https://github.com/owner/repo/issues/123 \
  --repo-path /path/to/approved/repo \
  --repository-context-json context.json \
  --repository-memory-json compact-search-read-result.json \
  --fetch-candidate-evidence \
  --goal-id example-goal \
  --validation-label "focused unit test" \
  --format json

# Or configure the reusable OpenViking provider once. The config stays local
# and binds one stable public default-branch scope; checkout revision is
# supplied separately for verification.
export LOOPX_ISSUE_FIX_REPOSITORY_MEMORY_PROVIDER_CONFIG=/path/to/provider.json
loopx issue-fix workflow-plan \
  --url https://github.com/owner/repo/issues/123 \
  --repo-path /path/to/approved/repo \
  --repository-context-json context.json \
  --repository-memory-query "affected module reproduction validation" \
  --fetch-candidate-evidence \
  --goal-id example-goal \
  --validation-label "focused unit test" \
  --format json

# Low-level provider preflight. Normal Issue-Fix callers use
# build_issue_fix_pr_description() so recall, fail-open, and receipt attribution
# stay on one explicit artifact boundary. Semantic content remains provider-owned.
loopx semantic-preference recall \
  --project . \
  --config .loopx/config/semantic-preference.json \
  --surface issue_fix.pr_description \
  --context repository=owner/repo \
  --execute \
  --format json

# If inbound feedback is configured, install the generic host collector once,
# inspect health, and drain durable events before acknowledging them.
loopx lark-inbox collector-install \
  --project . --config .loopx/config/lark/collector.json --execute --format json
loopx lark-inbox collector-status \
  --project . --config .loopx/config/lark/collector.json \
  --probe-event-bus --format json
loopx lark-inbox drain \
  --project . --config .loopx/config/lark/event-inbox.json --format json

# Select one route and persist compact goal-scoped feasibility state.
loopx issue-fix feasibility \
  --url https://github.com/owner/repo/issues/123 \
  --reproduction-status confirmed \
  --reproduction-label "focused contract repro" \
  --scope-class bounded \
  --validation-label "focused unit test" \
  --repository-context-json context.json \
  --repository-memory-json compact-search-read-result.json \
  --goal-id example-goal \
  --format json

# Promote a reproducible defect found during real work into one canonical issue.
# The structured input records open/closed duplicate-search evidence and the
# revision-pinned public facts; retries do not create another issue or row.
loopx issue-fix promote-discovered-issue \
  --goal-id example-goal \
  --project /path/to/connected/project \
  --promotion-json discovered-issue-promotion.json \
  --execute \
  --format json

# Project repository impact and attributed outputs without writing state.
loopx issue-fix metrics \
  --goal-id public-issue-fix-goal \
  --project /path/to/connected/project \
  --repo owner/repo \
  --repository-baseline-json baseline.json \
  --repository-current-json current.json \
  --supplement-json optional-public-counts.json \
  --format json

# Recommend reviewers without requesting external review.
loopx issue-fix reviewer-plan \
  --repo-path /path/to/approved/repo \
  --repo owner/repo \
  --base-ref origin/main \
  --exclude-reviewer @pull-request-author \
  --exclude-author-name "PR Author Git Name" \
  --reviewer-sources-json reviewer-sources.json \
  --execute \
  --format json

# Notify the default top non-author reviewer and verify the formal request or permission fallback.
loopx issue-fix reviewer-request \
  --url https://github.com/owner/repo/pull/456 \
  --repo-path /path/to/approved/repo \
  --base-ref origin/main \
  --reviewer-sources-json reviewer-sources.json \
  --goal-id example-goal \
  --project /path/to/approved/repo \
  --execute \
  --format json

# Project PR lifecycle into LoopX continuation state.
loopx issue-fix pr-lifecycle \
  --url https://github.com/owner/repo/pull/456 \
  --issue-ref issues_123 \
  --fetch-metadata \
  --goal-id example-goal \
  --format json

# Derive one issue status/output projection from existing domain state.
loopx issue-fix outcome \
  --goal-id example-goal \
  --project /path/to/approved/repo \
  --repo owner/repo \
  --issue-ref issues_123 \
  --pr-ref pull_456 \
  --delivery-evidence-json delivery-evidence.json \
  --write-delivery-evidence \
  --repository-memory-provider-json provider.json \
  --write-repository-memory \
  --repo-path /path/to/approved/repo \
  --repository-ref refs/remotes/origin/main \
  --agent-id codex-issue-fix \
  --format json
```

## Validation

```bash
python3 examples/issue-fix-capability-guide-smoke.py
python3 examples/issue-fix-reviewer-recommendation-smoke.py
python3 examples/issue-fix-reviewer-request-smoke.py
python3 examples/issue-fix-reviewer-notification-sink-smoke.py
python3 examples/issue-fix-workflow-plan-smoke.py
python3 examples/issue-fix-workflow-contract-smoke.py
python3 examples/issue-fix-repository-context-smoke.py
python3 examples/issue-fix-repository-memory-smoke.py
python3 examples/issue-fix-validated-memory-writeback-smoke.py
python3 examples/issue-fix-feasibility-smoke.py
python3 examples/issue-fix-discovered-issue-promotion-smoke.py
python3 examples/issue-fix-pr-lifecycle-smoke.py
python3 examples/issue-fix-metrics-projection-smoke.py
python3 examples/issue-fix-repository-snapshot-smoke.py
python3 examples/issue-fix-metrics-supplement-smoke.py
python3 examples/issue-fix-capability-gap-metrics-smoke.py
python3 examples/issue-fix-maintainer-correction-smoke.py
python3 examples/issue-fix-outcome-projection-smoke.py
python3 examples/issue-fix-explore-projection-smoke.py
python3 examples/issue-fix-acceptance-loop-smoke.py
loopx canary premerge --from-git-diff
```

## Non-Goals

- LoopX does not bypass repository review or branch protection.
- Reviewer recommendation is not reviewer assignment or availability proof.
- The capability does not default to automatic merge or production actions.
- It does not store raw transcripts, tool logs, expert answers, credentials, or
  private issue material in public state.
- It does not add repository-specific branches such as `if repo == ...` to the
  generic control plane.
