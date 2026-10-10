// <!-- loopx-managed-slash-command:v1 command=/loopx surface=pi-extension-runtime -->
//
// LoopX Pi goal loop runtime — the pure, directly executable core of the Pi
// host adapter. The installed extension (loopx-goal.ts) wires Pi's
// ExtensionAPI into createGoalLoop; node:test drives this same module through
// injected dependencies (tests/pi_goal_loop_runtime.test.mjs), so the
// store/quota/wait lifecycle is verified at runtime instead of by string
// matching.
//
// Lifecycle contract:
// - Only LoopX-derived terminal state stops auto-continuation; the loop never
//   self-declares closure.
// - Quota probe failures fail closed with a bounded retry instead of guessing.
// - dispose() atomically invalidates the extension instance: every timer is
//   cancelled, and an evaluation that is mid-flight across an await returns to
//   an epoch guard before any write / notify / send / timer side effect, so it
//   can never continue the old session past a reload / session-replacement
//   boundary.
// - Goal identity is a binding contract: every binding carries a per-session
//   generation that activate() increments, and every evaluation write commits
//   through the store's compare-and-swap (expected generation + goalId). A
//   stale evaluation whose write was already in-flight when the same session
//   activated a new goal is rejected at the store commit boundary, so it can
//   never merge old terminal/scheduler state or send the old task body.
// - Sessions without a session file (pi --no-session, ephemeral) use a unique
//   in-memory identity per extension instance and are never persisted, so a
//   later run cannot inherit the previous run's binding.
import { createHash, randomUUID } from "node:crypto"
import { promises as fs } from "node:fs"
import path from "node:path"

export const BRIDGE_SCHEMA_VERSION = "loopx_pi_goal_bridge_v0"
export const TERMINAL_STATE_SCHEMA_VERSION = "goal_terminal_state_v0"
export const SOURCE_COMPLETENESS_SCHEMA_VERSION = "goal_terminal_source_completeness_v0"
export const PI_SESSION_AUTHORITY_SCHEMA_VERSION = "loopx_pi_session_authority_v0"
export const PI_ACTIVATION_SCHEMA_VERSION = "loopx_pi_goal_activation_v0"
export const DEFAULT_RETRY_MINUTES = 3
export const TASK_LEASE_CAPABILITY = "task_lease_v0"
export const TASK_LEASE_SCHEMA_VERSION = "task_lease_v0"
export const TASK_LEASE_ACTIONS = Object.freeze([
  "acquire",
  "renew",
  "transfer",
  "release",
  "inspect",
])

const TASK_LEASE_MUTATIONS = new Set(["acquire", "renew", "transfer", "release"])
const TASK_LEASE_MAX_TTL_SECONDS = 24 * 60 * 60
const TASK_LEASE_TODO_ID_PATTERN = /^todo_[a-z0-9_-]{3,64}$/
const TASK_LEASE_AGENT_ID_PATTERN = /^[a-z][a-z0-9_.:@-]{0,79}$/
const TASK_LEASE_IDEMPOTENCY_PATTERN = /^[A-Za-z0-9_.:@/-]{1,160}$/
const TASK_LEASE_ACTION_FIELDS = Object.freeze({
  acquire: Object.freeze([
    ["idempotencyKey", "--idempotency-key", "token", true],
    ["ttlSeconds", "--ttl-seconds", "ttl", false],
    ["expectedVersion", "--expected-version", "version", false],
    ["writeScopes", "--write-scope", "scopes", false],
  ]),
  renew: Object.freeze([
    ["idempotencyKey", "--idempotency-key", "token", true],
    ["ttlSeconds", "--ttl-seconds", "ttl", false],
    ["expectedVersion", "--expected-version", "version", true],
  ]),
  transfer: Object.freeze([
    ["idempotencyKey", "--idempotency-key", "token", true],
    ["newOwner", "--new-owner", "owner", true],
    ["newIdempotencyKey", "--new-idempotency-key", "token", true],
    ["ttlSeconds", "--ttl-seconds", "ttl", false],
    ["expectedVersion", "--expected-version", "version", true],
  ]),
  release: Object.freeze([
    ["idempotencyKey", "--idempotency-key", "token", true],
    ["expectedVersion", "--expected-version", "version", true],
  ]),
  inspect: Object.freeze([]),
})

// The label prefix of a session key reserves room for the digest suffix so
// that createBindingStore's filename sanitization (160 chars) can never cut
// the digest off: label(48) + "-" + digest(16) = 65 chars.
const SESSION_KEY_LABEL_MAX = 48
const SESSION_KEY_DIGEST_LENGTH = 16

export function sanitizedKey(value) {
  const cleaned = String(value || "")
    .replace(/[^A-Za-z0-9_-]/g, "_")
    .slice(0, 160)
  return cleaned || "session"
}

