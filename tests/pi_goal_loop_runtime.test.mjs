import assert from "node:assert/strict"
import test from "node:test"
import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"

import {
  BRIDGE_SCHEMA_VERSION,
  DEFAULT_RETRY_MINUTES,
  buildTaskLeaseArgs,
  createBindingStore,
  createEphemeralSessionIdentity,
  createGoalLoop,
  createMemoryBindingStore,
  hasAbortedAssistantMessage,
  piThreadId,
  soleThreadBindingCandidate,
  runPiTaskLease,
  sanitizedKey,
  sessionKey,
  waitPlan,
} from "../loopx/pi_goal_mode/pi-goal-loop-runtime.mjs"


function memoryBindingStore() {
  const bindings = new Map()
  return {
    bindings,
    async read(key) {
      return bindings.get(key) || null
    },
    async write(key, changes, expected) {
      const current = bindings.get(key) || {}
      if (expected) {
        if (
          expected.generation !== undefined &&
          (current.generation || 0) !== expected.generation
        ) {
          return null
        }
        if (expected.goalId !== undefined && (current.goalId || "") !== expected.goalId) {
          return null
        }
        if (expected.autoResume !== undefined && current.autoResume !== expected.autoResume) {
          return null
        }
      }
      const value = {
        schemaVersion: BRIDGE_SCHEMA_VERSION,
        sessionKey: key,
        directory: "/workspace",
        goalId: "",
        agentId: "",
        registryPath: "",
        availableCapabilities: [],
        taskBody: "",
        autoResume: true,
        terminal: false,
        generation: 0,
        schedulerToken: "",
        unchangedPolls: 0,
        lastInjectedPrompt: "",
        ...current,
        ...changes,
      }
      bindings.set(key, value)
      return value
    },
    async remove(key) {
      bindings.delete(key)
    },
  }
}

function testAuthority(key, goalId) {
  return {
    schemaVersion: "loopx_pi_session_authority_v0",
    token: `host-token-${key}`,
    goalId,
    agentId: "agent-test",
    registryPath: "/workspace/registry.json",
    availableCapabilities: ["task_lease_v0"],
  }
}

async function activateWithAuthority(loop, key, fields) {
  const goalId = String(fields.goalId || `goal-${key}`)
  const authority = testAuthority(key, goalId)
  loop.bindAuthority(key, authority)
  return loop.activate(key, {
    ...fields,
    activationToken: authority.token,
  })
}


function terminalDecision() {
  return {
    should_run: false,
    effective_action: "terminal_no_followup",
    reason: "validated closure",
    goal_frontier_projection: {
      terminal_state: {
        schema_version: "goal_terminal_state_v0",
        kind: "no_followup",
        derived: true,
        source: "validated_goal_closure",
      },
      source_completeness: {
        schema_version: "goal_terminal_source_completeness_v0",
        user_todos: "valid",
        agent_todos: "valid",
      },
    },
  }
}


function backoffDecision() {
  return {
    should_run: false,
    scheduler_hint: {
      action: "backoff_waiting_for_user",
      reset_policy: { reset_token: "wait-1" },
      unchanged_poll: {
        limits: { local_scheduler: 3 },
        after_limits: { local_scheduler: "stop_tick_loop" },
        final_quota_replan_check_enabled: true,
        final_quota_replan_check_action: "rerun_quota_should_run_once",
      },
      cold_path_detail: {
        local_scheduler: {
          recommended_interval_minutes: 3,
          example_progression_minutes: [3, 6, 12],
          unchanged_poll_limit: 3,
          after_limit: "stop_tick_loop",
          final_quota_replan_check: {
            enabled: true,
            action: "rerun_quota_should_run_once",
          },
        },
      },
    },
  }
}


function gatedStore() {
  const inner = memoryBindingStore()
  const writes = []
  let reads = 0
  let gate = null
  let releaseGate = null
  let markPostProbeReadStarted = null
  const postProbeReadStarted = new Promise((resolve) => {
    markPostProbeReadStarted = resolve
  })
  return {
    store: {
      async read(key) {
        reads += 1
        // activate's generation read, settle's pre-check and
        // evaluateIdleOnce's binding read happen before the probe; the fourth
        // read is the post-probe read.
        if (reads === 4) {
          markPostProbeReadStarted()
          if (gate) await gate
        }
        return inner.read(key)
      },
      async write(key, changes) {
        writes.push({ key, changes })
        return inner.write(key, changes)
      },
      async remove(key) {
        return inner.remove(key)
      },
    },
    writes,
    postProbeReadStarted,
    hangPostProbeRead() {
      gate = new Promise((resolve) => {
        releaseGate = resolve
      })
    },
    releasePostProbeRead() {
      if (releaseGate) releaseGate()
    },
  }
}


function harness(initialDecision) {
  const store = memoryBindingStore()
  const calls = { send: 0, quota: 0, notify: 0, messages: [] }
  const scheduled = []
  let decision = initialDecision
  const loop = createGoalLoop({
    quotaProbe: async () => {
      calls.quota += 1
      const value = typeof decision === "function" ? decision(calls.quota) : decision
      if (value instanceof Error) throw value
      return value
    },
    sendMessage: (prompt) => {
      calls.send += 1
      calls.messages.push(prompt)
    },
    setTimer: (callback, delayMs) => {
      const timer = { callback, cleared: false, delayMs }
      scheduled.push(timer)
      return timer
    },
    clearTimer: (timer) => {
      timer.cleared = true
    },
  })
  const services = {
    store,
    isIdle: () => true,
    notify: () => {
      calls.notify += 1
    },
  }
  const activate = async (key, overrides = {}) =>
    activateWithAuthority(loop, key, {
      goalId: `goal-${key}`,
      taskBody: "LoopX task body",
      autoResume: true,
      terminal: false,
      ...overrides,
    })
  return {
    calls,
    loop,
    scheduled,
    services,
    store,
    activate,
    setDecision(value) {
      decision = value
    },
  }
}


test("run_now injects the heartbeat task body exactly once", async () => {
  const fixture = harness({ should_run: true, scheduler_hint: { action: "run_now" } })
  fixture.loop.bind("session-1", fixture.services)
  await fixture.activate("session-1")
  await fixture.loop.settle("session-1")

  assert.equal(fixture.calls.quota, 1)
  assert.equal(fixture.calls.send, 1)
  assert.equal(fixture.calls.messages[0], "LoopX task body")
  assert.equal(fixture.scheduled.length, 0)
  const binding = await fixture.store.read("session-1")
  assert.equal(binding.lastInjectedPrompt, "LoopX task body")
  assert.equal(binding.autoResume, true)
})

