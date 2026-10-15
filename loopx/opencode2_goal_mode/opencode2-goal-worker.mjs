// LoopX OpenCode 2 goal worker.
//
// A persistent out-of-process goal loop driver for OpenCode 2. OpenCode 2
// ships a new plugin API and its plugins cannot run the OpenCode 1 goal
// bridge, so LoopX drives OpenCode 2 sessions through the OpenCode 2 HTTP
// API instead:
//
//   prompt -> wait for the assistant turn to complete -> loopx quota
//   should-run -> run_now: continue | wait: backoff ladder | stop: visible
//   pause | terminal_no_followup: visible completion
//
// The worker owns the timers, so recurring operation survives TUI close and
// one-shot CLI usage. Session state, pause reason, and poll bookkeeping are
// persisted so a crashed worker can be restarted.
//
// This file has no runtime dependencies. It shells out to `opencode2 api`
// (which performs its own service discovery and authentication) and to
// `loopx` for quota decisions.

import { execFile as execFileCallback } from "node:child_process"
import { promises as fs, realpathSync } from "node:fs"
import os from "node:os"
import path from "node:path"
import { promisify } from "node:util"
import { fileURLToPath } from "node:url"

const execFile = promisify(execFileCallback)

export const STATE_SCHEMA_VERSION = "loopx_opencode2_goal_worker_v0"
export const TERMINAL_STATE_SCHEMA_VERSION = "goal_terminal_state_v0"
export const SOURCE_COMPLETENESS_SCHEMA_VERSION = "goal_terminal_source_completeness_v0"
export const DEFAULT_RETRY_MINUTES = 3
export const MAX_RETRY_MINUTES = 30
export const MESSAGE_POLL_MS = 3_000
export const DEFAULT_TURN_TIMEOUT_MS = 120 * 60 * 1000
export const DEFAULT_MAX_TURNS = 10_000
export const DEFAULT_MAX_DURATION_MS = 30 * 24 * 60 * 60 * 1000
export const LEASE_STALE_MS = 5 * 60 * 1000

export function continuationPrompt(directory) {
  const anchor = directory
    ? `\nWorking directory: ${directory}\nStay inside this project; do not touch other goals or repositories, and never run loopx install/update/rollback/slash-commands operations or modify the host loopx installation.`
    : ""
  return [
    "LoopX quota granted the next automatic turn.",
    "Continue the goal work and report progress.",
    "Do not self-declare completion; LoopX validates closure.",
    anchor,
  ]
    .filter(Boolean)
    .join(" ")
}

export const CONTINUATION_PROMPT = continuationPrompt()

const LIMIT_NOTICE = [
  "LoopX stopped this goal after the unchanged-poll limit.",
  "No new work was scheduled; review LoopX todos and resume explicitly when ready.",
].join(" ")
const TURN_LIMIT_NOTICE = [
  "LoopX stopped this goal after the turn budget was exhausted.",
  "Review LoopX todos before requesting another run.",
].join(" ")
const DURATION_LIMIT_NOTICE = [
  "LoopX stopped this goal after the duration budget was exhausted.",
  "Review LoopX todos before requesting another run.",
].join(" ")


export function sanitizedStateName(value) {
  const text = String(value || "").replace(/[^A-Za-z0-9_-]/g, "_")
  if (!text) throw new Error("goal id is required")
  return text.slice(0, 160)
}


export function defaultStateRoot() {
  if (process.env.LOOPX_OPENCODE2_STATE_DIR) {
    return path.resolve(process.env.LOOPX_OPENCODE2_STATE_DIR)
  }
  const xdgState = process.env.XDG_STATE_HOME || path.join(os.homedir(), ".local", "state")
  return path.join(xdgState, "loopx", "opencode2")
}


