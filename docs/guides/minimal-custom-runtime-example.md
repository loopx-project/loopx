# Minimal Custom Runtime Example

[简体中文](minimal-custom-runtime-example.zh-CN.md)

LoopX is agent-loop agnostic, but most hosts do **not** need a typed runtime
adapter. This page is the short contract requested by
[issue #2835](https://github.com/huangruiteng/loopx/issues/2835).

There are two integration depths. Start with path A unless you need
programmatic commit/replay/recovery.

## Path A — Mainstream: skill + wake + LoopX CLI

Pieces:

| Piece | Owns | Does not own |
| --- | --- | --- |
| **Host wake** | When a turn starts (cron, task queue, visible `/goal`, native loop, or a human) | Todo/gate/quota/evidence stores |
| **Lightweight skill / re-entry instruction** | How the agent reads a fresh packet, claims work, validates, and writes back | A second state machine |
| **LoopX CLI** | Durable goal, todos, claims, gates, evidence, refresh, quota, scheduler hints | Model tools and host process lifecycle |

Canonical turn:

```text
host wake
  -> lightweight skill / re-entry instruction
  -> loopx status
  -> loopx quota should-run
  -> loopx todo claim
  -> agent executes one bounded slice
  -> independent validation of the real postcondition
  -> loopx todo complete (evidence) + refresh-state
  -> loopx quota spend-slot --execute
  -> host applies scheduler_hint for the next wake
```

Executable public smoke (synthetic fixture only):

```bash
python3 examples/custom-runtime-minimal-cli-turn-smoke.py
```

The smoke proves the shipped CLI sequence on a temporary project: claim one
todo, write a public marker file, validate the marker, complete with evidence,
refresh state, and spend one controller slot. It does not copy private logs,
credentials, or live agent transcripts.

For day-to-day onboarding commands (`agent-onboard`, skill delivery, scheduler
ACK), use the longer
[Embed LoopX in Your Agent Runner](custom-agent-runner-integration.md) guide.

## Path B — Advanced: typed LoopX Turn adapter

Use this when a host must drive an external worker programmatically and needs
preview/commit/replay/recovery semantics rather than a cooperative agent that
calls the CLI itself:

```text
fresh LoopX decision / TurnEnvelope
  -> host adapter
  -> loopx_turn_result_v0
  -> independent effect validation
  -> durable commit / replay / recovery
```

Executable public smoke:

```bash
python3 examples/loopx-turn-fake-host-walkthrough-smoke.py
```

Path B is optional. It does not replace path A for Codex, Claude Code, Cursor,
shell, Grok Build, or other cooperative hosts.

## What a custom runtime must not do

- Implement a second Todo, gate, evidence, quota, or scheduler store.
- Spend quota before validated durable writeback.
- Treat model completion text as proof without reading the real artifact.
- Commit `.loopx/`, live `ACTIVE_GOAL_STATE.md`, credentials, or raw sessions.
- Silently switch a visible host into hidden unattended execution.

## Related surfaces

- [Custom agent runner integration](custom-agent-runner-integration.md)
- [Runtime connector catalog](../integrations/runtime-connector-catalog.md)
- [Host integration surface v0](../reference/protocols/host-integration-surface-v0.md)
- [LoopX Turn fake-host walkthrough smoke](https://github.com/loopx-project/loopx/blob/main/examples/loopx-turn-fake-host-walkthrough-smoke.py)