function taskLeaseBinding(overrides = {}) {
  return {
    directory: "project",
    registryPath: "registry.json",
    goalId: "goal-lease",
    agentId: "agent-lease",
    activationToken: "lease-token",
    availableCapabilities: ["task_lease_v0"],
    terminal: false,
    ...overrides,
  }
}

function taskLeaseAuthority(overrides = {}) {
  return {
    schemaVersion: "loopx_pi_session_authority_v0",
    token: "lease-token",
    goalId: "goal-lease",
    agentId: "agent-lease",
    registryPath: "registry.json",
    availableCapabilities: ["task_lease_v0"],
    ...overrides,
  }
}


test("validated terminal no-follow-up stops and notifies without a message", async () => {
  const fixture = harness(terminalDecision())
  fixture.loop.bind("session-terminal", fixture.services)
  await fixture.activate("session-terminal")
  const notifyAfterActivate = fixture.calls.notify
  await fixture.loop.settle("session-terminal")

  assert.equal(fixture.calls.quota, 1)
  assert.equal(fixture.calls.send, 0)
  assert.equal(fixture.calls.notify, notifyAfterActivate + 1)
  assert.equal(fixture.scheduled.length, 0)
  const binding = await fixture.store.read("session-terminal")
  assert.equal(binding.terminal, true)
  assert.equal(binding.autoResume, false)
})


test("backoff schedules a timer from the scheduler hint", async () => {
  const fixture = harness(backoffDecision())
  fixture.loop.bind("session-backoff", fixture.services)
  await fixture.activate("session-backoff")
  await fixture.loop.settle("session-backoff")

  assert.equal(fixture.calls.send, 0)
  assert.equal(fixture.scheduled.length, 1)
  assert.equal(fixture.scheduled[0].delayMs, 180_000)
  assert.equal(fixture.scheduled[0].cleared, false)
})


test("fails closed with a bounded retry after a quota probe failure", async () => {
  const fixture = harness(new Error("network timeout"))
  fixture.loop.bind("session-timeout", fixture.services)
  await fixture.activate("session-timeout")
  await fixture.loop.settle("session-timeout")

  assert.equal(fixture.calls.quota, 1)
  assert.equal(fixture.calls.send, 0)
  assert.equal(fixture.scheduled.length, 1)
  assert.equal(fixture.scheduled[0].delayMs, DEFAULT_RETRY_MINUTES * 60_000)
  assert.equal(fixture.scheduled[0].cleared, false)
})


test("a user-driven prompt pauses auto-resume and cancels the scheduled timer", async () => {
  const fixture = harness(backoffDecision())
  fixture.loop.bind("session-pause", fixture.services)
  await fixture.activate("session-pause")
  await fixture.loop.settle("session-pause")
  assert.equal(fixture.scheduled.length, 1)

  await fixture.loop.userPrompt("session-pause", "change direction")

  const binding = await fixture.store.read("session-pause")
  assert.equal(binding.autoResume, false)
  assert.equal(fixture.scheduled[0].cleared, true)

  const probesBefore = fixture.calls.quota
  await fixture.loop.settle("session-pause")
  assert.equal(fixture.calls.quota, probesBefore)
})


test("an aborted Pi run pauses durably and cancels the scheduled timer", async () => {
  const fixture = harness(backoffDecision())
  fixture.loop.bind("session-abort", fixture.services)
  await fixture.activate("session-abort")
  await fixture.loop.settle("session-abort")
  assert.equal(fixture.scheduled.length, 1)

  await fixture.loop.interrupt("session-abort")

  const binding = await fixture.store.read("session-abort")
  assert.equal(binding.autoResume, false)
  assert.equal(fixture.scheduled[0].cleared, true)

  const probesBefore = fixture.calls.quota
  await fixture.loop.settle("session-abort")
  assert.equal(fixture.calls.quota, probesBefore)

  fixture.setDecision({ should_run: true, scheduler_hint: { action: "run_now" } })
  await fixture.loop.resume("session-abort")
  await fixture.loop.settle("session-abort")
  assert.equal(fixture.calls.quota, probesBefore + 1)
  assert.equal(fixture.calls.send, 1)
})


test("agent_end abort detection ignores non-aborted and non-assistant messages", () => {
  assert.equal(hasAbortedAssistantMessage(null), false)
  assert.equal(hasAbortedAssistantMessage([{ role: "assistant", stopReason: "stop" }]), false)
  assert.equal(hasAbortedAssistantMessage([{ role: "user", stopReason: "aborted" }]), false)
  assert.equal(
    hasAbortedAssistantMessage([
      { role: "toolResult", content: [] },
      { role: "assistant", stopReason: "aborted" },
    ]),
    true,
  )
})


test("auto-resume compare-and-swap rejects a continuation write after pause", async () => {
  const store = createMemoryBindingStore()
  await store.write("session-cas-pause", {
    goalId: "goal-cas-pause",
    generation: 1,
    autoResume: true,
  })
  await store.write("session-cas-pause", { autoResume: false })

  const committed = await store.write(
    "session-cas-pause",
    { lastInjectedPrompt: "must-not-send" },
    { generation: 1, goalId: "goal-cas-pause", autoResume: true },
  )

  assert.equal(committed, null)
  assert.equal((await store.read("session-cas-pause")).lastInjectedPrompt, "")
})


test("an injected follow-up prompt keeps auto-resume armed", async () => {
  const fixture = harness({ should_run: true, scheduler_hint: { action: "run_now" } })
  fixture.loop.bind("session-injected", fixture.services)
  await fixture.activate("session-injected")
  await fixture.loop.settle("session-injected")
  assert.equal(fixture.calls.send, 1)

  await fixture.loop.userPrompt("session-injected", "LoopX task body")

  const binding = await fixture.store.read("session-injected")
  assert.equal(binding.autoResume, true)
})


test("session_shutdown during an in-flight probe stops the old session", async () => {
  let releaseDecision
  const pendingDecision = new Promise((resolve) => {
    releaseDecision = resolve
  })
  const fixture = harness(pendingDecision)
  fixture.loop.bind("session-shutdown-in-flight", fixture.services)
  await fixture.activate("session-shutdown-in-flight")

  const settled = fixture.loop.settle("session-shutdown-in-flight")
  for (let index = 0; index < 5 && fixture.calls.quota === 0; index += 1) {
    await Promise.resolve()
  }
  assert.equal(fixture.calls.quota, 1)

  // session_shutdown atomically disposes the extension instance.
  fixture.loop.dispose()
  releaseDecision({ should_run: true, scheduler_hint: { action: "run_now" } })
  await settled

  // The in-flight probe returns to the disposed guard: no old task body is
  // sent and no timer is rescheduled past the boundary.
  assert.equal(fixture.calls.send, 0)
  assert.equal(fixture.scheduled.length, 0)
})