// Collision-resistant session key from the full session file path. Uses a
// short human-readable basename prefix plus a SHA-256 digest of the complete
// path, so two files whose first 161 bytes are identical never produce the
// same durable key and the digest always survives filename sanitization.
export function sessionKey(sessionFile) {
  const digest = createHash("sha256")
    .update(sessionFile)
    .digest("hex")
    .slice(0, SESSION_KEY_DIGEST_LENGTH)
  const label = sanitizedKey(path.basename(sessionFile)).slice(0, SESSION_KEY_LABEL_MAX)
  return label ? `${label}-${digest}` : `session-${digest}`
}

export function stateRoot(directory) {
  if (process.env.LOOPX_PI_STATE_DIR) {
    return path.resolve(process.env.LOOPX_PI_STATE_DIR)
  }
  return path.join(directory, ".loopx", "pi")
}

function bindingDefaults(directory) {
  return {
    schemaVersion: BRIDGE_SCHEMA_VERSION,
    directory,
    goalId: "",
    agentId: "",
    registryPath: "",
    availableCapabilities: [],
    activationToken: "",
    taskBody: "",
    autoResume: true,
    terminal: false,
    // Why the loop stopped auto-resuming: "" (armed), "user_prompt", or
    // "aborted". A later /loopx resume clears it again.
    pauseReason: "",
    generation: 0,
    schedulerToken: "",
    unchangedPolls: 0,
    lastInjectedPrompt: "",
  }
}

function typedError(code, message) {
  const error = new Error(message)
  error.code = code
  return error
}

const authorityError = typedError
const taskLeaseRequestError = typedError

function normalizeAuthorityCapabilities(value) {
  if (!Array.isArray(value)) return []
  return [...new Set(value.map((item) => String(item).trim()).filter(Boolean))].sort((left, right) =>
    left.localeCompare(right),
  )
}

export function normalizePiSessionAuthority(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw authorityError("authority_not_bound", "Pi session has no host-verified authority")
  }
  const token = String(value.token || "").trim()
  const goalId = String(value.goalId || "").trim()
  const agentId = String(value.agentId || "").trim()
  const registryPath = String(value.registryPath || "").trim()
  if (!token || !goalId || !agentId || !registryPath) {
    throw authorityError(
      "authority_not_bound",
      "host-verified Pi authority must include token, goal, agent, and registry",
    )
  }
  return {
    schemaVersion: PI_SESSION_AUTHORITY_SCHEMA_VERSION,
    token,
    goalId,
    agentId,
    registryPath,
    availableCapabilities: normalizeAuthorityCapabilities(value.availableCapabilities),
  }
}

function sameAuthority(left, right) {
  return left?.schemaVersion === right?.schemaVersion &&
    left?.token === right?.token &&
    left?.goalId === right?.goalId &&
    left?.agentId === right?.agentId &&
    left?.registryPath === right?.registryPath &&
    JSON.stringify(left?.availableCapabilities || []) ===
      JSON.stringify(right?.availableCapabilities || [])
}

function assertActivationAuthority(authority, fields) {
  const normalized = normalizePiSessionAuthority(authority)
  const suppliedToken = String(fields?.activationToken || "").trim()
  if (!suppliedToken) {
    throw authorityError(
      "authority_token_required",
      "loopx_goal_activate requires the host-issued session authority token",
    )
  }
  if (suppliedToken !== normalized.token) {
    throw authorityError("authority_mismatch", "Pi session authority token does not match")
  }
  const checks = [
    ["goalId", fields?.goalId, normalized.goalId],
    ["agentId", fields?.agentId, normalized.agentId],
    ["registryPath", fields?.registryPath, normalized.registryPath],
  ]
  for (const [field, supplied, expected] of checks) {
    if (supplied !== undefined && String(supplied || "").trim() !== expected) {
      throw authorityError(
        "authority_mismatch",
        `${field} does not match the host-verified Pi session authority`,
      )
    }
  }
  if (fields?.availableCapabilities !== undefined) {
    const supplied = normalizeAuthorityCapabilities(fields.availableCapabilities)
    if (JSON.stringify(supplied) !== JSON.stringify(normalized.availableCapabilities)) {
      throw authorityError(
        "capability_not_verified",
        "availableCapabilities must match the host-verified Pi session authority",
      )
    }
  }
  return normalized
}

function casMatches(current, expected) {
  if (!expected) return true
  if (expected.generation !== undefined && (current?.generation || 0) !== expected.generation) {
    return false
  }
  if (expected.goalId !== undefined && (current?.goalId || "") !== expected.goalId) {
    return false
  }
  if (expected.autoResume !== undefined && current?.autoResume !== expected.autoResume) {
    return false
  }
  return true
}

// Pi emits only messages created by the just-finished run in agent_end. An
// assistant message with stopReason=aborted is the durable signal that the
// owner pressed Escape (or otherwise aborted the active run), so the host
// adapter must pause before agent_settled can evaluate another continuation.
export function hasAbortedAssistantMessage(messages) {
  return Array.isArray(messages) &&
    messages.some((message) => message?.role === "assistant" && message?.stopReason === "aborted")
}

