# DeepSeek Harness Native Control-Plane Integration

Status: implemented in this repository as the optional
[`dsh-loopx-plugin`](../../packages/dsh-loopx-plugin/README.md) package.

The package embeds LoopX into a visible DeepSeek Harness (DSH) Session without
moving execution authority out of DSH or durable control-plane authority out of
LoopX. It ships three cooperating surfaces:

- `/loopx-init`, which installs or repairs the LoopX CLI and DSH workflow
  skills;
- a passive same-session Driver, which may queue one LoopX continuation only
  after the exact Session invokes the installed `loopx` skill;
- a loopback-only GoalBar Host and web Client, which show one exact bound
  Goal/Agent lane and expose guarded Start and Pause actions.

The older [`deepseek-harness` connector](deepseek-harness-connector.md) remains
a separate headless Turn adapter. The native plugin uses the
`deepseek-harness-native` host surface and does not replace that connector.

## Route Decision

Treat the two DSH paths as different execution carriers over one LoopX
contract, not as competing implementations to collapse into one transport:

| Path | Primary use | Session and wakeup owner | Frontend |
| --- | --- | --- | --- |
| `dsh-loopx-plugin` | Person-driven work in a DSH Session | DSH Session; optional same-session Driver after an exact `loopx` skill invocation | DSH Web/Desktop GoalBar and native skills |
| LoopX Turn on DSH | Headless, bounded, quota-governed work selected without a person present | LoopX Turn and the outer scheduler | LoopX App/CLI executor readback, artifacts and Todo settlement |

Do not duplicate the LoopX control plane inside DSH. Both routes must converge
on the same Goal, Agent, selected Todo, Turn identity, task packet, validator,
writeback and settlement semantics. DSH owns model calls, tools, provider
retries and raw Session storage in both routes; LoopX owns durable decisions
and work authority.

Prioritize the plugin for interactive DSH work and the Turn adapter for
unattended, bounded execution. Qualify each route against its actual caller
and the shared contract; readiness of one does not qualify the other.

## Authority Boundary

| Owner | Responsibilities |
| --- | --- |
| DSH | Agent and Session lifecycle, model and tool execution, inbox ordering, session events, UI transport, sandbox, provider credentials, billing, and trace |
| LoopX | Goal, Agent, Todo, binding, quota, lifecycle, progress, scheduler, evidence, and settlement authority |
| `dsh-loopx-plugin` | Fixed-argv CLI adaptation, exact-Session activation state, one pending continuation reservation, loopback GoalBar transport, and compact UI state |

The plugin has no model-facing LoopX tools, no binding sidecar, and no durable
Goal or Todo store. Installing it does not create a Goal, bind a Session, spend
quota, activate the Driver, or grant model/tool authority.

## Install And Activate

Install the co-located package and run its no-argument initialization command:

```bash
cd packages/dsh-loopx-plugin
./install.sh
```

```text
/loopx-init
```

`/loopx-init` probes the existing CLI, performs at most one
`python3 -m pip install --upgrade loopx` when the CLI is missing or
incompatible, installs the packaged workflow skills, and verifies the
readback. It does not install the plugin that defines the command. Restart DSH
only when the returned result says the installed skills changed.

Then invoke the installed `loopx` skill with the task in the DSH Session that
should continue automatically. The skill uses DSH's exact session id and the
`deepseek-harness-native` host surface. Verify the resulting binding from that
Session's project:

```bash
loopx --registry .loopx/registry.json --format json \
  resolve-agent-thread \
  --host-surface deepseek-harness-native \
  --thread-id "$DSH_SESSION_ID"
```

Only `status=bound` with one exact Goal/Agent pair admits the GoalBar and
Driver. Missing or ambiguous bindings fail closed.

## Same-Session Driver

Loading the plugin is passive. The Driver becomes eligible only after the exact
current Session contains one of these typed activation facts:

- a `user/message` from the `loopx` skill invocation; or
- a `tool/call` for that skill paired by call id with a successful
  `tool/result`.

Ordinary prose, shell text, `/loopx-init`, a skill catalog, a failed or
unmatched tool call, an existing registry, or a binding does not activate the
Driver. Activation is in-memory and session-scoped; replacing or clearing the
Session recomputes it from that Session's typed event history.

At an eligible idle boundary, the Driver:

1. yields to existing human or plugin input;
2. resolves the exact Session binding with `resolve-agent-thread`;
3. calls `quota should-run` for the bound Goal and Agent with one stable
   `turn_instance_id`;
4. reads the canonical thin `heartbeat-prompt` only when quota admits work;
5. queues at most one typed `loopx-continuation` message into the same Agent;
6. revalidates the Agent, Session, reservation, binding, and quota before the
   message enters a model step.

DSH owns provider retries. The Driver retries only safe fixed-argv LoopX reads
and an idempotent quota receipt, with a finite retry budget. Human input wins
over an unclaimed automatic reservation, and cancellation or an invalidated
Session retires pending work.

## GoalBar

The package-root Host registers one `/loopx` Connection channel with
`loopback` authority. Its Client renders a compact GoalBar only for the exact
live Session binding. The wire protocol is
`loopx_goalbar_request_v2` / `loopx_goalbar_response_v2` and supports:

- `goalbar/read`;
- `goalbar/watch`;
- `goalbar/start`;
- `goalbar/pause`.

The Host derives cwd and Session identity from the live DSH Agent. It reads
binding, lifecycle, and agent-lane Todo progress through fixed LoopX CLI argv,
then computes an opaque source revision from the project registry and active
Goal state. The browser receives ids, activation, Agent status, counts,
cursors, source revisions, and fixed error codes—not Todo text, Goal
objectives, evidence, CLI output, exception details, registry paths, or
credentials.

`Start` is admitted only for a stopped Goal and idle exact Session. `Pause`
stops the Goal and retires future queued continuation; it does not abort a
claimed or running turn. Every action revalidates the binding and returns typed
success, rejection, unknown-result, or applied-with-warning state.

## Failure And Privacy Boundaries

- LoopX CLI output is decoded against exact schemas; malformed or mismatched
  output fails closed.
- The loopback transport is a network reachability fence, not user
  authentication. The current package does not support remote or LAN GoalBar
  access.
- Source revisions are change tokens, not authorization or compare-and-swap
  grants.
- Raw transcripts, raw tool output, private traces, credentials, local paths,
  and full Goal/Todo text do not cross the GoalBar wire boundary.
- The native plugin never takes over DSH model, tool, sandbox, provider, or
  retry ownership.

## Validation And Removal

From the package directory, maintainers can validate the actual shipped
surfaces:

```bash
pnpm build
pnpm smoke:artifact
pnpm smoke:profile
pnpm smoke:runtime
```

To disable all native plugin surfaces, remove the package from the web profile
and restart DSH:

```bash
dsh plugin --profile web remove dsh-loopx-plugin
```

This removes `/loopx-init`, the Driver, and the GoalBar. It does not remove the
LoopX CLI, project state, bindings, or installed skills. See the
[package README](../../packages/dsh-loopx-plugin/README.md) for skill-only
uninstall and package rollback procedures.

## Related Documents

- [DeepSeek Harness connector](deepseek-harness-connector.md)
- [DSH native LoopX design](../plans/2026-08-20-dsh-native-skill-driver.md)
- [Runtime connector catalog](runtime-connector-catalog.md)
- [Host integration surface v0](../reference/protocols/host-integration-surface-v0.md)