test("session_shutdown clears every scheduled timer", async () => {
  const fixture = harness(backoffDecision())
  for (const key of ["session-dispose-a", "session-dispose-b"]) {
    fixture.loop.bind(key, fixture.services)
    await fixture.activate(key)
    await fixture.loop.settle(key)
  }
  assert.equal(fixture.scheduled.length, 2)
  assert.equal(fixture.scheduled.every((timer) => timer.cleared), false)

  fixture.loop.dispose()

  assert.equal(fixture.scheduled.every((timer) => timer.cleared), true)
})


test("user intervention during an in-flight probe stops continuation", async () => {
  let releaseDecision
  const pendingDecision = new Promise((resolve) => {
    releaseDecision = resolve
  })
  const fixture = harness(pendingDecision)
  fixture.loop.bind("session-intervention", fixture.services)
  await fixture.activate("session-intervention")
  const settled = fixture.loop.settle("session-intervention")
  for (let index = 0; index < 5 && fixture.calls.quota === 0; index += 1) {
    await Promise.resolve()
  }
  assert.equal(fixture.calls.quota, 1)

  await fixture.loop.userPrompt("session-intervention", "change direction")
  releaseDecision({ should_run: true, scheduler_hint: { action: "run_now" } })
  await settled

  assert.equal(fixture.calls.send, 0)
  assert.equal((await fixture.store.read("session-intervention")).autoResume, false)
})


test("abort during an in-flight probe fences the pending continuation", async () => {
  let releaseDecision
  const pendingDecision = new Promise((resolve) => {
    releaseDecision = resolve
  })
  const fixture = harness(pendingDecision)
  fixture.loop.bind("session-abort-race", fixture.services)
  await fixture.activate("session-abort-race")
  const settled = fixture.loop.settle("session-abort-race")
  for (let index = 0; index < 5 && fixture.calls.quota === 0; index += 1) {
    await Promise.resolve()
  }
  assert.equal(fixture.calls.quota, 1)

  await fixture.loop.interrupt("session-abort-race")
  releaseDecision({ should_run: true, scheduler_hint: { action: "run_now" } })
  await settled

  assert.equal(fixture.calls.send, 0)
  assert.equal(fixture.scheduled.length, 0)
  assert.equal((await fixture.store.read("session-abort-race")).autoResume, false)
})


test("coalesces concurrent settles into one quota decision", async () => {
  let releaseDecision
  const pendingDecision = new Promise((resolve) => {
    releaseDecision = resolve
  })
  const fixture = harness(pendingDecision)
  fixture.loop.bind("session-coalesced", fixture.services)
  await fixture.activate("session-coalesced")
  const first = fixture.loop.settle("session-coalesced")
  const second = fixture.loop.settle("session-coalesced")
  releaseDecision({ should_run: true, scheduler_hint: { action: "run_now" } })
  await Promise.all([first, second])

  assert.equal(fixture.calls.quota, 1)
  assert.equal(fixture.calls.send, 1)
})


test("wait plan marks the third unchanged poll for a final check", () => {
  const binding = {
    schedulerToken: "wait-1",
    unchangedPolls: 2,
  }
  const plan = waitPlan(backoffDecision(), binding)
  assert.equal(plan.stop, true)
  assert.equal(plan.finalCheck, true)
  assert.equal(plan.minutes, DEFAULT_RETRY_MINUTES)
})


test("wait plan advances the progression while under the limit", () => {
  const binding = {
    schedulerToken: "wait-1",
    unchangedPolls: 1,
  }
  const plan = waitPlan(backoffDecision(), binding)
  assert.equal(plan.stop, false)
  assert.equal(plan.unchangedPolls, 2)
  assert.equal(plan.minutes, 6)
})


test("wait plan resets the unchanged count when the scheduler token changes", () => {
  const binding = {
    schedulerToken: "stale-token",
    unchangedPolls: 2,
  }
  const plan = waitPlan(backoffDecision(), binding)
  assert.equal(plan.stop, false)
  assert.equal(plan.unchangedPolls, 1)
  assert.equal(plan.minutes, 3)
})


test("third unchanged poll stops only after an unchanged final quota probe", async () => {
  const fixture = harness(backoffDecision())
  const key = "session-final-probe-stop"
  fixture.loop.bind(key, fixture.services)
  await fixture.activate(key)
  await fixture.store.write(key, {
    schedulerToken: "wait-1",
    unchangedPolls: 2,
  })

  await fixture.loop.settle(key)

  assert.equal(fixture.calls.quota, 2)
  assert.equal(fixture.calls.send, 0)
  assert.equal(fixture.scheduled.length, 0)
})


test("final quota probe follows a changed run-now decision", async () => {
  const runNow = {
    should_run: true,
    effective_action: "normal_run",
    scheduler_hint: {
      action: "run_now",
      cadence_class: "active_work",
      reset_policy: { reset_token: "run-2" },
    },
  }
  const fixture = harness((probeNumber) =>
    probeNumber === 1 ? backoffDecision() : runNow)
  const key = "session-final-probe-run-now"
  fixture.loop.bind(key, fixture.services)
  await fixture.activate(key)
  await fixture.store.write(key, {
    schedulerToken: "wait-1",
    unchangedPolls: 2,
  })

  await fixture.loop.settle(key)

  assert.equal(fixture.calls.quota, 2)
  assert.equal(fixture.calls.send, 1)
  assert.equal(fixture.scheduled.length, 0)
})


test("failed final quota probe schedules a bounded retry", async () => {
  const fixture = harness((probeNumber) =>
    probeNumber === 2 ? new Error("final probe failed") : backoffDecision())
  const key = "session-final-probe-retry"
  fixture.loop.bind(key, fixture.services)
  await fixture.activate(key)
  await fixture.store.write(key, {
    schedulerToken: "wait-1",
    unchangedPolls: 2,
  })

  await fixture.loop.settle(key)

  assert.equal(fixture.calls.quota, 2)
  assert.equal(fixture.calls.send, 0)
  assert.equal(fixture.scheduled.length, 1)
  assert.equal(fixture.scheduled[0].delayMs, DEFAULT_RETRY_MINUTES * 60_000)
})


test("binding store round-trips through the filesystem and retires cleanly", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "pi-goal-loop-store-"))
  try {
    const store = createBindingStore(root)
    const key = sanitizedKey("session / weird*name")

    assert.equal(await store.read(key), null)
    const written = await store.write(key, { goalId: "goal-store", taskBody: "body" })
    assert.equal(written.schemaVersion, BRIDGE_SCHEMA_VERSION)
    assert.equal(written.sessionKey, key)
    assert.equal(written.goalId, "goal-store")
    assert.equal(written.autoResume, true)

    const read = await store.read(key)
    assert.equal(read.taskBody, "body")
    assert.equal(read.directory, root)

    await store.remove(key)
    assert.equal(await store.read(key), null)
    await store.remove(key)
  } finally {
    await fs.rm(root, { recursive: true, force: true })
  }
})