// File-backed binding store scoped to one project. Bindings live under the
// gitignored `.loopx/` tree so they survive Pi restarts without touching
// tracked repository state. Writes for one key are serialized through a queue,
// and the optional `expected` argument turns a write into a compare-and-swap:
// the commit is rejected (returns null) unless the persisted generation and
// goalId still match, so a stale evaluation can never overwrite a newly
// activated binding.
export function createBindingStore(directory) {
  const root = stateRoot(directory)
  const target = (key) => path.join(root, `${sanitizedKey(key)}.json`)
  const queues = new Map()
  const enqueue = (key, task) => {
    const previous = queues.get(key) || Promise.resolve()
    const next = previous.then(task)
    queues.set(
      key,
      next.then(
        () => {},
        () => {},
      ),
    )
    return next
  }
  return {
    async read(key) {
      try {
        const payload = JSON.parse(await fs.readFile(target(key), "utf8"))
        if (payload?.schemaVersion !== BRIDGE_SCHEMA_VERSION || payload?.sessionKey !== key) {
          return null
        }
        return payload
      } catch (error) {
        if (error?.code === "ENOENT") return null
        throw error
      }
    },
    async write(key, changes, expected) {
      return enqueue(key, async () => {
        const current = await this.read(key)
        if (!casMatches(current, expected)) return null
        const payload = {
          ...bindingDefaults(directory),
          sessionKey: key,
          ...(current || {}),
          ...changes,
          updatedAt: new Date().toISOString(),
        }
        await fs.mkdir(root, { recursive: true, mode: 0o700 })
        const destination = target(key)
        const temporary = `${destination}.${process.pid}.${Date.now()}.tmp`
        await fs.writeFile(temporary, `${JSON.stringify(payload, null, 2)}\n`, {
          encoding: "utf8",
          mode: 0o600,
        })
        await fs.rename(temporary, destination)
        return payload
      })
    },
    async remove(key) {
      return enqueue(key, async () => {
        try {
          await fs.unlink(target(key))
        } catch (error) {
          if (error?.code !== "ENOENT") throw error
        }
      })
    },
  }
}

// In-memory binding store for ephemeral sessions: nothing ever reaches the
// filesystem, so a `pi --no-session` run cannot leak a binding to a later run.
// Single-threaded Map access makes the compare-and-swap atomic.
export function createMemoryBindingStore() {
  const bindings = new Map()
  return {
    bindings,
    async read(key) {
      return bindings.get(key) || null
    },
    async write(key, changes, expected) {
      const current = bindings.get(key) || {}
      if (!casMatches(current, expected)) return null
      const payload = {
        ...bindingDefaults(""),
        sessionKey: key,
        ...current,
        ...changes,
      }
      bindings.set(key, payload)
      return payload
    },
    async remove(key) {
      bindings.delete(key)
    },
  }
}

// Per-extension-instance identity for sessions without a session file. Every
// instance gets a unique key and its own in-memory store, so consecutive
// `--no-session` runs can never inherit a previous run's binding; the current
// run must activate the goal again through loopx_goal_activate.
export function createEphemeralSessionIdentity() {
  return {
    key: `ephemeral-${randomUUID()}`,
    store: createMemoryBindingStore(),
  }
}

// Mirrors the OpenCode bridge probe: LoopX quota should-run is the only
// continuation authority for the visible goal loop.
export function buildQuotaArgs(binding) {
  const args = []
  if (binding.registryPath) args.push("--registry", binding.registryPath)
  args.push(
    "--format",
    "json",
    "quota",
    "should-run",
    "--goal-id",
    binding.goalId,
    "--runtime-profile",
    "generic_cli",
    "--include-detail",
    "scheduler",
  )
  if (binding.agentId) args.push("--agent-id", binding.agentId)
  for (const capability of binding.availableCapabilities || []) {
    args.push("--available-capability", capability)
  }
  return args
}

function taskLeaseFailure(action, errorCode, message) {
  return {
    ok: false,
    schema_version: TASK_LEASE_SCHEMA_VERSION,
    action: action || null,
    error: message,
    error_code: errorCode,
  }
}

function nonEmptyTaskLeaseString(value, field) {
  const normalized = String(value ?? "").trim()
  if (!normalized) {
    throw taskLeaseRequestError("invalid_request", `${field} is required`)
  }
  return normalized
}

function taskLeaseFormString(value, field, pattern, description) {
  const normalized = nonEmptyTaskLeaseString(value, field)
  if (!pattern.test(normalized)) {
    throw taskLeaseRequestError("invalid_request", `${field} must be ${description}`)
  }
  return normalized
}

function taskLeaseGoalId(value, field = "goalId") {
  const normalized = nonEmptyTaskLeaseString(value, field)
  if (normalized === "." || normalized === ".." || /[\\/]/.test(normalized)) {
    throw taskLeaseRequestError("invalid_request", `${field} must be a single path segment`)
  }
  return normalized
}

