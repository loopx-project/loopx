# Codex App minimum schedule interval

This opt-in policy keeps LoopX's **Codex App schedule recommendations** at or
above an owner-configured minimum, including after backoff/reset. It does not
intercept App timer execution or enforce a token budget. Existing automations
are not changed by configuration alone.

## Configure and apply

Pause the affected automatic schedule while changing its policy. Use the same
registry and runtime root as its LoopX heartbeat. Read its current policy:

```sh
loopx --format json automation-cadence --goal-id my-goal --agent-id my-agent
```

Configure at the Goal level by omitting `--agent-id`, at the agent level as
below, or add `--automation-id my-automation` for one existing automation. Parent
and child constraints combine by maximum; an agent rule covers all of its
launch mechanisms' schedule projections. Use the returned configuration revision
instead of assuming it is zero:

```sh
loopx --format json automation-cadence --goal-id my-goal --agent-id my-agent \
  --min-interval-minutes 1440 --expected-revision 0 \
  --owner-reference owner-daily-cadence
```

Review the preview, then repeat with `--execute`. Inspect it again in a separate
command to confirm the saved revision and contributing sources. Do not infer
owner approval from a scheduler recommendation. A local owner-reference records
caller intent, not authentication against other processes sharing the OS user.

For an already bound App turn, inspect its `quota should-run` scheduler packet
using that turn's normal identity and selection contract. Do not create a new
turn solely to observe a timer. `scheduler_hint.app_automation` and its legacy
`codex_app` projection expose the floor, desired RRULE, apply/ACK state and
`guarantee`. Apply the desired schedule with the App's `automation_update` tool,
preserving task binding, prompt, status and notification preference. Then view
that same automation and compare its actual schedule before running the returned
ACK command. A configuration write or update-tool response alone is not actual
schedule readback. Resume only the affected schedule when its normal activation
requirements are met.

If the App rejects the required interval, hold that automation and retain the
failure; do not shorten the floor or activate an alternate scheduler. A daily
wall-clock schedule and 1440 elapsed minutes can differ around timezone/DST
changes; this policy uses elapsed minutes and projects a minute-based RRULE.

## Inspect the minimum-interval condition

The human CLI read prints the configuration revision, contributing source
revisions, minimum-interval state and exact UTC threshold time. The Chat
Dashboard exposes the same facts under **Settings → Automatic execution
interval**. Both consume the quota owner's typed readback; neither calculates
the condition from a local clock. An `eligible` state means only that the
minimum interval has elapsed. It does not assert session health, quota,
permissions, work availability or launch readiness.

For Lark, select an explicitly authorized attached Agent with `/agents` and its
exact `/agent` command, then send `/status`. That status uses the Agent-wide
scope because a Lark target has no automation identity. It shows the effective
minimum and exact UTC next-eligible time. A project conversation with no
selected Agent does not read or expose Goal cadence. An unavailable or invalid
owner read is reported as unavailable, never as eligible. Redelivery uses the
persisted status snapshot and does not read the owner again.

`quota should-run` always attaches the same readback for the selected Goal and
Agent. A waiting interval changes the decision to a quiet wait only for an
otherwise runnable hosted App automation. Generic CLI inspection and stronger
pause, health, gate, recovery, repair or settlement states keep their existing
decision. The scheduler emits no timer mutation, host action or notification
for this wait.

## Disable or roll back

Lowering or disabling a scope needs an explicit owner decision and the latest
revision. Zero disables only this scope; parent constraints still apply:

```sh
loopx --format json automation-cadence --goal-id my-goal --agent-id my-agent \
  --min-interval-minutes 0 --expected-revision 1 \
  --owner-reference owner-disable-daily-cadence --approve-reduction --execute
```

Read back the effective policy. Keep the schedule paused before downgrading to
an older LoopX version that ignores this policy. Do not delete policy files or
reset backoff history to bypass an interval. No policy is enabled by default.

## What this version verifies

CLI configuration/readback, inherited constraints, version conflicts,
recommendation/reset parity, hosted-App quota waiting, Dashboard readback and
selected-Agent Lark status are covered by focused owner, API, rendering and
replay tests. The packaged Dashboard scenario verifies Chinese desktop/mobile
and English desktop settings readback. These surfaces do not add another policy
store or modify an App timer.

The App-bundled runtime supports prompt hooks in an isolated test, but actual
App automatic-trigger coverage, hook trust/failure handling and reliable manual
intent remain unqualified. `pre_model_atomic_admission: not_qualified` is not a
claim that the host has no hook mechanism. See the RFC's research ledger.