test("an abort pause survives runtime replacement for a persistent session", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "pi-goal-loop-abort-"))
  try {
    const store = createBindingStore(root)
    const first = harness({ should_run: true, scheduler_hint: { action: "run_now" } })
    first.loop.bind("session-persisted-abort", {
      ...first.services,
      store,
    })
    await activateWithAuthority(first.loop, "session-persisted-abort", {
      goalId: "goal-persisted-abort",
      taskBody: "persisted body",
    })
    await first.loop.interrupt("session-persisted-abort")
    first.loop.dispose()

    const second = harness({ should_run: true, scheduler_hint: { action: "run_now" } })
    second.loop.bind("session-persisted-abort", {
      ...second.services,
      store,
    })
    await second.loop.settle("session-persisted-abort")

    assert.equal(second.calls.quota, 0)
    assert.equal(second.calls.send, 0)
    assert.equal((await store.read("session-persisted-abort")).autoResume, false)
  } finally {
    await fs.rm(root, { recursive: true, force: true })
  }
})


test("dispose during the post-probe read stops all side effects when it returns", async () => {
  const gated = gatedStore()
  const calls = { send: 0, quota: 0, notify: 0 }
  const scheduled = []
  const loop = createGoalLoop({
    quotaProbe: async () => {
      calls.quota += 1
      return { should_run: true, scheduler_hint: { action: "run_now" } }
    },
    sendMessage: () => {
      calls.send += 1
    },
    setTimer: (callback, delayMs) => {
      const timer = { callback, cleared: false, delayMs }
      scheduled.push(timer)
      return timer
    },
    clearTimer: (timer) => {
      timer.cleared = true
    },
  })
  loop.bind("session-race", {
    store: gated.store,
    isIdle: () => true,
    notify: () => {
      calls.notify += 1
    },
  })
  await activateWithAuthority(loop, "session-race", { goalId: "goal-race", taskBody: "old body" })
  const writesBefore = gated.writes.length
  assert.equal(calls.notify, 1) // only the activation notification

  // quota has already returned; the post-probe read is suspended.
  gated.hangPostProbeRead()
  const settled = loop.settle("session-race")
  await gated.postProbeReadStarted
  assert.equal(calls.quota, 1)

  // session_shutdown fires while the post-probe read is still pending.
  loop.dispose()
  gated.releasePostProbeRead()
  await settled

  // The evaluation returns to the epoch guard: no write, no notify, no
  // message, and no timer scheduled past the boundary.
  assert.equal(gated.writes.length, writesBefore)
  assert.equal(calls.notify, 1)
  assert.equal(calls.send, 0)
  assert.equal(scheduled.length, 0)
})


test("ephemeral session identities are unique per extension instance", () => {
  const first = createEphemeralSessionIdentity()
  const second = createEphemeralSessionIdentity()
  assert.notEqual(first.key, second.key)
  assert.notEqual(first.store, second.store)
  assert.match(first.key, /^ephemeral-/)
})


test("ephemeral sessions never inherit the previous run's binding", async () => {
  // Run 1: an ephemeral pi --no-session process binds old-goal into its own
  // in-memory identity.
  const run1 = harness(backoffDecision())
  const identity1 = createEphemeralSessionIdentity()
  run1.loop.bind(identity1.key, {
    store: identity1.store,
    isIdle: () => true,
    notify: () => {},
  })
  await activateWithAuthority(run1.loop, identity1.key, { goalId: "old-goal", taskBody: "old body" })

  // Run 2: a fresh ephemeral process must start empty — no quota probe, no
  // continuation of old-goal, no visible binding — and must activate again.
  const run2 = harness(backoffDecision())
  const identity2 = createEphemeralSessionIdentity()
  run2.loop.bind(identity2.key, {
    store: identity2.store,
    isIdle: () => true,
    notify: () => {},
  })
  await run2.loop.settle(identity2.key)
  assert.equal(run2.calls.quota, 0)
  assert.equal(run2.calls.send, 0)
  assert.equal(await identity2.store.read(identity2.key), null)
})


test("activation during a stale run_now branch prevents stale writes and messages", async () => {
  // Gated store: the first post-activation write (schedulerToken) blocks
  // instead of returning immediately, giving activate() a chance to advance
  // the per-key generation before the old evaluation continues.
  let releaseWrite
  const gate = new Promise((resolve) => {
    releaseWrite = resolve
  })
  const inner = createMemoryBindingStore()
  let writeCalls = 0
  let markWriteBlocked
  const writeBlockedPromise = new Promise((resolve) => {
    markWriteBlocked = resolve
  })
  const gatedStore = {
    async read(key) {
      return inner.read(key)
    },
    async write(key, changes, expected) {
      const index = writeCalls++
      if (index === 1) {
        markWriteBlocked()
        await gate
      }
      return inner.write(key, changes, expected)
    },
    async remove(key) {
      return inner.remove(key)
    },
  }

  const calls = { send: 0, quota: 0, notify: 0, messages: [] }
  const scheduled = []
  const loop = createGoalLoop({
    quotaProbe: async () => {
      calls.quota += 1
      return { should_run: true, scheduler_hint: { action: "run_now" } }
    },
    sendMessage: (msg) => {
      calls.send += 1
      calls.messages.push(msg)
    },
    setTimer: (cb, ms) => {
      const t = { cb, cleared: false, delayMs: ms }
      scheduled.push(t)
      return t
    },
    clearTimer: (t) => {
      t.cleared = true
    },
  })
  loop.bind("session-race", {
    store: gatedStore,
    isIdle: () => true,
    notify: () => {
      calls.notify++
    },
  })

  // Activate old goal (write #0, generation -> 1).
  await activateWithAuthority(loop, "session-race", { goalId: "old-goal", taskBody: "old-body" })
  assert.equal(calls.notify, 1)

  // Start settle; the probe returns run_now and the scheduler write blocks.
  // Wait for the write to start (so activate cannot preempt it).
  const settled = loop.settle("session-race")
  await writeBlockedPromise
  assert.equal(calls.quota, 1)

  // Now the old evaluation's first branch write is in-flight.  Activate a
  // new goal for the same key — this bumps the per-key generation.
  await activateWithAuthority(loop, "session-race", { goalId: "old-goal", taskBody: "new-body" })
  assert.equal(calls.notify, 2)

  // Release the old write and let the evaluation continue.
  releaseWrite()
  await settled

  // The stale evaluation must not write lastInjectedPrompt or send the old
  // task body after the generation moved.
  assert.equal(calls.send, 0)
  const binding = await inner.read("session-race")
  assert.equal(binding.goalId, "old-goal")
  assert.notEqual(binding.lastInjectedPrompt, "old-body")
})


