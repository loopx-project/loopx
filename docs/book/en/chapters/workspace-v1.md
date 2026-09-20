# Operate the LoopX 1.0 Workspace

The LoopX 1.0 milestone is not merely a new Dashboard. It brings long-running work across sessions and
Agents into one inspectable, operable Personal Workspace. The Workspace presents state and proposes
governed actions; the control-plane sources still own Goals, Todos, Gates, events, configuration, and
receipts.

This chapter connects the 1.0 operator surface to the control-plane model in the first six chapters. By
the end, you should be able to:

- start the Workspace and confirm that the page and status projection come from one LoopX runtime;
- move from the Manager overview into one Goal and distinguish active, attention, monitoring, and
  completed work;
- explain why Workspace writes pass through typed preview, governed apply, and verified receipt;
- distinguish Capability visibility, Goal configuration, Provider readiness, and current-Turn eligibility;
- understand what authority Goal Channels, periodic reports, and desktop updates add, and how to disable
  each path.

## What 1.0 actually ships

`v1.0.0` is the **Personal Workspace milestone**. It brings these entrypoints into one local operator
surface:

| Workspace surface | Question it answers | Authority boundary |
| --- | --- | --- |
| Manager overview | Which Goals need me, are running, are being observed, or are scheduled? | Derived from status projections; it does not redefine Todo lifecycle |
| Goal / Tasks | Which Agent lanes, decisions, active Todos, Monitors, and completed items exist? | Todo and Gate decisions remain with their control-plane owners |
| Chat | How do I continue with the current Goal, Agent, and Session? | A conversation is not durable Goal state |
| Files / Reports | What did a run deliver, and which reports were verified? | Shows public-safe previews and evidence pointers |
| Context / Settings | How are the repository, Session, Goal Channel, and optional features configured? | Writes require preview, apply, and readback |

This is not a new source of truth. The browser cannot bypass the Kernel to edit registries, Todos, quota,
or Host automation. Remote SSH projections remain read-only except for the
explicit Goal stop/resume control routed through an exact configured Host alias;
manual URLs cannot acquire that authority. Stage 2C authority and other candidate
Providers are still promoted in stages; the 1.0 label does not mean that every tenant has migrated.

