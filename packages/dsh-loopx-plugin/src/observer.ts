/**
 * L1 shadow observer for DeepSeek Harness (`dsh-session-events` provider).
 *
 * One-way only: this module consumes read-only harness events and appends
 * `reliability_observer_envelope_v0` records plus a
 * `reliability_observer_stats_v0` record to the LoopX reliability-diagnostics
 * ledger. It imports nothing from `driver.ts`, owns no `agent.send`, inbox,
 * timer, LoopX CLI, or continuation path, and every hook body is isolated so a
 * failure is counted instead of propagating into the harness. Field names
 * mirror `loopx/capabilities/reliability_diagnostics/envelope.py` exactly.
 */

import { createHash, randomUUID } from 'node:crypto'
import { lstatSync } from 'node:fs'
import { appendFile, mkdir } from 'node:fs/promises'
import { homedir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import type { Context } from '@deepseek-ai/cordis'
import type { Session, SessionEvent } from '@deepseek-ai/dsh-session'

export const name = 'dsh-loopx-shadow-observer'
export const inject: readonly string[] = []

export const CAPABILITY_ID = 'reliability-diagnostics'
export const PROVIDER_ID = 'dsh-session-events'
export const OBSERVER_ENVELOPE_SCHEMA_VERSION = 'reliability_observer_envelope_v0'
export const OBSERVER_STATS_SCHEMA_VERSION = 'reliability_observer_stats_v0'
export const LEDGER_DIRNAME = 'reliability_diagnostics'
export const DEFAULT_BUFFER_BOUND = 256
export const MAX_BUFFER_BOUND = 65_536
/** Declared skew for events stamped by the observer instead of the harness log. */
export const WALL_CLOCK_UNCERTAINTY_MS = 50

export const ENV_GOAL_ID = 'LOOPX_DSH_SHADOW_OBSERVER_GOAL_ID'
export const ENV_SESSION_ID = 'LOOPX_DSH_SHADOW_OBSERVER_SESSION_ID'
export const ENV_RUN_IDENTITY = 'LOOPX_DSH_SHADOW_OBSERVER_RUN_IDENTITY_JSON'
export const ENV_LEDGER_DIR = 'LOOPX_DSH_SHADOW_OBSERVER_LEDGER_DIR'
export const ENV_BUFFER_BOUND = 'LOOPX_DSH_SHADOW_OBSERVER_BUFFER_BOUND'

export const EVENT_SOURCES = [
  'session/created',
  'session/disposed',
  'session/event',
] as const
export const SOURCE_FIELDS_CONSUMED = [
  'event.data.callId',
  'event.data.error.code',
  'event.data.id',
  'event.data.message.source.callId',
  'event.data.name',
  'event.data.reason.kind',
  'event.data.source.kind',
  'event.data.step',
  'event.data.turn',
  'event.seq',
  'event.time',
  'event.type',
  'session.id',
] as const
export const RUN_IDENTITY_FIELDS = [
  'worker_id',
  'model_id',
  'task_id',
  'environment_id',
  'tools_id',
  'budget_id',
  'adapter_revision',
  'observer_revision',
] as const

const IDENTITY_TOKEN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,120}$/u
const SUMMARY_TOKEN = /^[A-Za-z0-9][A-Za-z0-9_./:-]{0,79}$/u
const LOCAL_PATH_SURFACES = [
  /(?<![:/a-z0-9])\/(?:users|home|volumes|private|tmp|var|etc|opt|srv|mnt|root|workspace|workspaces)\/[^\s`'"<>]+/iu,
  /(?<![:/a-z0-9])[a-z]:[/\\](?:users|documents and settings)[/\\][^\s`'"<>]+/iu,
] as const
const SECRET_LIKE_SURFACES = [
  /\bbearer\s+[a-z0-9._~+/=-]{16,}/iu,
  /\b(?:access|secret)[_-]?key\s*[=:]\s*[^\s`'"<>]+/iu,
  /\b(?:ak|sk)\s*[=:]\s*[^\s`'"<>]+/iu,
  /(?<![a-z0-9_])(?:ak|sk)[-_=:][a-z0-9_=-]{10,}/iu,
  /\bgh[pousr]_[a-z0-9]{20,}\b/iu,
  /\beyj[a-z0-9_-]{10,}\.[a-z0-9_-]{10,}\.[a-z0-9_-]{10,}\b/iu,
  /\btoken\s*[=:]\s*[^\s`'"<>]{12,}/iu,
] as const
const CREDENTIAL_FIELD_FAMILIES = new Set([
  'accesskey', 'accesstoken', 'apikey', 'authtoken', 'authorization',
  'clientsecret', 'cookie', 'credential', 'credentials', 'password',
  'privatekey', 'refreshtoken', 'secret', 'sessiontoken', 'token',
])
const CREDENTIAL_FIELD_SUFFIXES = [
  '_token', '_secret', '_password', '_credential', '_credentials',
] as const
const UNBOUNDED_PAYLOAD_FIELD_FAMILIES = new Set(['requestbody', 'responsebody'])

export type ObserverEventKind =
  | 'session_started'
  | 'turn_started'
  | 'turn_ended'
  | 'step_started'
  | 'step_ended'
  | 'user_message'
  | 'tool_called'
  | 'tool_completed'
  | 'agent_status'
  | 'agent_pre_step'
  | 'agent_error'
  | 'session_disposed'
  | 'unsupported'

export type ClockSource = 'harness_event_time' | 'observer_wall_clock' | 'fixture'

export type ObserverRejectionReason =
  | 'identity_invalid'
  | 'clock_invalid'
  | 'public_safety_violation'
  | 'observer_internal_failure'

export interface ObserverEnvelope {
  readonly schema_version: typeof OBSERVER_ENVELOPE_SCHEMA_VERSION
  readonly capability_id: typeof CAPABILITY_ID
  readonly provider_id: typeof PROVIDER_ID
  readonly observer_id: string
  readonly goal_id: string
  readonly session_id: string
  readonly agent_id?: string
  readonly sequence: number
  readonly observed_at: string
  readonly clock: { readonly source: ClockSource, readonly uncertainty_ms: number }
  readonly event_kind: ObserverEventKind
  readonly summary: Readonly<Record<string, number | string>>
  readonly source_refs: Readonly<Record<string, string>>
}

export interface ObserverRunIdentity {
  readonly worker_id: string
  readonly model_id: string
  readonly task_id: string
  readonly environment_id: string
  readonly tools_id: string
  readonly budget_id: string
  readonly adapter_revision: string
  readonly observer_revision: string
}

export interface ObserverStats {
  readonly schema_version: typeof OBSERVER_STATS_SCHEMA_VERSION
  readonly capability_id: typeof CAPABILITY_ID
  readonly provider_id: typeof PROVIDER_ID
  readonly observer_id: string
  readonly goal_id: string
  readonly run_identity: ObserverRunIdentity
  readonly event_sources: readonly string[]
  readonly source_fields_consumed: readonly string[]
  readonly emitted_at: string
  readonly observed_event_count: number
  readonly accepted_event_count: number
  readonly rejected_event_count: number
  readonly rejected_by_reason: Readonly<Record<string, number>>
  readonly buffer_bound: number
  readonly backpressure_drop_count: number
  readonly observer_failure_count: number
  readonly peak_buffered_event_count: number
  readonly flush_attempt_count: number
  /** Always empty: the observer has no outbound control path to declare. */
  readonly outbound_endpoints: readonly []
  readonly observation_entered_worker_context: false
  readonly observation_entered_scheduler_inputs: false
  readonly clock_source: ClockSource
}

export interface ShadowObserverConfig {
  readonly goalId: string
  readonly sessionId: string
  readonly runIdentity: ObserverRunIdentity
  readonly ledgerDir: string
  readonly bufferBound: number
}

export type LedgerAppender = (path: string, lines: readonly string[]) => Promise<void>

export interface ShadowObserverOptions {
  readonly config: ShadowObserverConfig
  readonly now?: (() => number) | undefined
  readonly appendLines?: LedgerAppender | undefined
  readonly warn?: ((message: string) => void) | undefined
  readonly observerId?: string | undefined
}

function normalizePublicSafeFieldName(value: string): string {
  const normalized = value
    .replace(/([a-z0-9])([A-Z])/gu, '$1_$2')
    .replace(/[^a-z0-9]+/giu, '_')
    .toLowerCase()
  const withoutLeading = normalized.startsWith('_') ? normalized.slice(1) : normalized
  return withoutLeading.endsWith('_') ? withoutLeading.slice(0, -1) : withoutLeading
}

function isPublicSafeText(value: string): boolean {
  return !LOCAL_PATH_SURFACES.some(pattern => pattern.test(value))
    && !SECRET_LIKE_SURFACES.some(pattern => pattern.test(value))
}

/** Keep the producer boundary equivalent to LoopX's recursive Python guard. */
function isPublicSafeValue(value: unknown): boolean {
  if (typeof value === 'string') return isPublicSafeText(value)
  if (Array.isArray(value)) return value.every(item => isPublicSafeValue(item))
  if (typeof value !== 'object' || value === null) return true
  return Object.entries(value).every(([key, item]) => {
    if (!isPublicSafeText(key)) return false
    const normalized = normalizePublicSafeFieldName(key)
    const flattened = normalized.replaceAll('_', '')
    if (CREDENTIAL_FIELD_FAMILIES.has(flattened)
      || CREDENTIAL_FIELD_SUFFIXES.some(suffix => normalized.endsWith(suffix))) return false
    if (UNBOUNDED_PAYLOAD_FIELD_FAMILIES.has(flattened)) return false
    if (normalized === 'raw' || normalized.startsWith('raw_')) return false
    return isPublicSafeValue(item)
  })
}

export function defaultLedgerDir(env: NodeJS.ProcessEnv = process.env): string {
  const configured = env[ENV_LEDGER_DIR]
  return resolve(configured?.trim()
    ? configured
    : join(homedir(), '.codex', 'loopx', LEDGER_DIRNAME))
}

/**
 * The observer is OFF unless one exact goal, session, and pinned run identity
 * are declared. Returning `undefined` is the feature-off path: no hooks or
 * files. Partial or malformed configuration never broadens observation.
 */
export function resolveShadowObserverConfig(
  env: NodeJS.ProcessEnv = process.env,
): ShadowObserverConfig | undefined {
  const goalId = env[ENV_GOAL_ID]?.trim()
  const sessionId = env[ENV_SESSION_ID]?.trim()
  const runIdentity = parseRunIdentity(env[ENV_RUN_IDENTITY])
  if (!goalId || !IDENTITY_TOKEN.test(goalId)
    || !isPublicSafeText(goalId)
    || !sessionId || !IDENTITY_TOKEN.test(sessionId)
    || !isPublicSafeText(sessionId)
    || runIdentity === undefined) return undefined
  const rawBound = Number.parseInt(env[ENV_BUFFER_BOUND] ?? '', 10)
  const bufferBound = Number.isInteger(rawBound) && rawBound >= 1 && rawBound <= MAX_BUFFER_BOUND
    ? rawBound
    : DEFAULT_BUFFER_BOUND
  return { goalId, sessionId, runIdentity, ledgerDir: defaultLedgerDir(env), bufferBound }
}

function validRunIdentity(value: unknown): value is ObserverRunIdentity {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const record = value as Record<string, unknown>
  const actual = Object.keys(record).sort((left, right) => left.localeCompare(right))
  const expected = [...RUN_IDENTITY_FIELDS].sort((left, right) => left.localeCompare(right))
  return actual.length === expected.length
    && expected.every((field, index) => actual[index] === field)
    && RUN_IDENTITY_FIELDS.every((field) => {
      const item = record[field]
      return typeof item === 'string' && IDENTITY_TOKEN.test(item) && isPublicSafeText(item)
    })
}

function parseRunIdentity(raw: string | undefined): ObserverRunIdentity | undefined {
  if (raw === undefined) return undefined
  try {
    const parsed = JSON.parse(raw) as unknown
    return validRunIdentity(parsed) ? parsed : undefined
  } catch {
    return undefined
  }
}

export function ledgerPath(config: ShadowObserverConfig): string {
  const digest = createHash('sha256').update(config.goalId, 'utf8').digest('hex')
  return join(config.ledgerDir, 'by-goal', `${digest}.ndjson`)
}

async function appendLedgerLines(config: ShadowObserverConfig, lines: readonly string[]): Promise<void> {
  if (lines.length === 0) return
  const legacy = join(config.ledgerDir, `${config.goalId.replaceAll(':', '_')}.ndjson`)
  if (lstatSync(legacy, { throwIfNoEntry: false })) {
    // The flush boundary counts this failure without exposing a local path or stopping the worker.
    const error = new Error('legacy diagnostic ledger requires offline reconciliation')
    error.name = 'LegacyDiagnosticLedgerError'
    throw error
  }
  const path = ledgerPath(config)
  await mkdir(dirname(path), { recursive: true })
  await appendFile(path, `${lines.join('\n')}\n`, 'utf8')
}

function token(value: unknown): string | undefined {
  return typeof value === 'string' && SUMMARY_TOKEN.test(value) ? value : undefined
}

function identity(value: unknown): string | undefined {
  return typeof value === 'string' && IDENTITY_TOKEN.test(value) ? value : undefined
}

function count(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : undefined
}

interface CompactEvent {
  readonly kind: ObserverEventKind
  readonly summary: Record<string, number | string>
  readonly sourceRefs: Record<string, string>
}

/**
 * Event types the installed generation no longer emits but that a durable log
 * written by an earlier release still replays. They stay unconsumed instead of
 * turning into one `unsupported` row per historical token.
 */
const RETIRED_SESSION_EVENT_TYPES: ReadonlySet<string> = new Set(['assistant/chunk'])

function compactSessionEvent(event: SessionEvent): CompactEvent | undefined {
  if (RETIRED_SESSION_EVENT_TYPES.has(event.type)) return undefined
  const data = event.data as Record<string, unknown>
  const summary: Record<string, number | string> = {}
  const sourceRefs: Record<string, string> = { event_seq: String(event.seq) }
  const put = (key: string, value: number | string | undefined): void => {
    if (value !== undefined) summary[key] = value
  }
  const ref = (key: string, value: string | undefined): void => {
    if (value !== undefined) sourceRefs[key] = value
  }
  put('turn', count(data.turn))
  put('step', count(data.step))
  switch (event.type) {
    case 'turn/start':
      return { kind: 'turn_started', summary, sourceRefs }
    case 'turn/end':
      put('reason', token((data.reason as Record<string, unknown> | undefined)?.kind))
      return { kind: 'turn_ended', summary, sourceRefs }
    case 'step/start':
      return { kind: 'step_started', summary, sourceRefs }
    case 'step/end':
      return { kind: 'step_ended', summary, sourceRefs }
    case 'user/message': {
      const source = data.source as Record<string, unknown> | undefined
      put('message_source_kind', token(source?.kind))
      ref('message_id', identity(data.id))
      return { kind: 'user_message', summary, sourceRefs }
    }
    case 'tool/call':
      put('tool_name', token(data.name))
      ref('tool_call_id', identity(data.callId))
      return { kind: 'tool_called', summary, sourceRefs }
    case 'tool/result': {
      const error = data.error as Record<string, unknown> | undefined
      const message = data.message as Record<string, unknown> | undefined
      const source = message?.source as Record<string, unknown> | undefined
      put('status', error === undefined ? 'ok' : 'error')
      put('error_class', error === undefined ? undefined : token(error.code))
      ref('tool_call_id', identity(source?.callId))
      return { kind: 'tool_completed', summary, sourceRefs }
    }
    default:
      put('source_event_type', token(event.type))
      return { kind: 'unsupported', summary, sourceRefs }
  }
}

/**
 * Bounded, crash-isolated observer. Mirrors
 * `ShadowObserverIntake` on the Python side: overflow is counted, never
 * blocking; failures are counted, never thrown; the stats record travels with
 * every flush.
 */
export class ShadowObserver {
  private readonly config: ShadowObserverConfig
  private readonly now: () => number
  private readonly appendLines: LedgerAppender
  private readonly warn: (message: string) => void
  private readonly observerId: string
  private nextSequence = 0
  private buffer: ObserverEnvelope[] = []
  private flushing: Promise<void> | undefined
  private flushRequested = false
  private disposed = false
  private observedEventCount = 0
  private acceptedEventCount = 0
  private rejectedEventCount = 0
  private readonly rejectedByReason = new Map<string, number>()
  private backpressureDropCount = 0
  private observerFailureCount = 0
  private peakBufferedEventCount = 0
  private flushAttemptCount = 0

  constructor(options: ShadowObserverOptions) {
    if (!IDENTITY_TOKEN.test(options.config.goalId) || !isPublicSafeText(options.config.goalId)) {
      throw new Error('goal id must be a public-safe identity token')
    }
    if (!IDENTITY_TOKEN.test(options.config.sessionId) || !isPublicSafeText(options.config.sessionId)) {
      throw new Error('session id must be a public-safe identity token')
    }
    if (!validRunIdentity(options.config.runIdentity)) throw new Error('run identity must be fully pinned')
    if (!Number.isInteger(options.config.bufferBound)
      || options.config.bufferBound < 1
      || options.config.bufferBound > MAX_BUFFER_BOUND) {
      throw new Error(`buffer bound must be within 1..${MAX_BUFFER_BOUND}`)
    }
    const observerId = options.observerId ?? `${PROVIDER_ID}-${randomUUID()}`
    if (!IDENTITY_TOKEN.test(observerId) || !isPublicSafeText(observerId)) {
      throw new Error('observer id must be a public-safe identity token')
    }
    this.config = {
      ...options.config,
      runIdentity: { ...options.config.runIdentity },
    }
    this.now = options.now ?? Date.now
    this.appendLines = options.appendLines ?? ((_path, lines) => appendLedgerLines(this.config, lines))
    this.warn = options.warn ?? (() => {})
    this.observerId = observerId
  }

  get path(): string {
    return ledgerPath(this.config)
  }

  observeSessionCreated(session: Session): void {
    this.isolated(() => this.record('session_started', session, undefined, {}, {}))
  }

  observeSessionEvent(session: Session, event: SessionEvent): void {
    this.isolated(() => {
      if (this.rejectIfUnbound(session)) return
      const compact = compactSessionEvent(event)
      if (compact === undefined) return
      const time = typeof event.time === 'number' && Number.isFinite(event.time) ? event.time : undefined
      this.record(compact.kind, session, time, compact.summary, compact.sourceRefs)
      if (event.type === 'turn/end') this.requestFlush()
    })
  }

  observeSessionDisposed(session: Session): void {
    this.isolated(() => {
      this.record('session_disposed', session, undefined, {}, {})
      this.requestFlush()
    })
  }

  stats(): ObserverStats {
    return {
      schema_version: OBSERVER_STATS_SCHEMA_VERSION,
      capability_id: CAPABILITY_ID,
      provider_id: PROVIDER_ID,
      observer_id: this.observerId,
      goal_id: this.config.goalId,
      run_identity: { ...this.config.runIdentity },
      event_sources: EVENT_SOURCES,
      source_fields_consumed: SOURCE_FIELDS_CONSUMED,
      emitted_at: new Date(this.now()).toISOString(),
      observed_event_count: this.observedEventCount,
      accepted_event_count: this.acceptedEventCount,
      rejected_event_count: this.rejectedEventCount,
      rejected_by_reason: Object.fromEntries([...this.rejectedByReason].sort(([left], [right]) => left.localeCompare(right))),
      buffer_bound: this.config.bufferBound,
      backpressure_drop_count: this.backpressureDropCount,
      observer_failure_count: this.observerFailureCount,
      peak_buffered_event_count: this.peakBufferedEventCount,
      flush_attempt_count: this.flushAttemptCount,
      outbound_endpoints: [],
      observation_entered_worker_context: false,
      observation_entered_scheduler_inputs: false,
      clock_source: 'harness_event_time',
    }
  }

  /** Write buffered envelopes plus a stats record; never rejects. */
  async flush(): Promise<void> {
    this.flushRequested = true
    let operation = this.flushing
    if (operation === undefined) {
      operation = this.flushRequestedBatches()
      this.flushing = operation
    }
    await operation
  }

  private async flushRequestedBatches(): Promise<void> {
    try {
      while (this.flushRequested) {
        this.flushRequested = false
        await this.flushBatch()
      }
    } finally {
      // Clear ownership before resolving so a later caller either joins this
      // drain or starts the next one; it can never observe a resolved owner.
      this.flushing = undefined
    }
  }

  private async flushBatch(): Promise<void> {
    const taken = this.buffer
    this.buffer = []
    this.flushAttemptCount += 1
    try {
      const records = [...taken, this.stats()]
      if (!records.every(record => isPublicSafeValue(record))) {
        throw new Error('observer public-safety invariant failed before append')
      }
      const lines = records.map(record => JSON.stringify(record))
      await this.appendLines(this.path, lines)
    } catch (error: unknown) {
      this.observerFailureCount += 1
      this.acceptedEventCount -= taken.length
      this.backpressureDropCount += taken.length
      this.safeWarn(
        `dsh-loopx shadow observer flush failed: ${error instanceof Error ? error.name : 'unknown'}`,
      )
    }
  }

  async dispose(): Promise<void> {
    if (this.disposed) return
    this.disposed = true
    await this.flush()
  }

  private isolated(body: () => void): void {
    if (this.disposed) return
    const observedBefore = this.observedEventCount
    try {
      body()
    } catch (error: unknown) {
      this.observerFailureCount += 1
      if (this.observedEventCount === observedBefore) this.observedEventCount += 1
      this.reject('observer_internal_failure')
      this.safeWarn(
        `dsh-loopx shadow observer hook failed: ${error instanceof Error ? error.name : 'unknown'}`,
      )
    }
  }

  private record(
    kind: ObserverEventKind,
    session: Session,
    harnessTimeMs: number | undefined,
    summary: Record<string, number | string>,
    sourceRefs: Record<string, string>,
  ): void {
    this.observedEventCount += 1
    const sessionId = identity(session.id)
    if (sessionId === undefined || sessionId !== this.config.sessionId) {
      this.reject('identity_invalid')
      return
    }
    const sequence = this.nextSequence
    this.nextSequence += 1
    if (this.buffer.length >= this.config.bufferBound) {
      this.backpressureDropCount += 1
      this.requestFlush()
      return
    }
    const observedAtMs = harnessTimeMs ?? this.now()
    const observedAt = new Date(observedAtMs)
    if (!Number.isFinite(observedAt.getTime())) {
      this.reject('clock_invalid')
      return
    }
    const envelope: ObserverEnvelope = {
      schema_version: OBSERVER_ENVELOPE_SCHEMA_VERSION,
      capability_id: CAPABILITY_ID,
      provider_id: PROVIDER_ID,
      observer_id: this.observerId,
      goal_id: this.config.goalId,
      session_id: sessionId,
      sequence,
      observed_at: observedAt.toISOString(),
      clock: harnessTimeMs === undefined
        ? { source: 'observer_wall_clock', uncertainty_ms: WALL_CLOCK_UNCERTAINTY_MS }
        : { source: 'harness_event_time', uncertainty_ms: 0 },
      event_kind: kind,
      summary,
      source_refs: sourceRefs,
    }
    if (!isPublicSafeValue(envelope)) {
      this.reject('public_safety_violation')
      return
    }
    this.buffer.push(envelope)
    this.acceptedEventCount += 1
    this.peakBufferedEventCount = Math.max(this.peakBufferedEventCount, this.buffer.length)
    if (this.buffer.length >= this.config.bufferBound) this.requestFlush()
  }

  private reject(reason: ObserverRejectionReason): void {
    this.rejectedEventCount += 1
    this.rejectedByReason.set(reason, (this.rejectedByReason.get(reason) ?? 0) + 1)
  }

  private rejectIfUnbound(session: Session): boolean {
    const sessionId = identity(session.id)
    if (sessionId === this.config.sessionId) return false
    this.observedEventCount += 1
    this.reject('identity_invalid')
    return true
  }

  private safeWarn(message: string): void {
    try {
      this.warn(message)
    } catch {
      // Logging is observational too; it must never reach the worker.
    }
  }

  private requestFlush(): void {
    void this.flush()
  }
}

/** Register only the session log's read-only publication hooks. */
export function registerShadowObserver(ctx: Context, config: ShadowObserverConfig): ShadowObserver {
  const observer = new ShadowObserver({
    config,
    warn: message => { ctx.logger.warn(message) },
  })
  ctx.effect(function* () {
    ctx.on('session/created', session => { observer.observeSessionCreated(session) })
    ctx.on('session/event', (session, event) => { observer.observeSessionEvent(session, event) })
    ctx.on('session/disposed', session => { observer.observeSessionDisposed(session) })
    yield async () => {
      await observer.dispose()
    }
  }, 'dsh-loopx shadow observer lifecycle')
  return observer
}

/** Cordis entrypoint. Partial configuration is the exact feature-off path. */
export function apply(ctx: Context): void {
  const config = resolveShadowObserverConfig()
  if (config === undefined) return
  registerShadowObserver(ctx, config)
}
