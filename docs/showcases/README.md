# LoopX Showcases

This directory is the complete public-safe case inventory for LoopX. Start with
independent user evidence, then use the case type and evidence label to separate
real-world adoption from contributor cases, creator dogfooding, and reproducible
demos.

## Start With Real Use

**Source note:** The chat screenshots in the first two cases are owner-approved
message excerpts from the LoopX public Lark developer group. They remain
user-reported evidence, not independently reproduced proof.

### 13+ hour C++ algorithm accuracy run

**Independent user** · `>13h` reported · no parameter micromanagement reported

LoopX kept a complex accuracy task aligned to its declared vision, then used
replan to trigger public research instead of continuing local parameter
thrashing. The user reported improved precision and retained experiment
evidence.

<p align="center">
  <a href="../assets/showcases/user-feedback/cpp-accuracy-13h-user-report.jpg"><img src="../assets/showcases/user-feedback/cpp-accuracy-13h-user-report.jpg" alt="Authorized user feedback reporting a LoopX C++ algorithm run lasting more than 13 hours with improved precision and retained evidence" width="48%"></a>
  <a href="../assets/showcases/user-feedback/cpp-accuracy-public-research-user-report.jpg"><img src="../assets/showcases/user-feedback/cpp-accuracy-public-research-user-report.jpg" alt="Authorized follow-up explaining that LoopX replan triggered public research and found a code-memory MCP" width="48%"></a>
</p>