test("activation during a stale terminal write cannot stop the new goal", async () => {
  // The old goal's terminal commit is blocked mid-flight; the same session
  // activates a new goal, then the stale write is released. The store's
  // compare-and-swap must reject the terminal commit so the new binding is
  // not permanently stopped and no stale terminal notification is sent.
  let releaseWrite
  const gate = new Promise((resolve) => {
    releaseWrite = resolve
  })
  const inner = createMemoryBindingStore()
  let writeCalls = 0
  let markWriteBlocked
  const writeBlockedPromise = new Promise((resolve) => {
    markWriteBlocked = resolve
  })
  const gatedStore = {
    async read(key) {
      return inner.read(key)
    },
    async write(key, changes, expected) {
      const index = writeCalls++
      if (index === 1) {
        markWriteBlocked()
        await gate
      }
      return inner.write(key, changes, expected)
    },
    async remove(key) {
      return inner.remove(key)
    },
  }

  const calls = { send: 0, quota: 0, notify: 0 }
  const scheduled = []
  const loop = createGoalLoop({
    quotaProbe: async () => {
      calls.quota += 1
      return terminalDecision()
    },
    sendMessage: () => {
      calls.send += 1
    },
    setTimer: (cb, ms) => {
      const t = { cb, cleared: false, delayMs: ms }
      scheduled.push(t)
      return t
    },
    clearTimer: (t) => {
      t.cleared = true
    },
  })
  loop.bind("session-terminal-race", {
    store: gatedStore,
    isIdle: () => true,
    notify: () => {
      calls.notify++
    },
  })

  await activateWithAuthority(loop, "session-terminal-race", {
    goalId: "old-goal",
    taskBody: "old-body",
  })
  const notifyAfterActivate = calls.notify
  assert.equal(notifyAfterActivate, 1)

  const settled = loop.settle("session-terminal-race")
  await writeBlockedPromise
  assert.equal(calls.quota, 1)

  // Re-activate the same session with a new goal while the terminal commit
  // is still in-flight.
  await activateWithAuthority(loop, "session-terminal-race", {
    goalId: "old-goal",
    taskBody: "new-body",
  })
  assert.equal(calls.notify, notifyAfterActivate + 1)

  releaseWrite()
  await settled

  // The stale terminal commit was rejected: the new goal stays alive, no
  // extra notification is emitted, and no message or timer appears.
  assert.equal(calls.notify, notifyAfterActivate + 1)
  assert.equal(calls.send, 0)
  assert.equal(scheduled.length, 0)
  const binding = await inner.read("session-terminal-race")
  assert.equal(binding.goalId, "old-goal")
  assert.equal(binding.terminal, false)
  assert.equal(binding.autoResume, true)
})


test("activation during a stale wait write prevents a stale timer", async () => {
  let releaseWrite
  const gate = new Promise((resolve) => {
    releaseWrite = resolve
  })
  const inner = createMemoryBindingStore()
  let writeCalls = 0
  let markWriteBlocked
  const writeBlockedPromise = new Promise((resolve) => {
    markWriteBlocked = resolve
  })
  const gatedStore = {
    async read(key) {
      return inner.read(key)
    },
    async write(key, changes, expected) {
      const index = writeCalls++
      if (index === 1) {
        markWriteBlocked()
        await gate
      }
      return inner.write(key, changes, expected)
    },
    async remove(key) {
      return inner.remove(key)
    },
  }

  const calls = { send: 0, quota: 0, notify: 0 }
  const scheduled = []
  const loop = createGoalLoop({
    quotaProbe: async () => {
      calls.quota += 1
      return backoffDecision()
    },
    sendMessage: () => {
      calls.send += 1
    },
    setTimer: (cb, ms) => {
      const t = { cb, cleared: false, delayMs: ms }
      scheduled.push(t)
      return t
    },
    clearTimer: (t) => {
      t.cleared = true
    },
  })
  loop.bind("session-wait-race", {
    store: gatedStore,
    isIdle: () => true,
    notify: () => {
      calls.notify++
    },
  })

  await activateWithAuthority(loop, "session-wait-race", {
    goalId: "old-goal",
    taskBody: "old-body",
  })

  const settled = loop.settle("session-wait-race")
  await writeBlockedPromise
  assert.equal(calls.quota, 1)

  await activateWithAuthority(loop, "session-wait-race", {
    goalId: "old-goal",
    taskBody: "new-body",
  })

  releaseWrite()
  await settled

  // The stale wait commit was rejected: no backoff timer is scheduled for
  // the old goal and the new binding keeps its identity.
  assert.equal(scheduled.length, 0)
  assert.equal(calls.send, 0)
  const binding = await inner.read("session-wait-race")
  assert.equal(binding.goalId, "old-goal")
})


test("file-backed store keeps two long-prefix session keys isolated", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "pi-goal-loop-key-"))
  try {
    const store = createBindingStore(root)
    // Both session files share a >160-char basename prefix and differ only
    // after the old truncation point; the digest must keep the keys apart.
    const shared = "/" + "p".repeat(200) + "/"
    const baseA = "s".repeat(170) + "a"
    const baseB = "s".repeat(170) + "b"
    const keyA = sessionKey(shared + baseA + ".json")
    const keyB = sessionKey(shared + baseB + ".json")
    assert.notEqual(keyA, keyB)

    // The digest suffix must survive filename sanitization, so each key gets
    // its own target file and the bindings never shadow each other.
    await store.write(keyA, { goalId: "goal-a", taskBody: "body-a" })
    assert.equal((await store.read(keyA)).goalId, "goal-a")

    await store.write(keyB, { goalId: "goal-b", taskBody: "body-b" })
    assert.equal((await store.read(keyA)).goalId, "goal-a")
    assert.equal((await store.read(keyB)).goalId, "goal-b")

    // Bidirectional round-trip: removing A leaves B untouched.
    await store.remove(keyA)
    assert.equal(await store.read(keyA), null)
    assert.equal((await store.read(keyB)).goalId, "goal-b")
  } finally {
    await fs.rm(root, { recursive: true, force: true })
  }
})


