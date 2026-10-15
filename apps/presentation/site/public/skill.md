---
name: loopx
description: Adopt and operate LoopX for durable goals, long-running agent work, scoped collaboration, evidence-backed progress, and recovery across sessions. Use when the user asks to set up LoopX, start or resume LoopX work, inspect progress, or select a LoopX workflow.
---

# LoopX: an agent's guide

LoopX is a local-first, stateful control plane for long-running AI agents.
Your host and model execute the work. LoopX keeps goals, decisions, tasks,
ownership, budgets, evidence, and next actions available across sessions.
It helps a user delegate work while retaining visibility and judgment.

This is the public discovery and onboarding guide at
<https://loopx-project.github.io/loopx/skill.md>. It is readable without an
account, browser JavaScript, or a LoopX installation. Reading it does not
install software, activate a runner, or authorize new work.

Use the user's language when explaining the product or reporting results.

## Contents

- [Choose whether LoopX fits](#choose-whether-loopx-fits)
- [Understand the product](#understand-the-product)
- [Install and verify](#install-and-verify)
- [Choose the host entry](#choose-the-host-entry)
- [Start, continue, and finish work](#start-continue-and-finish-work)
- [Select an existing skill](#select-an-existing-skill)
- [Common requests](#common-requests)
- [Diagnose and recover](#diagnose-and-recover)
- [Stop, update, and uninstall](#stop-update-and-uninstall)
- [Authority, privacy, and evidence](#authority-privacy-and-evidence)
- [Go deeper](#go-deeper)

## Choose whether LoopX fits

Use LoopX when work must survive conversation boundaries, wait for an external
result, retain a decision or approval gate, or return a verifiable outcome
through several turns or agent lanes. Examples include a reviewed software
change, a research project with retained sources, and an authorized experiment
with explicit evaluation criteria.

A one-session edit, translation, or question may need only the user's existing
agent. A question about LoopX calls for an explanation, not a new Goal. For a
status request, inspect existing state without starting a delivery workflow.
Only create a persistent goal or activate continuation when the user's request
calls for it. Preserve their existing goal and constraints when resuming.

## Understand the product

| Part | What it does for the user | Boundary to preserve |
| --- | --- | --- |
| Goal and acceptance | Retains the requested outcome, constraints, and definition of success | A task count or a progress label is not acceptance |
| Todos and ownership | Organizes work, dependencies, blockers, claims, and handoffs | Registering an agent does not prove it received or executed work |
| Governed turns | Uses current state, gates, quota, and the host contract to decide the next permitted action | A skill cannot override the runtime's decision or the user's authority |
| Durable evidence | Keeps result references, validation, decisions, and run history available to later turns | A transcript saying “done” is not independent verification |
| Personal Workspace | Presents Goals, Tasks, Chat, outputs, settings, and owner decisions in the local frontend | The public website is an introduction, not the user's live workspace |
| Host integration | Connects supported agent apps, CLIs, managed runtimes, and custom runners | Availability and continuation mechanics depend on the installed adapter |
| Collaboration | Supports scoped work ownership and result return across agents and runtimes | Shared state does not grant identity takeover or permission to spawn workers |
| Capabilities and extensions | Adds explicitly configured workflows and providers through existing owners | Discovery, installation, enablement, credentials, and action permission are separate |

The same lifecycle can support coding, PR delivery, research, material
management, performance diagnosis, and benchmark operations. Domain providers
and optional features have their own prerequisites and validation boundaries.
Inspect the installed capability configuration before claiming a feature is
enabled. Roadmap proposals and public demonstrations are not evidence that a
particular installation supports a complete journey.

LoopX does not supply free model inference, guarantee unattended completion,
or keep a powered-off execution host alive. Local orchestration does not mean
offline inference: configured model and tool providers may receive task data.

## Install and verify

First establish the target project, operating system, host, and installation
owner. If LoopX is already installed, inspect it before replacing anything:

```bash
loopx --help
loopx doctor
loopx workflow-skills
```

Use [Installing LoopX](https://loopx-project.github.io/loopx/docs/guides/installing-loopx/)
for the supported prerequisites and channel-specific procedure. Current source
requires Python 3.11+ and Node.js 22.22.3+; Node.js 24 LTS is recommended.
Validate the requirements for the release actually being installed.

For an authorized first install into a suitable Python environment:

```bash
node --version
python3 -m pip install --upgrade loopx
loopx workflow-skills --install
loopx doctor
```

On an externally managed Python installation, choose the documented `pipx` or
`uv tool` path instead of modifying the system interpreter. The published
archive installer is a recovery alternative. A source checkout is for
development and qualification. Keep one installation owner authoritative.

`workflow-skills --install` installs version-matched workflows and the generated
`loopx` entry for its target skill root; it does not connect a project or grant
execution authority. Read its installation report, reload the host's skills
after first install, and verify discovery there. Other host facades and native
plugins use their own documented installation paths.

When project connection is requested, run from the intended project:

```bash
loopx connect
loopx status
```

Follow any returned host-selection, onboarding, or missing-state packet.
Confirm the intended project and Goal in the result; do not replace an existing
Goal to repair discovery. For deeper installation diagnostics, `loopx doctor
--deep` starts and probes the managed runtime. A successful health probe still
does not prove a model turn or background driver is active.

### Version and source precedence

This URL describes current public source. The installed CLI's help, its
version-matched managed skills, and its current runtime contracts govern
execution. The skill source links below are for discovery and inspection;
`main` may be ahead of a released package. For a historical version, select its
release tag in the repository before reading source and supporting references.

Do not copy this page over the generated local `loopx/SKILL.md`, download a
single workflow file as an installation, or combine commands from incompatible
versions. Skills can depend on sibling references and scripts. Use the existing
installer to retain those resources, provenance, and upgrade/uninstall support.

## Choose the host entry

Prefer the host the user already has open. Read the
[driver selection guide](https://loopx-project.github.io/loopx/docs/guides/newcomer-command-path/)
and [runtime catalog](https://loopx-project.github.io/loopx/docs/integrations/runtime-connector-catalog/)
for exact support and activation in the installed version.

| Surface | Entry and continuation |
| --- | --- |
| Codex App | Select the installed `loopx` skill with `$loopx` or `/skills`. Activate the generated heartbeat only for requested continuing work, and follow runtime scheduler instructions. |
| Codex CLI | Use `$loopx` or `/skills` for skill discovery. `loopx codex-cli-bootstrap-message --project .` returns the visible TUI setup and native Goal instructions. Do not assume user-installed `/loopx` slash commands work here. |
| Claude Code | Use the installed `/loopx` entry. Native-loop gating is an explicit adapter choice; skill installation alone does not activate it. |
| OpenCode | Follow its documented surface installation and activation packet; the command, plugin, and runtime bridge work together. |
| DeepSeek Harness | Use the [native plugin](https://github.com/loopx-project/loopx/tree/main/packages/dsh-loopx-plugin) for its supported interactive host, or the separately documented SDK connector for supervised headless turns. |
| Other supported hosts | Use the runtime catalog's exact host surface and generated packet. Do not infer one host's commands or enforcement from another's. |
| Custom runner | Start with the [runner integration guide](https://loopx-project.github.io/loopx/docs/guides/custom-agent-runner-integration/); reuse admission, execution, settlement, and recovery contracts. |
| Local frontend or Lark | Follow the [Personal Workspace guide](https://loopx-project.github.io/loopx/docs/guides/personal-workspace-user-guide/) and the installed channel configuration. Verify account, conversation, and target scope before external actions. |

Keep visible continuation, application heartbeat, native plugins, and headless
execution distinct. If the current host lacks a runner hook, explain that
state tracking is available but subsequent turns require an available driver.
Do not silently substitute a headless executor or another host.

## Start, continue, and finish work

1. **Resolve intent and scope.** Retain the user's outcome, target project,
   acceptance criteria, cost limits, and external-action permissions. Read the
   project's existing state before deciding whether a new Goal is needed.
2. **Use the owning entry.** Start requested work through the installed `loopx`
   entry. For shell onboarding, inspect `loopx start-goal --help` and use the
   guided packet for the actual host. Do not manually synthesize registry state.
3. **Follow fresh runtime instructions.** Read required context and execute the
   current interaction contract's admission, claim/lease, work, validation, and
   settlement sequence. Preserve the returned project, Goal, and agent identity.
   An old packet or this guide cannot authorize another turn.
4. **Deliver a useful slice.** Implement or investigate within the admitted
   scope. Validate the result through its real consumer where applicable, and
   retain both successful evidence and relevant failures.
5. **Return and read back.** Use the contract's writeback commands and verify the
   resulting status. Explain what changed, what was tested, what remains, who
   owns the next action, and whether a real user decision is needed.
6. **Continue only as admitted.** Honor wait, stop, budget, and authority gates.
   Re-read current state after an interruption. Independent work may proceed
   only when the current contract permits it; a blocked task is not completed.

When several agents are authorized, establish the receiver, task boundary,
dependency artifact, acceptance check, and return path. Verify actual adoption
and independent acceptance before describing collaboration as successful.
Do not take over another agent identity merely because it shares the Goal.

## Select an existing skill

Load only the workflow relevant to the requested operation. The generated
`loopx` entry owns ordinary task startup; routine work already directed by a
current interaction contract does not require repeating project onboarding.

These workflows are distributed through `loopx workflow-skills`:

| User intent | Canonical workflow source | Important distinction |
| --- | --- | --- |
| Connect/configure a project, inspect routing, or repair installation/status discovery | [loopx-project](https://github.com/loopx-project/loopx/blob/main/skills/loopx-project/SKILL.md) | Project lifecycle, not every ordinary task turn |
| Review a PR or PR queue with evidence | [loopx-pr-review](https://github.com/loopx-project/loopx/blob/main/skills/loopx-pr-review/SKILL.md) | Follow its capability-owned plan and exact-head evidence; review does not authorize merge |
| Coordinate a delivery program across PRs or repositories | [loopx-pr-program](https://github.com/loopx-project/loopx/blob/main/skills/loopx-pr-program/SKILL.md) | Inventory and dependencies, not a substitute for per-PR review |
| Register a durable document, design, research source, or project reference | [loopx-doc-registry](https://github.com/loopx-project/loopx/blob/main/skills/loopx-doc-registry/SKILL.md) | Preserve provenance and access scope; registration is not public publication |
| Operate or analyze an authorized benchmark experiment | [loopx-benchmark](https://github.com/loopx-project/loopx/blob/main/skills/loopx-benchmark/SKILL.md) | Not ordinary microbenchmarking or merely solving an assigned task |
| Diagnose contradictory state, stalled continuation, or missing outcomes | [loopx-self-repair](https://github.com/loopx-project/loopx/blob/main/skills/loopx-self-repair/SKILL.md) | Repair the owning rule or projection without lowering gates |
| Investigate measured latency, CPU, memory, or I/O regression | [loopx-performance-diagnosis](https://github.com/loopx-project/loopx/blob/main/skills/loopx-performance-diagnosis/SKILL.md) | Controlled measurement of an owned process, not benchmark score optimization |

Additional repository workflows have narrower activation boundaries:

- [loopx-change-quality](https://github.com/loopx-project/loopx/blob/main/skills/loopx-change-quality/SKILL.md)
  qualifies an exact diff when the Goal policy enables it; follow its project
  installation instructions rather than assuming it is globally installed.
- [loopx-material](https://github.com/loopx-project/loopx/blob/main/skills/loopx-material/SKILL.md)
  operates an explicitly activated Material Lifecycle, including inventory,
  migration, ranking, owner-gated application, and rollback. Ordinary reading
  does not activate this workflow.
- [loopx-pr-merge](https://github.com/loopx-project/loopx/blob/main/skills/loopx-pr-merge/SKILL.md)
  is an optional maintainer workflow for an authorized merge decision after
  exact-head review. Its presence never grants merge authority.

Read a selected workflow's supporting resources from the same version and
directory. Do not fetch every skill or execute a linked script merely because
it is listed here.

## Common requests

**“Explain whether LoopX would help this project.”** Describe the relevant
capabilities and prerequisites, then compare them to the user's current
workflow. Do not install, connect, create a Goal, or schedule work just to
answer the question.

**“Set up LoopX and prepare a reviewed fix.”** Verify the environment, install
through the chosen owner if requested, connect the intended project, then use
the host entry with a bounded objective. For example:

```text
$loopx Fix the failing integration test, explain the cause, and prepare a
reviewable PR. Record validation evidence and wait for my approval to merge.
```

Installation, host discovery, project connection, executed work, PR review,
and final acceptance are separate results to verify.

**“Continue yesterday's work.”** Read the existing Goal, last accepted result,
current task owner, pending gates, and next permitted action. Reuse the correct
identity only when authorized. Obtain fresh admission before execution; do not
create a replacement Goal or replay an uncertain external effect.

**“What is waiting for me?”** Read `loopx status` in the intended project and
report concrete decisions and blockers with their evidence. Use documented
manager views for several projects. Do not infer liveness from registered
workers, stored status, or an old dashboard screenshot.

**“Keep track of these PRs / retain this research / explain this slowdown.”**
Select the PR-program, document-registry, or performance-diagnosis workflow
respectively. Preserve the user's requested scope. Recurring execution,
external publishing, experiment launches, and merge decisions require their
own existing authorization; choosing a workflow does not expand it.

## Diagnose and recover

| Observation | Next step |
| --- | --- |
| CLI missing or resolves to an unexpected installation | Inspect PATH and the installation owner; follow the installation guide without stacking another implicit install |
| Host cannot discover `loopx` | Inspect `workflow-skills`, its target directory and readback, then the host-specific registration; reload the host |
| CLI, App, service, or skills disagree | Read `doctor` and the matching update procedure; a CLI upgrade alone does not prove the running App or service was upgraded |
| Project or Goal is ambiguous | Select the intended project and read existing state; follow the guided selection gate |
| Task is waiting, denied, or out of budget | Report the actual gate and next owner; do not change quota, force claims, or bypass permission to make progress look healthy |
| Session interrupted or execution result uncertain | Inspect current status and the relevant history/receipt before retrying; use self-repair when the owning state is contradictory |
| Optional provider unavailable or unauthenticated | Report the missing prerequisite and configured scope; do not silently change provider or expose credentials |
| Tool or command not present in this release | Use installed help and version-matched documentation; disclose the mismatch instead of inventing an API or flag |

For a failure report, retain the relevant version, command, safe diagnostic,
last verified state, and smallest next check. Keep private logs local. A larger
timeout or successful retry alone does not establish that the cause is fixed.

## Stop, update, and uninstall

For a user-requested stop, prefer the existing Goal stop control in Personal
Workspace, which previews and reads back the lifecycle action. The CLI's
read-only preview is:

```bash
loopx goal-lifecycle --goal-id <goal-id> --operation stop
```

Follow the [lifecycle instructions](https://loopx-project.github.io/loopx/docs/guides/personal-workspace-user-guide/)
for authorized execution and readback. The actor flag records an authority
kind; it does not grant that authority. Stop preserves tasks and evidence and
is not completion. It prevents future admitted turns through the lifecycle and
host contracts; it does not guarantee immediate cancellation of an in-flight
tool or external job. Resume is an explicit lifecycle action and still obeys
all other gates. Uninstalling skills is not a substitute for stopping work.

For requested upgrades, inspect `loopx update check` and `loopx update plan`,
then follow the selected installation owner's apply procedure. Verify the
running CLI, skills, App, and services that were actually in scope. Do not
switch to development `main` merely to repair a stable installation.

To remove managed host material before an authorized package uninstall:

```bash
loopx slash-commands --uninstall
loopx workflow-skills --uninstall
```

Use the same package owner to uninstall the package (`python3 -m pip uninstall
loopx` for the corresponding pip environment). Follow the installation guide
for native snapshots, services, project-local skills, or plugins. Managed
uninstallers preserve modified files; read their report rather than deleting
those files by name. Package removal retains project and runtime state.
Data deletion, stopping external jobs, and rollback are separate operations.
For rollback, restore a known compatible release through the same installation
owner, refresh its host material, and verify readback; do not rewrite stored
state to force compatibility.

## Authority, privacy, and evidence

- **Scope follows the user and current contract.** Installation, skill
  discovery, a Goal, and quota do not independently authorize repository writes,
  deployment, messaging, external jobs, paid execution, or merging.
- **Use existing owners.** Do not manually edit control-plane state, derive
  permission from prose, or replace typed runtime decisions with this guide.
  Repository contribution rules still apply to work in that repository.
- **Keep private material private.** Do not publish credentials, transcripts,
  private documents, raw diagnostic dumps, or local operating context. Register
  a bounded reference with appropriate access when durable knowledge is needed.
- **Report evidence at its actual level.** Distinguish source inspection,
  isolated tests, packaged behavior, installed runtime, and live acceptance.
  Missing evidence remains unknown. A successful command or merged PR alone
  does not establish the user's final outcome.
- **Stop at a real boundary.** Explain the blocked action, reason, and owner;
  retain recoverable state and continue only independent work that is admitted.

## Go deeper

- [Official website](https://loopx-project.github.io/loopx/) and
  [documentation portal](https://loopx-project.github.io/loopx/docs/).
- [First-run path](https://loopx-project.github.io/loopx/docs/guides/getting-started/)
  and [long-running coding guide](https://loopx-project.github.io/loopx/docs/guides/long-running-coding-agents/).
- [Developer Book](https://loopx-project.github.io/loopx/docs/book/en/) for
  concepts, project onboarding, and contribution boundaries; the
  [Chinese edition](https://loopx-project.github.io/loopx/docs/book/) follows
  the same learning path.
- [Extension ownership and lifecycle](https://loopx-project.github.io/loopx/docs/reference/extensions/)
  for capability/provider placement, configuration, and compatibility.
- [Source and releases](https://github.com/loopx-project/loopx) for tagged
  versions, issues, and the canonical skills linked above.

For maintainers: the source of this guide is
`apps/presentation/site/public/skill.md`. The existing site build and Frontstage
Pages export publish it unchanged. Update onboarding and discovery here; keep
workflow procedures in their existing skills and runtime owners.
