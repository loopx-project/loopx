import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { performance } from 'node:perf_hooks'
import type { Session, SessionEvent } from '@deepseek-ai/dsh-session'
import { appendLedgerLines, ShadowObserver } from '../src/observer.ts'
import type { ObserverRunIdentity, ShadowObserverConfig } from '../src/observer.ts'

const GOAL_ID = 'goal-observer-overhead-fixture'
const SESSION_ID = 'session-observer-overhead-fixture'
const OBSERVER_ID = 'observer-overhead-fixture'
const EVENT_CYCLE = 6
const EVENT_TIME_BASE = 1_756_728_000_000

const RUN_IDENTITY: ObserverRunIdentity = {
  worker_id: 'worker-overhead-fixture',
  model_id: 'model-overhead-fixture',
  task_id: 'task-overhead-fixture',
  environment_id: 'environment-overhead-fixture',
  tools_id: 'tools-overhead-fixture',
  budget_id: 'budget-overhead-fixture',
  adapter_revision: 'adapter-overhead-fixture',
  observer_revision: 'observer-overhead-fixture',
}

const SESSION = { id: SESSION_ID } as unknown as Session

export type PlannedEvent =
  | { readonly kind: 'created' }
  | { readonly kind: 'event'; readonly event: SessionEvent }
  | { readonly kind: 'disposed' }

export interface TrialOptions {
  readonly inputEvents: number
  readonly bufferBound: number
  readonly failAppend?: boolean
}

export interface TrialMeasurement {
  readonly workloadEvents: number
  readonly observedEvents: number
  readonly expectedObservedEvents: number
  readonly acceptedEvents: number
  readonly persistedEvents: number
  readonly droppedEvents: number
  readonly rejectedEvents: number
  readonly flushAttempts: number
  readonly peakBufferedEvents: number
  readonly observerFailures: number
  readonly cpuUserMs: number
  readonly cpuSystemMs: number
  readonly cpuTotalMs: number
  readonly rssBeforeBytes: number
  readonly rssAfterBytes: number
  readonly rssDeltaBytes: number
  readonly wallMs: number
  readonly ledgerBytes: number
  readonly envelopeBytes: number
  readonly statsBytes: number
  readonly clockUncertaintyMaxMs: number
  readonly harnessTimeEvents: number
  readonly wallClockEvents: number
  readonly envelopeKinds: Readonly<Record<string, number>>
  readonly expectedEnvelopeKinds: Readonly<Record<string, number>>
  readonly appendLatenciesMs: readonly number[]
  readonly outboundEndpoints: number
  readonly workerContextInfluence: boolean
  readonly schedulerInputInfluence: boolean
}

export function normalizeEventCount(inputEvents: number): number {
  return Math.max(EVENT_CYCLE, Math.floor(inputEvents / EVENT_CYCLE) * EVENT_CYCLE)
}

/**
 * The record mix one trial must produce for its timings to be comparable: one
 * lifecycle record per session and one record per planned event kind per turn
 * cycle. Each trial carries this histogram so a silently retired event kind or
 * an unknown-kind substitution can never read as a clean measurement.
 */
export function plannedEnvelopeKinds(workloadEvents: number): Readonly<Record<string, number>> {
  const cycles = Math.floor(workloadEvents / EVENT_CYCLE)
  return {
    session_started: 1,
    session_disposed: 1,
    turn_started: cycles,
    step_started: cycles,
    tool_called: cycles,
    tool_completed: cycles,
    step_ended: cycles,
    turn_ended: cycles,
  }
}

function sessionEvent(type: string, seq: number, data: unknown): SessionEvent {
  return {
    type,
    seq,
    time: EVENT_TIME_BASE + seq * 1_000,
    data,
  } as unknown as SessionEvent
}

/**
 * A deterministic turn cycle: one start/step/tool/result/end per turn, plus a
 * terminal disposal. The tool intentionally avoids random data so two runs
 * compare the same serialized payloads.
 */