export function createStateStore({ root = defaultStateRoot(), now = () => Date.now() } = {}) {
  const target = (goalId) => path.join(root, `${sanitizedStateName(goalId)}.json`)
  return {
    async read(goalId) {
      try {
        const payload = JSON.parse(await fs.readFile(target(goalId), "utf8"))
        if (payload?.schemaVersion !== STATE_SCHEMA_VERSION || payload?.goalId !== goalId) {
          throw new Error("invalid LoopX OpenCode 2 worker state")
        }
        return payload
      } catch (error) {
        if (error?.code === "ENOENT") return null
        throw error
      }
    },
    async write(goalId, state) {
      await fs.mkdir(root, { recursive: true, mode: 0o700 })
      const destination = target(goalId)
      const temporary = `${destination}.${process.pid}.${now()}.tmp`
      const payload = {
        schemaVersion: STATE_SCHEMA_VERSION,
        goalId,
        ...state,
        updatedAt: new Date(now()).toISOString(),
      }
      await fs.writeFile(temporary, `${JSON.stringify(payload, null, 2)}\n`, {
        encoding: "utf8",
        mode: 0o600,
      })
      await fs.rename(temporary, destination)
      await fs.chmod(destination, 0o600)
      return payload
    },
    lockPath(goalId) {
      return `${target(goalId)}.lock`
    },
    async acquireLock(goalId, { staleMs = LEASE_STALE_MS, heartbeatMs = 60_000 } = {}) {
      const lockPath = this.lockPath(goalId)
      const release = async () => {
        try {
          await fs.unlink(lockPath)
        } catch (error) {
          if (error?.code !== "ENOENT") throw error
        }
      }
      const heartbeat = async () => {
        await fs.mkdir(root, { recursive: true, mode: 0o700 })
        const temporary = `${lockPath}.${process.pid}.tmp`
        await fs.writeFile(
          temporary,
          `${JSON.stringify({ pid: process.pid, heartbeatAt: new Date().toISOString() })}\n`,
          { encoding: "utf8", mode: 0o600 },
        )
        await fs.rename(temporary, lockPath)
        await fs.chmod(lockPath, 0o600)
      }
      const existing = await readLock(lockPath)
      if (existing && (await leaseIsLive(existing, { staleMs }))) {
        return {
          acquired: false,
          holder: existing,
          reason: `held by pid ${existing.pid} with a fresh heartbeat`,
        }
      }
      await heartbeat()
      const timer = setInterval(() => {
        heartbeat().catch(() => {})
      }, heartbeatMs)
      if (typeof timer?.unref === "function") timer.unref()
      return {
        acquired: true,
        holder: { pid: process.pid, heartbeatAt: new Date().toISOString() },
        reason: "",
        release: async () => {
          clearInterval(timer)
          await release()
        },
      }
    },
  }
}


async function readLock(lockPath) {
  try {
    const payload = JSON.parse(await fs.readFile(lockPath, "utf8"))
    if (!Number.isInteger(payload?.pid)) return null
    return payload
  } catch (error) {
    if (error?.code === "ENOENT") return null
    try {
      await fs.unlink(lockPath)
    } catch {
      // A malformed lock is retried as unreadable; removal is best-effort.
    }
    return null
  }
}


export function pidIsAlive(pid) {
  try {
    process.kill(pid, 0)
    return true
  } catch (error) {
    return error?.code === "EPERM"
  }
}


export async function leaseIsLive(lock, { staleMs = LEASE_STALE_MS, now = () => Date.now() } = {}) {
  const heartbeatAt = lock?.heartbeatAt ? Date.parse(lock.heartbeatAt) : NaN
  if (Number.isFinite(heartbeatAt) && now() - heartbeatAt < staleMs) return true
  return pidIsAlive(lock?.pid)
}


export function isTerminalNoFollowup(decision) {
  const frontier = decision?.goal_frontier_projection
  const terminal = frontier?.terminal_state
  const completeness = frontier?.source_completeness
  return Boolean(
    decision?.should_run === false &&
      decision?.effective_action === "terminal_no_followup" &&
      terminal?.schema_version === TERMINAL_STATE_SCHEMA_VERSION &&
      terminal?.kind === "no_followup" &&
      terminal?.derived === true &&
      terminal?.source === "validated_goal_closure" &&
      completeness?.schema_version === SOURCE_COMPLETENESS_SCHEMA_VERSION &&
      completeness?.user_todos === "valid" &&
      completeness?.agent_todos === "valid",
  )
}


export function shouldRunNow(decision) {
  // Scheduling never grants execution when Core denies admission.
  return decision?.ok !== false && decision?.should_run === true &&
    decision?.scheduler_hint?.action === "run_now"
}