test("store write failure fails closed with a bounded retry", async () => {
  const inner = createMemoryBindingStore()
  let writeCalls = 0
  const throwStore = {
    async read(key) {
      return inner.read(key)
    },
    async write(key, changes, expected) {
      // Throw on the first evaluation write (activate's write is index 0).
      if (writeCalls++ === 1) throw new Error("disk full")
      return inner.write(key, changes, expected)
    },
    async remove(key) {
      return inner.remove(key)
    },
  }
  const calls = { send: 0, quota: 0, notify: 0 }
  const scheduled = []
  const loop = createGoalLoop({
    quotaProbe: async () => {
      calls.quota += 1
      return { should_run: true, scheduler_hint: { action: "run_now" } }
    },
    sendMessage: () => {
      calls.send += 1
    },
    setTimer: (cb, ms) => {
      const t = { cb, cleared: false, delayMs: ms }
      scheduled.push(t)
      return t
    },
    clearTimer: (t) => {
      t.cleared = true
    },
  })
  loop.bind("session-throw", {
    store: throwStore,
    isIdle: () => true,
    notify: () => {
      calls.notify++
    },
  })

  // activate writes (index 0) — succeeds.
  await activateWithAuthority(loop, "session-throw", { goalId: "goal-throw", taskBody: "body" })

  // settle must not throw; the evaluation catches the store write failure
  // and reschedules instead of propagating.
  await loop.settle("session-throw")

  assert.equal(calls.send, 0)
  assert.ok(scheduled.length > 0, "store write failure must schedule a retry")
})


test("session key stays under the filename sanitization limit", () => {
  const longPath = "/" + "x".repeat(300) + "/" + "y".repeat(300) + ".json"
  const key = sessionKey(longPath)
  assert.ok(key.length < 160)
  assert.match(key, /-[0-9a-f]{16}$/)
})


test("session keys use a collision-resistant digest of the full path", () => {
  const a = sessionKey("/long/common/prefix/session-a.json")
  const b = sessionKey("/long/common/prefix/session-b.json")
  assert.notEqual(a, b)
  assert.match(a, /-/)
  assert.match(b, /-/)
})


test("session keys do not collide on long common prefixes", () => {
  const prefix = "/" + "x".repeat(200) + "/session"
  const a = sessionKey(prefix + "-a.json")
  const b = sessionKey(prefix + "-b.json")
  // Basename labels may be identical after sanitization; the digest
  // distinguishes them.
  assert.notEqual(a, b)
})


test("different session keys never read each other's bindings", async () => {
  const keyA = sessionKey("/workspace/.pi/sessions/session-a.json")
  const keyB = sessionKey("/workspace/.pi/sessions/session-b.json")
  const store = createMemoryBindingStore()
  await store.write(keyA, { goalId: "goal-a", taskBody: "body-a" })
  assert.equal(await store.read(keyB), null)
})


test("memory binding store never shares state across instances", async () => {
  const store = createMemoryBindingStore()
  const key = "ephemeral-session"
  assert.equal(await store.read(key), null)
  await store.write(key, { goalId: "goal-memory" })
  assert.equal((await store.read(key)).goalId, "goal-memory")

  const fresh = createMemoryBindingStore()
  assert.equal(await fresh.read(key), null)

  await store.remove(key)
  assert.equal(await store.read(key), null)
})


test("sanitized key is stable and bounded", () => {
  assert.equal(sanitizedKey("simple"), "simple")
  assert.equal(sanitizedKey(null), "session")
  assert.equal(sanitizedKey("a/b:c d"), "a_b_c_d")
  assert.equal(sanitizedKey("x".repeat(400)).length, 160)
})

test("stale userPrompt write after re-activate cannot pause new goal", async () => {
  // The old goal's userPrompt write is blocked mid-flight; the same session
  // activates a new goal and completes a settle cycle, which creates a new
  // timer. When the old userPrompt write is released, the store's CAS must
  // reject the stale autoResume=false commit so the new binding stays alive
  // and its timer is not cancelled.
  let releaseWrite
  const gate = new Promise((resolve) => {
    releaseWrite = resolve
  })
  const inner = createMemoryBindingStore()
  let writeCalls = 0
  let markWriteBlocked
  const writeBlockedPromise = new Promise((resolve) => {
    markWriteBlocked = resolve
  })
  const gatedStore = {
    async read(key) {
      return inner.read(key)
    },
    async write(key, changes, expected) {
      const index = writeCalls++
      if (index === 1) {
        markWriteBlocked()
        await gate
      }
      return inner.write(key, changes, expected)
    },
    async remove(key) {
      return inner.remove(key)
    },
  }

  const calls = { quota: 0, send: 0, notify: 0 }
  const scheduled = []
  const loop = createGoalLoop({
    quotaProbe: async () => {
      calls.quota += 1
      return runNowDecision("new-body")
    },
    sendMessage: () => {
      calls.send += 1
    },
    setTimer: (cb, ms) => {
      const t = { cb, cleared: false, delayMs: ms }
      scheduled.push(t)
      return t
    },
    clearTimer: (t) => {
      t.cleared = true
    },
  })
  loop.bind("session-user-prompt-race", {
    store: gatedStore,
    isIdle: () => true,
    notify: () => {
      calls.notify++
    },
  })

  await activateWithAuthority(loop, "session-user-prompt-race", {
    goalId: "old-goal",
    taskBody: "old-body",
  })
  assert.equal(calls.notify, 1)

  // userPrompt enters; after reading the old binding, its CAS write is
  // blocked before committing autoResume=false.
  const promptPromise = loop.userPrompt("session-user-prompt-race", "intervention")
  await writeBlockedPromise

  // Re-activate the same session with a new goal while the old userPrompt
  // write is still in-flight, then settle to create a new timer.
  await activateWithAuthority(loop, "session-user-prompt-race", {
    goalId: "old-goal",
    taskBody: "new-body",
  })
  assert.equal(calls.notify, 2)
  const settled = loop.settle("session-user-prompt-race")
  await settled
  assert.equal(calls.quota, 1)
  const activeTimers = scheduled.filter((t) => !t.cleared).length
  assert.equal(activeTimers, 1)

  releaseWrite()
  await promptPromise

  // The stale userPrompt commit was rejected: the new goal stays alive
  // with autoResume=true, no timer was cancelled, and no stale state leaked.
  const binding = await inner.read("session-user-prompt-race")
  assert.equal(binding.goalId, "old-goal")
  assert.equal(binding.autoResume, true)
  assert.equal(scheduled.filter((t) => !t.cleared).length, 1)
})


