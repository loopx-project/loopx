# Bounded spending and external observation

After T1 finishes, M1 may still await CI for C1 and G1 may still await a publication decision. Remaining budget does not justify repeatedly invoking a model; no message does not prove the external world is unchanged. Separate whether to act, when to look again, who observes, and when to reconsider the route.

## Separate five responsibilities {#observation-owners}

| Question | Owning facts or rules | Responsibility it cannot replace |
| --- | --- | --- |
| Which work may advance now? | Quota and the current interaction contract | No additional external permission |
| What is being watched, and what was last observed? | Monitor identity, target and observation metadata | No automatic CI access or process creation |
| When should another observation happen? | Cadence, next due and scheduler/backoff | No guarantee that a Host will be available then |
| Who actually observes or wakes execution? | An integrated, usable Host, runner or event channel | Configuration is not proof of runtime health |
| Is this wait or route still worthwhile? | Frontier, Vision and replan policy | Not merely increasing or decreasing cadence |

Actual resource costs have their own sources: provider bills, tool usage and machine time. Internal quota settlement does not replace them. A legitimate no-spend observation can still cost resources.

## How a faulty counting policy misleads decisions

The following is a **hypothetical faulty policy**, not a claim that current implementation retains this defect: track consecutive unchanged observations in one global target slot, resetting whenever the target changes.

| Time | Due work | Correct per-item fact | Hypothetical global interpretation |
| --- | --- | --- | --- |
| 10:30 | M1, cadence 30m | M1's first unchanged observation | Just saw M1 |
| 10:45 | M2, cadence 45m | M2's first unchanged observation | Switch to M2 and clear the previous target count |
| 11:00 | M1 | M1's second unchanged observation | Switch again and start counting again |
| 11:30 | M1 and M2 are each due | Update each observation independently | Depends on processing order rather than actual stagnation |

The problem is not a high threshold; it is conflating different targets. Preserve each Monitor's identity and count instead of repeatedly lowering the threshold. Conversely, without a usable Host executing due observations, correct counters remain static records: that is a liveness gap, not no-change evidence.

## Choose an observation frequency while waiting for CI {#running-wait}

| Option | Suitable conditions | Cost and limit |
| --- | --- | --- |
| A person returns to inspect | Low frequency, short duration, tolerable delay | Depends on attention and preserved handoff context |
| Fixed cadence | Predictable change rhythm | Repeated observations accumulate during long waits |
| Capped backoff | Long unchanged periods | Reduces repeated reads but increases detection latency |
| Real event channel with verification | Available notification path and identity binding | An event still needs target, revision and current-state checks |

M1 and G1 require different actions. CI needs external readback; publication needs an authorized decision. CI passed cannot substitute for approval, and a pending user decision must not automatically freeze independent work.

| Actual M1 observation | Retainable fact | Legal continuation |
| --- | --- | --- |
| CI for C1 remains pending | Source- and time-bound unchanged observation | Update observation state and wait under current policy |
| CI for C1 passed | New evidence bound to C1 | Recheck T3, including G1 |
| Current code is C2 | Old green evidence supports C1 only | Obtain applicable C2 validation without deleting C1 history |
| Read failed or access is denied | Current CI remains unconfirmed | Report the read gap, not unchanged or passed |

A Monitor records an observation completed by its caller. It does not itself access CI, and `next_due_at` does not create an execution process. A healthy wait needs the target, observation rules and actual execution surface together.

## Budget participates in admission, but is not authorization

Ordinary delivery remains budget-constrained: `spent_slots >= allowed_slots` in the current window triggers applicable throttling. Do not bypass exhaustion by switching Todos or renaming delivery as observation. Positive balance does not bypass Gates, identity, capability, workspace or dependencies.