export function buildEventPlan(inputEvents: number): readonly PlannedEvent[] {
  const normalized = normalizeEventCount(inputEvents)
  const cycles = normalized / EVENT_CYCLE
  const plan: PlannedEvent[] = [{ kind: 'created' }]
  let seq = 1
  for (let turn = 1; turn <= cycles; turn += 1) {
    plan.push({ kind: 'event', event: sessionEvent('turn/start', seq, { turn }) })
    seq += 1
    plan.push({ kind: 'event', event: sessionEvent('step/start', seq, { turn, step: 1 }) })
    seq += 1
    plan.push({
      kind: 'event',
      event: sessionEvent('tool/call', seq, {
        turn,
        step: 1,
        callId: `call-${turn}`,
        name: 'read',
      }),
    })
    seq += 1
    plan.push({
      kind: 'event',
      event: sessionEvent('tool/result', seq, {
        turn,
        step: 1,
        message: { source: { callId: `call-${turn}` } },
      }),
    })
    seq += 1
    plan.push({ kind: 'event', event: sessionEvent('step/end', seq, { turn, step: 1 }) })
    seq += 1
    plan.push({
      kind: 'event',
      event: sessionEvent('turn/end', seq, { turn, reason: { kind: 'completed' } }),
    })
    seq += 1
  }
  plan.push({ kind: 'disposed' })
  return plan
}

function safeGc(): void {
  const gc = (globalThis as { gc?: () => void }).gc
  gc?.()
}

function yieldToEventLoop(): Promise<void> {
  return new Promise(resolve => {
    setImmediate(resolve)
  })
}

function cpuMeasurement(cpu: NodeJS.CpuUsage): {
  readonly userMs: number
  readonly systemMs: number
  readonly totalMs: number
} {
  return {
    userMs: cpu.user / 1_000,
    systemMs: cpu.system / 1_000,
    totalMs: (cpu.user + cpu.system) / 1_000,
  }
}

export function runBaselineTrial(options: TrialOptions): TrialMeasurement {
  const plan = buildEventPlan(options.inputEvents)
  const workloadEvents = normalizeEventCount(options.inputEvents)
  safeGc()
  const rssBeforeBytes = process.memoryUsage().rss
  const cpuBefore = process.cpuUsage()
  const wallStart = performance.now()
  let checksum = 0
  for (const planned of plan) {
    if (planned.kind === 'event') checksum += planned.event.seq
    else checksum += 1
  }
  const wallMs = performance.now() - wallStart
  const cpu = cpuMeasurement(process.cpuUsage(cpuBefore))
  const rssAfterBytes = process.memoryUsage().rss
  if (!Number.isFinite(checksum) || checksum <= 0) {
    throw new Error('synthetic baseline event stream was empty')
  }
  return {
    workloadEvents,
    observedEvents: plan.length,
    expectedObservedEvents: plan.length,
    cpuUserMs: cpu.userMs,
    cpuSystemMs: cpu.systemMs,
    cpuTotalMs: cpu.totalMs,
    rssBeforeBytes,
    rssAfterBytes,
    rssDeltaBytes: rssAfterBytes - rssBeforeBytes,
    wallMs,
    acceptedEvents: 0,
    persistedEvents: 0,
    droppedEvents: 0,
    rejectedEvents: 0,
    flushAttempts: 0,
    peakBufferedEvents: 0,
    observerFailures: 0,
    ledgerBytes: 0,
    envelopeBytes: 0,
    statsBytes: 0,
    clockUncertaintyMaxMs: 0,
    harnessTimeEvents: 0,
    wallClockEvents: 0,
    // The baseline driver runs no observer and persists no envelopes.
    envelopeKinds: {},
    expectedEnvelopeKinds: {},
    appendLatenciesMs: [],
    outboundEndpoints: 0,
    workerContextInfluence: false,
    schedulerInputInfluence: false,
  }
}