export function waitPlan(decision, state) {
  const local = decision?.scheduler_hint?.unchanged_poll?.local_scheduler
  if (!local || local === "stop") return { stop: true }
  const token = decision?.scheduler_hint?.reset_policy?.reset_token || ""
  const sameIdentity = token && token === state.schedulerToken
  const unchangedPolls = sameIdentity ? Number(state.unchangedPolls || 0) : 0
  const limit = Number.isInteger(local.unchanged_poll_limit) ? local.unchanged_poll_limit : null
  if (limit !== null && unchangedPolls >= limit) return { stop: true }
  const progression = Array.isArray(local.example_progression_minutes)
    ? local.example_progression_minutes.filter((value) => Number(value) > 0)
    : []
  const fallback = Number(local.recommended_interval_minutes || DEFAULT_RETRY_MINUTES)
  const minutes = progression.length
    ? Number(progression[Math.min(unchangedPolls, progression.length - 1)])
    : fallback
  return {
    stop: false,
    minutes: Number.isFinite(minutes) && minutes > 0 ? minutes : DEFAULT_RETRY_MINUTES,
    schedulerToken: token,
    unchangedPolls: unchangedPolls + 1,
  }
}


export function retryPlan(decision, state) {
  // Fail-closed quota probe retry with a bounded backoff. Resets on success.
  const count = Number(state.probeRetryCount || 0)
  const minutes = Math.min(DEFAULT_RETRY_MINUTES * 2 ** Math.min(count, 4), MAX_RETRY_MINUTES)
  return {
    minutes,
    probeRetryCount: count + 1,
  }
}


export function turnVerdict(messages, sentPromptIds, { fallbackPromptCreated } = {}) {
  // A turn completes only when an assistant message finished at or after the
  // newest user message (regardless of who sent it) and that message is not
  // an intermediate tool-call step. User input never pauses the loop: the
  // model answers it, then quota gating continues as usual.
  //
  // Long tool-heavy turns can fill the newest message page entirely with
  // assistant messages, so the page may contain no user message at all. The
  // caller supplies fallbackPromptCreated (the worker's last prompt time) to
  // bound the verdict in that case; a user message on the page always wins.
  let newestUser = null
  let newestAssistant = null
  for (const message of messages || []) {
    const type = message?.type
    const created = Number(message?.time?.created)
    if (type === "user") {
      if (!newestUser || created > newestUser.time.created) {
        newestUser = { id: message?.id, time: { created }, text: String(message?.text || "").slice(0, 120) }
      }
    }
    if (type === "assistant") {
      const completed = Number(message?.time?.completed)
      if (!newestAssistant || created > newestAssistant.time.created) {
        newestAssistant = { time: { created, completed }, finish: message?.finish }
      }
    }
  }
  const boundaryCreated = newestUser
    ? newestUser.time.created
    : Number(fallbackPromptCreated)
  if (!Number.isFinite(boundaryCreated)) return { completed: false, waiting: true }
  const finished = Boolean(newestAssistant) && Number.isFinite(newestAssistant.time.completed)
  const terminalStep = finished && newestAssistant.finish !== "tool-calls"
  const afterPrompt = finished && newestAssistant.time.completed >= boundaryCreated
  const completed = terminalStep && afterPrompt
  return {
    completed,
    waiting: !completed,
  }
}


export function buildQuotaArgs(state) {
  const args = []
  if (state.registryPath) args.push("--registry", state.registryPath)
  args.push(
    "--format",
    "json",
    "quota",
    "should-run",
    "--goal-id",
    state.goalId,
    "--runtime-profile",
    "generic_cli",
    "--include-detail",
    "scheduler",
    "--record-host-poll",
  )
  if (state.agentId) args.push("--agent-id", state.agentId)
  for (const capability of state.availableCapabilities || []) {
    args.push("--available-capability", capability)
  }
  return args
}


export function parseExecFailure(error) {
  // opencode2 api reports HTTP failures through a non-zero exit code and a
  // stderr payload. Normalize the most useful part.
  const stderr = String(error?.stderr || "")
  let status = null
  const statusMatch = /status[ =:]+(\d{3})/i.exec(stderr)
  if (statusMatch) status = Number(statusMatch[1])
  const messageMatch = /(?:error|message)[ =:]+"?([^"\n]{0,180})/i.exec(stderr)
  return {
    code: error?.code || null,
    status,
    stderr: stderr.slice(0, 400),
    message: messageMatch ? messageMatch[1] : stderr.split("\n")[0]?.slice(0, 180) || String(error?.message || error),
  }
}