Read the resolved contract, distinguishing delivery, recovery/repair, observation, waiting and an outstanding decision. Unknown identity refuses delivery eligibility; it does not make diagnosis consume no resources. Relevant `quota should-run --codex-app` paths can create heartbeat receipts and must not be treated as side-effect-free polling. Use the [appendix](appendix-reference.md#read-before-change) for field reads.

Internal accounting attributes accepted work. Scheduler cadence changes, Gate notifications, dry runs, unchanged polls and repeated writeback must not masquerade as new delivery spend. Calling them free turns would hide their actual resource costs.

## After registering a wait, who may continue? {#wait-and-next-turn}

When T1 discovers a real dependency, record a specific target such as `monitor_changed:<todo_id>` or `todo_done:<todo_id>`. Preserve the validator, original work identity and dependency relation. Marking the Todo done is not a shortcut for clearing the blocker.

Independent T2 in the work graph does not let a Turn already bound to T1 rebind itself. Complete the original Turn's required blocked writeback and closeout, then let later admission select new work. **Turn closeout, Todo waiting and next-turn selection are three different facts.**

The [Turn chapter's wait sequence](03-one-turn.md#wait-closeout) explains this path with an explicit test from main `f49b4a00…`, labeled separately from older release promises. It checks no duplicate debit, no forced Monitor poll, and retained waiting/validation declarations. It is not a universal scheduling order for all Hosts.

## Cadence backoff versus route replanning

Backoff asks when to observe again under the same conditions. It derives a schedule from the resolved decision and runtime profile, preserving or resetting backoff with the applicable scheduler state's identity and `reset_token`. Read intervals, caps and unchanged limits from the current profile rather than treating sample numbers as a contract shared by every Host.

Without an event channel, external change is detected only after the next actual observation. Reset can change a future interval, not recover elapsed time. Use the bound ACK only after Host apply succeeds or readback already matches the target cadence. Failure or timeout needs the corresponding failure record, not a fabricated ACK. See [scheduler entrypoints](appendix-reference.md#scheduler-entry).

Replan asks whether waiting still serves the objective and whether another next step is preferable. Current Monitor policy combines current-Agent advancement, Monitor type and unchanged history; a counter alone is insufficient.

| Current input | Judgment supported by the relevant tests |
| --- | --- |
| No selectable advancement for the current Agent; eligible ordinary monitor-only lane reaches threshold | May create `monitor_no_change_streak` replan obligation |
| The current Agent has selectable advancement | Tested cases choose work without that Monitor-derived obligation |
| Only a peer has advancement | Peer progress does not erase the current Agent's stagnation |
| Monitor explicitly uses `watch_only` | Tested cases do not create this obligation even with a large count |

The [policy tests](https://github.com/loopx-project/loopx/blob/76b7583a9f67d6090b43a8c6e58c42cb67a1f3c6/tests/control_plane/test_monitor_replan_agent_scope.py) use threshold 5. That is the tested policy, not a theorem that every wait must change course after five observations. They preset counters and evaluate decisions, not actual interleaved polling, concurrent counter writes or real Host waking. See the [Monitor exercise](12-control-plane-course.md#checkpoint-monitor).

Replan is therefore not stronger backoff. One changes observation frequency; the other reassesses the route and needs an outcome accepted by its current contract. Saying “I reconsidered” does not replace the required change or supported terminal reasoning.

## Define an explainable observation

Before creating or changing a Monitor, identify its stable target, available observation handle, cadence/next due, relevant-change criterion, observation boundary and no-change accounting.

The metadata contract permits `expires_at`, `resume_when` or explicit `watch_only=true` as boundedness alternatives. Expiry and due are different: one constrains termination, the other schedules another observation. Absence of expiry alone does not establish an invalid Monitor.

Change must matter to the task. An unrelated response timestamp need not change acceptance conditions; identical text need not denote the same revision. Keep the validated object and interpretation in evidence rather than accumulating every raw response in the hot path.

[`monitor_metadata.ts`](https://github.com/loopx-project/loopx/blob/76b7583a9f67d6090b43a8c6e58c42cb67a1f3c6/loopx/control_plane/todos/monitor_metadata.ts) handles observation metadata and replay. The caller supplies domain observations; controlled writeback validates and records them. Another lane running must not overwrite this Monitor's count. See the [state-machine topic](core-state-machines.md) for canonical observation/successor transaction boundaries; these do not make network reads, settlement and every display atomic together.

## Diagnose the missing relationship

| Symptom | Evidence to collect first | Continuation and stopping condition |
| --- | --- | --- |
| No messages | Due state, actual Host state, last successful observation | Report an execution gap if no Host exists, not an unchanged external world |
| Repeated observations | Target identity, fingerprint, individual counters and cadence | Repair the owning policy, not every threshold |
| New evidence but no progress | Evidence revision, current Gates, dependencies and admission | Keep waiting if conditions are unmet; do not edit display state alone |
| Repeated replan requests | Current lane, selectable work, watch-only and ACK outcomes | Submit an accepted replan result, not a fabricated ACK |
| Observation committed but display stale | Original receipt, current source and projection | Restore projection, not repeat the business mutation |

Higher frequency generally costs more reads; longer backoff can increase detection delay. Judge policy against the task's latency, resource and human-intervention needs, not counts of turns or notifications.

You should be able to name the wait target, observer, next observation, relevant change and remaining execution conditions. Continue with the [Host](06-codex-app.md) or [CLI](07-codex-cli.md) for operation, and the [appendix](appendix-reference.md#diagnostic-routing) for diagnosis.