test("task-lease argv encodes all lifecycle actions with exact flags", () => {
  const binding = taskLeaseBinding()
  const prefix = ["--registry", "registry.json", "--format", "json", "task-lease"]
  const cases = [
    [
      {
        action: "acquire",
        todoId: "todo_alpha",
        idempotencyKey: "turn-1",
        ttlSeconds: 90,
        expectedVersion: 4,
        writeScopes: ["loopx/**", "docs/**"],
      },
      [
        "acquire", "--goal-id", "goal-lease", "--todo-id", "todo_alpha", "--owner", "agent-lease",
        "--idempotency-key", "turn-1", "--ttl-seconds", "90", "--expected-version", "4",
        "--write-scope", "loopx/**", "--write-scope", "docs/**",
      ],
    ],
    [
      {
        action: "renew",
        todoId: "todo_alpha",
        idempotencyKey: "turn-1",
        ttlSeconds: 120,
        expectedVersion: 5,
      },
      [
        "renew", "--goal-id", "goal-lease", "--todo-id", "todo_alpha", "--owner", "agent-lease",
        "--idempotency-key", "turn-1", "--ttl-seconds", "120", "--expected-version", "5",
      ],
    ],
    [
      {
        action: "transfer",
        todoId: "todo_alpha",
        idempotencyKey: "turn-1",
        newOwner: "agent-next",
        newIdempotencyKey: "turn-2",
        ttlSeconds: 180,
        expectedVersion: 6,
      },
      [
        "transfer", "--goal-id", "goal-lease", "--todo-id", "todo_alpha", "--owner", "agent-lease",
        "--idempotency-key", "turn-1", "--new-owner", "agent-next", "--new-idempotency-key", "turn-2",
        "--ttl-seconds", "180", "--expected-version", "6",
      ],
    ],
    [
      { action: "release", todoId: "todo_alpha", idempotencyKey: "turn-1", expectedVersion: 7 },
      [
        "release", "--goal-id", "goal-lease", "--todo-id", "todo_alpha", "--owner", "agent-lease",
        "--idempotency-key", "turn-1", "--expected-version", "7",
      ],
    ],
    [
      { action: "inspect", todoId: "todo_alpha" },
      ["inspect", "--goal-id", "goal-lease", "--todo-id", "todo_alpha"],
    ],
  ]
  for (const [request, suffix] of cases) {
    assert.deepEqual(buildTaskLeaseArgs(binding, request), [...prefix, ...suffix])
  }
})


test("task-lease mutations require session authority and capability before CLI execution", async () => {
  const calls = []
  const runCli = async (...args) => {
    calls.push(args)
    return {
      stdout: JSON.stringify({ ok: true, schema_version: "task_lease_v0", action: "acquire" }),
      returncode: 0,
    }
  }
  const request = { action: "acquire", todoId: "todo_alpha", idempotencyKey: "turn-1" }
  const cases = [
    [null, "missing_goal_binding", taskLeaseAuthority()],
    [taskLeaseBinding({ goalId: "" }), "authority_mismatch", taskLeaseAuthority()],
    [taskLeaseBinding({ terminal: true }), "inactive_goal_binding"],
    [taskLeaseBinding({ agentId: "" }), "authority_mismatch", taskLeaseAuthority()],
    [
      taskLeaseBinding({ availableCapabilities: [] }),
      "capability_not_advertised",
      taskLeaseAuthority({ availableCapabilities: [] }),
    ],
  ]
  for (const [binding, errorCode, authority = taskLeaseAuthority()] of cases) {
    const result = await runPiTaskLease(binding, request, runCli, authority)
    assert.equal(result.ok, false)
    assert.equal(result.error_code, errorCode)
  }
  const missingHostAuthority = await runPiTaskLease(taskLeaseBinding(), request, runCli)
  assert.equal(missingHostAuthority.error_code, "authority_not_bound")
  const authorityMismatch = await runPiTaskLease(
    taskLeaseBinding(),
    { ...request, goalId: "goal-other" },
    runCli,
    taskLeaseAuthority(),
  )
  assert.equal(authorityMismatch.error_code, "authority_mismatch")
  const invalidForm = await runPiTaskLease(
    taskLeaseBinding(),
    { ...request, ttlSeconds: 86401 },
    runCli,
    taskLeaseAuthority(),
  )
  assert.equal(invalidForm.error_code, "invalid_request")
  assert.equal(calls.length, 0)
})


test("inspect is read-only and preserves a compact typed receipt", async () => {
  const calls = []
  const runCli = async (...args) => {
    calls.push(args)
    return {
      stdout: JSON.stringify({
        ok: true,
        schema_version: "task_lease_v0",
        action: "inspect",
        active: false,
        lease: null,
        lease_path: "synthetic-private-lease-path/todo_alpha.json",
      }),
      returncode: 0,
    }
  }
  const result = await runPiTaskLease(
    taskLeaseBinding({ availableCapabilities: [] }),
    { action: "inspect", todoId: "todo_alpha" },
    runCli,
    taskLeaseAuthority({ availableCapabilities: [] }),
  )
  assert.deepEqual(result, {
    ok: true,
    schema_version: "task_lease_v0",
    action: "inspect",
    active: false,
    lease: null,
  })
  assert.deepEqual(calls[0][0], [
    "--registry", "registry.json", "--format", "json", "task-lease", "inspect",
    "--goal-id", "goal-lease", "--todo-id", "todo_alpha",
  ])
  assert.equal(calls[0][1], "project")
})


test("non-zero write-scope and CAS failures stay typed and omit private paths", async () => {
  const conflict = {
    ok: false,
    schema_version: "task_lease_v0",
    action: "acquire",
    error: "write scope overlaps another active task lease",
    error_code: "write_scope_conflict",
    conflicts: [{
      todo_id: "todo_beta",
      owner: "agent-other",
      write_scopes: ["src/**"],
      lease_path: "synthetic-private-lease-path/todo_beta.json",
    }],
    lease_path: "synthetic-private-lease-path/todo_alpha.json",
  }
  const first = await runPiTaskLease(
    taskLeaseBinding(),
    { action: "acquire", todoId: "todo_alpha", idempotencyKey: "turn-1", writeScopes: ["src/**"] },
    async () => ({
      stdout: JSON.stringify({ ok: true, schema_version: "task_lease_v0", action: "acquire" }),
      returncode: 0,
    }),
    taskLeaseAuthority(),
  )
  assert.equal(first.ok, true)
  const second = await runPiTaskLease(
    taskLeaseBinding(),
    { action: "acquire", todoId: "todo_beta", idempotencyKey: "turn-2", writeScopes: ["src/**"] },
    async () => ({ stdout: JSON.stringify(conflict), returncode: 1 }),
    taskLeaseAuthority(),
  )
  assert.equal(second.error_code, "write_scope_conflict")
  assert.deepEqual(second.conflicts, [{ todo_id: "todo_beta", owner: "agent-other", write_scopes: ["src/**"] }])
  assert.equal(JSON.stringify(second).includes("lease_path"), false)

  const cas = await runPiTaskLease(
    taskLeaseBinding(),
    { action: "renew", todoId: "todo_alpha", idempotencyKey: "turn-1", expectedVersion: 8 },
    async () => ({
      stdout: JSON.stringify({
        ok: false,
        schema_version: "task_lease_v0",
        action: "renew",
        error: "lease owner or idempotency key mismatch",
        error_code: "lease_cas_mismatch",
        expected_version: 8,
        actual_version: 9,
        lease_path: "synthetic-private-lease-path/todo_alpha.json",
      }),
      returncode: 1,
    }),
    taskLeaseAuthority(),
  )
  assert.equal(cas.error_code, "lease_cas_mismatch")
  assert.equal(cas.expected_version, 8)
  assert.equal(cas.actual_version, 9)
  assert.equal(JSON.stringify(cas).includes("synthetic-private-lease-path"), false)
})