export function createApiTransport({ bin = process.env.OPENCODE2_BIN || "opencode2", execFileImpl = execFile } = {}) {
  const call = async (method, apiPath, body = null, timeoutMs = 60_000) => {
    const args = ["api", method, apiPath]
    if (body !== null) args.push("-d", JSON.stringify(body))
    const { stdout } = await execFileImpl(bin, args, {
      timeout: timeoutMs,
      maxBuffer: 32 * 1024 * 1024,
    })
    return JSON.parse(stdout || "{}")
  }
  return {
    async createSession(directory) {
      const response = await call("post", "/api/session", { directory })
      return response?.data?.id || null
    },
    async sessionExists(sessionID) {
      try {
        await call("get", `/api/session/${sessionID}`)
        return true
      } catch (error) {
        const failure = parseExecFailure(error)
        if (failure.status === 404) return false
        throw error
      }
    },
    async listMessages(sessionID) {
      // Bounded newest-first page: long sessions grow unbounded message
      // lists, and the turn verdict only needs the recent tail.
      try {
        const response = await call("get", `/api/session/${sessionID}/message?limit=30&order=desc`)
        return response?.data || []
      } catch (error) {
        if (!String(error?.message || "").includes("JSON")) throw error
        // Some hosts truncate oversized payloads; a smaller tail is enough
        // for the completion verdict.
        const response = await call("get", `/api/session/${sessionID}/message?limit=5&order=desc`)
        return response?.data || []
      }
    },
    async sessionInfo(sessionID) {
      const response = await call("get", `/api/session/${sessionID}`)
      return response?.data || null
    },
    async waitForSession(sessionID, { timeoutMs = 60_000 } = {}) {
      try {
        await call("post", `/api/session/${sessionID}/wait`, null, timeoutMs)
        return true
      } catch (error) {
        // A chunk timeout means the turn is still running; the caller
        // verifies the message tail and re-issues the wait.
        if (error?.killed === true || error?.code === "ETIMEDOUT") return false
        throw error
      }
    },
    async sendPrompt(sessionID, text) {
      const response = await call("post", `/api/session/${sessionID}/prompt`, {
        text,
        delivery: "queue",
      })
      return response?.data?.id || null
    },
    async sendSynthetic(sessionID, text) {
      const response = await call("post", `/api/session/${sessionID}/synthetic`, { text })
      return response?.data?.id || null
    },
  }
}


export function createQuotaProbe({ bin = process.env.LOOPX_BIN || "loopx", execFileImpl = execFile } = {}) {
  return async function probe(state, { directory }) {
    const { stdout } = await runQuotaProbe(state, directory, bin, execFileImpl)
    const decision = JSON.parse(stdout || "{}")
    if (!decision || typeof decision !== "object" || Array.isArray(decision)) {
      throw new Error("loopx quota should-run returned a non-object payload")
    }
    return decision
  }
}


async function runQuotaProbe(state, directory, bin, execFileImpl) {
  try {
    return await execFileImpl(bin, buildQuotaArgs(state), {
      cwd: state.directory || directory,
      timeout: 30_000,
      maxBuffer: 4 * 1024 * 1024,
    })
  } catch (error) {
    // A denied or gated decision exits non-zero but still carries the
    // authoritative JSON payload on stdout. Only fail closed when the
    // payload itself is unavailable.
    if (typeof error?.stdout === "string" && error.stdout.trim()) {
      return { stdout: error.stdout }
    }
    throw error
  }
}


export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}