Use the [LoopX v1.0.0 release](https://github.com/huangruiteng/loopx/releases/tag/v1.0.0) for shipped
facts and the [Personal Workspace guide](/loopx/docs/guides/personal-workspace-user-guide/) for detailed
UI and recovery instructions.

## Start and verify one runtime

Confirm the installed version and environment, then start the local Workspace:

```bash
loopx --version
loopx doctor
loopx dashboard --no-open
```

The command prints the actual loopback URL. The default page and status projection can be read back with:

```bash
curl -fsS http://127.0.0.1:8767/chat/ >/dev/null
curl -fsS http://127.0.0.1:8767/status.json
```

`loopx dashboard` serves the packaged Workspace, status projection, and Agent Chat together. If a matching
desktop shell already runs the service, the command reuses the process only after validating its capability
fingerprint; it does not start a second source of truth. Ports are defaults, not permanent contracts, so
automation should consume the URL printed by the command.

After opening the Workspace, perform three readbacks:

1. compare the Manager Goal count with `loopx status`;
2. compare the selected Goal's Agent lanes and Task states with
   `loopx todo list --goal-id <goal-id>`;
3. confirm that Context names the host and worktree you intend to operate.

A rendered page is not proof of a healthy control plane. If `status.json`, Goal details, or the selected
source fails, recover that runtime or projection before attempting a write.

## Read one unit of work from the Workspace

The Manager's four lanes are operator projections, not four new Todo states:

- **Needs you:** User Todos, authority Gates, and decisions reserved for the owner;
- **In progress:** runnable Agent Todos and active lanes;
- **Observing:** Monitors with a cadence, trigger, or external-fact wait;
- **Scheduled:** Host schedules that are bound but not currently due.

Inside a Goal, reduce a card back to the control-plane questions:

```text
Goal / Acceptance
  -> selected Todo and owner
  -> Gate, capability and workspace eligibility
  -> current Session / Host
  -> evidence, receipt and successor
```

Completed history is read-only evidence and does not re-enter the frontier. Files and report summaries are
not the complete raw artifact. For an audit, follow the `todo_id`, run identity, evidence pointer, or
versioned artifact back to its authoritative source.

## Writes: preview, apply, receipt

Workspace changes to Goals, Todos, Heartbeats, Monitors, and settings follow one safety chain:

```text
typed preview -> human or policy review -> governed apply -> verified receipt -> refreshed projection
```

A preview freezes normalized parameters, scope, and the current revision. Apply may execute only while that
preview still matches current state; changed state returns stale or a Gate rather than silently reusing an
old decision. Only the receipt and readback prove the write. A button click or successful HTTP response is
not enough.

For example, the first Goal-stop command is preview-only:

```bash
loopx goal-lifecycle --goal-id <goal-id> --operation stop
loopx goal-lifecycle --goal-id <goal-id> --operation stop --actor-kind owner --execute
loopx quota status --goal-id <goal-id>
```

Executed lifecycle transitions require an explicit `--actor-kind owner` or
`controller`; anonymous previews remain read-only.

Stopping a Goal removes it from active attention and projects zero effective automatic-run quota while
preserving Todos, history, evidence, and configuration. Explicit `resume --execute` restores scheduling
eligibility but does not bypass Todo, Gate, or quota rules. Do not describe stop as completing the Goal, and
do not use a quota edit to accidentally resume an owner-stopped Goal.

## Configure Capabilities and machine policy

The 1.0 Workspace exposes Goal capabilities and typed machine policy, but four facts remain distinct:

| Fact | Read surface | What it does not prove |
| --- | --- | --- |
| Capability shipped | `loopx capability list/show` | The current Goal enabled it |
| Goal configured | `loopx configure-goal --goal-id <goal-id>` | A Provider is ready |
| Provider ready | The matching Extension / Provider doctor | The current Turn passed its Gates |
| Current Turn eligible | Capability / workspace results from `quota should-run` | Any additional external authority |

Start with read-only discovery:

```bash
loopx capability list --format json
loopx machine-config describe
loopx machine-config inspect --format json
loopx configure-goal --goal-id <goal-id>
```

Machine policy and Goal settings must first produce a delta or plan, then be explicitly executed and read
back at the resulting revision. Do not guess flags from Capability names or turn “visible in the catalog”
into “enabled.” Enabling optional adaptive child-agent capacity does not force parallel execution or grant
new Goal, repository, credential, publication, or production authority.

## Goal Channels: messages are not implicit authority

The Workspace's Lark settings can connect a Goal to an exact Topic and target Agent. Capture scope selects
which messages enter the connection; it does not enlarge Agent authority. Ingress mode determines how a
message enters the runtime:

- `live_steering` targets the exact active Turn of that Agent;
- `session_queue` enters a bounded FIFO for the same exact Session and runs after the current Turn;
- `async_inbox` enters the Agent's local private inbox for an explicit later drain.

After configuration, read back the Goal, Agent, Topic, ingress mode, Session binding, and listener state.
To validate `async_inbox`, send a new test message yourself and then run:

```bash
loopx lark-inbox drain --goal-id <goal-id> --agent-id <agent-id>
```

Disconnect removes only this Goal's Topic route; it does not delete the Goal, Session, history, or another
connection. Message arrival does not grant sending, repository-write, or production authority. Those
effects continue through their own Gates and Provider readbacks.

## Reports: one generation and standing delivery are separate

An explicit request to “generate this week's project report” in an active project session enables one
provider-free Markdown and HTML generation. Inspect the built-in profile first:

```bash
loopx periodic-report inspect-profile --preset weekly --format json
```

The receipt should report both `active: true` and `generation_allowed: true`. The built-in weekly profile
has no schedule and no sink, so one generation does not create a recurring task or send a message.

Standing reports use a separate authority chain: a custom profile declares cadence, a Host Automation
wakes the work, and `enabled: true` plus an explicit `route_ref` on a machine or Goal subscription grants
standing delivery. Pause the Automation, disable the profile, or disable the subscription to stop its
corresponding path. Successful generation is not proof of successful external delivery; Provider, sender
identity, route, and message readback are verified separately.

## Desktop updates and recovery

The 1.0 macOS updater pairs the App and bundled runtime at one revision. Older desktop shells require one
manual replacement. Afterwards, use **Recovery & updates** to select stable or main explicitly, install,
and restart.

- **Validate:** compare the App version, Workspace runtime identity, `loopx --version`, and `loopx doctor`;
- **Repair:** **Repair this version** reinstalls the runtime bundled with the current App;
- **Roll back:** when a verified backup exists, use **Restore previous version**, restart, and recheck identity;
- **Boundary:** updates use fixed official feeds. macOS uses updater signatures plus ad-hoc code signing and
  must not be described as notarized. Restoring an install does not promise to reverse a future incompatible
  Goal schema.

Browser and PWA users continue through the CLI update flow. A CLI update cannot repair native shell startup
or updater defects.

## 1.0 acceptance checklist

For one Workspace acceptance pass, confirm at least that:

- `loopx --version` matches the intended release and required `loopx doctor` checks pass;
- the Workspace and `status.json` come from the same verified runtime;
- Manager and Goal views can be explained by existing Goal, Todo, Gate, and Monitor state;
- every write has a preview, apply, receipt, and refreshed readback;
- Capability, Goal config, Provider readiness, and Turn eligibility are not collapsed into one “enabled” bit;
- Goal Channel and report delivery have exact route, identity, and readback evidence;
- staged authority, SSH sources, and browser presentation are not mistaken for new write authority.

Continue according to your task: return to [Connect an existing Git project](./05-connect-existing-project.md)
for project operation, use the [Developer contribution map](./source-protocol-map.md) when changing the
Workspace or control-plane implementation, or open the
[Personal Workspace guide](/loopx/docs/guides/personal-workspace-user-guide/) for detailed UI behavior.
