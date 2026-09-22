<div align="center">

<h1 align="center">LoopX</h1>

<img src="docs/assets/loopx-social-preview.png" alt="LoopX loop engineering social preview banner" width="420">

**The open, provider-neutral, stateful control plane for long-horizon agents.**

<sub>Runs on top of Codex, Claude Code, Cursor, and other agent harnesses. LoopX preserves objectives, gates, todos, evidence, quota, and handoffs across turns; the harness executes bounded work.</sub>

**[LHTB](https://zli12321.github.io/LHTB/index.html) · 46 tasks · GPT-5.6 Sol:** LoopX 1.0.3 Heartbeat reaches **0.4948 mean reward** — **+17.3% vs Plain Codex**, **+10.6% vs native Codex Goal**.<br>
<sub><a href="#lhtb-results">Results and pass rates ↓</a></sub>

<a href="https://trendshift.io/repositories/102379?utm_source=repository-badge&amp;utm_medium=badge&amp;utm_campaign=badge-repository-102379"><img src="https://trendshift.io/api/badge/repositories/102379" alt="loopx-project/loopx on Trendshift" width="220" height="48"></a>

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE) [![Release](https://img.shields.io/github/v/release/loopx-project/loopx?filter=v*&display_name=tag)](https://github.com/loopx-project/loopx/releases/latest) [![Discord](https://img.shields.io/badge/Discord-Join-5865F2?logo=discord&logoColor=white)](https://discord.gg/XmGgQyCFZd) [![TypeScript core](https://img.shields.io/badge/core-TypeScript-3178C6?logo=typescript&logoColor=white)](docs/architecture/rfcs/typescript-control-plane-migration-v0.md) [![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml) [![Local first](https://img.shields.io/badge/control--plane-local--first-brightgreen.svg)](docs/public-private-boundary.md) [![Loop Agents](https://img.shields.io/badge/status-loop%20agents%20active-brightgreen.svg)](docs/product/release-readiness.md)

[Public website](https://loopx-project.github.io/loopx/) · [Blog](https://loopx-project.github.io/loopx/blog/) · [Docs](https://loopx-project.github.io/loopx/docs/) · [Developer Book](https://loopx-project.github.io/loopx/docs/book/en/) · [Try LoopX](#try-loopx) · [LHTB results](#lhtb-results) · [See real loops](#evidence) · [How it works](#why-loopx) · [简体中文](README.zh-CN.md)

</div>

---

Open and provider-neutral, LoopX is a lightweight state kernel and local-first
control plane for loop engineering. It runs on top of different agent harnesses
rather than replacing them, providing the long-horizon state, semantic
decisions about what happens next, governance, recovery, and human-agent
collaboration that keep long-running work reviewable, restartable, and easier
to hand off across turns, tools, and agents.

**Loop engineering for long-horizon AI agents and peer agent teams.**

> Keep the loop moving. Keep the judgment human.

## Learn LoopX

| Your next step | Start here |
| --- | --- |
| Run your first long-horizon task | [Install and connect](#try-loopx) |
| Manage work through a visual interface | [Personal Agent Workspace](#meet-the-personal-agent-workspace) |
| Inspect the evidence before adopting | [LHTB results](#lhtb-results) · [Real project cases](#used-in-real-projects) |

- **Developer Book** - the curated bilingual path from control-plane foundations to project onboarding and developer contributions. [中文版](https://loopx-project.github.io/loopx/docs/book/) · [English](https://loopx-project.github.io/loopx/docs/book/en/)
- **Getting started** - install, connect a project, and run your first governed loop. [Guide](docs/guides/getting-started.md)
- **Docs** - the full reference and operations site. [LoopX Docs](https://loopx-project.github.io/loopx/docs/)

## Meet the Personal Agent Workspace

Keep long-horizon goals in one local-first workspace. Goals, attention,
conversations, tasks, files, schedules, and recovery stay durable across days,
restarts, and harnesses. Reopen a project, inspect the previous turn’s state
and evidence, and continue the next permitted action.

<a href="docs/assets/personal-workspace/loopx-dashboard-launch.mp4">
  <img src="docs/assets/personal-workspace/workspace-1.0.webp" alt="LoopX Workspace: owner decisions, Agent tasks, scheduled watches and completed work" width="960">
</a>

LoopX 1.0 brings these long-horizon control states into the Personal Workspace. It gives you one place to:

- see what needs you, what is running, what is being watched, and what is
  scheduled or stopped;
- configure Goal capabilities, distinguish machine defaults from Goal overrides,
  and preview changes before applying them;
- steer a live turn, queue a message, or use the async inbox from a connected
  Lark conversation with explicit Goal/Agent/session routing;
- inspect deliverable files, periodic reports, and their supporting evidence;
- continue across Codex, Claude Code, direct-model, and other registered Agent
  sessions without losing Goal state or evidence;
- review protected changes through typed preview, explicit confirmation, and
  receipts while LoopX state—not the browser—remains authoritative.

For Manager group conversations, LoopX keeps message visibility separate from
Turn authority; see the bilingual [Lark Manager context and authority
contract](docs/reference/protocols/lark-manager-context-authority-v0.md).

```bash
loopx dashboard
```

`loopx dashboard` is the supported browser/PWA launch path. You can also download
native desktop previews from the [1.0 release](https://github.com/loopx-project/loopx/releases/tag/v1.0.0);
they reuse the same loopback services and Goal state. Apple Silicon macOS supports
signed App updates that pair the shell with its bundled runtime, plus repair and
recovery. Python 3.11+ is required; the App is ad-hoc signed, not notarized.
Windows preview installers currently use manual updates and a separately installed CLI.
[Desktop installation, updates, and source development](apps/desktop/loopx-control-plane/README.md).

<img src="docs/assets/personal-workspace/capability-1.0.webp" alt="Real Workspace recording: configure child-task capacity and allowed responsibility domains" width="960">

From a source checkout, run `python -m demo.workspace serve` to explore a community
event, a home-energy comparison, and a neighborhood website release. Each has four
work roles, 18 tasks, two decisions, and two watches. The screenshot above comes
from this reproducible workspace. [Scenarios and replay instructions](demo/workspace/README.md).

[Watch the full 32-second walkthrough](docs/assets/personal-workspace/loopx-dashboard-launch.mp4)
· [Read the workspace guide](docs/guides/personal-workspace-user-guide.md)
· [Try the five-minute tour](docs/guides/personal-workspace-trial-guide.md)

<a id="how-it-works"></a>

## Why LoopX

An agent can finish a task in one session. Long-running work is harder:
objectives change, owner decisions appear, evidence goes stale, agents hand work
to peers, and a scheduler can keep spending after no useful transition remains.
Chat memory and a timer are not enough to govern that.

LoopX keeps the durable control state in one compact layer:

```text
objective / issue / project
   │
   ▼
LoopX state: objective + gates + todos + scope + evidence + quota
   │
   ├─ human judgment needed? ── yes ─▶ ask a concrete question and wait
   │
   ├─ safe fallback available? ──────▶ run one bounded agent slice
   │
   ▼
Codex / Claude Code / Cursor / shell agent executes one turn
   │
   ▼
write evidence + handoff + next todo ─▶ quota decides the next tick
```

Agent runtimes execute the work. LoopX governs the state that lets engineering,
research, discovery, and operations loops continue across runs. It is not
another agent framework or a provider-specific orchestration runtime.

![LoopX control-plane board](docs/assets/control-plane-board.svg)

A useful mental model is an
**[agent-native Kanban for long-running work](docs/development/control-plane-course/00-concept-primer.md)**.
Cards carry identity, authority, evidence, and continuation. Moves are validated
operators such as claim, gate, monitor, and writeback. The board is a
projection; LoopX state remains the source of truth.

Registered agents are peers. Claims, leases, task boundaries, capabilities, and
typed continuation decide who acts next; no durable leader identity is
required.

LoopX is useful when you run:

- multi-day engineering, research, benchmark, or experiment objectives;
- issue and PR loops that must preserve scope, evidence, and review state;
- recurring heartbeat or monitor work;
- projects with owner, safety, publication, or private-data gates;
- peer-agent teams where ownership, leases, and handoff matter;
- creator, research, or operations workflows whose progress must remain
  legible to a non-engineering operator.

LoopX is not an autonomous production controller. Dangerous permissions,
publishing, production writes, and final ownership stay with the human.

<a id="see-it-in-action"></a>

## Evidence

<a id="lhtb-results"></a>

### LHTB: Higher Mean Reward With the Same Model

**LoopX 1.0.3 Heartbeat reaches 0.4948 mean reward across 46 matched tasks:**
**+17.3% over Plain Codex** and **+10.6% over native Codex Goal**.
All three use GPT-5.6 Sol, max reasoning effort, and disabled Web Search.
[Long-Horizon Terminal-Bench](https://zli12321.github.io/LHTB/index.html) goes beyond coding: these tasks span research
reproduction, scientific simulation, multimodal analysis, professional
workflows, games, and systems work.

| Execution mode | Mean reward ↑ | Strict pass rate (≥0.95) | Pass rate (≥0.80) |
| --- | ---: | ---: | ---: |
| Plain Codex | 0.4218 | 7/46 (15.2%) | 12/46 (26.1%) |
| Native Codex Goal | 0.4475 | 4/46 (8.7%) | 14/46 (30.4%) |
| **LoopX 1.0.3 Heartbeat** | **0.4948** | **7/46 (15.2%)** | **15/46 (32.6%)** |

The ≥0.80 threshold is a supplementary, post-hoc view; ≥0.95 remains the
benchmark's strict solved threshold. No score in this study equals 0.80, so
these counts also match the study brief's >0.80 view.

Against native Goal, task-level rewards improve on **23**, tie on **13**, and
regress on **10** tasks. Strict solves match Plain Codex, so the mean gain is
not a claim that every task improves or that more tasks are fully solved than
Plain. Mean reward also credits partial progress.

One effective trial per task and mode; designated replacement trials and
unequal runtime/budgets, including longer budgets for some Heartbeat replacements.
These are observed system results, not an equal-budget efficiency result or
an isolated causal estimate of LoopX's effect.

[Interactive results, task comparisons and methodology](https://loopx-project.github.io/loopx/benchmarks/lhtb/)
· [Five-arm study and limits](benchmark/LHTB/studies/five-arm-gpt56sol-max/README.md)
· [Public task-level scores](benchmark/LHTB/studies/five-arm-gpt56sol-max/data.json)

Beyond benchmarks, LoopX also has inspectable long-running project evidence.
The public OpenViking contribution sequence and the redacted, owner-run
Auto ML showcase each span **200+ hours of elapsed loop lifetime**, preserving
bounded turns, decisions, and evidence updates. This measures wall-clock project
time, not continuous model execution or unattended production autonomy. Open
each visual to inspect the public-safe task graph, evidence branches, and
cross-turn decisions; each case states its source and reproducibility boundary.

### Open-Source Issue Fix

**200+ hour public contribution arc: PR delivery and reusable fix knowledge
evolve together.**

<a href="docs/assets/long-running-loop-openviking-trajectory.png">
  <img src="docs/assets/long-running-loop-openviking-trajectory.png" alt="Open-source issue-fix trajectory linking focused PR delivery with reusable LoopX capabilities" width="760">
</a>

LoopX's creator uses this path as an
[OpenViking contributor](https://github.com/volcengine/OpenViking/pulls?q=is%3Apr+author%3Ahuangruiteng).
The represented public contribution sequence spans more than 200 elapsed hours
from its first PR creation to the latest represented review or update. The
[Issue-Fix capability](loopx/capabilities/issue_fix/README.md) keeps rolling
repository context, revision-stamped fix knowledge, and reviewer-facing
preferences separate; linked PRs plus current checkout source and tests remain
authoritative.

### Auto ML Experiment

**Redacted owner-run showcase: a 200+ hour experiment arc keeps hypotheses,
matched evidence, invalid lineages, running replicates, and promote/stop gates
visible in one graph.**

<a href="docs/assets/long-running-loop-ml-experiment-trajectory.png">
  <img src="docs/assets/long-running-loop-ml-experiment-trajectory.png" alt="Auto ML Experiment trajectory with experiment lineages, evidence gates, and promotion decisions" width="760">
</a>

The redacted public-safe graph preserves decision lineage across that 200+ hour
elapsed window. It is an owner-run showcase, not a claim of continuous compute,
independent reproduction, a production result, or company or employer
endorsement. The redacted image is not sufficient to reproduce the underlying
experiment independently.

### Auto Research

**Reproducible public KNN demo: proposer, executor, and evaluator/promoter agents
iterate in parallel while todo, quota, evidence, and targeted wake remain
visible.**

<a href="docs/assets/auto-research-multi-agent-showcase.png">
  <img src="docs/assets/auto-research-multi-agent-showcase.png" alt="Auto Research multi-agent workspace with proposer, executor, evaluator/promoter, todo, quota, evidence, and targeted wake activity">
</a>

This screenshot comes from LoopX's built-in exact-KNN demo. The public task,
editable and protected files, deterministic CPU evaluator, and dev/held-out
commands all live in this repository. Follow the
[showcase walkthrough](docs/product/use-cases/auto-research/decentralized-auto-research-showcase.md)
or the [demo command path](demo/auto_research/README.md) to reproduce the
workflow; it is a demo result, not a production research claim.

### Used In Real Projects

- **Independent user · `>13h` C++ accuracy run.** The user reported that a
  multi-stage task stayed aligned, triggered public research, adopted a
  [public code-memory tool](https://github.com/DeusData/codebase-memory-mcp),
  and improved final precision. [Read the evidence boundary](docs/showcases/cases/independent-cpp-accuracy-long-run.md).
- **Independent user · `4d` unattended run.** The user reported four days
  without human intervention, useful ongoing work, and a periodic report
  surface. [Read the redacted case](docs/showcases/cases/independent-four-day-unattended-agent.md).
- **Independent user · `7` merged PRs.** A LoopX-attributed Engine refactor is
  visible in a [public issue](https://github.com/zilliztech/mfs/issues/166) and
  seven merged PRs; attribution and the reported `1B+` token scale remain user
  reports. [Inspect the case](docs/showcases/cases/independent-public-engine-refactor.md).

These are the three strongest current cases, not the full inventory. Browse the
[complete Showcase catalog](docs/showcases/README.md) for contributor cases,
creator dogfooding, reproducible demos, and explicit evidence-strength labels.

### Exploratory Benchmark Studies

- **[SWE-Marathon](https://loopx-project.github.io/loopx/benchmarks/swe-marathon/):**
  Five execution modes on 15 matched tasks compare self-verification, scores,
  and cost. More self-verification did not consistently yield higher scores.
- **[LHTB × LoopX](https://loopx-project.github.io/loopx/benchmarks/lhtb/):**
  The [results above](#lhtb-results) lead into five execution mechanisms,
  task-level comparisons, regressions, and exploratory task-type analysis.
- **[DeepSWE behavior analysis](https://loopx-project.github.io/loopx/benchmarks/deepswe/behavior-discovery/)** (Chinese):
  Selected cases examine how domain hints relate to requirement retention and
  verification choices, offering mechanism hypotheses for further testing.

SWE-Marathon and LHTB have one effective trial per task and mode; DeepSWE uses
selected cases and post-hoc analysis. None establishes a general performance
gain.

More inspectable surfaces:

- the [public homepage](https://loopx-project.github.io/loopx/) for the product
  narrative, quick start, and long-running evidence;
- the [complete Showcase catalog](docs/showcases/README.md) and its
  [bilingual hosted index](docs/showcases/index.html);
- the [cross-runtime implementation review demo](docs/product/use-cases/cross-runtime/cross-runtime-impl-review-demo.md);
- the public [user manual](https://my.feishu.cn/wiki/CaL5wMk9ui17ngkWzeUcMlAYnZg).

<a id="quick-start"></a>

## Try LoopX

Requirements: Python 3.11+ and Node.js 22.18.0+; Node.js 24 LTS is recommended.
Use an active Python environment whose console scripts are on `PATH`; macOS and
Linux use a POSIX shell, while native Windows uses PowerShell 7. Node.js runs
the managed, idle-exiting TypeScript Effect core; LoopX starts it automatically.
Git is only needed for contributor clone/canary workflows.

Install from PyPI without cloning:

```bash
python3 -m pip install --upgrade loopx
loopx workflow-skills --install
loopx doctor
```

Existing installs can use `loopx update plan` and `loopx update apply`; LoopX
keeps the detected pip, pipx, or archive owner instead of switching channels.

On native Windows PowerShell 7, use the same PyPI release without a POSIX
compatibility layer:

```powershell
py -3.11 -m pip install --upgrade loopx
loopx workflow-skills --install
loopx doctor
```

Restart your agent host after first install so it reloads the workflow skills.
See [Installing LoopX](docs/guides/installing-loopx.md) for `pipx`, host
command surfaces, native Windows checkout installation, upgrade, rollback,
uninstall, and the archive fallback.

Then connect from your project root:

```bash
cd /path/to/your-project
loopx connect
loopx status
```

If the project has not been initialized and `connect` tells you state is
missing, use the guided path:

```bash
loopx start-goal --guided --project . --goal-text "Your long-running objective"
```

LoopX should reuse existing state rather than overwrite it. Keep `.loopx/`,
`.codex/goals/`, and `.local/` ignored.

### Start From Your Agent

| Host | Recommended start | Loop driver |
| --- | --- | --- |
| Codex App | Ask the agent to connect this project to LoopX, run `loopx doctor`, preserve existing state, and report the current gate and next todo. Then use `$loopx <complex task>` or choose `loopx` from `/skills`. | Codex App heartbeat automation, refreshed from `quota should-run.scheduler_hint` |
| Codex App over SSH | `loopx agent-onboard --agent-type codex-app-ssh --project .` | The returned visible `/goal <task_body>` |
| Codex CLI | Start `codex` in the project, ask it to connect and diagnose LoopX, then use `$loopx <complex task>` or `/skills`. | Visible `/goal <task_body>`; no hidden headless execution by default |
| Claude Code | Install the opt-in adapter, then run `/loopx <task>` followed by `/loop`. | Native Claude Code `/loop` gated by LoopX |
| KunlunCode | Run `loopx-kunluncode connect --project . --goal-id <goal-id> --agent-id <registered-agent-id>`, add a bounded todo, then run `loopx-kunluncode run --project .`. | Native Goal Pro through app-server; LoopX writes completion and quota only after strict verification |
| OpenCode | Install the static command facade; opt in to `--with-goal-bridge` for recurring goals. | OpenCode command facade and explicit goal bridge |
| Pi | Install the opt-in goal extension with `loopx slash-commands --install --surface pi`, then use `/loopx <task>` from a trusted Pi session. | Visible Pi goal extension gated by LoopX quota (`loopx_goal_activate` + `agent_settled` continuation) |
| ZCode | Install the skill facade with `loopx slash-commands --install --surface zcode`, then invoke the `$loopx` skill (or `/loopx <complex task>`) from a ZCode session in the project. | The ZCode session's own turn loop; every continuation enters through `quota should-run` |
| Antigravity CLI (agy) | Install the skill facade with `loopx slash-commands --install --surface agy`, then invoke the `loopx` skill (or `/loopx <complex task>`) from an `agy` session in the project. | The session's native `/goal` loop (audited until `<!-- GOAL_COMPLETE -->`) with `schedule` self-wakes while the session lives; the facade instructs every turn/wake to re-enter through `quota should-run` — advisory pacing, not a host-enforced gate |
| Kiro CLI | Install the skill facade with `loopx slash-commands --install --surface kiro-cli`, then run `/loopx <complex task>` from a `kiro-cli` session in the project. | The session's native `/goal --max <N> <task_body> Done when: <criteria>` loop, with the acceptance criteria stated inside the goal statement because the host derives them from it, bounded by the host's own iteration budget (default 5) and settled through the built-in `goal` completion contract; the facade instructs every turn and iteration to re-enter through `quota should-run` — advisory pacing, not a host-enforced gate |
| DeepSeek Harness (dsh) | Install the [native DSH plugin](packages/dsh-loopx-plugin/README.md), select the `loopx` skill, and describe the task. The [dsh goal-mode adapter](loopx/dsh_goal_mode/README.md) remains available for headless turns. | Native same-session continuation and GoalBar, or headless dsh segments; both remain gated by LoopX authority |
| Cursor, shell, or custom runner | Use the installer and `loopx doctor`; connect manually or call LoopX from your runner. | Your shell, scheduler, or runner |

The exact, copy-ready setup messages and host recovery paths live in
[Getting Started](docs/guides/getting-started.md). Host integrations can inspect
the [Codex App host command registry contract](docs/reference/protocols/codex-app-host-command-registry-v0.md),
the [Codex CLI packaged install path](docs/product/runtimes/codex-cli/codex-cli-packaged-install.md),
the [Claude Code adapter](loopx/claude_goal_mode/README.md), the
[KunlunCode native Goal adapter](loopx/kunluncode_goal_mode/README.md), the
[Kiro CLI goal-mode adapter](loopx/kiro_cli_goal_mode/README.md), or the
[DeepSeek Harness turn adapter](loopx/dsh_goal_mode/README.md).

See the [60-second DSH × LoopX Replan recording and reproducible
fixture](docs/showcases/cases/dsh-loopx-replan-demo.md) for the native path:
install the plugin, select one skill, change a material constraint, and inspect
the preserved decision trail.

For custom runners, start with the
[minimal custom runtime example](docs/guides/minimal-custom-runtime-example.md)
(`python3 examples/custom-runtime-minimal-cli-turn-smoke.py`), then the full
[Embed LoopX in Your Agent Runner](docs/guides/custom-agent-runner-integration.md)
guide and the [worker bridge install contract](docs/integrations/worker-bridge-install-contract.md).
The core tick is deliberately small:

```text
loopx quota should-run      # should this registered agent act now?
loopx todo claim            # who owns this slice?
loopx todo update           # what changed?
loopx refresh-state         # what should the next turn see?
loopx quota spend-slot      # account for a completed, validated slice
```

### First-Run Feedback

If LoopX works for you, a one-minute public issue helps us learn what a real
first run looks like. It is optional, contains no telemetry, and should not
include logs, paths, credentials, internal project names, or goal contents:

- [First-run feedback](https://github.com/loopx-project/loopx/issues/new?template=first_run.yml)
- [Usage story for longer runs](https://github.com/loopx-project/loopx/issues/new?template=usage_story.yml)

`loopx first-run-report` prints the same prefilled link locally without
sending anything.

A successful connection has:

- `loopx doctor` passing;
- `.loopx/registry.json` and a projected active goal state;
- `loopx status` showing the current objective, concrete user gate, and next
  agent todo;
- a visible loop driver or an exact activation instruction;
- local runtime state ignored rather than committed.

Clone-based install is only for contributors who want the live canary wrapper:

```bash
git clone https://github.com/loopx-project/loopx ~/loopx
~/loopx/scripts/install-local.sh
loopx doctor
```

<a id="capability-surface"></a>

## Capabilities

LoopX keeps the architecture explicit so the same governed outcome can survive
a change of agent harness or external provider. The terms describe different
boundaries rather than interchangeable kinds of plugin:

| Boundary | Meaning | Go deeper |
| --- | --- | --- |
| **Kernel** | Owns durable goal, todo, gate, evidence, quota, recovery, and scheduling truth. | [Architecture](docs/architecture.md) |
| **Capability** | Defines a stable, provider-neutral contract for producing one bounded, verifiable caller outcome from LoopX state. | [Capability catalog](loopx/capabilities/README.md) |
| **Provider** | Calls an external system or local implementation and returns bounded observations, effect results, and readback. | [Provider responsibilities](docs/reference/extensions.md#runtime-responsibilities) |
| **Extension** | Packages and operates an optional provider through explicit install, readiness, enable, upgrade, disable, and rollback lifecycle. | [Extension lifecycle](docs/reference/extensions.md#runtime-lifecycle) |

Host declarations such as `--available-capability shell` describe observed
execution support. They are runtime capacities in this product map, not product
capabilities and not permission grants. The effective capability still applies
its own policy and authority checks before proposing a transition.

### Core Control-Plane Promises

The Kernel folds its mechanics into five questions. Each question delivers one
product promise on top of any agent harness: objective → long-horizon state;
next → semantic decisions; human judgment → human-agent collaboration;
evidence → recovery; continuation → governance.

| Question | What LoopX keeps visible |
| --- | --- |
| What is the objective? | The active goal, explicit scope, and current authority. |
| What happens next? | Ordered user and agent todos, ownership, claims, and leases. |
| What needs human judgment? | Concrete user gates instead of a vague "waiting for owner." |
| What evidence changed? | Compact run history, validation, blockers, and accepted writeback. |
| May the loop continue? | Quota, capabilities, safe fallback, scheduler hints, and stop conditions. |

### Control-Plane Surface

| Surface | What it does | Start with |
| --- | --- | --- |
| Goal state and status | Tracks active state, todos, claims, gates, evidence, run history, and first-screen attention. | `loopx status`, `loopx diagnose`, `loopx review-packet` |
| Quota and interaction contract | Decides whether a turn should deliver, ask, wait, self-repair, or stay quiet. | `loopx quota should-run`, [quota allocation](docs/quota-allocation.md) |
| Agent runtime bridges | Keeps Codex App, Codex CLI, Claude Code, and generic workers aligned with the same guard. | `loopx heartbeat-prompt`, `loopx codex-cli-bootstrap-message`, `loopx worker-bridge` |
| Operator surfaces | Renders compact status without making the browser the state authority. | `loopx serve-status`, [dashboard](apps/presentation/dashboard/README.md) |
| Session dash | Starts a live single-page panel that tracks fleet progress: sessions, their goals, and each goal's status/todo progress, with result statistics; auto-refreshes in place. | `loopx dash`, [session dash design](docs/product/surfaces/session-dash-panel-design.md) |
| External projections | Projects todos and gates into collaboration surfaces while LoopX remains authoritative. | `loopx lark-kanban`, [Lark Kanban adapter](docs/integrations/lark-kanban-control-plane-adapter.md) |
| Domain capabilities | Packages repeatable work lanes such as issue fixing, content operations, value connector planning, ML experiment advice, benchmark evidence, and Explore. | `loopx issue-fix`, `loopx content-ops`, `loopx value-connectors`, `loopx ml-experiment`, `loopx benchmark`, [Explore](loopx/capabilities/explore/README.md) |
| Experimental context learning | Lets named registered agents trial provider-neutral Reward Memory through ignored, default-off project configuration. OpenViking is one provider option, not a global dependency. | `loopx reward-memory experiment-status`, [Reward Memory architecture](loopx/capabilities/reward_memory/README.md) |
| Governance patterns | Captures reusable routing, gate, evidence, projection, and planning shapes. | [interaction patterns](docs/concepts/interaction-pattern-catalog.md), [state model](docs/state-interaction-model.md) |

The shipped primitives include lifetime goals, concrete user gates, audited safe
fallbacks, peer todo ownership, quota and steering, compact run history,
evidence-backed handoff, a read-first management surface, project-level value
signals, and public/private boundary checks.

### Product Capability Paths

Capabilities turn those generic primitives into outcome-owned work lanes. Start
from the outcome, then inspect the current registered implementation and its
write boundary:

| You need to... | Capability | Start with |
| --- | --- | --- |
| Turn a public issue into a reviewable, evidence-backed change | [Issue Fix](loopx/capabilities/issue_fix/README.md) | `loopx capability show issue-fix --format json` |
| Qualify the exact final diff before delivery | [Change Quality](loopx/capabilities/change_quality/README.md) | `loopx capability show change-quality-qualification --format json` |
| Preserve a changing stack of already reviewed branches | [Integration Branch](loopx/capabilities/integration_branch/README.md) | `loopx capability show integration-branch-reconcile --format json` |
| Explore uncertain research without losing hypotheses and findings | [Explore](loopx/capabilities/explore/README.md) | `loopx capability show explore --format json` |
| Rebase decisions on current evidence and verified outcomes | [Decision Context](loopx/capabilities/decision_context/README.md) | `loopx capability show decision-context --format json` |
| Produce scheduled or progress-triggered reports with receipts | [Periodic Report](loopx/capabilities/periodic_report/README.md) | `loopx capability show periodic-report --format json` |

Run `loopx capability list --format json` for the authoritative catalog in the
installed release. A capability detail reports its user value, maturity,
provider readiness, entry commands, write boundaries, protocols, and durable
validation. Browse the [human-readable capability index](loopx/capabilities/README.md)
to choose by outcome; use [Extensions and Capabilities](docs/reference/extensions.md)
when installing or building a provider.

### Runtime Responsibilities

| Role | Responsibility |
| --- | --- |
| **Agent** | Plans, analyzes, uses tools, and performs one bounded action through a host/runtime. |
| **Provider** | Calls external systems and returns observations, effect results, and readback. |
| **Capability** | Defines the caller outcome, normalizes provider output, validates it, and proposes a typed transition. |
| **Kernel** | Owns durable todos, gates, monitors, accepted writeback, quota, recovery, and scheduling. |

The execution path is `Agent -> Capability -> Provider`; the control path
returns `Provider readback -> Capability transition -> Kernel`. An extension is
how an optional provider is packaged and managed, not another control-plane
owner. See [Architecture](docs/architecture.md) and
[Extensions and Capabilities](docs/reference/extensions.md).

## Advanced Paths

The first useful loop does not require every optional surface. Add these only
when the work needs them.

Inspect the current goal's read-only capability catalog before enabling an
advanced path:

```bash
loopx configure-goal --goal-id <goal-id>
```

Without `--execute`, this reports current/default state, fit, boundaries, and
copyable commands without changing project state.

### Presets and Auto Research

Safe presets cover daily triage, changelog drafts, and PR watching. The
one-command research path coordinates proposer, executor, and
evaluator/promoter roles while keeping quota and evidence visible. See the
[beginner preset guide](docs/product/foundations/beginner-loop-presets.md) and
[Auto Research demo path](demo/auto_research/README.md).

```bash
loopx preset list
loopx preset show daily-triage
```

Preset inspection is read-only. For a connected recurring goal,
`loopx ready-score --goal-id <goal-id> --agent-id <agent-id>` reports whether
the loop is ready to run repeatedly.

### Governed Turns

LoopX can generate one pure, bounded turn decision from a validated receipt,
fresh quota state, and a provider-neutral budget. The current Codex CLI
quickstart and activation contract are documented in
[LoopX Turn for Codex CLI](docs/product/runtimes/codex-cli/loopx-turn-codex-cli-quickstart.md).

### Explore Graph and Harness

Explore is supported, optional, and default-off. It works best when a task has
a measurable offline evaluation, baseline, treatment, and guardrails; it is not
a substitute for production approval. Start with the
[Explore capability](loopx/capabilities/explore/README.md) and its
[Lark presentation mapping](loopx/capabilities/explore/README.md#presentation-sink-lark-mapping).

### Review Agent Work

Use `loopx review-packet` for a compact owner-facing view of decisions,
evidence, validation, and unresolved gates. The
[intelligent management surface](docs/product/surfaces/intelligent-management-surface.md)
describes the operator model; the
[project-level reward model](docs/product/foundations/project-level-reward-model.md)
describes conservative value signals across output quantity, quality, token
cost, and user attention cost.

For one concrete peer workflow, see the
[cross-runtime implementation review demo](docs/product/use-cases/cross-runtime/cross-runtime-impl-review-demo.md):
Claude implements and Codex reviews while LoopX keeps ownership, evidence,
quota, and handoff explicit.

### App and Projection Paths

- Local read-first UI: [dashboard guide](apps/presentation/dashboard/README.md)
- Public product overview: [public homepage](https://loopx-project.github.io/loopx/)
- Documentation portal: [hosted docs](https://loopx-project.github.io/loopx/docs/)
- Feishu/Lark projection: [Lark Kanban adapter](docs/integrations/lark-kanban-control-plane-adapter.md)
- Generic host integration: [integration guide](docs/integration.md)
- Custom multi-agent runner:
  [minimal custom runtime example](docs/guides/minimal-custom-runtime-example.md),
  then [custom runner integration](docs/guides/custom-agent-runner-integration.md)

Optional projections make state easier to inspect; they do not become the
source of truth.

### Operating and Recovery

Start daily inspection with:

```bash
loopx status
loopx history --goal-id your-project-goal
loopx quota should-run --goal-id your-project-goal
```

Automatic turns must check quota first and append spend only after validated
writeback. Quiet skips, preflight failures, and dry-run previews do not spend.
When a user gate blocks one lane, a separately audited safe fallback may
continue, but it must not bypass the gate.

Peer agents use `loopx todo claim` before delivery and `loopx todo update`
after validation so ownership and evidence remain visible.

Scheduler cadence follows `quota should-run.scheduler_hint`; installed Codex
App automations acknowledge the current hint through the returned
`ack_hint.cli_args`. Collision recovery, monitor semantics, self-repair, and
the exact operator commands are maintained in
[Getting Started](docs/guides/getting-started.md),
[Quota Allocation](docs/quota-allocation.md), and
[Long-Task Cadence Policy](docs/operations/long-task-cadence-policy.md).

Before publishing public docs or examples:

```bash
loopx check \
  --scan-path README.md \
  --scan-path docs/ \
  --scan-path examples/
```

## Current Technical Directions

LoopX has three active strategic programs plus an architecture and research
incubator. These are direction signals, not delivery promises; `main`, released
artifacts, and stable reference contracts remain the source of shipped truth.

- **Long-Horizon Benchmarks and Evidence:** reproducible capability evidence
  and controlled mechanism research across complementary benchmark
  environments. [Direction tracker](https://github.com/loopx-project/loopx/issues/3243)
- **Operator Surface and IM Integration:** an operator workspace, session
  records, and bounded collaboration surfaces. The Personal Workspace is
  shipped; unified steward, execution, and recovery journeys remain under
  qualification, with `@maxliux5` as implementation lead.
  [Direction tracker](https://github.com/loopx-project/loopx/issues/3244)
- **Shared Goal Authority and Cross-host Coordination:** provider-neutral
  coordination for explicitly shared goals, with NoKV as an unpromoted
  provider candidate rather than a new control-plane authority.
  [Direction tracker](https://github.com/loopx-project/loopx/issues/3245)
- **Architecture and Research Incubator:** Effect Program hardening,
  TypeScript parity migration, hierarchical stride, research exploration,
  human attention, artifact lifecycle, and memory utility work at explicitly
  different maturity levels.
  [Direction tracker](https://github.com/loopx-project/loopx/issues/3246)

Read the canonical
[Technical Directions map](docs/project/technical-directions.md) for stages,
promotion gates, contributor-safe cuts, and ownership boundaries. Use the
pinned [GitHub Discussion](https://github.com/loopx-project/loopx/discussions/2851)
for community discussion. Core control-plane reliability continues as the
shared foundation beneath these programs.

## Advanced Documentation

Start with the path that matches your current task. Use the hosted
[documentation portal](https://loopx-project.github.io/loopx/docs/) for the
published docs site; the [documentation index](docs/README.md) remains the
complete source map. This list stays selective; each category index owns its
deeper documents and versioned protocols.

### Use and Operate

- [Getting Started](docs/guides/getting-started.md): install, connect,
  diagnose, daily workflow, heartbeats, dashboard, development, and commands.
- [User Manual](https://my.feishu.cn/wiki/CaL5wMk9ui17ngkWzeUcMlAYnZg):
  public onboarding, concepts, FAQ, and selected cases.
- [Operations](docs/operations/README.md): goal continuation, todo, cadence,
  attention, and authority workflows.
- [Quota Allocation](docs/quota-allocation.md) and
  [Heartbeat Automation Prompt](docs/heartbeat-automation-prompt.md): scheduler
  eligibility, spend, and scheduled continuation.
- [Dashboard](apps/presentation/dashboard/README.md) and
  [Status Data Contract](docs/status-data-contract.md): operator-facing state
  and projection contracts.
- [Release Readiness](docs/product/release-readiness.md): install/update paths,
  compatibility gates, release notes, and safe-to-depend-on surfaces.

### Understand the Control Plane

- [Architecture](docs/architecture.md): lifetime-goal invariant and kernel.
- [State Interaction Model](docs/state-interaction-model.md): actors, stores,
  interaction contract, and writeback.
- [Concepts](docs/concepts/README.md): reusable routing, gate, evidence,
  projection, and planning patterns.
- [Product Foundations](docs/product/foundations/README.md): Loop Engineering
  principles, project-level reward, and reward-style replanning.
- [Product Vision](docs/product/vision.md): the broader Loop Agent direction.

### Integrate and Extend

- [Integration Guide](docs/integration.md)
- [Minimal Custom Runtime Example](docs/guides/minimal-custom-runtime-example.md)
- [Custom Agent Runner Integration](docs/guides/custom-agent-runner-integration.md)
- [Integrations](docs/integrations/README.md): runtime, host, collaboration, and
  external-system adapters, including worker bridge and Lark.
- [Extensions and Capabilities](docs/reference/extensions.md)

### Build and Review LoopX

- [Developer Guide](docs/development/README.md): contributor workflows,
  benchmark development, documentation layout, and quality gates.
- [Reference and Protocols](docs/reference/README.md): stable contracts and
  versioned implementation protocols, including host command and reward memory
  architecture.
- [Control-Plane Developer Course](docs/development/control-plane-course/README.md):
  nine Chinese, code-led lectures.
- [Testing and Quality](docs/development/testing-and-quality.md): validation
  layers and risk-based checks.
- [Public/Private Boundary](docs/public-private-boundary.md): safe fixtures,
  examples, evidence, and publication.

### Inspect Outcomes

- [Showcase Catalog](docs/showcases/README.md): public-safe cases and evidence
  labels.
- [Research and Evidence](docs/research/README.md): benchmark investigations
  and source-backed findings.
- [Update Notes](docs/update-notes/README.md): public-safe progress notes.

### Project and Community

- [Current Technical Directions](docs/project/technical-directions.md)
- [Project Governance](.github/GOVERNANCE.md)
- [Contributing](CONTRIBUTING.md) and [Contributor Tasks](docs/development/contributor-tasks.md)
- [Authors and Contributors](docs/project/authors.md)
- [Project History](docs/project/history.md)
- [Name and Marks](docs/project/trademarks.md)
- [Brand Guide for External Use](docs/project/brand-guide.md): how projects and companies may reference LoopX, its integrations, and its artwork
- [ADOPTERS](ADOPTERS.md): voluntary self-attested adoption directory
- [Ecosystem Adoption](docs/community/ecosystem-adoption.md) - integrations,
  sampling, and derivatives we observe and track

## Partner Projects

LoopX welcomes collaboration with other open-source projects to build the
long-running agent ecosystem. Our confirmed partners include:

- [OpenViking](https://github.com/volcengine/OpenViking) - Self-evolving
  context database for AI agents
- [NoKV](https://github.com/NoKV-Lab/NoKV) - AI native distributed file system

<a id="community--feedback"></a>

## Community and Feedback

LoopX is already running real long-running agent goals and is under active
development. The most useful feedback comes from real long-running agent
projects: where the control plane helped, where it felt heavy, and which gates
or handoffs disappeared from view.

- Use [GitHub Issues](https://github.com/loopx-project/loopx/issues) for
  reproducible bugs, install problems, and feature requests.
- Open PRs for docs fixes, showcase writeups, and small public-safe examples.
- Join the [Discord community](https://discord.gg/XmGgQyCFZd), or use Lark or
  WeChat below.

See [Support](.github/SUPPORT.md) for channel routing, service boundaries, and
official publication sources.

<p align="center">
  <a href="docs/assets/loopx-lark-developer-group.png"><img src="docs/assets/loopx-lark-developer-group.png" alt="LoopX Lark developer group QR code" width="280"></a>
  <a href="docs/assets/loopx-wechat-contact.png"><img src="docs/assets/loopx-wechat-contact.png" alt="LoopX WeChat contact QR code" width="220"></a>
</p>
<p align="center">
  <sub><strong>Lark:</strong> scan to join directly<br><strong>WeChat: <code>huangrt00</code></strong> · mention LoopX in the friend request</sub>
</p>

## Contributing

External contributors should start with
[Contributor Tasks](docs/development/contributor-tasks.md) for public, claimable work and
[Contributing](CONTRIBUTING.md) for setup, validation, and boundary rules.
Project roles and public history are recorded in
[Governance](.github/GOVERNANCE.md),
[Authors and Contributors](docs/project/authors.md), and
[Project History](docs/project/history.md).

LoopX keeps local active state separate from the public repository. Do not
commit `.loopx/`, `.codex/goals/`, live `ACTIVE_GOAL_STATE.md`, raw benchmark
traces, credentials, private logs, or operator artifacts.

## Current Status

LoopX 1.0 is a usable local control plane for long-running agent work and is
entering broader adoption. It is not a full agent platform, an agent runtime, or
an autonomous production controller.

Today LoopX ships a durable state kernel for goals, typed todos and decision
scopes, peer claims and leases, evidence and writeback, quota-aware scheduling,
and cross-turn continuation. Guided start, recurring heartbeat, isolated Codex
CLI turns, evidence-backed Issue-Fix admission, optional Explore and auto
research paths, public validation canaries, and the multi-project Personal
Workspace all build on that shared control state.

Support levels remain explicit. The state and CLI contracts are the stable
center; several host integrations and advanced paths are optional, default-off,
or experimental. LoopX does not grant credentials, approve destructive or
production actions, publish on a user's behalf without authorization, or turn
an unverified run into evidence of success.

Current investment is organized through the
[Technical Directions map](docs/project/technical-directions.md): long-horizon
benchmark evidence, operator surface and IM integration, shared-goal cross-host
coordination, and an explicitly staged architecture and research incubator.

## Star History

<p align="center">
  <a href="https://github.com/loopx-project/loopx/stargazers"><img src="https://loopx-project.github.io/loopx/site-assets/star-history.svg" alt="LoopX GitHub star history from verified snapshots" width="800"></a><br>
  <sub>Generated every six hours from GitHub's official stargazer timestamps using a repository-authorized workflow. A snapshot is published only when the fetched rows match GitHub's current star count; GitHub's image cache may delay refreshes.</sub>
</p>

## License

Apache License 2.0 beginning with `v0.4.8`. See [LICENSE](LICENSE) and
[NOTICE](NOTICE). Releases through `v0.4.7` remain under their original MIT
terms; the historical text and notice are preserved in
[LICENSE-MIT](LICENSE-MIT). The [licensing policy](docs/project/licensing.md)
explains the version, contribution, patent-grant, and open-core boundaries.

[osai-verify: eb42dd9cf910399988f0]: #