export async function runObserverTrial(options: TrialOptions): Promise<TrialMeasurement> {
  const plan = buildEventPlan(options.inputEvents)
  const workloadEvents = normalizeEventCount(options.inputEvents)
  const ledgerDir = await mkdtemp(join(tmpdir(), 'loopx-observer-overhead-'))
  const appendLatenciesMs: number[] = []
  try {
    const config: ShadowObserverConfig = {
      goalId: GOAL_ID,
      sessionId: SESSION_ID,
      runIdentity: RUN_IDENTITY,
      ledgerDir,
      bufferBound: options.bufferBound,
    }
    const observer = new ShadowObserver({
      config,
      now: () => EVENT_TIME_BASE,
      observerId: OBSERVER_ID,
      // Delegate to the shipped writer so the measured append path (including
      // the legacy-ledger refusal and directory creation) cannot drift from
      // what the observer actually does in production. Only the timing wraps it.
      appendLines: async (_path, lines) => {
        if (options.failAppend === true) {
          throw new Error('synthetic ledger append failure')
        }
        const start = performance.now()
        await appendLedgerLines(config, lines)
        appendLatenciesMs.push(performance.now() - start)
      },
    })
    safeGc()
    const rssBeforeBytes = process.memoryUsage().rss
    const cpuBefore = process.cpuUsage()
    const wallStart = performance.now()
    observer.observeSessionCreated(SESSION)
    for (const planned of plan) {
      if (planned.kind === 'created') continue
      if (planned.kind === 'event') {
        observer.observeSessionEvent(SESSION, planned.event)
        if (planned.event.type === 'turn/end') {
          // One macrotask tick lets a completed append resolve. An append still
          // in flight coalesces with this turn's flush request instead of being
          // awaited; the report prints the actual flush attempts.
          await yieldToEventLoop()
        }
      } else {
        observer.observeSessionDisposed(SESSION)
      }
    }
    await observer.dispose()
    const wallMs = performance.now() - wallStart
    const cpu = cpuMeasurement(process.cpuUsage(cpuBefore))
    const rssAfterBytes = process.memoryUsage().rss
    const stats = observer.stats()
    let persistedEvents = 0
    let envelopeBytes = 0
    let statsBytes = 0
    let clockUncertaintyMaxMs = 0
    let harnessTimeEvents = 0
    let wallClockEvents = 0
    let envelopeKinds: Record<string, number> = {}
    try {
      const text = await readFile(observer.path, 'utf8')
      for (const line of text.split('\n')) {
        if (line === '') continue
        const lineBytes = Buffer.byteLength(line, 'utf8') + 1
        if (!line.includes('reliability_observer_envelope_v0')) {
          statsBytes += lineBytes
          continue
        }
        persistedEvents += 1
        envelopeBytes += lineBytes
        try {
          const record = JSON.parse(line) as {
            clock?: { source?: string, uncertainty_ms?: number },
            event_kind?: string,
          }
          const source = record.clock?.source
          if (source === 'harness_event_time') harnessTimeEvents += 1
          else if (typeof source === 'string') wallClockEvents += 1
          const uncertainty = record.clock?.uncertainty_ms
          if (typeof uncertainty === 'number' && Number.isFinite(uncertainty)) {
            clockUncertaintyMaxMs = Math.max(clockUncertaintyMaxMs, uncertainty)
          }
          const kind = record.event_kind
          if (typeof kind === 'string') envelopeKinds[kind] = (envelopeKinds[kind] ?? 0) + 1
        } catch {
          // A malformed line still counts as persisted; integrity reports it.
        }
      }
    } catch {
      persistedEvents = 0
      envelopeBytes = 0
      statsBytes = 0
      harnessTimeEvents = 0
      wallClockEvents = 0
      envelopeKinds = {}
    }
    return {
      workloadEvents,
      observedEvents: stats.observed_event_count,
      expectedObservedEvents: plan.length,
      cpuUserMs: cpu.userMs,
      cpuSystemMs: cpu.systemMs,
      cpuTotalMs: cpu.totalMs,
      rssBeforeBytes,
      rssAfterBytes,
      rssDeltaBytes: rssAfterBytes - rssBeforeBytes,
      wallMs,
      acceptedEvents: stats.accepted_event_count,
      persistedEvents,
      droppedEvents: stats.backpressure_drop_count,
      rejectedEvents: stats.rejected_event_count,
      flushAttempts: stats.flush_attempt_count,
      peakBufferedEvents: stats.peak_buffered_event_count,
      observerFailures: stats.observer_failure_count,
      ledgerBytes: envelopeBytes + statsBytes,
      envelopeBytes,
      statsBytes,
      clockUncertaintyMaxMs,
      harnessTimeEvents,
      wallClockEvents,
      envelopeKinds,
      expectedEnvelopeKinds: plannedEnvelopeKinds(workloadEvents),
      appendLatenciesMs,
      outboundEndpoints: stats.outbound_endpoints.length,
      workerContextInfluence: stats.observation_entered_worker_context,
      schedulerInputInfluence: stats.observation_entered_scheduler_inputs,
    }
  } finally {
    await rm(ledgerDir, { recursive: true, force: true })
  }
}