function taskLeaseTodoId(value, field = "todoId") {
  return taskLeaseFormString(value, field, TASK_LEASE_TODO_ID_PATTERN, "a todo_<token> id")
}

function taskLeaseOwner(value, field = "agentId") {
  return taskLeaseFormString(value, field, TASK_LEASE_AGENT_ID_PATTERN, "a public-safe agent id")
}

function taskLeaseIdempotencyKey(value, field) {
  return taskLeaseFormString(value, field, TASK_LEASE_IDEMPOTENCY_PATTERN, "a public-safe token")
}

function taskLeaseWriteScope(value, field) {
  const normalized = nonEmptyTaskLeaseString(value, field)
  if (
    normalized.length > 160 ||
    normalized.startsWith("/") ||
    normalized.startsWith("~") ||
    normalized.split("/").includes("..") ||
    /[\s<>]/.test(normalized)
  ) {
    throw taskLeaseRequestError("invalid_request", `${field} must be a relative write scope`)
  }
  return normalized
}

function assertTaskLeaseInteger(value, field, { minimum = 0, maximum } = {}) {
  if (
    !Number.isInteger(value) ||
    value < minimum ||
    (maximum !== undefined && value > maximum)
  ) {
    const range = maximum === undefined ? `>= ${minimum}` : `between ${minimum} and ${maximum}`
    throw taskLeaseRequestError("invalid_request", `${field} must be an integer ${range}`)
  }
  return value
}

function taskLeaseBindingAuthority(binding, action, verifiedAuthority) {
  if (!binding || typeof binding !== "object") {
    throw taskLeaseRequestError("missing_goal_binding", "Pi session has no active LoopX goal binding")
  }
  if (verifiedAuthority !== undefined) {
    const authority = normalizePiSessionAuthority(verifiedAuthority)
    const bindingCapabilities = normalizeAuthorityCapabilities(binding.availableCapabilities)
    if (
      String(binding.activationToken || "") !== authority.token ||
      String(binding.goalId || "") !== authority.goalId ||
      String(binding.agentId || "") !== authority.agentId ||
      String(binding.registryPath || "") !== authority.registryPath ||
      JSON.stringify(bindingCapabilities) !== JSON.stringify(authority.availableCapabilities)
    ) {
      throw taskLeaseRequestError(
        "authority_mismatch",
        "Pi session binding does not match the host-verified authority",
      )
    }
  }
  const goalId = taskLeaseGoalId(binding.goalId)
  const fields = TASK_LEASE_ACTION_FIELDS[action]
  if (!fields) {
    throw taskLeaseRequestError(
      "unsupported_action",
      `task lease action must be one of: ${TASK_LEASE_ACTIONS.join(", ")}`,
    )
  }
  if (!TASK_LEASE_MUTATIONS.has(action)) return { goalId, owner: "", fields }
  if (binding.terminal === true) {
    throw taskLeaseRequestError("inactive_goal_binding", "Pi session goal binding is terminal")
  }
  const owner = taskLeaseOwner(binding.agentId)
  const capabilities = Array.isArray(binding.availableCapabilities)
    ? binding.availableCapabilities
    : []
  if (!capabilities.includes(TASK_LEASE_CAPABILITY)) {
    throw taskLeaseRequestError(
      "capability_not_advertised",
      `Pi must explicitly advertise ${TASK_LEASE_CAPABILITY} before ${action}`,
    )
  }
  return { goalId, owner, fields }
}

function taskLeaseFieldValues(request, field, kind, required) {
  const value = request[field]
  if (value === undefined) {
    if (required) throw taskLeaseRequestError("invalid_request", `${field} is required`)
    return []
  }
  if (kind === "string") return [nonEmptyTaskLeaseString(value, field)]
  if (kind === "token") return [taskLeaseIdempotencyKey(value, field)]
  if (kind === "owner") return [taskLeaseOwner(value, field)]
  if (kind === "version") return [String(assertTaskLeaseInteger(value, field))]
  if (kind === "ttl") {
    return [
      String(
        assertTaskLeaseInteger(value, field, {
          minimum: 1,
          maximum: TASK_LEASE_MAX_TTL_SECONDS,
        }),
      ),
    ]
  }
  if (!Array.isArray(value)) {
    throw taskLeaseRequestError("invalid_request", `${field} must be an array`)
  }
  return value.map((item) => taskLeaseWriteScope(item, `${field} entry`))
}

