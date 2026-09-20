# GitHub Maintenance & Ops Automation: Best Practices from Running LoopX

> [简体中文](github-maintenance-ops-best-practices.zh-CN.md)

LoopX maintains its own open-source repository with LoopX. This page records
the operating patterns and best practices that emerged, generalized so other
open-source maintainers can reuse them.

## 1. The Claim

Repository maintenance and operations is long-horizon, interruptible,
high-context work: triage, review, release, security response, ecosystem
outreach, and content are never one prompt away from done. That is the same
problem shape LoopX solves for long-running agents, so running the repository
with LoopX is both dogfooding and a source of best practices.

Three operating patterns emerged:

1. a maintenance loop that owns PR review discipline, release and security
   handling, triage, and infrastructure repair;
2. an ecosystem and value loop that watches adoption, evaluates forks and
   derivatives, invites upstream contributions, and turns public evidence into
   documentation and social content;
3. an issue-to-PR bot that drives a public issue through feasibility, focused
   fix, evidence, reviewer routing, and merge observation.

## 2. Three Operating Patterns

### Pattern A: Maintenance Loop

The maintenance loop runs under heartbeat automation with its own goal,
todos, quota, and monitor contracts. It performs the repetitive but
judgment-heavy work of a maintainer:

- PR review with an explicit skill and exact-head evidence gate, instead of
  ad-hoc review from memory;
- issue/PR triage with typed classification so incoming work is tagged and
  routed;
- release and security response, including coordinated disclosure, with a
  recorded decision boundary;
- infrastructure repair such as fixing a stale star-history service or
  updating automation scheduling from a hint, backed by a small reusable tool
  that reads the hint, backs up state, applies the change, and acknowledges.

Public evidence: releases v0.4.5 through v0.4.7, the PR review skill
refinements, and the review/release discipline recorded in repository history.

### Pattern B: Ecosystem And Value Loop

The ecosystem loop treats the repository as a product that must be observed,
evaluated, and fed:

- a weekly scan classifies mentions into integrations, sampling/borrowing,
  derivatives, and coverage signals;
- each scan updates a public adoption inventory through a PR, so the inventory
  is a maintained artifact rather than a one-time report;
- forks and same-name projects are evaluated for absorbable capabilities
  (offline packaging, fork-safe CI, platform qualification, localization,
  quality-gate workflows);
- valuable sources receive an upstream PR invitation directly on their PR or
  issue thread;
- social content is produced from the same public evidence through a
  content-ops pipeline with explicit review states, and publication stays
  gated until the owner approves.