export async function waitForTurn({
  transport,
  sessionID,
  state,
  sentPromptIds,
  pollMs = MESSAGE_POLL_MS,
  timeoutMs = DEFAULT_TURN_TIMEOUT_MS,
  stallMs = 30 * 60 * 1000,
  holdMs = 15 * 60 * 1000,
  waitChunkMs = 60_000,
  shouldPause = async () => false,
}) {
  // Block on the session wait endpoint (resolves at idle) in bounded chunks,
  // then verify the message tail. Intermediate tool-call steps complete their
  // messages without ending the turn, so the verdict requires a non-tool-call
  // final message after the newest own prompt. A session whose message
  // activity stops moving is reported as stalled instead of blocking until
  // the turn budget. Session updated timestamps are not a reliable liveness
  // signal in every build, so stall detection keys on message activity.
  //
  // A user pause (for example ESC in the host TUI) also stops message
  // activity mid-turn. Before escalating to a permanent stall, the worker
  // returns a quiet hold once the hold window passes; the main loop then
  // waits without prompting and re-enters with the persisted activity time,
  // so a paused session is never prompted and the stall window still
  // escalates from the true last activity.
  const startedAt = Date.now()
  let lastActivityAt = Number(state?.pendingTurnActivityAt) || Date.now()
  const fallbackPromptCreated = Number(
    state?.lastPromptAt ?? state?.startedAtMs ?? Date.now()
  )
  while (true) {
    if (await shouldPause(state)) return { kind: "paused_state" }
    await transport.waitForSession(sessionID, { timeoutMs: waitChunkMs })
    const messages = await transport.listMessages(sessionID)
    const activity = latestMessageActivity(messages)
    if (activity !== null && activity > lastActivityAt) lastActivityAt = activity
    const quietForMs = Date.now() - lastActivityAt
    if (quietForMs > stallMs) {
      return { kind: "session_stalled", elapsedMs: Date.now() - startedAt }
    }
    if (quietForMs > holdMs) {
      return { kind: "turn_hold", activityAt: lastActivityAt, quietForMs }
    }
    const verdict = turnVerdict(messages, sentPromptIds, { fallbackPromptCreated })
    if (verdict.completed) return { kind: "completed", ...verdict }
    if (Date.now() - startedAt > timeoutMs) {
      return {
        kind: "turn_timeout",
        elapsedMs: Date.now() - startedAt,
        lastMessageCount: messages.length,
      }
    }
    await sleep(pollMs)
  }
}


export function latestMessageActivity(messages) {
  let latest = null
  for (const message of messages || []) {
    const created = Number(message?.time?.created)
    const completed = Number(message?.time?.completed)
    for (const candidate of [created, completed]) {
      if (Number.isFinite(candidate) && (latest === null || candidate > latest)) {
        latest = candidate
      }
    }
  }
  return latest
}


export async function runWorker({
  goalId,
  directory,
  agentId = "",
  registryPath = "",
  availableCapabilities = [],
  sessionId = null,
  taskBody = null,
  maxTurns = DEFAULT_MAX_TURNS,
  maxDurationMs = DEFAULT_MAX_DURATION_MS,
  forceResume = false,
  waitTurnOptions = {},
  log = async () => {},
  stateStore = createStateStore(),
  transport = null,
  quotaProbe = null,
  sleepImpl = sleep,
} = {}) {
  if (!goalId) throw new Error("--goal-id is required")
  if (!directory) throw new Error("--directory is required")
  const effectiveTransport = transport || createApiTransport()
  const effectiveProbe = quotaProbe || createQuotaProbe()

  const lease = await stateStore.acquireLock(goalId)
  if (!lease.acquired) {
    await log("warn", "worker lease is held; refusing to start a second driver", {
      goalId,
      ...lease.holder,
    })
    return { kind: "lease_held", holder: lease.holder }
  }
  try {
    return await runWithLease({
      goalId,
      directory,
      agentId,
      registryPath,
      availableCapabilities,
      sessionId,
      taskBody,
      maxTurns,
      maxDurationMs,
      forceResume,
      waitTurnOptions,
      log,
      stateStore,
      transport: effectiveTransport,
      quotaProbe: effectiveProbe,
      sleepImpl,
    })
  } finally {
    await lease.release()
  }
}


