import assert from "node:assert/strict"
import { execFile as execFileCallback } from "node:child_process"
import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"
import { promisify } from "node:util"
import test from "node:test"

import {
  continuationPrompt,
  createApiTransport,
  createStateStore,
  isTerminalNoFollowup,
  leaseIsLive,
  parseWorkerArgs,
  retryPlan,
  runWorker,
  sanitizedStateName,
  shouldRunNow,
  turnVerdict,
  waitPlan,
} from "../loopx/opencode2_goal_mode/opencode2-goal-worker.mjs"


const execFile = promisify(execFileCallback)


function goalNotFoundDecision() {
  return {
    ok: false,
    should_run: false,
    state: "unknown",
    status: "goal_not_found",
    effective_action: "quota_skip",
    reason: "goal is not present in the registered quota plan",
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


function waitDecision() {
  return {
    should_run: false,
    scheduler_hint: {
      action: "backoff_waiting_for_user",
      reset_policy: { reset_token: "wait-token" },
      unchanged_poll: {
        local_scheduler: {
          recommended_interval_minutes: 3,
          example_progression_minutes: [3, 6, 12],
          unchanged_poll_limit: 3,
        },
      },
    },
  }
}


function memoryStateStore() {
  const states = new Map()
  const locks = new Map()
  return {
    states,
    locks,
    async read(goalId) {
      return states.get(goalId) || null
    },
    async write(goalId, state) {
      const value = {
        schemaVersion: "loopx_opencode2_goal_worker_v0",
        goalId,
        ...state,
        updatedAt: new Date().toISOString(),
      }
      states.set(goalId, value)
      return value
    },
    lockPath(goalId) {
      return `memory://${goalId}.lock`
    },
    async acquireLock(goalId, options = {}) {
      if (locks.get(goalId)) {
        return {
          acquired: false,
          holder: locks.get(goalId),
          reason: "held by another driver",
        }
      }
      locks.set(goalId, { pid: process.pid, heartbeatAt: new Date().toISOString() })
      return {
        acquired: true,
        holder: { pid: process.pid },
        reason: "",
        release: async () => {
          locks.delete(goalId)
        },
      }
    },
  }
}


function fakeTransport({ pausedUserId = null } = {}) {
  const calls = { prompts: [], synthetics: [], sessions: 0, exists: 0 }
  const userMessages = []
  const assistantMessages = []
  let nextId = 1
  return {
    calls,
    userMessages,
    assistantMessages,
    async createSession() {
      calls.sessions += 1
      return `ses_fake_${nextId++}`
    },
    async sessionExists() {
      calls.exists += 1
      return true
    },
    async listMessages() {
      await new Promise((resolve) => setImmediate(resolve))
      const lastUser = userMessages.at(-1)
      const lastAssistant = assistantMessages.at(-1)
      if (lastUser && (!lastAssistant || lastAssistant.created < lastUser.created)) {
        assistantMessages.push({
          id: `msg_asst_${nextId++}`,
          created: lastUser.created + 1,
          completed: lastUser.created + 2,
        })
      }
      const messages = [
        ...userMessages.map((user) => ({
          id: user.id,
          type: "user",
          text: user.text,
          time: { created: user.created },
        })),
        ...assistantMessages.map((assistant) => ({
          id: assistant.id,
          type: "assistant",
          finish: assistant.finish ?? "stop",
          time: { created: assistant.created, completed: assistant.completed },
        })),
      ]
      return messages
    },
    async sendPrompt(_sessionID, text) {
      const message = { id: `msg_user_${nextId}`, text, created: Date.now() + nextId }
      nextId += 1
      userMessages.push(message)
      calls.prompts.push(text)
      return message.id
    },
    async sendSynthetic(_sessionID, text) {
      calls.synthetics.push(text)
      return `msg_syn_${nextId++}`
    },
    async waitForSession() {
      await new Promise((resolve) => setImmediate(resolve))
      return true
    },
    async sessionInfo() {
      return { time: { updated: Date.now() } }
    },
    addUserMessage(text) {
      const message = { id: `msg_ext_${nextId++}`, text, created: Date.now() + 100_000 }
      userMessages.push(message)
      return message.id
    },
  }
}


function makeWorkerOverrides({ decisions, transport, stateStore, sleepMs = [] } = {}) {
  let probeIndex = 0
  return {
    transport,
    stateStore,
    quotaProbe: async () => {
      const decision = decisions[Math.min(probeIndex, decisions.length - 1)]
      probeIndex += 1
      if (decision instanceof Error) throw decision
      return decision
    },
    sleepImpl: async () => {
      sleepMs.push("slept")
    },
  }
}


test("sanitizedStateName keeps goal ids filesystem-safe", () => {
  assert.equal(sanitizedStateName("a/b\\c d"), "a_b_c_d")
  assert.throws(() => sanitizedStateName(""), /goal id is required/)
})


test("leaseIsLive honors fresh heartbeats and stale heartbeats from dead pids", async () => {
  const now = () => 1_000_000
  // A fresh heartbeat holds the lease even when the pid already exited;
  // the worker must wait out the staleness window instead of double-driving.
  assert.equal(
    await leaseIsLive({ pid: 999_999_999, heartbeatAt: new Date(now() - 1000).toISOString() }, { staleMs: 5000, now }),
    true,
  )
  // A stale heartbeat from a dead pid releases the lease.
  assert.equal(
    await leaseIsLive({ pid: 999_999_999, heartbeatAt: new Date(now() - 9000).toISOString() }, { staleMs: 5000, now }),
    false,
  )
  // A live pid with a stale heartbeat still holds the lease.
  assert.equal(
    await leaseIsLive({ pid: process.pid, heartbeatAt: new Date(now() - 9000).toISOString() }, { staleMs: 5000, now }),
    true,
  )
})


test("turnVerdict completes only after a final non-tool-call reply to the newest own prompt", () => {
  const own = (id, created) => ({ id, type: "user", text: "go", time: { created } })
  const assistant = (created, completed, finish = "stop") => ({ id: "a", type: "assistant", time: { created, completed }, finish })
  assert.equal(turnVerdict([], ["own-1"]).waiting, true)
  assert.equal(turnVerdict([assistant(10, 20)], ["own-1"]).waiting, true)
  assert.equal(turnVerdict([own("own-1", 10)], ["own-1"]).waiting, true)
  assert.equal(
    turnVerdict([own("own-1", 10), assistant(11, 12)], ["own-1"]).completed,
    true,
  )
  // A stale assistant reply from a previous turn must not complete a new prompt.
  assert.equal(
    turnVerdict([own("own-1", 10), assistant(11, 12), own("own-2", 20)], ["own-1", "own-2"]).completed,
    false,
  )
  // An intermediate tool-call step completes its message but not the turn.
  assert.equal(
    turnVerdict([own("own-1", 10), assistant(11, 12, "tool-calls")], ["own-1"]).completed,
    false,
  )
  // A later final step ends the same turn.
  assert.equal(
    turnVerdict(
      [own("own-1", 10), assistant(11, 12, "tool-calls"), assistant(13, 14, "stop")],
      ["own-1"],
    ).completed,
    true,
  )
  // A user message the worker did not send never pauses: the verdict just
  // waits for the assistant reply to the newest user message.
  const foreign = turnVerdict([own("stranger", 30)], ["own-1", "own-2"])
  assert.equal(foreign.completed, false)
  assert.equal(foreign.waiting, true)
  const answered = turnVerdict(
    [own("stranger", 30), assistant(31, 32, "stop")],
    ["own-1", "own-2"],
    { fallbackPromptCreated: 20 },
  ).completed
  assert.equal(answered, true)
  // A page filled entirely with assistant messages (long tool-heavy turns)
  // still completes against the worker's last-prompt boundary.
  const toolHeavy = turnVerdict(
    [assistant(11, 12, "tool-calls"), assistant(13, 14, "stop")],
    [],
    { fallbackPromptCreated: 10 },
  )
  assert.equal(toolHeavy.completed, true)
  // Without any boundary the verdict keeps waiting.
  assert.equal(turnVerdict([assistant(11, 12, "stop")], []).waiting, true)
  // A stale assistant reply before the fallback boundary does not complete.
  assert.equal(
    turnVerdict([assistant(5, 6, "stop")], [], { fallbackPromptCreated: 10 }).completed,
    false,
  )
})


test("waitPlan stops at the unchanged-poll limit and follows the progression ladder", () => {
  const decision = waitDecision()
  assert.deepEqual(waitPlan(decision, { schedulerToken: "", unchangedPolls: 0 }), {
    stop: false,
    minutes: 3,
    schedulerToken: "wait-token",
    unchangedPolls: 1,
  })
  assert.deepEqual(waitPlan(decision, { schedulerToken: "wait-token", unchangedPolls: 1 }), {
    stop: false,
    minutes: 6,
    schedulerToken: "wait-token",
    unchangedPolls: 2,
  })
  assert.deepEqual(waitPlan(decision, { schedulerToken: "wait-token", unchangedPolls: 2 }), {
    stop: false,
    minutes: 12,
    schedulerToken: "wait-token",
    unchangedPolls: 3,
  })
  assert.equal(waitPlan(decision, { schedulerToken: "wait-token", unchangedPolls: 3 }).stop, true)
  assert.equal(waitPlan({ should_run: false, scheduler_hint: {} }, {}).stop, true)
})


test("retryPlan backs off exponentially with a ceiling", () => {
  assert.equal(retryPlan({}, {}).minutes, 3)
  assert.equal(retryPlan({}, { probeRetryCount: 1 }).minutes, 6)
  assert.equal(retryPlan({}, { probeRetryCount: 4 }).minutes, 30)
  assert.equal(retryPlan({}, { probeRetryCount: 9 }).minutes, 30)
})


test("decision helpers recognize terminal closure and run_now", () => {
  assert.equal(isTerminalNoFollowup(terminalDecision()), true)
  assert.equal(shouldRunNow({ should_run: true, scheduler_hint: { action: "run_now" } }), true)
  assert.equal(shouldRunNow({ should_run: true }), false)
  assert.equal(shouldRunNow({ should_run: false }), false)
  assert.equal(
    shouldRunNow({ should_run: true, scheduler_hint: { action: "backoff_waiting_for_user" } }),
    false,
  )
})


test("worker admits initial and continuation prompts then stops at validated closure", async () => {
  const stateStore = memoryStateStore()
  const transport = fakeTransport()
  const sleepMs = []
  const overrides = makeWorkerOverrides({
    transport,
    stateStore,
    sleepMs,
    decisions: [
      { should_run: true, scheduler_hint: { action: "run_now" } },
      { should_run: true, scheduler_hint: { action: "run_now" } },
      terminalDecision(),
      goalNotFoundDecision(),
    ],
  })
  const result = await runWorker({
    goalId: "goal-e2e",
    directory: "/workspace",
    taskBody: "Ship the task.",
    ...overrides,
  })
  assert.equal(result.kind, "paused_state")
  assert.deepEqual(transport.calls.prompts, ["Ship the task.", continuationPrompt("/workspace")])
  assert.equal(transport.calls.synthetics.length, 0)
  const state = await stateStore.read("goal-e2e")
  assert.equal(state.phase, "paused")
  assert.equal(state.turnCount, 2)
  assert.equal(sleepMs.length, 0)
  assert.equal(state.autoResume, false)
})


test("worker waits quietly between quota polls without prompting", async () => {
  const stateStore = memoryStateStore()
  const transport = fakeTransport()
  const sleepMs = []
  const overrides = makeWorkerOverrides({
    transport,
    stateStore,
    sleepMs,
    decisions: [waitDecision(), terminalDecision(), goalNotFoundDecision()],
  })
  const result = await runWorker({ goalId: "goal-wait", directory: "/workspace", taskBody: "Task.", ...overrides })
  assert.equal(result.kind, "paused_state")
  assert.equal(transport.calls.prompts.length, 0)
  assert.ok(sleepMs.length >= 1)
  const state = await stateStore.read("goal-wait")
  assert.equal(state.phase, "paused")
  assert.equal(state.autoResume, false)
})


test("worker pauses visibly when the unchanged-poll limit stops the loop", async () => {
  const stateStore = memoryStateStore()
  const transport = fakeTransport()
  const limitDecision = {
    should_run: false,
    scheduler_hint: {
      action: "backoff_waiting_for_user",
      reset_policy: { reset_token: "stop-token" },
      unchanged_poll: {
        local_scheduler: {
          recommended_interval_minutes: 3,
          example_progression_minutes: [3, 6],
          unchanged_poll_limit: 1,
        },
      },
    },
  }
  const overrides = makeWorkerOverrides({
    transport,
    stateStore,
    decisions: [limitDecision],
  })
  const result = await runWorker({ goalId: "goal-stop", directory: "/workspace", taskBody: "Task.", ...overrides })
  assert.equal(result.kind, "limit_stopped")
  assert.equal(transport.calls.prompts.length, 0)
  assert.ok(transport.calls.synthetics.some((text) => text.includes("unchanged-poll limit")))
  const state = await stateStore.read("goal-stop")
  assert.equal(state.phase, "limit_stopped")
  assert.equal(state.autoResume, false)
  assert.equal(state.pausedReason, "unchanged_poll_limit")
})


test("worker keeps looping after an external user message", async () => {
  const stateStore = memoryStateStore()
  const transport = fakeTransport()
  const overrides = makeWorkerOverrides({
    transport,
    stateStore,
    decisions: [
      { should_run: true, scheduler_hint: { action: "run_now" } },
      { should_run: true, scheduler_hint: { action: "run_now" } },
      goalNotFoundDecision(),
    ],
  })
  const run = runWorker({ goalId: "goal-user-msg", directory: "/workspace", taskBody: "Task.", ...overrides })
  await new Promise((resolve) => setTimeout(resolve, 0))
  transport.addUserMessage("What is the current status?")
  const result = await run
  assert.equal(result.kind, "goal_gone")
  assert.equal(transport.calls.prompts.length, 2)
  assert.equal(transport.calls.synthetics.length, 0)
  const state = await stateStore.read("goal-user-msg")
  assert.equal(state.phase, "goal_gone")
  assert.equal(state.pausedReason, "goal_not_found")
})


test("worker refuses to start while another live lease holds the goal", async () => {
  const stateStore = memoryStateStore()
  await stateStore.acquireLock("goal-held")
  const transport = fakeTransport()
  const overrides = makeWorkerOverrides({
    transport,
    stateStore,
    decisions: [],
  })
  const result = await runWorker({
    goalId: "goal-held",
    directory: "/workspace",
    taskBody: "Task.",
    ...overrides,
  })
  assert.equal(result.kind, "lease_held")
  assert.equal(transport.calls.prompts.length, 0)
})


test("worker backs off quota probe failures and resets on success", async () => {
  const stateStore = memoryStateStore()
  const transport = fakeTransport()
  const sleepMs = []
  const decisions = [
    new Error("probe timeout"),
    { should_run: true, scheduler_hint: { action: "run_now" } },
    terminalDecision(),
    goalNotFoundDecision(),
  ]
  const overrides = makeWorkerOverrides({ transport, stateStore, sleepMs, decisions })
  const result = await runWorker({ goalId: "goal-probe-fail", directory: "/workspace", taskBody: "Task.", ...overrides })
  assert.equal(result.kind, "paused_state")
  const state = await stateStore.read("goal-probe-fail")
  assert.equal(state.probeRetryCount, 0)
  assert.equal(state.autoResume, false)
  assert.deepEqual(transport.calls.prompts, ["Task."])
})


test("worker skips a paused state unless forceResume is set", async () => {
  const stateStore = memoryStateStore()
  await stateStore.write("goal-paused-state", {
    directory: "/workspace",
    agentId: "",
    registryPath: "",
    availableCapabilities: [],
    sessionID: "ses_fake_1",
    autoResume: false,
    phase: "paused_user_intervention",
    schedulerToken: "",
    unchangedPolls: 0,
    probeRetryCount: 0,
    turnCount: 1,
    sentPromptIds: [],
  })
  const transport = fakeTransport()
  const overrides = makeWorkerOverrides({ transport, stateStore, decisions: [] })
  const result = await runWorker({
    goalId: "goal-paused-state",
    directory: "/workspace",
    ...overrides,
  })
  assert.equal(result.kind, "paused_state")
  assert.equal(transport.calls.prompts.length, 0)
})


test("worker reports a deleted bound session instead of recreating it", async () => {
  const stateStore = memoryStateStore()
  await stateStore.write("goal-deleted", {
    directory: "/workspace",
    agentId: "",
    registryPath: "",
    availableCapabilities: [],
    sessionID: "ses_gone",
    autoResume: true,
    phase: "working",
    schedulerToken: "",
    unchangedPolls: 0,
    probeRetryCount: 0,
    turnCount: 1,
    sentPromptIds: [],
  })
  const transport = fakeTransport()
  transport.sessionExists = async () => false
  const overrides = makeWorkerOverrides({ transport, stateStore, decisions: [] })
  const result = await runWorker({
    goalId: "goal-deleted",
    directory: "/workspace",
    ...overrides,
  })
  assert.equal(result.kind, "session_deleted")
  const state = await stateStore.read("goal-deleted")
  assert.equal(state.phase, "session_deleted")
})


test("worker stops after the turn budget and posts a visible notice", async () => {
  const stateStore = memoryStateStore()
  await stateStore.write("goal-turns", {
    directory: "/workspace",
    agentId: "",
    registryPath: "",
    availableCapabilities: [],
    sessionID: "ses_fake_1",
    autoResume: true,
    phase: "working",
    schedulerToken: "",
    unchangedPolls: 0,
    probeRetryCount: 0,
    turnCount: 3,
    sentPromptIds: ["msg_user_old"],
  })
  const transport = fakeTransport()
  const overrides = makeWorkerOverrides({ transport, stateStore, decisions: [] })
  const result = await runWorker({
    goalId: "goal-turns",
    directory: "/workspace",
    maxTurns: 3,
    ...overrides,
  })
  assert.equal(result.kind, "turn_limit")
  assert.ok(transport.calls.synthetics.some((text) => text.includes("turn budget")))
})


test("first prompt counts toward the turn budget and its turn finishes before stopping", async () => {
  const stateStore = memoryStateStore()
  const transport = fakeTransport()
  const overrides = makeWorkerOverrides({ transport, stateStore, decisions: [{ should_run: true, scheduler_hint: { action: "run_now" } }] })
  const result = await runWorker({
    goalId: "goal-first-turn",
    directory: "/workspace",
    taskBody: "Ship the task.",
    maxTurns: 1,
    ...overrides,
  })
  assert.equal(result.kind, "turn_limit")
  assert.deepEqual(transport.calls.prompts, ["Ship the task."])
  assert.ok(
    transport.calls.synthetics.some((text) => text.includes("turn budget")),
    "the initial prompt must count toward the budget",
  )
  const state = await stateStore.read("goal-first-turn")
  assert.equal(state.turnCount, 1)
  assert.equal(state.pendingTurn, false)
})


test("worker stops after the duration budget and posts the duration notice", async () => {
  const stateStore = memoryStateStore()
  const transport = fakeTransport()
  const overrides = makeWorkerOverrides({ transport, stateStore, decisions: [] })
  const result = await runWorker({
    goalId: "goal-duration",
    directory: "/workspace",
    taskBody: "Task.",
    maxDurationMs: -1,
    ...overrides,
  })
  assert.equal(result.kind, "duration_limit")
  assert.ok(
    transport.calls.synthetics.some((text) => text.includes("duration budget")),
    "duration stop must post the duration notice, not the turn notice",
  )
})


test("createApiTransport probes the opencode2 api CLI shape", () => {
  assert.equal(typeof createApiTransport().sendPrompt, "function")
  assert.equal(typeof createApiTransport().sendSynthetic, "function")
})


test("worker entry runs main() even when argv[1] goes through a symlinked path", async () => {
  const workerFile = path.resolve(
    path.join(import.meta.dirname, "..", "loopx", "opencode2_goal_mode", "opencode2-goal-worker.mjs"),
  )
  const linkDir = path.join(os.tmpdir(), `loopx-worker-entry-${process.pid}`)
  await fs.rm(linkDir, { recursive: true, force: true })
  await fs.mkdir(path.dirname(workerFile), { recursive: true })
  await fs.symlink(path.dirname(workerFile), linkDir)
  try {
    const argvPath = path.join(linkDir, path.basename(workerFile))
    let stdout = ""
    let exitCode = 0
    try {
      const result = await execFile(process.execPath, [argvPath, "--goal-id", "x", "--directory", "/tmp"], {
        timeout: 30_000,
        maxBuffer: 4 * 1024 * 1024,
      })
      stdout = result.stdout
    } catch (error) {
      exitCode = error?.code ?? 1
      stdout = String(error?.stdout || "")
    }
    assert.equal(exitCode, 1, "the worker entry must fail closed with a non-zero exit")
    const rendered = JSON.parse(stdout.trim())
    assert.equal(rendered.operation, "opencode2_goal_worker")
    assert.equal(rendered.ok, false)
    assert.ok(
      /first run requires --task-body/.test(rendered.error || ""),
      "the worker entry must execute the real driver path",
    )
  } finally {
    await fs.rm(linkDir, { recursive: true, force: true })
  }
})


test("parseWorkerArgs maps repeated capabilities and rejects unknown flags", () => {
  const args = parseWorkerArgs([
    "--goal-id",
    "goal-args",
    "--directory",
    "/tmp",
    "--capability",
    "shell",
    "--capability",
    "edit",
    "--force-resume",
  ])
  assert.equal(args.goalId, "goal-args")
  assert.equal(args.directory, "/tmp")
  assert.deepEqual(args.availableCapabilities, ["shell", "edit"])
  assert.equal(args.forceResume, true)
  assert.throws(() => parseWorkerArgs(["--bogus"]), /unknown argument/)
})


test("createQuotaProbe reads a denied decision that exits non-zero", async () => {
  const { createQuotaProbe } = await import("../loopx/opencode2_goal_mode/opencode2-goal-worker.mjs")
  const denied = JSON.stringify({
    ok: false,
    should_run: false,
    effective_action: "operator_gate_notify",
    scheduler_hint: { unchanged_poll: { local_scheduler: null } },
  })
  const execFileImpl = async () => {
    const error = new Error("Command failed")
    error.stdout = denied
    throw error
  }
  const probe = createQuotaProbe({ execFileImpl })
  const decision = await probe(
    { goalId: "goal-denied", directory: "/workspace", registryPath: "", agentId: "", availableCapabilities: [] },
    { directory: "/workspace" },
  )
  assert.equal(decision.should_run, false)
})


test("worker pauses when the session stops making progress", async () => {
  const stateStore = memoryStateStore()
  const transport = fakeTransport()
  const frozenAt = Date.now() - 100_000
  transport.listMessages = async () => {
    await new Promise((resolve) => setImmediate(resolve))
    return transport.userMessages.map((user) => ({
      id: user.id,
      type: "user",
      text: user.text,
      time: { created: Math.min(user.created, frozenAt) },
    }))
  }
  const overrides = makeWorkerOverrides({
    transport,
    stateStore,
    decisions: [{ should_run: true, scheduler_hint: { action: "run_now" } }],
  })
  const result = await runWorker({
    goalId: "goal-stalled",
    directory: "/workspace",
    taskBody: "Task.",
    ...overrides,
    waitTurnOptions: { stallMs: 1000 },
  })
  assert.equal(result.kind, "session_stalled")
  const state = await stateStore.read("goal-stalled")
  assert.equal(state.phase, "session_stalled")
  assert.equal(state.autoResume, false)
  assert.equal(state.pausedReason, "session_stalled")
  assert.ok(transport.calls.synthetics.some((text) => text.includes("stopped making progress")))
})


test("worker holds quietly during a user pause and resumes when activity returns", async () => {
  const stateStore = memoryStateStore()
  const transport = fakeTransport()
  let sessionPaused = true
  const frozenAt = Date.now() - 200_000
  transport.listMessages = async () => {
    await new Promise((resolve) => setImmediate(resolve))
    const messages = transport.userMessages.map((user) => ({
      id: user.id,
      type: "user",
      text: user.text,
      time: { created: Math.min(user.created, frozenAt) },
    }))
    if (!sessionPaused) {
      const now = Date.now()
      messages.push({
        id: "msg_final",
        type: "assistant",
        finish: "stop",
        time: { created: now, completed: now + 10 },
      })
    }
    return messages
  }
  const overrides = makeWorkerOverrides({
    transport,
    stateStore,
    decisions: [{ should_run: true, scheduler_hint: { action: "run_now" } }, terminalDecision()],
  })
  const running = runWorker({
    goalId: "goal-hold",
    directory: "/workspace",
    taskBody: "Task.",
    waitTurnOptions: { holdMs: 1000, stallMs: 100_000 },
    ...overrides,
  })
  // Wait for the first hold to land, then resume the paused session.
  const deadline = Date.now() + 10_000
  while (Date.now() < deadline) {
    const state = await stateStore.read("goal-hold")
    if (state?.phase === "session_paused_hold") break
    await new Promise((resolve) => setTimeout(resolve, 20))
  }
  const held = await stateStore.read("goal-hold")
  assert.equal(held?.phase, "session_paused_hold")
  assert.equal(held?.autoResume, true, "a user pause must not permanently pause the worker")
  assert.ok(
    transport.calls.synthetics.some((text) => text.includes("holds quietly")),
    "the hold must be visible in the session",
  )
  sessionPaused = false
  const result = await running
  assert.equal(result.kind, "paused_state")
  const state = await stateStore.read("goal-hold")
  assert.equal(state.phase, "paused")
})


test("worker resumes by prompting when quota grants run_now and no turn is pending", async () => {
  const stateStore = memoryStateStore()
  await stateStore.write("goal-resume", {
    directory: "/workspace",
    agentId: "e2e",
    registryPath: "",
    availableCapabilities: [],
    sessionID: "ses_resume_1",
    autoResume: true,
    phase: "limit_stopped",
    schedulerToken: "",
    unchangedPolls: 2,
    probeRetryCount: 0,
    turnCount: 3,
    pendingTurn: false,
    lastPromptAt: Date.now() - 60_000,
    pendingTurnActivityAt: Date.now() - 60_000,
  })
  const transport = fakeTransport()
  const overrides = makeWorkerOverrides({
    transport,
    stateStore,
    decisions: [
      { should_run: true, scheduler_hint: { action: "run_now" } },
      terminalDecision(),
      goalNotFoundDecision(),
    ],
  })
  const result = await runWorker({
    goalId: "goal-resume",
    directory: "/workspace",
    ...overrides,
  })
  assert.equal(result.kind, "paused_state")
  assert.equal(
    transport.calls.prompts.length,
    1,
    "a resumed worker with no pending turn must prompt on run_now",
  )
  const state = await stateStore.read("goal-resume")
  assert.equal(state.turnCount, 4)
})

test("initial task is gated before any model prompt and hard pause is durable", async () => {
  const stateStore = memoryStateStore()
  const transport = fakeTransport()
  let probes = 0
  const args = { goalId: "initial-pause", directory: "/workspace", taskBody: "Task", stateStore, transport,
    quotaProbe: async () => { probes++; return { should_run: false, scheduler_hint: { action: "stop_until_explicit_resume" } } } }
  await runWorker(args)
  assert.equal(transport.calls.prompts.length, 0)
  assert.equal((await stateStore.read("initial-pause")).autoResume, false)
  await runWorker(args)
  assert.equal(probes, 1)
})

test("pause during quota probe survives both success and failed probe", async () => {
  for (const fail of [false, true]) {
    const stateStore = memoryStateStore()
    const transport = fakeTransport()
    await runWorker({ goalId: "probe-pause", directory: "/workspace", taskBody: "Task", stateStore, transport,
      quotaProbe: async () => {
        const state = await stateStore.read("probe-pause")
        await stateStore.write("probe-pause", { ...state, autoResume: false })
        if (fail) throw new Error("probe unavailable")
        return { should_run: true, scheduler_hint: { action: "run_now" } }
      } })
    assert.equal(transport.calls.prompts.length, 0)
    assert.equal((await stateStore.read("probe-pause")).autoResume, false)
  }
})

test("force resume cannot bypass a still-paused Core goal", async () => {
  const stateStore = memoryStateStore()
  const transport = fakeTransport()
  await stateStore.write("resume-denied", { directory: "/workspace", autoResume: false, turnCount: 0, pendingTurn: false })
  await runWorker({ goalId: "resume-denied", directory: "/workspace", taskBody: "Task", forceResume: true, stateStore, transport,
    quotaProbe: async () => ({ should_run: false, scheduler_hint: { action: "stop_until_explicit_resume" } }) })
  assert.equal(transport.calls.prompts.length, 0)
  assert.equal((await stateStore.read("resume-denied")).autoResume, false)
})

test("restart after denied initial dispatch preserves the original private task", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "worker-pause-"))
  try {
    const transport = fakeTransport()
    const args = { goalId: "restart", directory: "/workspace", transport, maxTurns: 1 }
    await runWorker({ ...args, taskBody: "Original task", stateStore: createStateStore({ root }),
      quotaProbe: async () => ({ should_run: false, scheduler_hint: { action: "stop_until_explicit_resume" } }) })
    assert.equal(transport.calls.prompts.length, 0)
    await runWorker({ ...args, forceResume: true, stateStore: createStateStore({ root }),
      quotaProbe: async () => ({ should_run: true, scheduler_hint: { action: "run_now" } }) })
    assert.deepEqual(transport.calls.prompts, ["Original task"])
  } finally { await fs.rm(root, { recursive: true, force: true }) }
})
