# Operate the LoopX 1.0 Workspace

`v1.0.0` is the **Personal Workspace milestone**: it gathers long-running work spanning sessions and Agents into an inspectable, operable local operator surface. This chapter explains how that operator surface relates to the control-plane sources of truth, and why actions taken in the UI must follow a governed path.

## An afternoon where the UI looked fine {#pause-readback}

This is a synthetic diagnostic situation, not a verified production incident.

```text
14:02  The operator opens the Workspace. The Manager overview shows three
       Goals, with two cards in the "in progress" lane.
14:03  They open one Goal and see Todos, an Agent lane, and a report summary.
14:05  They click Pause. HTTP returns 200.
14:06  The page still shows the Goal as active.
14:08  They refresh. Still active.
14:20  They check the CLI: `loopx quota status` says this Goal is already paused.
14:21  They identify disagreement and check the Goal, source, read times
       and receipt for that particular operation.
```

These observations do not establish that the 14:05 action failed to apply, and CLI paused alone does not prove the click succeeded. A commit may have succeeded while projection lagged, or another control state may explain paused. Verify the same object and source, then locate the matching operation receipt.

| Additional evidence | Supported conclusion | Next step |
| --- | --- | --- |
| Valid original stop receipt and current stopped source | The operation was accepted; old active display is no longer applicable | Repair projection or read routing, not repeat stop |
| Original entrypoint explicitly rejected or confirmed non-commit | That request did not complete the intended change | Repair the refusal condition and preview through the current owner |
| Original outcome unreadable or identity mismatched | That operation remains unconfirmed | Preserve the request and restore readback, not repeated clicks |

