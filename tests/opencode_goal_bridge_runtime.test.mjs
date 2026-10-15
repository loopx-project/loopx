import assert from "node:assert/strict"
import test from "node:test"
import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"

import { createLoopxGoalPlugin, createFileBindingStore } from "../loopx/opencode_goal_mode/goal-bridge-runtime.mjs"


function fakeTool(spec) {
  return spec
}
const optional = () => ({ optional })
fakeTool.schema = {
  string: optional,
  number: optional,
  array: () => ({ optional }),
  object: () => ({}),
  enum: () => ({}),
}


function memoryLockStore() {
  return {
    async read() {
      return null
    },
    async acquire() {
      return {
        acquired: true,
        holder: { pid: process.pid },
        release: async () => {},
      }
    },
  }
}


function memoryBindingStore() {
  const bindings = new Map()
  return {
    bindings,
    async read(sessionID) {
      return bindings.get(sessionID) || null
    },
    async write(sessionID, binding) {
      const value = { schemaVersion: "loopx_opencode_goal_bridge_v0", sessionID, ...binding }
      bindings.set(sessionID, value)
      return value
    },
    async remove(sessionID) {
      bindings.delete(sessionID)
    },
  }
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


function harness(initialDecision, { lockStore = memoryLockStore(), store = memoryBindingStore(), resumeResult = null, beforeHostStop = async () => {} } = {}) {
  const calls = { chat: 0, event: 0, complete: 0, dispose: 0, quota: 0, resume: 0, pause: 0 }
  const scheduled = []
  let decision = initialDecision
  const GoalPlugin = async (_context, options) => ({
    config: async () => {},
    "command.execute.before": beforeHostStop,
    "chat.message": async () => {
      calls.chat += 1
    },
    event: async () => {
      calls.event += 1
    },
    tool: {
      goal_set: fakeTool({
        args: {},
        execute: async () => JSON.stringify({ version: 1, operation: "set", ok: true }),
      }),
      goal_resume: fakeTool({
        args: {},
        execute: async () => {
          calls.resume += 1
          if (resumeResult) return resumeResult
          return JSON.stringify({ version: 1, operation: "resume", ok: true })
        },
      }),
      goal_pause: fakeTool({
        args: {},
        execute: async () => {
          calls.pause += 1
          await beforeHostStop()
          return JSON.stringify({ version: 1, operation: "pause", ok: true })
        },
      }),
      goal_block: fakeTool({
        args: {},
        execute: async () => { await beforeHostStop(); return JSON.stringify({ version: 1, operation: "block", ok: true }) },
      }),
      goal_complete: fakeTool({
        args: {},
        execute: async (_args, context) => {
          calls.complete += 1
          const verdict = await options.auditor({ sessionID: context.sessionID })
          return JSON.stringify({
            version: 1,
            operation: "complete",
            ok: verdict.approved,
            ...(verdict.approved ? {} : { error: "completion_rejected" }),
          })
        },
      }),
      set_goal: fakeTool({
        args: {},
        execute: async () => "New active goal: replacement task",
      }),
      update_goal: fakeTool({
        args: {},
        execute: async (args) => {
          if (["paused", "blocked"].includes(args.status)) await beforeHostStop()
          if (args.status === "blocked") return "Goal marked blocked."
          if (args.status === "paused") return "Goal paused."
          if (args.status === "resumed") return "Goal resumed with fresh limits."
          if (args.status === "complete") return "Goal marked complete and archived."
          return `Objective updated: ${args.objective}`
        },
      }),
      clear_goal: fakeTool({
        args: {},
        execute: async () => "Goal cleared.",
      }),
    },
    dispose: async () => {
      calls.dispose += 1
    },
  })
  const plugin = createLoopxGoalPlugin({
    GoalPlugin,
    tool: fakeTool,
    bindingStore: store,
    lockStore,
    quotaProbe: async () => {
      calls.quota += 1
      if (decision instanceof Error) throw decision
      return decision
    },
    setTimer: (callback, delay) => {
      const timer = { callback, cleared: false, delay, unref() {} }
      scheduled.push(timer)
      return timer
    },
    clearTimer: (timer) => {
      timer.cleared = true
    },
  })
  return {
    calls,
    plugin,
    scheduled,
    store,
    setDecision(value) {
      decision = value
    },
  }
}


test("suppresses the OpenCode 1.18 command reflection that pauses a new goal", async () => {
  const fixture = harness({ should_run: true, scheduler_hint: { action: "run_now" } })
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  await hooks["command.execute.before"](
    { command: "goal", sessionID: "session-command", arguments: "ship the task" },
    { parts: [] },
  )
  await hooks["chat.message"](
    { sessionID: "session-command" },
    { parts: [{ type: "text", text: "ship the task" }] },
  )
  assert.equal(fixture.calls.chat, 0)
})


test("gates active and quiet idle turns through LoopX quota", async () => {
  const fixture = harness({ should_run: true, scheduler_hint: { action: "run_now" } })
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  await hooks.tool.loopx_goal_activate.execute(
    { goalId: "goal-1", objective: "LoopX task body" },
    { sessionID: "session-idle" },
  )
  await hooks.event({ event: { type: "session.idle", properties: { sessionID: "session-idle" } } })
  assert.equal(fixture.calls.event, 1)

  fixture.setDecision({
    should_run: false,
    scheduler_hint: {
      action: "backoff_waiting_for_user",
      reset_policy: { reset_token: "wait-1" },
      unchanged_poll: {
        local_scheduler: {
          recommended_interval_minutes: 3,
          example_progression_minutes: [3, 6, 12],
          unchanged_poll_limit: 3,
        },
      },
    },
  })
  await hooks.event({ event: { type: "session.idle", properties: { sessionID: "session-idle" } } })
  assert.equal(fixture.calls.event, 1)
  assert.equal(fixture.scheduled.at(-1).delay, 180_000)
})


test("fails closed and schedules a bounded retry after a quota network timeout", async () => {
  const fixture = harness(new Error("network timeout"))
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  await hooks.tool.loopx_goal_activate.execute(
    { goalId: "goal-timeout", objective: "LoopX task body" },
    { sessionID: "session-timeout" },
  )

  await hooks.event({
    event: { type: "session.idle", properties: { sessionID: "session-timeout" } },
  })

  assert.equal(fixture.calls.quota, 1)
  assert.equal(fixture.calls.event, 0)
  assert.equal(fixture.scheduled.length, 1)
  assert.equal(fixture.scheduled[0].delay, 180_000)
  assert.equal(fixture.scheduled[0].cleared, false)
})


test("fails closed when the session binding cannot be read", async () => {
  const fixture = harness({ should_run: true, scheduler_hint: { action: "run_now" } })
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  await hooks.tool.loopx_goal_activate.execute(
    { goalId: "goal-binding-error", objective: "LoopX task body" },
    { sessionID: "session-binding-error" },
  )
  fixture.store.read = async () => {
    const error = new Error("binding filesystem unavailable")
    error.code = "EACCES"
    throw error
  }

  await hooks.event({
    event: { type: "session.idle", properties: { sessionID: "session-binding-error" } },
  })

  assert.equal(fixture.calls.quota, 0)
  assert.equal(fixture.calls.event, 0)
  assert.equal(fixture.scheduled.length, 0)
  assert.equal(fixture.store.bindings.has("session-binding-error"), true)
})


test("keeps scheduled quota timers isolated between sessions", async () => {
  const fixture = harness({
    should_run: false,
    scheduler_hint: {
      action: "backoff_waiting_for_user",
      reset_policy: { reset_token: "wait-multi" },
      unchanged_poll: {
        local_scheduler: {
          recommended_interval_minutes: 3,
          unchanged_poll_limit: 3,
        },
      },
    },
  })
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  for (const sessionID of ["session-a", "session-b"]) {
    await hooks.tool.loopx_goal_activate.execute(
      { goalId: `goal-${sessionID}`, objective: "LoopX task body" },
      { sessionID },
    )
    await hooks.event({ event: { type: "session.idle", properties: { sessionID } } })
  }

  assert.equal(fixture.scheduled.length, 2)
  assert.equal(fixture.scheduled[0].cleared, false)
  assert.equal(fixture.scheduled[1].cleared, false)

  fixture.setDecision({ should_run: true, scheduler_hint: { action: "run_now" } })
  await hooks.event({
    event: { type: "session.idle", properties: { sessionID: "session-a" } },
  })

  assert.equal(fixture.scheduled[0].cleared, true)
  assert.equal(fixture.scheduled[1].cleared, false)
  assert.equal(fixture.calls.event, 1)
})


test("replacing the host goal cancels the old LoopX timer and binding", async () => {
  const fixture = harness({
    should_run: false,
    scheduler_hint: {
      action: "backoff_waiting_for_user",
      unchanged_poll: {
        local_scheduler: { recommended_interval_minutes: 3, unchanged_poll_limit: 3 },
      },
    },
  })
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  await hooks.tool.loopx_goal_activate.execute(
    { goalId: "goal-replaced", objective: "LoopX task body" },
    { sessionID: "session-replaced" },
  )
  await hooks.event({
    event: { type: "session.idle", properties: { sessionID: "session-replaced" } },
  })
  assert.equal(fixture.scheduled.length, 1)

  await hooks.tool.goal_set.execute(
    { objective: "ordinary host goal" },
    { sessionID: "session-replaced" },
  )

  assert.equal(fixture.scheduled[0].cleared, true)
  assert.equal(await fixture.store.read("session-replaced"), null)
})


test("legacy goal replacement and objective edits detach the LoopX binding", async () => {
  for (const [toolName, args] of [
    ["set_goal", { objective: "legacy replacement" }],
    ["update_goal", { objective: "legacy objective edit" }],
  ]) {
    const fixture = harness({ should_run: true, scheduler_hint: { action: "run_now" } })
    const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
    const sessionID = `session-${toolName}`
    await hooks.tool.loopx_goal_activate.execute(
      { goalId: `goal-${toolName}`, objective: "LoopX task body" },
      { sessionID },
    )

    await hooks.tool[toolName].execute(args, { sessionID })

    assert.equal(await fixture.store.read(sessionID), null)
  }
})


test("canonical pause and block tools stop scheduled LoopX continuation", async () => {
  for (const toolName of ["goal_pause", "goal_block"]) {
    const fixture = harness({
      should_run: false,
      scheduler_hint: {
        action: "backoff_waiting_for_user",
        unchanged_poll: {
          local_scheduler: { recommended_interval_minutes: 3, unchanged_poll_limit: 3 },
        },
      },
    })
    const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
    const sessionID = `session-${toolName}`
    await hooks.tool.loopx_goal_activate.execute(
      { goalId: `goal-${toolName}`, objective: "LoopX task body" },
      { sessionID },
    )
    await hooks.event({ event: { type: "session.idle", properties: { sessionID } } })

    await hooks.tool[toolName].execute({}, { sessionID })

    assert.equal(fixture.scheduled[0].cleared, true)
    assert.equal((await fixture.store.read(sessionID)).autoResume, false)
  }
})


test("canonical resume re-enables quota without a duplicate resume probe", async () => {
  const fixture = harness({ should_run: true, scheduler_hint: { action: "run_now" } })
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  const sessionID = "session-canonical-resume"
  await hooks.tool.loopx_goal_activate.execute(
    { goalId: "goal-canonical-resume", objective: "LoopX task body" },
    { sessionID },
  )
  await hooks.tool.goal_pause.execute({}, { sessionID })

  await hooks.tool.goal_resume.execute({}, { sessionID })
  await hooks.event({ event: { type: "session.idle", properties: { sessionID } } })

  assert.equal((await fixture.store.read(sessionID)).autoResume, true)
  assert.equal(fixture.calls.resume, 1)
  assert.equal(fixture.calls.event, 1)
})


test("legacy status updates preserve pause resume and completion semantics", async () => {
  const fixture = harness({ should_run: true, scheduler_hint: { action: "run_now" } })
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  const sessionID = "session-legacy-status"
  await hooks.tool.loopx_goal_activate.execute(
    { goalId: "goal-legacy-status", objective: "LoopX task body" },
    { sessionID },
  )

  await hooks.tool.update_goal.execute({ status: "paused" }, { sessionID })
  assert.equal((await fixture.store.read(sessionID)).autoResume, false)
  await hooks.tool.update_goal.execute({ status: "resumed" }, { sessionID })
  assert.equal((await fixture.store.read(sessionID)).autoResume, true)
  await hooks.tool.update_goal.execute(
    { status: "blocked", blocker: "waiting for external approval" },
    { sessionID },
  )
  assert.equal((await fixture.store.read(sessionID)).autoResume, false)
  await hooks.tool.update_goal.execute({ status: "resumed" }, { sessionID })
  assert.equal((await fixture.store.read(sessionID)).autoResume, true)
  await hooks.tool.update_goal.execute({ status: "complete" }, { sessionID })
  assert.equal(await fixture.store.read(sessionID), null)
})


test("legacy objective edits detach even when combined with a status update", async () => {
  for (const status of ["paused", "blocked", "resumed"]) {
    const fixture = harness({ should_run: true, scheduler_hint: { action: "run_now" } })
    const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
    const sessionID = `session-objective-${status}`
    await hooks.tool.loopx_goal_activate.execute(
      { goalId: `goal-objective-${status}`, objective: "LoopX task body" },
      { sessionID },
    )

    await hooks.tool.update_goal.execute(
      { objective: "new host objective", status },
      { sessionID },
    )

    assert.equal(await fixture.store.read(sessionID), null)
  }
})


test("disposal clears every active session timer before disposing the base plugin", async () => {
  const fixture = harness({
    should_run: false,
    scheduler_hint: {
      action: "backoff_waiting_for_user",
      reset_policy: { reset_token: "wait-dispose" },
      unchanged_poll: {
        local_scheduler: {
          recommended_interval_minutes: 3,
          unchanged_poll_limit: 3,
        },
      },
    },
  })
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  for (const sessionID of ["session-dispose-a", "session-dispose-b"]) {
    await hooks.tool.loopx_goal_activate.execute(
      { goalId: `goal-${sessionID}`, objective: "LoopX task body" },
      { sessionID },
    )
    await hooks.event({ event: { type: "session.idle", properties: { sessionID } } })
  }

  await hooks.dispose()

  assert.equal(fixture.scheduled.length, 2)
  assert.equal(fixture.scheduled.every((timer) => timer.cleared), true)
  assert.equal(fixture.calls.dispose, 1)
})


test("disposal prevents an in-flight quota decision from continuing the session", async () => {
  let releaseDecision
  const pendingDecision = new Promise((resolve) => {
    releaseDecision = resolve
  })
  const fixture = harness(pendingDecision)
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  await hooks.tool.loopx_goal_activate.execute(
    { goalId: "goal-dispose-in-flight", objective: "LoopX task body" },
    { sessionID: "session-dispose-in-flight" },
  )
  const idle = hooks.event({
    event: { type: "session.idle", properties: { sessionID: "session-dispose-in-flight" } },
  })
  for (let index = 0; index < 5 && fixture.calls.quota === 0; index += 1) {
    await Promise.resolve()
  }
  assert.equal(fixture.calls.quota, 1)

  await hooks.dispose()
  releaseDecision({ should_run: true, scheduler_hint: { action: "run_now" } })
  await idle

  assert.equal(fixture.calls.event, 0)
  assert.equal(fixture.scheduled.length, 0)
  assert.equal(fixture.calls.dispose, 1)
})


test("coalesces concurrent idle events into one quota decision", async () => {
  let releaseDecision
  const pendingDecision = new Promise((resolve) => {
    releaseDecision = resolve
  })
  const fixture = harness(pendingDecision)
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  await hooks.tool.loopx_goal_activate.execute(
    { goalId: "goal-concurrent", objective: "LoopX task body" },
    { sessionID: "session-concurrent" },
  )
  const first = hooks.event({
    event: { type: "session.idle", properties: { sessionID: "session-concurrent" } },
  })
  const second = hooks.event({
    event: {
      type: "session.status",
      properties: { sessionID: "session-concurrent", status: { type: "idle" } },
    },
  })
  releaseDecision({ should_run: true, scheduler_hint: { action: "run_now" } })
  await Promise.all([first, second])
  assert.equal(fixture.calls.quota, 1)
  assert.equal(fixture.calls.event, 1)
})


test("continues after the user intervenes during a quota probe", async () => {
  let releaseDecision
  const pendingDecision = new Promise((resolve) => {
    releaseDecision = resolve
  })
  const fixture = harness(pendingDecision)
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  await hooks.tool.loopx_goal_activate.execute(
    { goalId: "goal-intervention", objective: "LoopX task body" },
    { sessionID: "session-intervention" },
  )
  const idle = hooks.event({
    event: { type: "session.idle", properties: { sessionID: "session-intervention" } },
  })
  await hooks["chat.message"](
    { sessionID: "session-intervention" },
    { parts: [{ type: "text", text: "change direction" }] },
  )
  const binding = await fixture.store.read("session-intervention")
  assert.equal(binding.autoResume, true)
  assert.equal(binding.userMessagePending, true)
  releaseDecision({ should_run: true, scheduler_hint: { action: "run_now" } })
  await idle
  assert.equal(fixture.calls.event, 1)
  assert.equal(fixture.calls.resume, 1)
  assert.equal((await fixture.store.read("session-intervention")).userMessagePending, false)
})


test("validated closure stops timers and subsequent idle events cannot restart it", async () => {
  const fixture = harness(terminalDecision())
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  await hooks.tool.loopx_goal_activate.execute(
    { goalId: "goal-terminal", objective: "LoopX task body" },
    { sessionID: "session-terminal" },
  )
  const idle = { event: { type: "session.idle", properties: { sessionID: "session-terminal" } } }
  await hooks.event(idle)
  assert.equal(fixture.calls.complete, 0)
  assert.equal(fixture.calls.event, 0)
  assert.equal(fixture.calls.pause, 1)
  assert.equal((await fixture.store.read("session-terminal")).autoResume, false)
  assert.equal(fixture.scheduled.length, 0)
  fixture.setDecision({ should_run: true, scheduler_hint: { action: "run_now" } })
  await hooks.event(idle)
  assert.equal(fixture.calls.event, 0)
  assert.equal(fixture.calls.quota, 1)
})


test("pauses the OpenCode goal visibly when the unchanged-poll limit stops the timer", async () => {
  const fixture = harness({
    should_run: false,
    scheduler_hint: {
      action: "backoff_waiting_for_user",
      reset_policy: { reset_token: "stop-1" },
      unchanged_poll: {
        local_scheduler: {
          recommended_interval_minutes: 3,
          example_progression_minutes: [3, 6],
          unchanged_poll_limit: 1,
        },
      },
    },
  })
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  await hooks.tool.loopx_goal_activate.execute(
    { goalId: "goal-stop", objective: "LoopX task body" },
    { sessionID: "session-stop" },
  )
  const store = fixture.store
  await store.write("session-stop", {
    ...(await store.read("session-stop")),
    schedulerToken: "stop-1",
    unchangedPolls: 1,
  })
  await hooks.event({
    event: { type: "session.idle", properties: { sessionID: "session-stop" } },
  })
  const binding = await store.read("session-stop")
  assert.equal(binding.autoResume, false)
  assert.equal(binding.lastPausedReason, "unchanged_poll_limit")
  assert.equal(fixture.calls.pause, 1)
  assert.equal(fixture.calls.event, 0)
  assert.equal(fixture.scheduled.length, 0)
})


test("backs off quota probe retries with a bounded exponential ladder", async () => {
  const fixture = harness(new Error("network timeout"))
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  await hooks.tool.loopx_goal_activate.execute(
    { goalId: "goal-backoff", objective: "LoopX task body" },
    { sessionID: "session-backoff" },
  )
  const idle = () =>
    hooks.event({
      event: { type: "session.idle", properties: { sessionID: "session-backoff" } },
    })
  await idle()
  assert.equal(fixture.scheduled.at(-1).delay, 180_000)
  await idle()
  assert.equal(fixture.scheduled.at(-1).delay, 360_000)
  assert.equal((await fixture.store.read("session-backoff")).probeRetryCount, 2)
  fixture.setDecision({ should_run: true, scheduler_hint: { action: "run_now" } })
  await idle()
  assert.equal((await fixture.store.read("session-backoff")).probeRetryCount, 0)
})


test("refuses to evaluate while another process holds the session lock", async () => {
  const heldLock = {
    async acquire() {
      return { acquired: false, holder: { pid: process.pid } }
    },
  }
  const fixture = harness(
    { should_run: true, scheduler_hint: { action: "run_now" } },
    { lockStore: heldLock },
  )
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  await hooks.tool.loopx_goal_activate.execute(
    { goalId: "goal-locked", objective: "LoopX task body" },
    { sessionID: "session-locked" },
  )
  await hooks.event({
    event: { type: "session.idle", properties: { sessionID: "session-locked" } },
  })
  assert.equal(fixture.calls.quota, 0)
  assert.equal(fixture.calls.event, 0)
  assert.equal(fixture.scheduled.length, 1)
  assert.equal(fixture.scheduled[0].delay, 180_000)
})


test("user messages keep the loop running and clear after the resume probe", async () => {
  const fixture = harness({ should_run: true, scheduler_hint: { action: "run_now" } })
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  await hooks.tool.loopx_goal_activate.execute(
    { goalId: "goal-user-msg", objective: "LoopX task body" },
    { sessionID: "session-user-msg" },
  )
  await hooks["chat.message"](
    { sessionID: "session-user-msg" },
    { parts: [{ type: "text", text: "are you done yet?" }] },
  )
  const afterMessage = await fixture.store.read("session-user-msg")
  assert.equal(afterMessage.autoResume, true)
  assert.equal(afterMessage.userMessagePending, true)
  await hooks.event({
    event: { type: "session.idle", properties: { sessionID: "session-user-msg" } },
  })
  const afterIdle = await fixture.store.read("session-user-msg")
  assert.equal(afterIdle.autoResume, true)
  assert.equal(afterIdle.userMessagePending, false)
  assert.equal(afterIdle.phase, "working")
})


test("probeLoopxQuota reads a denied decision that exits non-zero", async () => {
  const { probeLoopxQuota } = await import("../loopx/opencode_goal_mode/goal-bridge-runtime.mjs")
  const denied = JSON.stringify({
    ok: false,
    should_run: false,
    effective_action: "operator_gate_notify",
    scheduler_hint: { unchanged_poll: { local_scheduler: null } },
  })
  const execFileImpl = async () => {
    const error = new Error("Command failed: loopx ...")
    error.stdout = denied
    throw error
  }
  const decision = await probeLoopxQuota(
    { goalId: "goal-denied", directory: "/workspace" },
    { directory: "/workspace", execFileImpl },
  )
  assert.equal(decision.should_run, false)
  assert.equal(decision.effective_action, "operator_gate_notify")
})


test("probeLoopxQuota fails closed when a non-zero exit carries no payload", async () => {
  const { probeLoopxQuota } = await import("../loopx/opencode_goal_mode/goal-bridge-runtime.mjs")
  const execFileImpl = async () => {
    const error = new Error("Command failed: loopx ...")
    error.stdout = ""
    throw error
  }
  await assert.rejects(
    probeLoopxQuota(
      { goalId: "goal-crash", directory: "/workspace" },
      { directory: "/workspace", execFileImpl },
    ),
    /Command failed/,
  )
})

test("ordinary user input cannot resume a host before Core admission", async () => {
  const fixture = harness({ should_run: false, scheduler_hint: { action: "stop_until_explicit_resume" } })
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  await hooks.tool.loopx_goal_activate.execute({ goalId: "goal-paused", objective: "Task" }, { sessionID: "paused" })
  await hooks["chat.message"]({ sessionID: "paused" }, { parts: [{ type: "text", text: "How is it going?" }] })
  const before = fixture.calls.resume
  await hooks.event({ event: { type: "session.idle", properties: { sessionID: "paused" } } })
  assert.equal(fixture.calls.resume, before)
  assert.equal((await fixture.store.read("paused")).autoResume, false)
  fixture.setDecision({ should_run: true, scheduler_hint: { action: "run_now" } })
  await hooks.event({ event: { type: "session.idle", properties: { sessionID: "paused" } } })
  assert.equal(fixture.calls.quota, 1)
})

test("explicit pause and resume discard the earlier in-flight admission", async () => {
  let release
  const decision = new Promise(resolve => { release = resolve })
  // The injected probe receives the same asynchronous result as the real CLI.
  const fixture = harness(decision)
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  await hooks.tool.loopx_goal_activate.execute({ goalId: "race", objective: "Task" }, { sessionID: "race" })
  const idle = { event: { type: "session.idle", properties: { sessionID: "race" } } }
  const pending = hooks.event(idle)
  while (!fixture.calls.quota) await new Promise(resolve => setImmediate(resolve))
  await hooks.tool.goal_pause.execute({}, { sessionID: "race" })
  await hooks.tool.goal_resume.execute({}, { sessionID: "race" })
  release({ should_run: true, scheduler_hint: { action: "run_now" } })
  await pending
  assert.equal(fixture.calls.event, 0)
  fixture.setDecision({ should_run: true, scheduler_hint: { action: "run_now" } })
  await hooks.event(idle)
  assert.equal(fixture.calls.event, 1)
})

test("permission denial stays paused through ordinary chat and idle events", async () => {
  const fixture = harness({ should_run: true, scheduler_hint: { action: "run_now" } })
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  await hooks.tool.loopx_goal_activate.execute({ goalId: "deny", objective: "Task" }, { sessionID: "deny" })
  await hooks.event({ event: { type: "permission.replied", properties: { sessionID: "deny", reply: "reject" } } })
  await hooks["chat.message"]({ sessionID: "deny" }, { parts: [{ type: "text", text: "Status?" }] })
  const resumed = fixture.calls.resume
  await hooks.event({ event: { type: "session.idle", properties: { sessionID: "deny" } } })
  assert.equal(fixture.calls.quota, 0)
  assert.equal(fixture.calls.resume, resumed)
  assert.equal((await fixture.store.read("deny")).autoResume, false)
})


test("unconfirmed native resume does not forward the continuation event", async () => {
  const fixture = harness({ should_run: true, scheduler_hint: { action: "run_now" } },
    { resumeResult: JSON.stringify({ ok: false, error: "host_unavailable" }) })
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  await hooks.tool.loopx_goal_activate.execute({ goalId: "resume-failed", objective: "Task" }, { sessionID: "failed" })
  await hooks["chat.message"]({ sessionID: "failed" }, { parts: [{ type: "text", text: "Status?" }] })
  await hooks.event({ event: { type: "session.idle", properties: { sessionID: "failed" } } })
  assert.equal(fixture.calls.event, 0)
  assert.equal((await fixture.store.read("failed")).autoResume, false)
})

for (const operation of ["goal_pause", "goal_block", "legacy_pause", "command_pause"]) {
  test(`${operation} fences admission before awaiting the native stop`, async () => {
    let releaseProbe, releaseStop, stopEntered
    const probe = new Promise(resolve => { releaseProbe = resolve })
    const entered = new Promise(resolve => { stopEntered = resolve })
    const heldStop = new Promise(resolve => { releaseStop = resolve })
    const fixture = harness(probe, { beforeHostStop: async () => { stopEntered(); await heldStop } })
    const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
    await hooks.tool.loopx_goal_activate.execute({ goalId: "slow-stop", objective: "Task" }, { sessionID: "slow" })
    const idle = hooks.event({ event: { type: "session.idle", properties: { sessionID: "slow" } } })
    while (!fixture.calls.quota) await new Promise(resolve => setImmediate(resolve))
    const stopping = operation === "command_pause"
      ? hooks["command.execute.before"]({ command: "goal", sessionID: "slow", arguments: "pause" }, {})
      : hooks.tool[operation === "legacy_pause" ? "update_goal" : operation].execute(
          operation === "legacy_pause" ? { status: "paused" } : {}, { sessionID: "slow" })
    await entered
    releaseProbe({ should_run: true, scheduler_hint: { action: "run_now" } })
    await idle
    try {
      assert.equal(fixture.calls.event, 0)
      assert.equal((await fixture.store.read("slow")).autoResume, false)
    } finally {
      releaseStop()
      await stopping
      await hooks.dispose()
    }
  })
}

test("native pause failure keeps local continuation disarmed without claiming host success", async () => {
  const fixture = harness({ should_run: true, scheduler_hint: { action: "run_now" } },
    { beforeHostStop: async () => { throw new Error("native pause unavailable") } })
  const hooks = await fixture.plugin({ directory: "/workspace", client: {} })
  await hooks.tool.loopx_goal_activate.execute({ goalId: "failed-stop", objective: "Task" }, { sessionID: "failed-stop" })
  await assert.rejects(hooks.tool.goal_pause.execute({}, { sessionID: "failed-stop" }), /native pause unavailable/)
  assert.equal((await fixture.store.read("failed-stop")).autoResume, false)
  await hooks.event({ event: { type: "session.idle", properties: { sessionID: "failed-stop" } } })
  assert.equal(fixture.calls.quota, 0)
  assert.equal(fixture.calls.event, 0)
  await hooks.dispose()
})

test("filesystem binding preserves pause across bridge recreation", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "bridge-pause-"))
  try {
    const first = harness({ should_run: false, scheduler_hint: { action: "stop_until_explicit_resume" } },
      { store: createFileBindingStore({ root }) })
    const hooks = await first.plugin({ directory: "/workspace", client: {} })
    await hooks.tool.loopx_goal_activate.execute({ goalId: "durable", objective: "Task" }, { sessionID: "durable" })
    const idle = { event: { type: "session.idle", properties: { sessionID: "durable" } } }
    await hooks.event(idle)
    await hooks.dispose()
    const second = harness({ should_run: true, scheduler_hint: { action: "run_now" } },
      { store: createFileBindingStore({ root }) })
    const restored = await second.plugin({ directory: "/workspace", client: {} })
    await restored.event(idle)
    assert.equal(second.calls.quota, 0)
    await restored.tool.goal_resume.execute({}, { sessionID: "durable" })
    await restored.event(idle)
    assert.equal(second.calls.quota, 1)
    assert.equal(second.calls.event, 1)
    await restored.dispose()
  } finally { await fs.rm(root, { recursive: true, force: true }) }
})