test("task-lease transport and malformed payloads fail closed", async () => {
  const request = { action: "inspect", todoId: "todo_alpha" }
  const cases = [
    [async () => { throw new Error("socket unavailable") }, "transport_error"],
    [async () => ({ stdout: "not-json", returncode: 0 }), "protocol_error"],
    [async () => ({
      stdout: JSON.stringify({ ok: true, schema_version: "task_lease_v0", action: "renew" }),
      returncode: 0,
    }), "protocol_error"],
  ]
  for (const [runCli, errorCode] of cases) {
    const result = await runPiTaskLease(taskLeaseBinding(), request, runCli, taskLeaseAuthority())
    assert.equal(result.ok, false)
    assert.equal(result.error_code, errorCode)
  }
})


test("host-bound Pi authority rejects agent and capability rebinding before lease transport", async () => {
  const store = createMemoryBindingStore()
  const loop = createGoalLoop({
    quotaProbe: async () => ({ should_run: false }),
    sendMessage: () => {},
    setTimer: () => null,
    clearTimer: () => {},
  })
  const authorityA = {
    ...testAuthority("session-authority", "goal-a"),
    availableCapabilities: [],
  }
  loop.bind("session-authority", {
    store,
    authority: authorityA,
    isIdle: () => true,
    notify: () => {},
  })
  await loop.activate("session-authority", {
    activationToken: authorityA.token,
    goalId: authorityA.goalId,
    agentId: authorityA.agentId,
    registryPath: authorityA.registryPath,
    availableCapabilities: authorityA.availableCapabilities,
    taskBody: "agent A body",
  })

  const calls = []
  let activationFailure = null
  try {
    await loop.activate("session-authority", {
      activationToken: authorityA.token,
      goalId: "goal-b",
      agentId: "agent-b",
      registryPath: "/workspace/other-registry.json",
      availableCapabilities: ["task_lease_v0"],
      taskBody: "spoofed agent B body",
    })
  } catch (error) {
    activationFailure = error
  }
  assert.equal(activationFailure?.code, "authority_mismatch")
  const binding = await store.read("session-authority")
  assert.equal(binding.goalId, "goal-a")
  assert.equal(binding.agentId, "agent-test")
  assert.deepEqual(binding.availableCapabilities, [])

  // A caller must not continue into the lease facade after the authority
  // failure; the transport is therefore never given the spoofed owner.
  if (!activationFailure) {
    await runPiTaskLease(binding, {
      action: "acquire",
      todoId: "todo_beta",
      idempotencyKey: "spoofed-turn",
    }, async (...args) => {
      calls.push(args)
      return { stdout: "", returncode: 1 }
    }, authorityA)
  }
  assert.equal(calls.length, 0)

  const tampered = { ...(await store.read("session-authority")), agentId: "agent-b" }
  const tamperedResult = await runPiTaskLease(
    tampered,
    { action: "acquire", todoId: "todo_beta", idempotencyKey: "spoofed-turn" },
    async (...args) => {
      calls.push(args)
      return { stdout: "", returncode: 1 }
    },
    authorityA,
  )
  assert.equal(tamperedResult.error_code, "authority_mismatch")
  assert.equal(calls.length, 0)
})

test("piThreadId derives a stable identity from the Pi session file", () => {
  // The Pi adapter must supply a thread id itself because the CLI's
  // HOST_THREAD_ID_ENV has no "pi" entry and never reads PI_SESSION_ID.
  assert.equal(
    piThreadId("/home/example/.pi/agent/sessions/project/2026-10-05T06-37-44-101Z_abc.jsonl"),
    "2026-10-05T06-37-44-101Z_abc",
  )
  // The same session resolves to the same id across calls.
  const file = "/tmp/sessions/2026-01-02T00-00-00-000Z_deadbeef.jsonl"
  assert.equal(piThreadId(file), piThreadId(file))
  // Distinct sessions never collide.
  assert.notEqual(piThreadId("/tmp/s/one.jsonl"), piThreadId("/tmp/s/two.jsonl"))
  // Anything outside the accepted character set is replaced, not dropped.
  assert.equal(piThreadId("/tmp/s/sess name*weird.jsonl"), "sess-name-weird")
  // A --no-session run has no file and must keep the selection-packet flow.
  assert.equal(piThreadId(""), null)
  assert.equal(piThreadId(undefined), null)
  assert.equal(piThreadId("/tmp/s/"), null)
})

test("soleThreadBindingCandidate binds only a single unambiguous lane", () => {
  const packetWith = (choices) => ({
    goal_id: "smoke-goal",
    command_pack: {
      host_loop_activation: {
        activation_allowed: false,
        activation_state: "thread_binding_selection_required",
        identity_selection_gate: { choices },
      },
    },
  })

  assert.deepEqual(
    soleThreadBindingCandidate(packetWith([{ agent_id: "smoke-agent" }])),
    { goalId: "smoke-goal", agentId: "smoke-agent" },
  )
  // The top-level activation block is read as well as the command_pack one.
  assert.deepEqual(
    soleThreadBindingCandidate({
      goal_id: "smoke-goal",
      host_loop_activation: { identity_contract: { choices: [{ agent_id: "a" }] } },
    }),
    { goalId: "smoke-goal", agentId: "a" },
  )
  // No candidates, several lanes, or a missing goal stay a user decision.
  assert.equal(soleThreadBindingCandidate(packetWith([])), null)
  assert.equal(soleThreadBindingCandidate(packetWith([{ agent_id: "a" }, { agent_id: "b" }])), null)
  assert.equal(soleThreadBindingCandidate({ command_pack: {} }), null)
  assert.equal(soleThreadBindingCandidate(packetWith([{ agent_id: "   " }])), null)
  // A single candidate with no resolvable goal id is not actionable either.
  assert.equal(soleThreadBindingCandidate({ command_pack: { goal_id: "" } }), null)
})