*Source: owner-approved message excerpts from the LoopX public Lark developer
group. Runtime and outcome are user-reported; the referenced [code-memory
MCP](https://github.com/DeusData/codebase-memory-mcp) is public, but the private
project and its measurements are not independently reproducible.
[Read the case](cases/independent-cpp-accuracy-long-run.md).*

### Four-day unattended agent run

**Independent user** · `4d` reported · no intervention reported during the run

LoopX kept one agent doing useful work across a four-day window and preserved a
periodic report surface for later inspection.

<p align="center">
  <a href="../assets/showcases/user-feedback/four-day-unattended-user-report.jpg"><img src="../assets/showcases/user-feedback/four-day-unattended-user-report.jpg" alt="Minimally redacted authorized chat excerpt reporting a four-day LoopX agent run without human intervention" width="72%"></a>
</p>

*Source: an owner-approved, minimally redacted message excerpt from the LoopX
public Lark developer group. The workload, run history, and quality assessment
remain private and user-reported.
[Read the case](cases/independent-four-day-unattended-agent.md).*

### Public Engine refactor across seven merged PRs

**Independent user** · seven merged PRs · maintainer review and merge remained
present

One durable refactor goal became staged component extractions across the public
[`zilliztech/mfs` Engine issue](https://github.com/zilliztech/mfs/issues/166)
and seven merged PRs. The repository independently verifies the issue and PR
sequence; LoopX attribution, perceived quality, and the reported `1B+` token
scale remain user-reported.

[Read the case and inspect all seven PRs](cases/independent-public-engine-refactor.md).

Open the [hosted Showcase index](https://loopx-project.github.io/loopx/docs/showcases/index.html)
for the bilingual visual case surface. The
[feedback coverage map](user-feedback-coverage.md) records every input cluster,
including useful signals that were deliberately not promoted to success cases.

## What A Case Includes

Showcases are not raw run logs. Each case should reduce a real collaboration
into a reusable control-plane pattern:

- the situation before LoopX was useful;
- the LoopX behavior that changed the work loop;
- the user-facing value in plain language;
- the evidence boundary, including what must stay private;
- a reproducible demo or the reason a demo is still pending;
- optional data that a future website can render as a public evidence sequence.

## Catalog Contract

The machine-readable catalog lives in
[showcase-catalog.json](showcase-catalog.json). Public docs and future frontend
surfaces should consume that file instead of scraping prose.
The first frontend surface contract lives in
[frontend-surface.md](frontend-surface.md).
Seed-user feedback and case candidates should follow the
[PoC feedback and case report loop](poc-feedback-case-report-loop.md) before
they become catalog entries or Frontstage cards.

The first static visual asset is the public-safe
[control-plane board](../assets/control-plane-board.svg), which shows a user
gate staying visible while a scoped side path continues through claimed todo,
quota guard, run history, and evidence writeback.
The first creator-operator storyboard is
[creator-ops-fake-data-storyboard.md](creator-ops-fake-data-storyboard.md).
Its feedback and source-status contract is
[creator-ops-feedback-boundary-contract.md](creator-ops-feedback-boundary-contract.md).
The first static frontstage prototype is generated from the catalog with
`python3 examples/showcase-frontstage-prototype.py --output /tmp/loopx-showcases.html`.

The dashboard frontstage now has a separate public-safe share-bundle path for
showing a live-looking control-plane board without exposing local state:

```bash
cd apps/presentation/dashboard
npm run export:frontstage-share
```

This writes `/tmp/loopx-frontstage-share-bundle` with the static
[public homepage](https://loopx-project.github.io/loopx/), compiled dashboard, a
sanitized `goal_channel_projection_v0` status fixture, direct `/frontstage/`
static-route support, and a manifest. GitHub Pages publishes this generated
artifact, not live registry files or local status exports. The interactive
dashboard route remains an exporter compatibility surface, not a promoted
public entry. New users should start from the homepage; public cases,
efficiency evidence, and the public boundary come from this directory, while
live local `statusUrl` feeds belong only to explicit ops-mode inspection.
For animated showcase assets, start from the
[public storyboard artifact](showcase-animation-storyboard.json). Keep
`showcase-catalog.json` as the only case data source.
Generate the first catalog-backed animation prototype with
`python3 examples/showcase-animation-prototype.py --output /tmp/loopx-showcase-animation.html`
or open the committed
[showcase-animation-prototype.html](showcase-animation-prototype.html). Validate
the artifact with `python3 examples/showcase-animation-prototype-smoke.py`.

![Hosted LoopX frontstage showing public-safe showcase cases](../assets/frontstage-showcase-first-screen.png)

## Experimental Feature Demos

### DSH × LoopX: Replan a real decision

[![DSH × LoopX recording cover](../assets/showcases/dsh-loopx/dsh-loopx-cover.png)](../assets/showcases/dsh-loopx/dsh-loopx-quickstart-replan.mp4)

The [60-second real DSH recording](../assets/showcases/dsh-loopx/dsh-loopx-quickstart-replan.mp4)
starts with one explicit `loopx` skill selection, then shows a serverless
constraint changing a logging-library decision from Pino to Roarr without
losing the earlier evidence. The public fixture verifies 3/3 behaviors and
documents why 12 packages became 4.

    python3 examples/dsh-loopx-demo-smoke.py

Read the [case and evidence boundary](cases/dsh-loopx-replan-demo.md), or
[reproduce the DSH path](../../examples/dsh-loopx-demo/README.md).

### Start With A Useful Loop

If you want a lightweight first demo before reading the case studies, start
with the beginner preset picker. It shows how a useful loop compiles to real
LoopX commands without granting write authority:

```bash
loopx preset list
loopx preset show daily-triage
loopx preset show ci-sweeper
```

Daily Triage, Changelog Draft, and PR Watch are beginner report/draft/watch
paths. CI Sweeper and Dependency Sweeper are visible because they are high-ROI
maintainer workflows, but they stay opt-in and begin with a dry-run or policy
report before any isolated worktree patch is attempted.

### Auto Research One-Click Start

The auto-research path is the experimental one-command agent-team demo:

```bash
loopx auto-research "How should we evaluate whether multi-agent auto research creates value?"
loopx auto-research start "How should we evaluate whether multi-agent auto research creates value?" --execute
```

The contract command previews the research brief, evidence boundary, and next
launch packet. The `start --execute` command opens visible Codex CLI lanes
through the generic multi-agent kernel; lane-authored evidence still has to be
written back through LoopX state before the demo can claim progress. See the
[auto-research command path](../../demo/auto_research/README.md).
For the shipped stop marker, `--attach` takeover, and state-aware wake cycle,
use the contributor
[stop/takeover/wake walkthrough](../guides/auto-research-stop-takeover-wake-walkthrough.md).

### Review Agent Work

Review Agent Work is also an experimental entry: it uses the read-first
dashboard path to inspect connected projects, user gates, agent lanes, todos,
and evidence before granting more control.

```bash
loopx serve-status --global-registry --port 8766 --limit 80
cd apps/presentation/dashboard && npm run dev
```

CLI state remains the source of truth, browser writes require explicit local
opt-in, and review signals stay separate from execution permission.

## Independent User Cases

| Case | Problem | LoopX behavior | Outcome boundary |
| --- | --- | --- | --- |
| [13+ hour C++ algorithm accuracy run](cases/independent-cpp-accuracy-long-run.md) | Complex accuracy work risked local thrashing and context loss | Vision-aligned replan triggered public research and a new code-memory method | Improved precision is user-reported; public tool is inspectable |
| [Four-day unattended agent run](cases/independent-four-day-unattended-agent.md) | Operator needed useful work without repeated prompting | Durable continuation plus a periodic report surface | Four days and usefulness are user-reported |
| [Public Engine refactor](cases/independent-public-engine-refactor.md) | Monolithic Engine mixed nine responsibility areas | One durable goal became staged, PR-sized component extractions | Seven PRs are public; attribution and token scale are user-reported |

The catalog and hosted index keep these external cases first. README files keep
only the strongest three and link back here; the full inventory stays in this
directory.

## Contributor, Creator, And Demo Cases

| Case | Type | Pattern | Evidence |
| --- | --- | --- | --- |
| [External hardware-agent workflow](cases/0619-dynamic-workflow-hardware-agent.html) | Contributor case | Dynamic workflow, multi-agent convergence | Contributor-approved interactive case |
| [LoopX self-iteration](cases/0619-loopx-self-iteration.md) | Creator dogfooding | Self-iteration, peer claims, evidence writeback | Public Git evidence |
| [Overnight PR batch](cases/0627-overnight-pr-batch.md) | Creator dogfooding | PR-sized slices, validation writeback | 22 merged commits over a 10-hour public Git window |
| [Overnight project refactor](cases/0623-overnight-project-refactor.md) | Creator dogfooding | PR-sized slices, todo follow-up, supersede | Public-safe lifecycle narrative |
| [Blocked P0 safe rotation](cases/0617-blocked-p0-safe-rotation.md) | Reproducible demo | Concrete user gate, safe P1/P2 fallback | Focused synthetic smoke |
| [PR issue automatic fix](cases/0624-pr-issue-auto-fix.md) | Reproducible demo | Issue-fix workflow, repro, reviewer handoff | Public-safe pattern case |
| [Agent-to-agent PR comment loop](cases/0623-agent-to-agent-pr-comments.md) | Reproducible demo | Claimed handoff, comment, fix, review packet | Public-safe pattern case |
| [DSH × LoopX Replan](cases/dsh-loopx-replan-demo.md) | Reproducible recorded demo | Native skill selection, durable Replan, GoalBar closeout | Real DSH recording plus deterministic public fixture |

## Appendix Cases

| Case | Pattern | Status | Public Surface |
| --- | --- | --- | --- |
| [0620 creator-operator long-running agent case](cases/0620-creator-operator-case-spec.md) | Creator-operator workflow, user gate, feedback capture, material library | Synthetic product case spec | [Fake-data storyboard](creator-ops-fake-data-storyboard.md), [feedback contract](creator-ops-feedback-boundary-contract.md) |

Appendix cases are useful product direction, but they should not appear as
frontstage top cards until there is real public evidence or an approved
public-safe user story.

## Case Lifecycle

1. **Captured**: a real project shows a durable behavior pattern. Keep raw
   screenshots, private chats, and internal links outside this repo.
2. **Reported**: reduce the feedback into the
   [case report shape](poc-feedback-case-report-loop.md#case-report-shape):
   domain, loop length, hard part, LoopX behavior, human decision, evidence,
   and private boundary.
3. **Sanitized**: write a public-safe case card with the domain generalized,
   the evidence boundary explicit, and no private source material.
4. **Reproducible**: add a small synthetic demo or smoke that proves the
   reusable LoopX behavior without depending on private artifacts.
5. **Frontend-ready**: add or update the catalog fields needed for a visual
   website card, such as evidence sequence, pattern tags, and suggested visual
   layout.

Cases can enter the catalog before they have a runnable demo, but their status
must say so clearly. A redacted stub should make a modest claim and name the
missing public evidence instead of filling gaps with speculation.

## Redaction Rules

Do not commit:

- private document or chat URLs;
- raw screenshots from internal tools;
- names of non-public users, teams, customers, or proprietary projects;
- local filesystem paths, task ids, credentials, raw traces, benchmark task
  text, or verifier output;
- claims about benchmark performance that are not backed by public compact
  evidence.

Do commit:

- generalized domain labels, such as `hardware-agent-development` or
  `benchmark-rotation`;
- reusable control-plane patterns, such as `concrete_user_gate`,
  `blocked_priority_fallback`, or `dynamic_workflow`;
- synthetic demos that exercise public LoopX contracts;
- explicit `evidence_boundary` notes that keep future authors honest.

## Future Frontend Shape

The catalog is intentionally small enough for a static website to render.
A good first website view would show:

- a card grid of cases grouped by pattern family;
- a visual timeline for each case: trigger, LoopX state, agent action,
  user decision, and outcome;
- a "try the demo" command when a case has a synthetic reproduction;
- a redaction badge when a case is a stub awaiting contributor details.

The frontend should use the catalog as the source of truth and link back to the
human-readable case pages for narrative context.