// Build the exact CLI argv used by the installed Pi extension. Goal and owner
// authority come from the active session binding; request fields are only the
// lifecycle inputs the agent is allowed to choose.
export function buildTaskLeaseArgs(binding, request, verifiedAuthority) {
  request = request ?? {}
  if (!request || typeof request !== "object" || Array.isArray(request)) {
    throw taskLeaseRequestError("invalid_request", "task lease request must be an object")
  }
  const action = String(request.action || "").trim()
  const { goalId, owner, fields } = taskLeaseBindingAuthority(binding, action, verifiedAuthority)
  for (const field of ["goalId", "owner"]) {
    if (request[field] !== undefined) {
      throw taskLeaseRequestError(
        "authority_mismatch",
        `${field} is supplied by the active Pi session binding`,
      )
    }
  }
  const allowed = new Set(["action", "todoId", ...fields.map(([field]) => field)])
  const unsupported = Object.keys(request).filter(
    (field) => request[field] !== undefined && !allowed.has(field),
  )
  if (unsupported.length) {
    throw taskLeaseRequestError(
      "invalid_request",
      `${action || "task lease"} does not accept: ${unsupported.join(", ")}`,
    )
  }
  const todoId = taskLeaseTodoId(request.todoId)
  const args = []
  if (binding.registryPath) args.push("--registry", String(binding.registryPath))
  args.push(
    "--format",
    "json",
    "task-lease",
    action,
    "--goal-id",
    goalId,
    "--todo-id",
    todoId,
  )
  if (TASK_LEASE_MUTATIONS.has(action)) args.push("--owner", owner)
  for (const [field, flag, kind, required] of fields) {
    for (const value of taskLeaseFieldValues(request, field, kind, required)) {
      args.push(flag, value)
    }
  }
  return args
}

function parseTaskLeaseCliPayload(stdout) {
  const text = String(stdout || "").trim()
  if (!text) return null
  try {
    const payload = JSON.parse(text)
    if (
      !payload ||
      typeof payload !== "object" ||
      Array.isArray(payload) ||
      payload.schema_version !== TASK_LEASE_SCHEMA_VERSION
    ) return null
    return payload
  } catch {
    return null
  }
}

function validTaskLeaseCliPayload(payload, action, returncode) {
  if (!payload || payload.action !== action || typeof payload.ok !== "boolean") return false
  if (payload.ok === false &&
      (typeof payload.error !== "string" || !payload.error.trim() ||
       typeof payload.error_code !== "string" || !payload.error_code.trim())) return false
  if (Number.isInteger(returncode)) {
    if (payload.ok === true && returncode !== 0) return false
    if (payload.ok === false && returncode === 0) return false
  }
  return true
}

function compactTaskLeasePayload(payload) {
  const privatePaths = new Set()
  const collect = (value) => {
    if (Array.isArray(value)) value.forEach(collect)
    else if (value && typeof value === "object") {
      for (const [key, child] of Object.entries(value)) {
        if (key === "lease_path" && typeof child === "string" && child) privatePaths.add(child)
        collect(child)
      }
    }
  }
  collect(payload)
  const compact = (value) => {
    if (typeof value === "string") {
      let text = value
      for (const privatePath of privatePaths) text = text.split(privatePath).join("<private-path>")
      return text
    }
    if (Array.isArray(value)) return value.map(compact)
    if (!value || typeof value !== "object") return value
    return Object.fromEntries(
      Object.entries(value)
        .filter(([key]) => key !== "lease_path")
        .map(([key, child]) => [key, compact(child)]),
    )
  }
  return compact(payload)
}

// Execute a task-lease request through an injected CLI transport. A typed
// non-zero payload is authoritative and survives process rejection; malformed
// or empty output fails closed without exposing stderr or raw host details.
function cliReturncode(error) {
  if (typeof error?.returncode === "number") return error.returncode
  if (typeof error?.code === "number") return error.code
  return 1
}

export async function runPiTaskLease(binding, request, runCli, verifiedAuthority) {
  request = request ?? {}
  const action = String(request?.action || "").trim() || null
  if (verifiedAuthority === undefined) {
    return taskLeaseFailure(
      action,
      "authority_not_bound",
      "Pi session has no current host-verified authority",
    )
  }
  let args
  try {
    args = buildTaskLeaseArgs(binding, request, verifiedAuthority)
  } catch (error) {
    return taskLeaseFailure(
      action,
      String(error?.code || "invalid_request"),
      String(error?.message || "invalid task lease request"),
    )
  }
  if (typeof runCli !== "function") {
    return taskLeaseFailure(action, "transport_error", "task lease CLI transport is unavailable")
  }
  let result
  try {
    result = await runCli(args, String(binding.directory || ""))
  } catch (error) {
    result = {
      stdout: error?.stdout,
      returncode: cliReturncode(error),
    }
  }
  const stdout = typeof result === "string" ? result : result?.stdout
  const returncode = typeof result === "string" ? undefined : result?.returncode
  const payload = parseTaskLeaseCliPayload(stdout)
  if (validTaskLeaseCliPayload(payload, action, returncode)) return compactTaskLeasePayload(payload)
  return taskLeaseFailure(
    action,
    String(stdout || "").trim() ? "protocol_error" : "transport_error",
    String(stdout || "").trim()
      ? "task lease CLI returned an invalid typed payload"
      : "task lease CLI returned no typed payload",
  )
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
  const hint = decision?.scheduler_hint
  return hint?.action === "run_now" || decision?.should_run === true
}