Public evidence: the ecosystem adoption inventory
([`ecosystem-adoption.md`](ecosystem-adoption.md)), the TypeScript migration
RFC ([#3225](https://github.com/huangruiteng/loopx/issues/3225),
[#3226](https://github.com/huangruiteng/loopx/pull/3226)), and the content-ops
capability.

### Pattern C: Issue-To-PR Bot

The issue-fix capability turns one public issue into a focused, verified,
reviewable PR and follows it to merged, closed, or an explicit no-follow-up.
It combines four layers with clear boundaries:

- the state kernel provides goal, todo, quota, authority, monitor, replan, and
  terminal closeout across turns;
- optional repository memory provides advisory context, always validated
  against the current checkout;
- domain state records feasibility, repository context, delivery evidence,
  reviewer route, PR lifecycle, and outcome;
- the agent runtime provides understanding, coding, and execution.

The core promise: the capability is driven from a long-running maintenance
goal, not from one command. The goal keeps selecting candidates, producing
focused PRs, monitoring their lifecycle, and resuming the next issue.
`/loopx Fix <issue-url>` seeds one candidate; feasibility, PR lifecycle, and
outcome persist in domain state across turns, model switches, CI waits, and
review round-trips.

Public evidence: the
[issue-fix capability documentation](../../loopx/capabilities/issue_fix/README.md) and
the OpenViking pilot recorded in the showcase appendix.

## 3. Best Practices

### 3.1 State Over Chat Memory

Every repetitive operation needs a durable state surface: todos, quota,
monitor, and replan. When a maintenance or outreach action is interrupted, the
next turn resumes from state, not from conversation history. Heartbeat
automation uses backoff and quiet waits instead of polling or forcing progress.

### 3.2 Turn Judgment Into Contracts, Skills, And CLI

The highest-leverage maintainer work is judgment. Once a judgment is
recognized, encode it: a PR review skill with exact-head evidence, a
scope-fit gate that rejects modules without a real call site, a typed decision
packet instead of prose, and a CLI entry point instead of instructions in chat.
This converts "review carefully" from a reminder into a checkable gate.

### 3.3 Classify Intake And Run An Adoption Funnel

Incoming issues, PRs, and mentions are classified before work. Issues and PRs
get typed tags; ecosystem mentions are split into integrations, sampling,
derivatives, and coverage. A weekly monitor updates the public inventory
through a PR, and material transitions create concrete follow-up todos instead
of being remembered.

### 3.4 Keep Security And Release Discipline Explicit

Security reports, disclosure, and release cutovers are recorded decision
paths, not improvisation. The pattern: reproduce and fix, self-review at exact
head, publish coordinated advisories when appropriate, then release with a
readable changelog and validation commands. Human maintainers keep the final
decision on what is disclosed and when.

### 3.5 Outreach Is A Cadence, Not A Campaign

Fork and derivative evaluation runs on a schedule. For each valuable source,
send one focused invitation on the author's own PR or issue thread, with a
concrete upstream target and an offer to review. Record outreach locally, and
let a monitor surface follow-ups instead of polling in chat.

### 3.6 Content Ops Feeds The Repository And Vice Versa

Public repository evidence is the source map for documentation and social
content. Content moves through a gated pipeline: source → angle → draft →
feedback → publish gate → readback. Approved content links back to the
repository, and reader signals return as new evidence. No raw private state
enters public packets.

### 3.7 Convergence Needs Parity First

When two execution paths drift semantically (for example turn settlement and
quota settlement), freeze both with parity fixtures first, then extract a
shared driver, then delete the duplicated branches. The same rule applies to a
language migration: contract freeze, parity gates, then block-by-block
replacement with rollback records.

### 3.8 Human Gates Stay Real

Automation reduces the cost of repetitive work but never removes the human
from judgment: publishing, disclosure, merge decisions, and scope changes
remain explicit gates. Approvals are bound to a specific revision, and
revising approved content invalidates the approval.

## 4. Failure Modes And Boundaries

- Long-running exploration can be wide and still miss a key detail; harnesses
  fix "runs long", but "reads fully" still needs better tool design.
- Low-quality contributor PRs can slip through when review is ad hoc; an
  explicit review skill and exact-head evidence make the signal visible.
- Monitor thresholds are heuristics; tune defaults from observed behavior
  (for example increasing the unchanged-streak before replan) and record why.
- Do not rewrite a large core in one pass; use contracts, parity, and
  reversible slices.
- Public materials must not leak internal context: no private links, raw
  logs, credentials, or unapproved internal metrics.

## 5. Getting Started

### 5.1 Minimal setup

Requirements are Python 3.11+ plus `curl` and `tar`. Install without cloning,
then connect from your project root:

```bash
curl -fsSL https://loopx-project.github.io/loopx/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"
loopx doctor

cd /path/to/your-project
loopx connect
loopx start-goal --guided --project . --goal-text "Your long-running objective"
```

Keep `.loopx/`, `.codex/goals/`, and `.local/` ignored. From your agent host,
drive the goal with `/loopx <task>` (Codex App/CLI), `/loopx` + `/loop`
(Claude Code), the goal bridge (OpenCode), or the Pi goal extension.

### 5.2 Command cheat sheet

```text
loopx status                    # goal/registry health and next action
loopx todo list --goal-id <id>  # project todos
loopx quota should-run          # should this registered agent act now?
loopx todo claim                # who owns this slice?
loopx todo update               # what changed?
loopx refresh-state             # what should the next turn see?
loopx quota spend-slot          # account for a completed, validated slice
```

Capability entries:

```text
loopx issue-fix workflow-plan                      # plan an issue fix route
/loopx Fix https://github.com/owner/repo/issues/123
loopx content-ops queue-status --item-json <item>  # content pipeline queue
loopx value-connectors source-map --format json    # connector-first source map
```

### 5.3 Your first continuous monitor

A maintenance monitor is just a typed todo with a cadence. These are the
monitors actually running on this repository today, split by operating loop.

| Loop | Monitor (`target_key`) | Cadence | What it does |
| --- | --- | --- | --- |
| Maintenance | GitHub issue intake (`github-open-issue-intake`) | 6h | Classify and route newly opened issues |
| Maintenance | Open PR review queue (`github:huangruiteng/loopx:open-pr-review-queue`) | 3m | Scan the PR queue; review only when gates pass |
| Maintenance | Public smoke quality repair (`github:huangruiteng/loopx:public-smoke-quality`) | 15m | Detect and repair public-smoke failures |
| Maintenance | Non-benchmark quality watch (`public-nonbenchmark-quality-watch`) | 6h | Watch quality regressions outside benchmarks |
| Maintenance | Repository quality (`repository-quality-monitor`) | 14d | README first screen, quickstart, public-boundary scan |
| Maintenance | Community feedback funnel (`community-feedback-funnel`) | 14d | Align feedback entries and triage |
| Maintenance | Collaboration/showcase tracker (`collaboration-showcase-candidate-tracker`) | 14d | Track collaboration and showcase candidates |
| Ecosystem | GitHub mention scan (`github-loopx-mention-scan`) | 7d | Scan public mentions; update the adoption inventory via PR |
| Ecosystem | Fork contribution outreach (`github-loopx-fork-outreach-scan`) | 7d | Evaluate forks/derivatives; send upstream PR invitations |
| Ecosystem | Content-ops cadence (`loopx-x-content-ops-cadence`) | 1d | Manage the content pipeline rhythm |
| Ecosystem | X profile readback (`x-profile-public-readback`) | 7d | Read back the public X profile |
| Ecosystem | X distribution funnel (`x-public-distribution-funnel`) | 7d | Observe the X distribution funnel |

Full copy-ready example for the ecosystem mention scan:

```bash
loopx todo add --goal-id <goal-id> --project . --role agent \
  --claimed-by <your-agent-id> --task-class continuous_monitor \
  --action-kind github_mention_scan --target-key github-mention-scan \
  --cadence 7d --next-due-at "2026-08-22T10:00:00+08:00" \
  --expires-at "2027-08-15T10:00:00+08:00" \
  --continuation-policy same_agent_non_delivery \
  --text "[P2] 每周扫描仓库公开提及，分类并更新采纳清单"
```

And the fork-outreach scan that runs next to it:

```bash
loopx todo add --goal-id <goal-id> --project . --role agent \
  --claimed-by <your-agent-id> --task-class continuous_monitor \
  --action-kind github_loopx_fork_contribution_outreach \
  --target-key github-loopx-fork-outreach-scan \
  --cadence 7d --next-due-at "2026-08-22T10:30:00+08:00" \
  --expires-at "2027-08-15T10:00:00+08:00" \
  --continuation-policy same_agent_non_delivery \
  --text "[P2] 每周评估 fork/衍生项目可吸收能力，有价值来源发上游 PR 邀请"
```

Heartbeat automation picks the monitor up on its due date. Each run records
evidence on the same todo; a material change creates a follow-up todo, and a
no-change run stays a quiet no-op instead of forcing progress.

### 5.4 Enable goal-driven issue-to-PR automation

Issue-fix is a goal loop, not a one-shot command. Start a maintenance goal, or
use the active repository goal, whose objective is to keep fixing public
issues and following each PR to its terminal state:

```text
/loopx 持续修复仓库公开 issue：选择可修复候选，产出 focused PR，
跟进 CI 与 review 到 merged/closed，再接下一个 issue
```

`/loopx Fix https://github.com/owner/repo/issues/123` seeds one candidate into
that goal. The capability builds feasibility, repository-context, reviewer,
validation, and PR-lifecycle packets and assigns one explicit route:
`fix_pr` / `comment_only` / `triage_only`. The same domain state accumulates
feasibility, PR lifecycle, and outcome across multiple issues; a merged or
closed PR resumes the next candidate instead of stopping the loop.

The host agent may create or update a PR only when LoopX state records that
authority and repository policy allows it; merge remains a separate decision
unless explicitly authorized.

### 5.5 Optional content-ops pipeline

For creator/operator workflows, start with the connector source map, then
project the item queue:

```bash
loopx value-connectors source-map --format json
loopx content-ops queue-status --item-json path/to/item.json --format json
```

Items move through source → angle → draft → feedback → publish gate →
readback, and publishing stays blocked until an explicit owner decision.

## 6. Reusable Playbook

1. Weekly: scan public mentions, update the adoption inventory through a PR.
2. Per incoming issue/PR: classify, tag, and route; drive fixable issues
   through the issue-fix loop.
3. Per fork/derivative: evaluate the diff against upstream, decide
   absorbability, send one upstream PR invitation, record it.
4. Per security report: reproduce, fix, self-review at exact head, decide
   disclosure, release.
5. Per social idea: build a public-safe source map, draft through the
   content-ops pipeline, gate on owner approval, publish, read back.

## 7. Evidence Index

- Ecosystem adoption inventory:
  [`ecosystem-adoption.md`](ecosystem-adoption.md)
- TypeScript migration RFC: issue
  [#3225](https://github.com/huangruiteng/loopx/issues/3225), RFC PR
  [#3226](https://github.com/huangruiteng/loopx/pull/3226)
- Issue-fix capability:
  [`loopx/capabilities/issue_fix/README.md`](../../loopx/capabilities/issue_fix/README.md)
- Content-ops capability:
  [`loopx/capabilities/content_ops/README.md`](../../loopx/capabilities/content_ops/README.md)
- Releases v0.4.5 through v0.4.7:
  [releases](https://github.com/huangruiteng/loopx/releases)
