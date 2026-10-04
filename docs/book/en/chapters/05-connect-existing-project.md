# Connect an existing Git project

Earlier chapters separated state, authority, execution, and evidence. This chapter reconnects them in
one practical outcome: **help an existing project produce its first reviewable piece of work, with an
explainable way to continue or stop.**

Use [the running task](00-reading-guide.md#running-example): A implements compatible JSON output in T1,
B documents it in T2, M1 observes CI, G1 retains the maintainer's schema/publication decision, and T3
delivers when its conditions hold. This is a synthetic teaching task, not a record of a successful run.
Use your actual project and current packet for commands; T1, M1, and C1 are not CLI ids or API payloads.

!!! tip "Fast reading path"
    For a first connection, follow [preparation](#prepare-project), [connection and identity](#connect-and-identify),
    and [connection acceptance](#connection-acceptance), then activate the Host using its App or CLI chapter.
    Return to [the first delivery](#first-delivery) to finish actual work. With an existing Goal, begin by
    reading its state rather than bootstrapping again. [Optional capabilities](#optional-capabilities) are
    not prerequisites for the main path.

## Three different completion points {#three-completions}

Installing, connecting, and delivering are not the same accomplishment. Installation asks whether the
CLI and required runtime are usable. Connection asks whether work identity, state location, and execution
route are explicit. Delivery asks whether the actual artifact meets its current acceptance conditions.
A connected project may have no executing Turn; a successful Turn may still leave external checks or
approval outstanding.

| Completion point | Supported conclusion | Conclusion still unsupported |
| --- | --- | --- |
| Environment ready | The current entrypoint and required dependencies passed their corresponding checks | The right project Goal is selected and a Host is running continuously |
| Connection accepted | Goal, Agent, project boundary, and next entrypoint are explicit; writes have been read back | T1 is implemented, CI passed, and publication is authorized |
| A piece of work delivered | Current artifacts and validation are reviewable, with complete lifecycle records | Every Todo, approval, and the entire Goal are complete |

**The "connection accepted" row must be observable as these facts**: the environment is ready
(`loopx doctor` reports a usable installation; `.loopx/registry.json` and
`.loopx/goals/<goal-id>/ACTIVE_GOAL_STATE.md` exist); `loopx status` shows the active state and current
frontier; reconnecting reuses the exact existing `goal_id` instead of overwriting it; a new executor gets
a fresh `agent_id` unless the user explicitly authorizes a takeover; and `.loopx/` and `.loopx/goals/` do
not enter Git. This local state is control-plane state, not project source.

The chapter therefore does not join every command into one script to paste blindly. Separate reads,
previews, and execution, continuing when the preceding step provides enough basis. When a command refuses,
identify the condition it protects rather than bypassing the check to make the tutorial work.

## 1. Prepare the project and protect existing state {#prepare-project}

Start at the root of the Git project you intend to manage, not the LoopX source repository. Open your Agent development tool from the repository root. Adapt the goal and Host in this prompt, then
send it as one onboarding contract:

```text
Safely connect the current Git project to LoopX.

Goal:
- Establish a recoverable, verifiable release workflow for this project.
- The current Host is Codex App. If the environment is not that Host, tell me first; do not guess.

Execution contract:
1. Begin with a read-only inspection of the project root, current branch, git status, .gitignore, and any
   existing .loopx/registry.json, .loopx/goals/, or other LoopX state. Do not overwrite, reset, or clean
   existing material.
2. Run loopx --version and loopx doctor, then read the current --help for every command you need. Do not
   rely on remembered arguments from an older version. If LoopX is not installed, report what is missing
   and where the official installer writes before asking for installation authority. Do not describe a
   discovered install command as a completed installation.
3. If LoopX state exists, read loopx registry, loopx status, and relevant history first. Prefer the exact
   existing goal_id. Do not force a reconnect or select a Goal from objective similarity.
4. Ensure .loopx/, .loopx/goals/, and .local/ are ignored by Git. If those paths already serve another
   project purpose or are tracked, stop and report the conflict. Do not delete or untrack them yourself.
5. For a project that is not connected, run loopx connect --dry-run first and show the state it would
   create or change. Run loopx connect only after confirming there is no conflict. Do not bootstrap again
   merely to “start over” when a registry already exists.
6. If several Goals are possible, stop at the read-only goal_selection_gate and show me the choices and
   your recommendation. Before I choose, do not write Todos, register an Agent, or activate a Host loop.
7. For a new executor, choose a fresh public-safe agent_id. Preview registration, then use the command
   supported by the current CLI and read it back. Reuse an existing agent_id only when I explicitly
   authorize takeover.
8. Generate the transaction packet with loopx start-goal --guided --project . and the exact goal text.
   Pass the correct --host-surface when the Host is known. Execute only packet steps allowed by the
   current authority.
9. Stop at a Gate for user approval, external writes, credentials, wider permissions, Host selection, or
   destructive Git operations. Do not decide those for me.
10. Verify loopx status, todo list, history, quota should-run, git status, and
   git ls-files .loopx .loopx/goals .local.
11. Do not commit or push. Finish with an "onboarding report" that names goal_id, agent_id, Host, changed
    files, current Todos and Gates, executed mutations, verification, unresolved issues, and the next
    action. If you completed only a preview, explicitly say that onboarding is not complete.
```

This prompt delegates execution, not authority. You still decide:

- which Goal to select when several exist;
- whether to take over an existing Agent identity;
- which Host surface owns activation;
- whether external writes, credentials, or a wider write scope are allowed;
- whether repository changes are committed or pushed.

### The onboarding report

An auditable onboarding report includes:

```yaml
onboarding:
  status: complete | blocked | preview_only
  project_root: <repository root>
  goal_id: <exact goal id>
  agent_id: <fresh id or explicitly approved takeover id>
  host_surface: <exact host or unresolved>
changes:
  - <changed path and why>
gates:
  - <decision still owned by the user>
verification:
  doctor: pass | fail
  status_readback: pass | fail
  local_state_ignored: pass | fail
  tracked_private_state: []
next_action: <one concrete next step>
```

Do not accept “the command succeeded” as sufficient evidence. Require state readback and Git-isolation
proof.

### Example: first onboarding

```text
Use the Agent onboarding contract in this chapter to connect the current project to LoopX.
The goal is "Create a recoverable build, approval, and Pages deployment flow for every release candidate."
The current Host is the visible Codex CLI TUI. Use a fresh public-safe agent_id.
Do not commit, push, or trigger a deployment. Stop for my decision on Goal selection, authority, or any
external write.
```

### Example: continue existing state safely

```text
First inspect the current LoopX registry, Goals, Todos, Gates, and history read-only, then help me continue
the project. Prefer an exact existing goal_id, but do not automatically take over an existing agent_id.
If you find multiple Goals, an active lease, an unfinished mutation, or a workspace-route mismatch, return
diagnosis and choices only. Do not write state, commit, or push.
```

### Read the repository first

Whether you act yourself or delegate to an Agent, inspect the repository read-only from its root before
writing any LoopX state:

```bash
git rev-parse --show-toplevel
git branch --show-current
git status --short
git ls-files .loopx .loopx/goals .local
```

Repository root, current branch, and existing changes are separate facts. Do not reset or delete valuable
changes. In a linked worktree, identify the tree where delivery will actually happen. A clean status in
one directory does not establish that no work happened in another.

A project may already contain `.loopx/registry.json` or `.loopx/goals/`. Identify existing Goals, in-flight
work, and state locations first. Do not copy another project's registry or overwrite useful state to
obtain a blank starting point. This is a common layout, not a complete physical map of every authority,
lease, or log:

```text
your-project/
  .loopx/registry.json
  .loopx/goals/<goal-id>/ACTIVE_GOAL_STATE.md

configured runtime root/
  provider-owned state, execution records and receipts
```

Markdown may be a source on a legacy path or a compatibility view under a selected authority. Use
[the state chapter](state-substrate.md) to identify the mode; a filename cannot establish write authority.

### Ignore rules, the index, and history answer different questions

When these names do not conflict with existing project content, establish ignore rules for the local
state directories actually used. Common entries are:

```text
.loopx/
.loopx/goals/
.local/
```

Resolve a conflicting product use of the same directory first. These rules also do not replace checks
on a custom runtime location. Ask Git about the actual paths:

```bash
git check-ignore -v .loopx/registry.json
git check-ignore -v .loopx/goals/example/ACTIVE_GOAL_STATE.md
git check-ignore -v --no-index .loopx/registry.json
git ls-files .loopx .loopx/goals .local
git check-ignore -v .loopx/goals/example/ACTIVE_GOAL_STATE.md
```

`check-ignore` explains the matching rule; inspect the rule itself, because a negated `!` pattern does not
mean the path is ignored. Tracked files are not governed by ignore rules by default. `--no-index`
diagnoses rules without considering the index; it neither forces exclusion nor specifically handles
nonexistent files. By default, `ls-files` lists paths in the current index. Output means tracked or staged
content, **not necessarily a pushed commit**. No output does not prove that past commits or a remote have
never contained sensitive material. See [Git check-ignore](https://git-scm.com/docs/git-check-ignore)
and [Git ls-files](https://git-scm.com/docs/git-ls-files).

When local state is tracked unexpectedly, inspect its contents, staging, and history before an authorized
operator repairs the index or history. The chapter does not require automatic `git rm`, directory
clearing, or history rewriting. Deleting a working directory does not retract already published information.

## 2. Verify the installation, not only the command name {#verify-installation}

The book's baseline requires Python 3.11+ and Node.js 22.22.3+. Code blocks here use a POSIX shell.
Native Windows PowerShell 7 users should use equivalent steps in the
[installation guide](/loopx/docs/guides/installing-loopx/); WSL is not required just for this tutorial.

For an existing installation, first run:

```bash
loopx --version
node --version
loopx doctor
```

When LoopX is absent and changes to the current Python environment are authorized, the release entry is:

```bash
python3 -m pip install --upgrade loopx
loopx workflow-skills --install
loopx doctor
```

Installing packages and skills writes to the environment; package installation may access the network.
These are not read-only checks. Ordinary users need not clone LoopX first; a full source checkout belongs
to the implementation and regression exercises later in the book. For an existing pip, pipx, or archive
installation, use the [appendix's upgrade guidance](appendix-reference.md) to identify the installation
owner instead of layering another installation over an unexplained one.

`doctor` answers more about dependencies, installation, and integration than `command -v loopx`, but it
is not proof that every Host journey has been qualified. For the actual Effect runtime and journal
checkpoint path, use the deeper probe when needed:

```bash
loopx doctor --deep
```

LoopX manages the on-demand, idle-exiting TypeScript Effect runtime; users do not supervise a daemon
manually. In that runtime's diagnostic context, `stopped` can mean restartable on demand;
`missing`, `unsupported`, or `probe_failed` must be addressed first. Do not apply that interpretation to
an exited Agent Host with no available wake mechanism.

When installation is blocked, record the failed check and current version, then return to the installation
owner. Changing Goals, copying state directories, or disabling authority checks does not repair dependencies.

## 3. Connect and identify: choose the object before accepting writes {#connect-and-identify}

For an unconnected project whose existing state has been inspected, preview first:

```bash
loopx connect --dry-run
```

Confirm the project root, Goal, and state locations, then connect and read back:

```bash
loopx connect
loopx registry
loopx status
```

With an existing project, read its current connection and continue through the existing route instead
of reconnecting to retry a task. `connect` / `bootstrap` register a Goal and write active state; first
connection does not invent a set of onboarding Todos for the caller. When there is no executable work,
confirm the actual task and work boundary rather than manufacturing quota.

### A guided packet is a plan, not an execution receipt

Have the current entrypoint interpret the objective:

```bash
loopx start-goal --guided --project . \
  --goal-text "Add compatible JSON output while preserving default text; deliver after validation and documentation, without automatic publication"
```

The response is a guided transaction packet. A successful preview does not establish that Todos were
written, an Agent was registered, or a Host started. Read the selection it currently requires and
continue with its exact commands; do not revive an obsolete instruction from an earlier conversation.

When several Goals exist, select an exact `goal_id` from `goal_selection_gate` choices. Similar objective
text does not justify a silent merge. Goal selection and Agent takeover are different: one identifies
the long-lived work boundary, while the other concerns execution responsibility.

With task text but no Agent argument, the fresh-identity default depends on existing registered lanes
and an explicit `--new-peer` request. Do not treat an existing lane as a new session's automatic identity.
When a new executor is needed, use the supported registration entrypoint:

```bash
loopx register-agent --goal-id <selected-goal-id> --agent-id <new-public-safe-agent-id> --require-new
loopx register-agent --goal-id <selected-goal-id> --agent-id <new-public-safe-agent-id> --require-new --execute
```

Preview first and execute within existing authorization. `--require-new` makes an already registered id
return a collision instead of an idempotent success; the guided packet's fresh-registration command carries
the same flag. Continue to Todo writeback or activation with the new id only when the execute result reports
`ok`, `changed`, and `written` as true, `global_sync.ok` as true, and `registration_readback.verified` as true.
`changed=false` or a collision means the id already exists: choose another fresh id rather than treating it
as a successful registration. Taking over an existing identity is an explicit choice and remains subject to
current lease, workspace, and lifecycle constraints.

### Selecting a Host does not grant capabilities

Specify the actual Host when known. Choose one of the following; do not activate both sequentially:

```bash
# Codex App
loopx start-goal --guided --project . \
  --goal-text "Add and validate compatible JSON output without automatic publication" \
  --host-surface codex-app

# Codex CLI visible TUI
loopx start-goal --guided --project . \
  --goal-text "Add and validate compatible JSON output without automatic publication" \
  --host-surface codex-cli-tui
```

Once identities are known, also use the packet's exact Goal/Agent binding. Keep selection unresolved
when the Host is unknown rather than substituting a familiar name for observation. Activation and
readback belong to the [Codex App](06-codex-app.md) and [Codex CLI](07-codex-cli.md) chapters; other
adapters are indexed in the [Runtime Connector Catalog](/loopx/docs/integrations/runtime-connector-catalog/).
Type registration, launchability here, autonomous continuation inside a process, and waking after that
process exits are distinct conditions. An accepted argument does not prove them all.

## 4. Accept the connection: which next step is supported now? {#connection-acceptance}

Use reading entrypoints to inspect current facts:

```bash
loopx registry
loopx status
loopx todo list --goal-id <goal-id>
loopx history --goal-id <goal-id>
git status --short
git ls-files .loopx .loopx/goals .local
```

These do not request Todo completion or delivery-quota debit, but reading does not promise a process
with no IO: supporting actions may include runtime startup. Strict isolation requirements need separate
verification of the entrypoint, provider, and settings. Commands that sound like checks do not all belong
to the same side-effect category.

When ready to enter a Turn, use the current Host's admission route, for example:

```bash
loopx quota should-run --goal-id <goal-id> --agent-id <agent-id>
```

Read the full interaction contract, selected Todo, authority, workspace, and next instructions, not
just the `should_run` boolean. In particular, relevant `--codex-app` admission paths can create a
heartbeat receipt. That differs from simply listing state and should not be repeated indefinitely as
a diagnostic script. `refresh-state`, acquire/renew, settlement, and scheduler ACK are also conditional
operations, not generic queries.

| Four questions | Answer needed to accept onboarding |
| --- | --- |
| What facts support it? | Actual project and release, exact Goal/Agent, selected state source, Host, and workspace |
| Why allow or refuse? | Whether connection/registration conditions hold; which work current admission permits, or which condition is missing |
| What evidence supports the conclusion? | Execution results and corresponding readback, Git index checks, and current contract; not directory existence or a success sentence |
| Which entrypoint comes next? | Activate the selected Host or advance selected work when ready; return missing identity, dependency, authority, or runtime conditions to their owner |

The report can be ordinary prose; no new YAML schema or state ledger is needed. For example:

> Project connection and identity have been read back. Private state is absent from the current index.
> Host activation is still unconfirmed, so connection preparation is complete but no first execution
> has happened. Next, use the CLI chapter's current activation packet and verify the result; do not
> describe T1 as delivered.

This is a **teaching example** of a local report, not a product return type. Remove private paths,
objective content, and credentials before sharing it publicly.

## 5. From connection to the first delivery {#first-delivery}

Return here after the Host chapter. The goal is an independently verifiable work segment, not a screen
that says running. This walkthrough is not an end-to-end test of a real Host; accept actual work using
evidence from your own run.

### Scene one: A owns T1, not the entire publication workflow

T1 consumes current code and compatibility requirements. Its allowed outcome is reviewable code and
tests; publication remains T3's responsibility. Through the current Todo/Host entrypoint, the Agent
confirms work ownership, writable workspace, and validation declaration, then executes the selected
bounded segment.

There is deliberately no universal start command that bypasses the current packet. Different Hosts,
authority modes, and existing work states have different prerequisites. A returned legal action means
more than copying a historical `should_run=true` from a book. See [one full Turn](03-one-turn.md#running-turn)
and [work graphs and authority](work-graph-and-authority.md).

### Scene two: what conclusion do checks at C1 support?

After producing the artifact, validate default text behavior, JSON output, and invalid inputs affected
by the change. Record the actual revision, checks executed, and results. Exit zero is meaningful only
when that validator actually checked the intended postcondition. When declarations or code change
during validation, recheck the result's applicability rather than letting old validation accept new inputs.

Once the owning lifecycle accepts work, read back the Todo and writeback outcome. Settle when required
by the current contract. `spend_required` and `spend_receipt_required` describe different recovery needs:
the former still owes settlement, while the latter needs receipt recovery. Even a `settlement_owed.command`
from a trusted current response requires checking the original identity, registry/runtime, and authorization.
Do not remove binding arguments or execute commands copied from arbitrary logs. See
[the settlement exercise](12-control-plane-course.md#checkpoint-settlement).

Confirmed writeback does not disappear after a later settlement timeout. An unknown effect belongs to
[recovery under the original identity](04-runtime-boundaries.md), not another execution with a new id.
Internal quota accounting is also not the model supplier's actual bill.

### Scene three: wait for CI without stopping all independent work

Local validation cannot replace M1's observation of remote C1. Registered waiting should identify its
target, applicable revision, next observation, and termination or ongoing-watch policy, with an actually
available execution surface. Supported boundedness choices include expiry, a resume condition, or
explicit watch-only; one field is not the only valid choice for every Monitor.

When G1 has not approved publication, T3 obtains no authority from green CI or T1 settlement. T2 may
continue when it is still provably independent and its own constraints hold. A user decision can be
required while an Agent continues different work. See [observation and scheduling](04b-budget-and-admission.md)
and [the work graph](work-graph-and-authority.md).

An unchanged observation must not impersonate delivery; it can still incur compute and network cost.
No notification is not evidence that the external source stayed unchanged. When the Host has exited
with no available waking mechanism, report the operational gap rather than calling quietness healthy waiting.

### Scene four: changed conditions call for changed judgments

Suppose the code moves from C1 to C2. Green CI for C1 remains a historical fact but cannot automatically
accept C2. Locate affected validation and dependencies rather than clearing every record. Whether G1's
decision still covers the artifact depends on its actual object, scope, and conditions, not the word approved.

When the maintainer also changes the objective or acceptance, use existing planning/replan and decision
entrypoints to relate the change to current work. C1/C2 illustrates applicability; it does not declare
an implemented unified Goal intent-version schema. An Agent cannot mint authority to change the goal
merely to make completion easier.

### Scene five: return the result to the person who must accept it

Reassess the next step when T3's current conditions have supporting evidence. Returning a reviewable
result and performing external publication remain different actions; the latter retains its independent
authorization and execution requirements. Ordinary owner decisions also cannot all be delegated to the
quota rules for automatic Turns.

A useful return identifies the artifact and version, supported and outstanding acceptance conditions,
checks actually performed and their limits, and who decides or acts next. An artifact link, internal
settlement, actual receipt by the recipient, and Goal acceptance are separate facts. Verify each instead
of promoting one to all of them.

For example, as a local report rather than another persisted format:

> JSON output and documentation are ready at C2. The listed compatibility checks and C2 CI support
> the current implementation conclusion. Work writeback and settlement are traceable. The maintainer's
> publication decision is still outstanding, so return the artifact for review without publishing.
> G1's decision entrypoint comes next; once accepted and still applicable, reassess T3.

Even without saying everything is complete, this report supports a handoff. “Tests green, task done”
provides neither the scope of what was proved nor the responsibilities still outstanding.

## 6. Delegate onboarding to an Agent without delegating guesses about authority {#delegate-onboarding}

Give the following prompt to the Agent already working in the project. It is a delegation example, not
a new machine-enforced protocol. Ordinary operations within explicit existing authorization need not
interrupt the user again for every command. Identity takeover, expanded authority, external publication,
and destructive actions must not be inferred from a vague objective.

```text
Establish and carry out compatible JSON-output work in the current Git project without automatic publication.

Read the actual project root, branch, existing changes, LoopX installation, and registry/Goal first; do not overwrite or clear state.
Check ignore rules and the Git index for local runtime material. Report conflicts or tracked private material without cleaning history yourself.
Continue an existing Goal by exact id. A new executor must not automatically take over an existing Agent identity.
Read connect/start-goal previews first; execute only steps with current authorization, a known target, and a known Host.
For a selection or missing condition, say what is missing and who confirms it; do not bypass it with a new id, disabled guard, or repeated bootstrap.
Advance permitted T1 work, validate the actual artifact, perform current-contract writeback and required settlement, and read back the result.
When waiting for CI or approval, name the target, next observation condition, and execution surface. Continue only provably independent work.
Report actual facts, allow/refuse reasons, supporting evidence, and the next entrypoint.
Distinguish a preview, connection, one delivery, and full acceptance. Do not commit, push, or publish without authorization.
```

Preserve the original identity and locate the discrepancy when results differ from your prediction.
[The four exercises](12-control-plane-course.md#reader-checkpoints) show how to inspect counterexamples;
[diagnostic routing](12-control-plane-course.md#diagnostic-routing) locates the next entrypoint. Their
state deletion and corruption injections belong only to isolated tests, never to this project.

## 7. Repair the missing condition, not the entire workflow {#onboarding-recovery}

| Observation | Facts to establish now | Next entrypoint and acceptance condition |
| --- | --- | --- |
| doctor fails | Actual command location and failed dependency or integration | Installation guide or runtime owner; the corresponding check passes again, rather than a new Goal repairing installation |
| Several Goals appear in the registry | Which long-lived objective is intended | Current selection packet; choose exactly and rerun, without merging by text similarity |
| Project state exists but global view lacks it | Whether source write exists and global synchronization failed | Registry permissions and current `sync-global` usage; read source and projection separately, not missing visibility as missing connection |
| Delivery worktree disagrees | Actual editing location and recorded delivery location | Current `refresh-state --delivery-workspace-path` route; recognize a governed write, then verify both sides rather than copying state |
| Writeback exists but the Turn is unsettled | Original Turn's completed effects and outstanding records | [Recovery](04-runtime-boundaries.md); retain historical facts and finish owed steps without repeating the whole Host |
| No selectable Todo or usable Host | Work boundary, wait conditions, and execution capability | Original planning/Host entrypoint; wait or hand off explicitly when needed, not fabricate work from remaining budget |

A failure report needs version, entrypoint, object binding, and minimal reproduction evidence. Public
issues contain public-safe material, not raw registries, private objectives, credentials, transcripts,
or complete logs.

## 8. Add optional capabilities when work needs them {#optional-capabilities}

You can now take ordinary work from connection to an explicit return. Capabilities, Providers, and
Extensions need not all be configured first. Ask whether the missing piece is a caller outcome,
execution implementation, environment readiness, or authority; they belong to different owners.

```bash
loopx capability list --format json
loopx capability show <capability-id> --format json
loopx --format json configure-goal --goal-id <goal-id>
loopx extension list --format json
```

Catalog visibility does not mean the Provider is installed; installation does not mean enabled;
doctor-ready does not mean the current Todo is authorized. When `--extension-manifest` only affects a
catalog read, it is not an installation receipt. `configure-goal` without settings reads current features.
Each setting belongs to its feature; there is no generic enable-any-capability-id operation.

For example, the current change-quality configuration path is:

```bash
loopx configure-goal --goal-id <goal-id> --change-quality-enabled
loopx configure-goal --goal-id <goal-id> --change-quality-enabled --execute
```

The first previews; execute the second only when that configuration change is authorized, then read back
the actual setting. Configuration belongs to the project source identified by `source_registry`, not a
convenient global mirror. `--runtime-root` does not confer different configuration authority. A Todo's
`required_capabilities` are existing execution prerequisites; `target_capabilities` are what it builds or
repairs. A missing target must not automatically forbid the work that repairs it.

To learn independent Providers, continue through [placement](08-extension-placement.md),
[the complete teaching package](09-extension-scaffold.md), and [lifecycle](10-extension-lifecycle.md).
That path uses `packages/loopx-text-stats/` to teach packaging, manifest, input, installation, invocation,
disabling, and rollback together, without maintaining a second installation walkthrough here. Existing
domain packages such as `loopx-finance-value-discovery` follow their own README and input contract. They
are not prerequisites for this chapter, and a package name establishes no data-collection, account-access,
or external-execution capability.

## Costs, applicability, and the next chapter

Durable state needs backup, migration, and ownership; it is not disposable cache. Changes to Host,
workspace, or installed version require renewed checks on affected conditions. Extra reads and validation
cost something, but do not conclude without measurement that every task must be slower. Declining
onboarding or stopping at preview is valid; do not invent governance work just to use a product.

This chapter's concrete procedure assumes Git engineering, not that LoopX excludes research or material
work. Other domains need their own artifact versions and verification sources. Offline, isolation, and
organizational restrictions also require checking the actual Host/provider combination. Neither local-first
alone proves compliance nor an offline requirement alone rules it out.

You should now distinguish prepared, executing, accepted for this Turn, still waiting, and delivered,
with grounds for each judgment. Choose [Codex App](06-codex-app.md) or [Codex CLI](07-codex-cli.md) for the
actual Host; return to [recovery](04-runtime-boundaries.md) after interruption and
[observation](04b-budget-and-admission.md) while waiting. Enter the
[contribution map](source-protocol-map.md) when you are ready to change LoopX itself.