async function runWithLease({
  goalId,
  directory,
  agentId,
  registryPath,
  availableCapabilities,
  sessionId,
  taskBody,
  maxTurns,
  maxDurationMs,
  forceResume,
  waitTurnOptions,
  log,
  stateStore,
  transport,
  quotaProbe,
  sleepImpl,
}) {
  let state = await stateStore.read(goalId)
  const startedAt = Date.now()

  if (!state) {
    if (!sessionId && !taskBody) {
      throw new Error(
        "first run requires --task-body (or an existing --session-id); no worker state exists for this goal",
      )
    }
    state = await stateStore.write(goalId, {
      directory,
      agentId: String(agentId || ""),
      registryPath: String(registryPath || ""),
      availableCapabilities: Array.isArray(availableCapabilities)
        ? availableCapabilities.map(String)
        : [],
      sessionID: sessionId ? String(sessionId) : "",
      taskBody: taskBody || "",
      autoResume: true,
      phase: "starting",
      schedulerToken: "",
      unchangedPolls: 0,
      probeRetryCount: 0,
      turnCount: 0,
      pendingTurn: false,
      startedAt: new Date().toISOString(),
    })
  }

  if (state.autoResume === false && !forceResume) {
    await log("info", "worker state is paused; pass --force-resume to restart", { goalId })
    return { kind: "paused_state" }
  }
  if (state.autoResume === false && forceResume) {
    state = await stateStore.write(goalId, {
      ...state,
      autoResume: true,
      lastPausedReason: "",
      pausedReason: "",
    })
    await log("info", "worker state resumed with --force-resume", { goalId })
  }
  if (!Number.isFinite(Number(state.lastPromptAt))) {
    const migratedAt = Date.parse(state.startedAt) || Date.now()
    state = await stateStore.write(goalId, { ...state, lastPromptAt: migratedAt })
    await log("info", "migrated worker state with a lastPromptAt boundary", {
      goalId,
      lastPromptAt: migratedAt,
    })
  }

  const directoryNow = directory || state.directory

  let sessionID = state.sessionID || sessionId
  if (!sessionID) {
    sessionID = await transport.createSession(directoryNow)
    if (!sessionID) throw new Error("opencode2 session creation returned no id")
    state = await stateStore.write(goalId, { ...state, sessionID })
    await log("info", "created OpenCode 2 session", { goalId, sessionID })
  }
  if (!(await transport.sessionExists(sessionID))) {
    state = await stateStore.write(goalId, {
      ...state,
      phase: "session_deleted",
      pausedReason: "opencode2_session_deleted",
    })
    await log("error", "bound OpenCode 2 session no longer exists", { goalId, sessionID })
    return { kind: "session_deleted", sessionID }
  }


  while (true) {
    if (Date.now() - startedAt > maxDurationMs) {
      state = await stateStore.write(goalId, { ...state, phase: "duration_limit" })
      await transport.sendSynthetic(sessionID, DURATION_LIMIT_NOTICE)
      await log("info", "stopped after the duration budget", { goalId })
      return { kind: "duration_limit" }
    }
    if (!state.pendingTurn && state.turnCount >= maxTurns) {
      state = await stateStore.write(goalId, { ...state, phase: "turn_limit" })
      await transport.sendSynthetic(sessionID, TURN_LIMIT_NOTICE)
      await log("info", "stopped after the turn budget", { goalId })
      return { kind: "turn_limit" }
    }

    if (state.pendingTurn) {
      const waitOutcome = await waitForTurn({
        transport,
        sessionID,
        state,
        sentPromptIds: state.sentPromptIds || [],
        ...waitTurnOptions,
        shouldPause: async () => {
          const stored = await stateStore.read(goalId)
          return Boolean(stored && stored.autoResume === false)
        },
      })
      if (waitOutcome.kind === "turn_hold") {
        const wasHolding = state.phase === "session_paused_hold"
        state = await stateStore.write(goalId, {
          ...state,
          phase: "session_paused_hold",
          pendingTurnActivityAt: Number(waitOutcome.activityAt) || state.pendingTurnActivityAt,
          pausedReason: "session_paused_hold",
        })
        if (!wasHolding) {
          await transport.sendSynthetic(
            sessionID,
            "The session appears paused. The LoopX worker holds quietly and resumes automatically when the session continues.",
          )
          await log("info", "pending turn went quiet; holding without prompting", {
            goalId,
            sessionID,
            quietForMs: waitOutcome.quietForMs,
          })
        }
        await sleepImpl(5 * 60_000)
        continue
      }
      if (waitOutcome.kind === "paused_state") {
        await log("info", "worker state was paused while waiting for the turn", { goalId, sessionID })
        return { kind: "paused_state" }
      }
      if (waitOutcome.kind === "session_stalled") {
        state = await stateStore.write(goalId, {
          ...state,
          phase: "session_stalled",
          autoResume: false,
        pausedReason: "session_stalled",
      })
      await transport.sendSynthetic(
        sessionID,
        "LoopX paused this goal because the OpenCode session stopped making progress.",
      )
      await log("warn", "session stalled; worker paused", { goalId, sessionID })
      return { kind: "session_stalled" }
    }
    if (waitOutcome.kind === "turn_timeout") {
      state = await stateStore.write(goalId, {
        ...state,
        phase: "turn_timeout",
        autoResume: false,
        pausedReason: "turn_timeout",
      })
      await transport.sendSynthetic(
        sessionID,
        "LoopX paused this goal because the last OpenCode turn exceeded the wait budget.",
      )
      await log("warn", "turn wait budget exhausted", { goalId, sessionID })
      return { kind: "turn_timeout" }
    }

    state = await stateStore.write(goalId, { ...state, pendingTurn: false })
    if (state.turnCount >= maxTurns) {
      state = await stateStore.write(goalId, { ...state, phase: "turn_limit" })
      await transport.sendSynthetic(sessionID, TURN_LIMIT_NOTICE)
      await log("info", "stopped after the turn budget", { goalId, sessionID })
      return { kind: "turn_limit" }
    }
    }

    state = await stateStore.read(goalId)
    if (!state || state.autoResume === false) {
      return { kind: "paused_state" }
    }

    let decision
    try {
      decision = await quotaProbe(state, { directory: directoryNow })
      const current = await stateStore.read(goalId)
      if (!current || current.autoResume === false) return { kind: "paused_state" }
      state = current
      state = await stateStore.write(goalId, {
        ...state,
        probeRetryCount: 0,
        lastPollAt: new Date().toISOString(),
      })
    } catch (error) {
      const current = await stateStore.read(goalId)
      if (!current || current.autoResume === false) return { kind: "paused_state" }
      state = current
      const plan = retryPlan(decision, state)
      state = await stateStore.write(goalId, {
        ...state,
        phase: "quota_probe_retry",
        probeRetryCount: plan.probeRetryCount,
        lastPollAt: new Date().toISOString(),
      })
      await log("warn", "LoopX quota probe failed closed; scheduling a bounded retry", {
        goalId,
        error: String(error?.message || error).slice(0, 200),
        retryMinutes: plan.minutes,
      })
      await sleepImpl(plan.minutes * 60_000)
      continue
    }

    if (decision?.status === "goal_not_found" || decision?.state === "unknown") {
      state = await stateStore.write(goalId, {
        ...state,
        phase: "goal_gone",
        autoResume: false,
        pausedReason: "goal_not_found",
      })
      await log("info", "goal is no longer registered; worker finished", { goalId, sessionID })
      return { kind: "goal_gone" }
    }

    const stopped = decision?.scheduler_hint?.action === "stop_until_explicit_resume"
    if (stopped || isTerminalNoFollowup(decision)) {
      state = await stateStore.write(goalId, {
        ...state,
        autoResume: false,
        phase: "paused",
        pausedReason: stopped ? "stop_until_explicit_resume" : "terminal_no_followup",
      })
      await log("info", "Core stopped automatic continuation; explicit resume requires fresh admission", { goalId, sessionID })
      return { kind: "paused_state" }
    }

    if (shouldRunNow(decision)) {
      state = await stateStore.write(goalId, {
        ...state,
        phase: "working",
        schedulerToken: "",
        unchangedPolls: 0,
        standbyPolls: 0,
      })
      const initialBody = state.taskBody || taskBody
      // A paused initial dispatch must retain its original objective across restart.
      // Legacy bindings may supply it explicitly; never substitute a generic continue.
      if (state.turnCount === 0 && !initialBody && !sessionId) {
        throw new Error("unstarted worker requires its original --task-body or an existing --session-id")
      }
      await transport.sendPrompt(sessionID, state.turnCount === 0 && initialBody ? initialBody : continuationPrompt(directoryNow))
      state = await stateStore.write(goalId, {
        ...state,
        pendingTurn: true,
        lastPromptAt: Date.now(),
        pendingTurnActivityAt: Date.now(),
        turnCount: Number(state.turnCount || 0) + 1,
      })
      await log("info", "quota granted another turn", { goalId, sessionID, turnCount: state.turnCount })
      continue
    }

    const wait = waitPlan(decision, state)
    if (wait.stop) {
      state = await stateStore.write(goalId, {
        ...state,
        phase: "limit_stopped",
        autoResume: false,
        pausedReason: "unchanged_poll_limit",
      })
      await transport.sendSynthetic(sessionID, LIMIT_NOTICE)
      await log("info", "unchanged-poll limit stopped the worker", { goalId, sessionID })
      return { kind: "limit_stopped" }
    }
    state = await stateStore.write(goalId, {
      ...state,
      phase: "waiting",
      schedulerToken: wait.schedulerToken,
      unchangedPolls: wait.unchangedPolls,
    })
    await log("info", "waiting before the next quota poll", {
      goalId,
      sessionID,
      minutes: wait.minutes,
    })
    await sleepImpl(wait.minutes * 60_000)
  }
}