export function waitPlan(decision, binding) {
  const hint = decision?.scheduler_hint || {}
  const unchanged = hint?.unchanged_poll || {}
  const local = hint?.cold_path_detail?.local_scheduler
  if (!local || unchanged?.local_scheduler === "stop") {
    return {
      stop: true,
      minutes: DEFAULT_RETRY_MINUTES,
      schedulerToken: binding.schedulerToken,
      unchangedPolls: binding.unchangedPolls,
    }
  }
  const reset = hint?.reset_policy || {}
  const token = String(reset?.reset_token || "")
  const sameIdentity = Boolean(token) && token === binding.schedulerToken
  const unchangedPolls = sameIdentity ? Number(binding.unchangedPolls || 0) : 0
  const limit = Number.isInteger(unchanged?.limits?.local_scheduler)
    ? Number(unchanged.limits.local_scheduler)
    : null
  const afterLimit = String(unchanged?.after_limits?.local_scheduler || "")
  const atLimit = (
    limit !== null &&
    unchangedPolls + 1 >= limit &&
    afterLimit === "stop_tick_loop"
  )
  if (atLimit) {
    const finalCheck = (
      unchanged?.final_quota_replan_check_enabled === true &&
      unchanged?.final_quota_replan_check_action === "rerun_quota_should_run_once"
    )
    return {
      stop: true,
      finalCheck,
      minutes: DEFAULT_RETRY_MINUTES,
      schedulerToken: token,
      unchangedPolls,
    }
  }
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

function schedulerContractIdentity(decision) {
  const hint = decision?.scheduler_hint || {}
  return [
    Boolean(decision?.should_run),
    String(hint?.action || ""),
    String(hint?.cadence_class || ""),
    String(hint?.reset_policy?.reset_token || ""),
    String(decision?.effective_action || ""),
    String(decision?.selected_todo?.todo_id || ""),
  ]
}

function sameSchedulerContract(left, right) {
  const leftIdentity = schedulerContractIdentity(left)
  const rightIdentity = schedulerContractIdentity(right)
  return leftIdentity.every((value, index) => value === rightIdentity[index])
}

// The quota-gated auto-continuation loop for one extension instance.
//
// options:
//   quotaProbe(binding)      async LoopX quota should-run probe
//   sendMessage(prompt)      inject the heartbeat task body as a follow-up
//   setTimer(cb, delayMs)    schedule a timer, returns an opaque handle
//   clearTimer(handle)       cancel a scheduled timer
//
// The loop keeps per-session timers and evaluations but owns one instance-wide
// epoch, so session shutdown atomically invalidates every session's scheduled
// and in-flight work at once. Goal identity is enforced through the binding's
// persisted generation: activate() increments it and every evaluation write
// commits via the store's compare-and-swap with the captured generation and
// goalId, so a stale evaluation cannot commit past a re-activation even when
// its write was already in-flight.
export function createGoalLoop(options) {
  const { quotaProbe, sendMessage, setTimer, clearTimer } = options
  const timers = new Map()
  const evaluations = new Map()
  const contexts = new Map()
  const authorities = new Map()
  let disposed = false
  let epoch = 0

  const cancelScheduled = (key) => {
    const timer = timers.get(key)
    if (timer !== undefined) clearTimer(timer)
    timers.delete(key)
  }

  const cancelAll = () => {
    for (const timer of timers.values()) clearTimer(timer)
    timers.clear()
    evaluations.clear()
  }

  const scheduleEvaluation = (key, minutes) => {
    if (disposed) return
    cancelScheduled(key)
    const scheduledEpoch = epoch
    const timer = setTimer(async () => {
      timers.delete(key)
      if (disposed || epoch !== scheduledEpoch) return
      try {
        await evaluateIdle(key)
      } catch {
        // Evaluation must never crash the host; fail closed with a retry.
        scheduleEvaluation(key, DEFAULT_RETRY_MINUTES)
      }
    }, Math.max(1, minutes) * 60_000)
    timers.set(key, timer)
  }

  const evaluateIdleOnce = async (key) => {
    if (disposed) return
    const instanceEpoch = epoch
    const services = contexts.get(key)
    if (!services) {
      cancelScheduled(key)
      return
    }
    const { store, isIdle } = services
    // Every store write inside this evaluation can throw (disk error,
    // permission denied). The timer callback already reschedules on error;
    // this top-level catch gives the direct evaluation path the same
    // fail-closed-with-retry contract.
    try {
    let binding = null
    try {
      binding = await store.read(key)
    } catch {
      cancelScheduled(key)
      return
    }
    if (disposed || epoch !== instanceEpoch) return
    if (!binding || binding.terminal) {
      cancelScheduled(key)
      return
    }
    if (binding.autoResume === false || !isIdle()) {
      cancelScheduled(key)
      return
    }

    // Capture the binding identity so every commit below can CAS against it:
    // a re-activation that happens while this evaluation is in-flight bumps
    // the persisted generation and rejects the stale commit.
    const capturedGen = binding.generation || 0
    const capturedGoalId = binding.goalId
    const expected = { generation: capturedGen, goalId: capturedGoalId, autoResume: true }

    let decision
    try {
      decision = await quotaProbe(binding)
    } catch {
      if (disposed || epoch !== instanceEpoch) return
      // Fail closed: never continue without LoopX authority. Bounded retry.
      scheduleEvaluation(key, DEFAULT_RETRY_MINUTES)
      return
    }
    if (disposed || epoch !== instanceEpoch) return

    const current = await store.read(key)
    if (disposed || epoch !== instanceEpoch) return
    if (
      !current ||
      current.terminal ||
      current.autoResume === false ||
      current.generation !== capturedGen ||
      current.goalId !== capturedGoalId
    ) {
      cancelScheduled(key)
      return
    }

    const applyDecision = async (nextDecision, allowFinalCheck = true) => {
      if (isTerminalNoFollowup(nextDecision)) {
        // Commit through the store's compare-and-swap: if the same session
        // activated a new goal while this write was in-flight, the commit is
        // rejected and the new binding stays alive.
        const committed = await store.write(key, { terminal: true, autoResume: false }, expected)
        if (!committed) {
          cancelScheduled(key)
          return
        }
        if (disposed || epoch !== instanceEpoch) return
        cancelScheduled(key)
        services.notify(
          `LoopX goal ${current.goalId} reached validated terminal no-follow-up; loop stopped.`,
          "info",
        )
        return
      }

      if (shouldRunNow(nextDecision)) {
        cancelScheduled(key)
        const schedulerCommit = await store.write(
          key,
          { schedulerToken: "", unchangedPolls: 0 },
          expected,
        )
        if (!schedulerCommit) {
          cancelScheduled(key)
          return
        }
        if (disposed || epoch !== instanceEpoch) return
        const prompt = current.taskBody || current.goalId
        const promptCommit = await store.write(key, { lastInjectedPrompt: prompt }, expected)
        if (!promptCommit) {
          cancelScheduled(key)
          return
        }
        if (disposed || epoch !== instanceEpoch) return
        sendMessage(prompt)
        return
      }

      const wait = waitPlan(nextDecision, current)
      if (wait.stop) {
        cancelScheduled(key)
        if (!(wait.finalCheck && allowFinalCheck)) return
        let finalDecision
        try {
          finalDecision = await quotaProbe(current)
        } catch {
          if (disposed || epoch !== instanceEpoch) return
          scheduleEvaluation(key, DEFAULT_RETRY_MINUTES)
          return
        }
        if (disposed || epoch !== instanceEpoch) return
        const latest = await store.read(key)
        if (disposed || epoch !== instanceEpoch) return
        if (
          !latest ||
          latest.terminal ||
          latest.autoResume === false ||
          latest.generation !== capturedGen ||
          latest.goalId !== capturedGoalId
        ) {
          cancelScheduled(key)
          return
        }
        if (sameSchedulerContract(nextDecision, finalDecision)) return
        await applyDecision(finalDecision, false)
        return
      }

      const waitCommit = await store.write(
        key,
        {
          schedulerToken: wait.schedulerToken,
          unchangedPolls: wait.unchangedPolls,
        },
        expected,
      )
      if (!waitCommit) {
        cancelScheduled(key)
        return
      }
      if (disposed || epoch !== instanceEpoch) return
      scheduleEvaluation(key, wait.minutes)
    }

    await applyDecision(decision)
    } catch {
      // Fail closed with bounded retry: any unhandled error (store write
      // failure, etc.) must not break the evaluation chain permanently.
      cancelScheduled(key)
      scheduleEvaluation(key, DEFAULT_RETRY_MINUTES)
    }
  }

  const evaluateIdle = (key) => {
    if (disposed) return Promise.resolve()
    const existing = evaluations.get(key)
    if (existing) return existing
    const evaluation = evaluateIdleOnce(key).finally(() => {
      if (evaluations.get(key) === evaluation) evaluations.delete(key)
    })
    evaluations.set(key, evaluation)
    return evaluation
  }

  return {
    // Bind the ctx-derived services (store, isIdle, notify) for a session key.
    // Re-binding on every event keeps the loop free of host types while still
    // using the freshest context available. The adapter must pass one stable
    // store instance per key so the store's per-key commit queue is shared.
    bind(key, services) {
      if (services?.authority !== undefined) {
        const authority = normalizePiSessionAuthority(services.authority)
        const current = authorities.get(key)
        if (current && !sameAuthority(current, authority)) {
          throw authorityError(
            "authority_mismatch",
            "Pi session authority cannot change after host binding",
          )
        }
        authorities.set(key, authority)
      }
      contexts.set(key, services)
    },
    // Host-only seam: the Pi adapter calls this after it receives a verified
    // startup/session packet. Model-callable tools never receive this method.
    bindAuthority(key, authority) {
      const normalized = normalizePiSessionAuthority(authority)
      const current = authorities.get(key)
      if (current && !sameAuthority(current, normalized)) {
        throw authorityError(
          "authority_mismatch",
          "Pi session authority cannot change after host binding",
        )
      }
      authorities.set(key, normalized)
      return normalized
    },
    cancel(key) {
      cancelScheduled(key)
    },
    async activate(key, fields) {
      if (disposed) return null
      const services = contexts.get(key)
      if (!services) throw new Error("loopx_goal_activate requires a bound session context")
      const authority = authorities.get(key) || services.authority
      const verified = assertActivationAuthority(authority, fields)
      // Increment the persisted generation so every in-flight evaluation for
      // this key is rejected at its next compare-and-swap commit.
      const current = await services.store.read(key)
      const generation = (current?.generation || 0) + 1
      const binding = await services.store.write(key, {
        ...fields,
        activationToken: verified.token,
        goalId: verified.goalId,
        agentId: verified.agentId,
        registryPath: verified.registryPath,
        availableCapabilities: verified.availableCapabilities,
        generation,
      })
      if (!binding) {
        throw authorityError("authority_mismatch", "Pi session binding changed during activation")
      }
      if (disposed) return null
      cancelScheduled(key)
      services.notify(
        `LoopX goal ${binding.goalId} activated; continuation gated by LoopX quota.`,
        "info",
      )
      return binding
    },
    async settle(key) {
      if (disposed) return
      const instanceEpoch = epoch
      const services = contexts.get(key)
      if (!services) return
      let binding = null
      try {
        binding = await services.store.read(key)
      } catch {
        return
      }
      if (disposed || epoch !== instanceEpoch) return
      if (!binding || binding.terminal || binding.autoResume === false) return
      await evaluateIdle(key)
    },
    // `options.hostGenerated` marks a prompt the host surface produced on its
    // own rather than the owner typing: Pi sends the continuation that follows
    // its own context compaction through the same event as user input, and that
    // continuation must not be treated as the user taking the wheel. Prompts the
    // loop injected itself are already recognized through lastInjectedPrompt.
    async userPrompt(key, prompt, options = {}) {
      if (disposed) return
      const instanceEpoch = epoch
      const services = contexts.get(key)
      if (!services) return
      if (options?.hostGenerated === true) return
      let binding = null
      try {
        binding = await services.store.read(key)
      } catch {
        return
      }
      if (disposed || epoch !== instanceEpoch) return
      if (!binding || binding.terminal) return
      if (String(prompt || "") !== binding.lastInjectedPrompt) {
        const expected = { generation: binding.generation || 0, goalId: binding.goalId }
        const committed = await services.store.write(
          key,
          { autoResume: false, pauseReason: "user_prompt" },
          expected,
        )
        if (!committed) return
        if (disposed || epoch !== instanceEpoch) return
        cancelScheduled(key)
        services.notify(
          `LoopX goal ${committed.goalId} auto-continuation paused by user input; run /loopx resume to continue.`,
          "info",
        )
      }
    },
    async interrupt(key) {
      if (disposed) return null
      const instanceEpoch = epoch
      const services = contexts.get(key)
      if (!services) return null
      let binding = null
      try {
        binding = await services.store.read(key)
      } catch {
        return null
      }
      if (disposed || epoch !== instanceEpoch) return null
      if (!binding || binding.terminal) return binding
      const expected = { generation: binding.generation || 0, goalId: binding.goalId }
      const committed = await services.store.write(
        key,
        { autoResume: false, pauseReason: "aborted" },
        expected,
      )
      if (!committed || disposed || epoch !== instanceEpoch) return committed
      cancelScheduled(key)
      services.notify(
        `LoopX goal ${committed.goalId} auto-continuation paused after abort; run /loopx resume to continue.`,
        "info",
      )
      return committed
    },
    async resume(key) {
      if (disposed) return null
      const services = contexts.get(key)
      if (!services) return null
      const binding = await services.store.write(key, {
        autoResume: true,
        terminal: false,
        pauseReason: "",
      })
      if (disposed) return null
      cancelScheduled(key)
      services.notify(`LoopX goal ${binding.goalId} auto-continuation resumed.`, "info")
      return binding
    },
    // Session shutdown / session replacement: atomically invalidate this
    // extension instance and cancel every timer. A quota probe that is
    // already in flight — or an evaluation suspended on any later store
    // await — returns to the epoch guard, so it cannot write, notify, send
    // the old task body, or reschedule a timer past the boundary.
    dispose() {
      disposed = true
      epoch += 1
      cancelAll()
    },
  }
}