An operation receipt explains historical causality; current source explains present state; the page reflects a particular projection. They relate but do not substitute for one another. Use the [appendix](appendix-reference.md#diagnostic-routing) for field evidence.

## Why "what the page shows is what is true" does not hold {#action-owners}

The Workspace presents and initiates governed actions, but Goal, Todo, Gate, events, configuration, and receipts remain owned by the control-plane sources of truth. Three consequences follow unavoidably:

- **Projections lag.** The state on the page is a read model from some moment; between your seeing it and its being generated, the source may already have changed.
- **HTTP success is not write completion.** A request being accepted and an action completing are two different things, and the second may be stopped by a Gate or rejected by a stale check.
- **A button is not an independent authority.** Goal stop/resume checks owner authority and the reviewed fingerprint; Todo actions use lifecycle rules; quota governs eligibility for automated Turns.

The whole chapter compresses into one sentence: **the Workspace is where you observe and initiate; it is not a new source of truth.**

## Confirm the runtime before trusting the page

A page appearing does not mean the control plane is healthy. Check version and diagnostics before starting or reusing the service; `dashboard --no-open` is not merely a state query:

```bash
loopx --version
loopx doctor
loopx dashboard --no-open
```

The command prints the actual loopback URL. The default page and status projection can be read back like this:

```bash
curl -fsS http://127.0.0.1:8767/chat/ >/dev/null
curl -fsS http://127.0.0.1:8767/status.json
```

`loopx dashboard` serves the packaged Workspace, the status projection, and Agent Chat together. If a desktop shell of the same version has already started the service, it reuses the process verified through the capability fingerprint rather than starting a second source of truth. **The port is only a default**; automated checks should read the command output instead of treating the default URL as a permanent contract.

Once the Workspace is open, make three matching checks:

1. whether the Goal count in the Manager overview matches `loopx status`;
2. whether the target Goal's Agent lane and Task states match `loopx todo list --goal-id <goal-id>`;
3. whether the repository / source in Context points at the host and worktree you mean to operate on.

If any of the three disagrees, restore the corresponding runtime or projection before performing a write.

## Read the page back into control-plane questions

The Manager's four lanes are operator projections, not four new Todo states:

| Lane | What it actually means |
|---|---|
| Needs you | User Todos, permission Gates, or actions only the owner can decide |
| In progress | Agent Todos currently advancing, and active lanes |
| Watching | Monitors with a cadence, trigger condition, or external fact to wait on |
| Scheduled | Work bound to a Host schedule that is not yet due |

Inside a Goal, translate cards back into control-plane questions:

```text
Goal / Acceptance
  → selected Todo and owner
  → Gate, capability and workspace eligibility
  → current Session / Host
  → evidence, receipt and successor
```

Completed history is read-only evidence and does not re-enter the frontier. Reports and artifact summaries under Files are not the complete original either; to audit, follow the `todo_id`, run identity, evidence pointer, or versioned artifact back to the authoritative source.

## Writes: preview, apply, receipt

Goal, Todo, Heartbeat, Monitor, and settings changes in the Workspace all follow one safety chain:

```text
typed preview -> human or policy review -> governed apply -> verified receipt -> refreshed projection
```

**Preview** freezes the normalized arguments, blast radius, and current revision. **Apply** may only execute an action that still matches that preview; if state has changed it must return stale or a Gate rather than quietly applying an old decision. **Receipt and readback** are what prove the write completed — a button click or a successful HTTP response is not enough.

Taking a Goal pause as the example, the first command only previews:

```bash
loopx goal-lifecycle --goal-id <goal-id> --operation stop
loopx goal-lifecycle --goal-id <goal-id> --operation stop --actor-kind owner --execute
loopx quota status --goal-id <goal-id>
```

Executing a lifecycle transition requires explicitly passing `--actor-kind owner` or `controller`; anonymous preview stays read-only.

A pause removes that Goal from active attention and projects effective automated-run quota to zero, while Todos, history, evidence, and configuration remain. Resuming requires an explicit `resume --execute` and does not bypass Todo, Gate, or quota. Do not describe stop as "completing the Goal," and do not accidentally resume an owner-stopped Goal by editing quota.

## Four kinds of "enabled" must stay separate

The 1.0 Workspace can display Goal capability and typed machine policy, but four facts must be judged separately:

| Fact | Read it with | What it does not mean |
|---|---|---|
| Capability published | `loopx capability list/show` | The current Goal has enabled it |
| Goal configured | `loopx configure-goal --goal-id <goal-id>` | The Provider is ready |
| Provider ready | the relevant Extension / Provider doctor | The current Turn passes its Gate |
| Current Turn eligible | the capability / workspace result of `quota should-run` | It grants extra external permission |

Start with read-only discovery:

```bash
loopx capability list --format json
loopx machine-config describe
loopx machine-config inspect --format json
loopx configure-goal --goal-id <goal-id>
```

Machine policy and Goal settings both require generating a delta / plan, then explicitly executing and reading the revision back. Do not guess configuration flags from a Capability name, and do not write "visible in the catalog" as "enabled." Enabling an optional capability such as adaptive sub-Agents does not force parallelism and grants no new Goal, repository, credential, release, or production permission.

## Goal Channel: a message is not implicit authority

Workspace Lark / Feishu settings can connect a Goal to a specific Topic and target Agent. Capture scope decides only which messages enter the connection; **it does not widen Agent permissions.** Ingress mode decides how messages enter the runtime:

| Mode | Behavior |
|---|---|
| `live_steering` | Delivered only to that Agent's exact currently active Turn |
| `session_queue` | Enter a bounded FIFO on the same exact Session, processed after the current Turn |
| `async_inbox` | Enter the Agent's local private inbox, awaiting an explicit drain |

After configuring, read back the Goal, Agent, Topic, ingress mode, Session binding, and listen state. To verify `async_inbox`, send a new test message yourself and then run:

```bash
loopx lark-inbox drain --goal-id <goal-id> --agent-id <agent-id>
```

Disconnect removes only that Goal's Topic route; it does not delete the Goal, Session, history, or other connections. A message arriving also does not mean the Agent gained send, repository-write, or production permission; those actions still pass through their own Gates and Provider readback.

## One-off generation and continuous delivery are two authorization chains

Explicitly requesting "generate this week's project report" in an active project session enables a single provider-free Markdown / HTML generation. Inspect the built-in profile read-only first:

```bash
loopx periodic-report inspect-profile --preset weekly --format json
```

`active` and `generation_allowed` in the receipt should both be `true`. The built-in weekly profile has no schedule and no sink, so **one generation creates no recurring task and sends no message**.

Continuous reporting is a separate authorization chain: a custom profile declares the cadence, Host Automation performs the waking, and `enabled: true` on a machine or Goal subscription together with an explicit `route_ref` constitutes continuous-delivery authority. Pausing the Automation, disabling the profile, or closing the subscription stops the corresponding path. A successful generation does not mean a successful external send; the Provider, sending identity, route, and message readback each still need verification.

## Cost and boundary

**Cost one: operations get longer.** A change is no longer "one click" but preview → review → apply → readback. For familiar actions this feels laborious.

**Cost two: the UI must be re-read, never judged from a stale screen.** What you see may lag at any moment, so any consequential judgment needs a fresh read of the source.

**Cost three: four kinds of "enabled" must be distinguished.** Collapsing them into one switch misjudges capability availability, and keeping them apart carries real mental overhead.

**Boundary one: the Workspace does not own facts.** A broken UI proves neither a healthy control plane nor stopped execution. Distinguish source, runtime and projection first. Only after confirming source and execution conditions can the fault be limited to display recovery, not manual state edits through the UI. It also does not constitute Stage 2C authority — qualification and promotion of a shared-authority provider come from separate shadow and conformance work, not from the existence of an operator surface.

**Boundary two: desktop updates are narrowly scoped.** Updates can only come from the fixed official feed; macOS uses updater signature and ad-hoc code signing and should not be described as notarized. Restoring a previous version also does not promise to reverse a future-incompatible Goal schema.

**Boundary three: a CLI update cannot repair native-shell defects.** Browser / PWA users continue through the CLI update flow; launcher or updater problems in the desktop shell need desktop-side handling.

## 1.0 acceptance checklist

For one Workspace acceptance pass, confirm at least that:

- `loopx --version` matches the intended release and required `loopx doctor` checks pass;
- the Workspace and `status.json` come from the same verified runtime;
- Manager and Goal views can be explained by existing Goal, Todo, Gate, and Monitor state;
- every write has a preview, apply, receipt, and refreshed readback;
- Capability, Goal config, Provider readiness, and Turn eligibility are not collapsed into one "enabled" bit;
- Goal Channel and report delivery have exact route, identity, and readback evidence;
- staged authority, SSH sources, and browser presentation are not mistaken for new write authority.

## Invariants

1. **The UI is a projection, not a source of truth.** Re-read the source before any consequential judgment.
2. **HTTP success does not prove the write completed.** Only a receipt and readback do.
3. **A click is not authorization.** Goal lifecycle accepts owner operations, Todo lifecycle checks work transitions, and the current quota contract admits automated Turns. The page grants none of these permissions.
4. **The four kinds of "enabled" need separate evidence.** They may be prerequisites for one another; one switch does not establish that every condition holds.
5. **A message arriving does not widen permission.** Neither capture scope nor ingress mode grants new write authority.

These five answer one question: **when the interface tells you everything is fine, what makes that believable?**