export function parseWorkerArgs(argv) {
  const args = { availableCapabilities: [] }
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index]
    const value = argv[index + 1]
    const readValue = (name) => {
      if (value === undefined || value === "") throw new Error(`${name} requires a value`)
      index += 1
      return value
    }
    switch (token) {
      case "--goal-id":
        args.goalId = readValue(token)
        break
      case "--directory":
        args.directory = readValue(token)
        break
      case "--agent-id":
        args.agentId = readValue(token)
        break
      case "--registry":
        args.registryPath = readValue(token)
        break
      case "--session-id":
        args.sessionId = readValue(token)
        break
      case "--task-body":
        args.taskBody = readValue(token)
        break
      case "--capability":
        args.availableCapabilities.push(readValue(token))
        break
      case "--max-turns": {
        const parsed = Number(readValue(token))
        if (!Number.isInteger(parsed) || parsed < 1) throw new Error("--max-turns must be a positive integer")
        args.maxTurns = parsed
        break
      }
      case "--max-duration-minutes": {
        const parsed = Number(readValue(token))
        if (!Number.isFinite(parsed) || parsed < 1) throw new Error("--max-duration-minutes must be a positive number")
        args.maxDurationMs = Math.round(parsed * 60 * 1000)
        break
      }
      case "--force-resume":
        args.forceResume = true
        break
      case "--state-dir":
        process.env.LOOPX_OPENCODE2_STATE_DIR = readValue(token)
        break
      case "--help":
        args.help = true
        break
      default:
        throw new Error(`unknown argument: ${token}`)
    }
  }
  return args
}


