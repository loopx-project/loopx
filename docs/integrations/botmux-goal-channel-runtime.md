# Botmux Goal Channel Runtime

LoopX can bind an existing [botmux](https://github.com/deepcoldy/botmux) bot
to one Goal Channel. Botmux owns IM delivery, Lark event subscriptions,
persistent agent sessions, streaming cards, and terminal access. LoopX remains
the only authority for goal state, todos, gates, quota, evidence, and accepted
transitions.

The two surfaces are complementary:

```text
LoopX Goal Channel sync -> Lark status, Kanban, and gate notifications
Lark interaction -> botmux -> configured agent runtime -> LoopX command
```

A notification or botmux delivery receipt is an observation only. It does not
change canonical LoopX state. State changes only when the configured agent
runtime explicitly invokes a validated LoopX transition.

## Prerequisites

- botmux is installed and running;
- the selected botmux bot can invoke the installed LoopX CLI or managed skill;
- the bot is allowed to talk in the selected Lark chat;
- botmux runs on the host that owns the LoopX project checkout;
- the bot's `defaultWorkingDir` or the selected chat's oncall `workingDir`
  resolves to the LoopX project root;
- the botmux Dashboard token is available in an environment variable.

LoopX never stores the Dashboard token. The local-private runtime binding stores
only the environment variable name.

## Configure

Export the existing botmux Dashboard token:

```bash
export BOTMUX_DASHBOARD_TOKEN='<local-private-token>'
```

Preview the binding first:

```bash
loopx goal-channel runtime setup \
  --goal-id <goal-id> \
  --bot-id <botmux-lark-app-id> \
  --chat-id <lark-chat-id>
```

The preview probes botmux liveness, confirms that the selected bot is online,
in the selected chat, and bound to the LoopX project directory, then uses
botmux's dry-run trigger contract to verify the chat route. Apply the
local-private binding explicitly:

```bash
loopx goal-channel runtime setup \
  --goal-id <goal-id> \
  --bot-id <botmux-lark-app-id> \
  --chat-id <lark-chat-id> \
  --execute
```

The binding is written beside the project registry as
`.loopx/goal-channel-runtime.json` with owner-only permissions. It contains
private provider identifiers and must remain ignored and untracked.

## Operate

Verify readiness without changing provider state:

```bash
loopx goal-channel runtime doctor --goal-id <goal-id>
```

Preview the next bounded turn:

```bash
loopx goal-channel runtime trigger --goal-id <goal-id>
```

Queue it explicitly:

```bash
loopx goal-channel runtime trigger --goal-id <goal-id> --execute
```

The default instruction asks the configured agent runtime to use its installed
LoopX CLI or managed skill, select the active next action, and require artifact,
validation, and state writeback before the turn stops. Use `--instruction` only
when the owner needs a different bounded instruction.

The default semantic turn key is derived from the current goal state. Repeating
the same command does not dispatch another turn. After a deliberate state-free
follow-up, provide an explicit new key:

```bash
loopx goal-channel runtime trigger \
  --goal-id <goal-id> \
  --turn-key <owner-chosen-semantic-key> \
  --execute
```

Read botmux's typed lifecycle result:

```bash
loopx goal-channel runtime status --goal-id <goal-id>
```

Add `--execute` only when the observed state should be persisted to the
local-private LoopX receipt.

## Failure Semantics

- The first visible-chat dispatch records an `attempting` receipt before the
  provider call. If the connection fails after submission, LoopX reports
  `botmux_dispatch_outcome_unknown` and does not blindly retry.
- Follow-up turns reuse the stored botmux session and botmux's native
  `turnIdempotencyKey`.
- `running`, `completed`, `failed`, and `not_found` are provider observations.
  They do not independently complete, reopen, or otherwise mutate a LoopX goal.
- A Lark message or botmux card is delivery evidence, not LoopX transition
  authority.

## Disable Or Roll Back

The integration is opt-in and does not alter botmux configuration. Preview and
then disable only the selected goal binding:

```bash
loopx goal-channel runtime disable --goal-id <goal-id>
loopx goal-channel runtime disable --goal-id <goal-id> --execute
```

Disabling clears the selected goal's active botmux session pointer but leaves
other goal bindings and the botmux daemon untouched. Existing Goal Channel
status/Kanban sync remains independent.