export function renderResult(result) {
  return JSON.stringify({ version: 1, operation: "opencode2_goal_worker", ok: true, result }, null, 2)
}


function isMainModule() {
  // Compare realpaths: on macOS /var is a symlink to /private/var, so the
  // argv[1] used to invoke the materialized worker can resolve differently
  // from import.meta.url even though they name the same file. A mismatch
  // silently skips main() and the CLI reports a fake success.
  if (!process.argv[1]) return false
  const argvPath = path.resolve(process.argv[1])
  const modulePath = fileURLToPath(import.meta.url)
  try {
    return realpathSync(argvPath) === realpathSync(modulePath)
  } catch {
    return argvPath === modulePath
  }
}


async function main() {
  const args = parseWorkerArgs(process.argv.slice(2))
  if (args.help || !args.goalId) {
    process.stdout.write(
      [
        "LoopX OpenCode 2 goal worker",
        "",
        "usage: opencode2-goal-worker.mjs --goal-id <id> --directory <dir>",
        "       [--agent-id <id>] [--registry <path>] [--capability <name>]...",
        "       [--session-id <id>] [--task-body <text>] [--max-turns <n>]",
        "       [--max-duration-minutes <n>] [--force-resume] [--state-dir <dir>]",
        "",
      ].join("\n"),
    )
    return 0
  }
  const log = async (level, message, extra = {}) => {
    process.stderr.write(`${new Date().toISOString()} ${level} ${message} ${JSON.stringify(extra)}\n`)
  }
  try {
    const result = await runWorker({ ...args, log })
    process.stdout.write(`${renderResult(result)}\n`)
    return 0
  } catch (error) {
    await log("error", "worker failed", { error: String(error?.stack || error) })
    process.stdout.write(
      `${JSON.stringify({
        version: 1,
        operation: "opencode2_goal_worker",
        ok: false,
        error: String(error?.message || error),
      })}\n`,
    )
    return 1
  }
}


if (isMainModule()) {
  main().then((code) => {
    process.exitCode = code
  })
}
