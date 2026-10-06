import { readFileSync } from 'node:fs'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import { SessionId, SessionStore } from '@deepseek-ai/dsh-session'
import type { Session, SessionEvent, UserMessage } from '@deepseek-ai/dsh-session'
import {
  ledgerPath,
  ENV_GOAL_ID,
  ENV_RUN_IDENTITY,
  ENV_SESSION_ID,
  OBSERVER_ENVELOPE_SCHEMA_VERSION,
  OBSERVER_STATS_SCHEMA_VERSION,
  registerShadowObserver,
  resolveShadowObserverConfig,
  ShadowObserver,
} from '../src/observer.ts'
import type {
  ObserverEnvelope,
  ObserverStats,
  ShadowObserverConfig,
} from '../src/observer.ts'

const goalId = 'goal-observer-fixture'
const sessionId = 'session-fixture'
const runIdentity = {
  worker_id: 'worker-fixture',
  model_id: 'model-fixture',
  task_id: 'task-fixture',
  environment_id: 'environment-fixture',
  tools_id: 'tools-fixture',
  budget_id: 'budget-fixture',
  adapter_revision: 'adapter-fixture',
  observer_revision: 'observer-fixture',
} as const
const config: ShadowObserverConfig = {
  goalId,
  sessionId,
  runIdentity,
  ledgerDir: '/ledger',
  bufferBound: 4,
}
interface PublicSafetyFixture {
  readonly schema_version: string
  readonly unsafe_summary_tokens: readonly string[]
  readonly unsafe_identity_tokens: readonly string[]
  readonly invalid_source_ref_tokens: readonly string[]
  readonly safe_summary_tokens: readonly string[]
  readonly safe_identity_tokens: readonly string[]
}
const TEST_DIR = dirname(fileURLToPath(import.meta.url))
const publicSafetyFixture = JSON.parse(readFileSync(join(
  TEST_DIR,
  '../../../tests/fixtures/control_plane/reliability_diagnostics_public_safety_v0.json',
), 'utf8')) as PublicSafetyFixture
const ENVELOPE_FIELDS = [
  'schema_version', 'capability_id', 'provider_id', 'observer_id', 'goal_id', 'session_id',
  'sequence', 'observed_at', 'clock', 'event_kind', 'summary', 'source_refs',
]
const STATS_FIELDS = [
  'schema_version', 'capability_id', 'provider_id', 'observer_id', 'goal_id', 'run_identity',
  'event_sources', 'source_fields_consumed', 'emitted_at',
  'observed_event_count', 'accepted_event_count', 'rejected_event_count', 'rejected_by_reason',
  'buffer_bound', 'backpressure_drop_count', 'observer_failure_count', 'peak_buffered_event_count',
  'flush_attempt_count', 'outbound_endpoints', 'observation_entered_worker_context',
  'observation_entered_scheduler_inputs', 'clock_source',
]

function fakeSession(id = 'session-fixture'): Session {
  return { id } as unknown as Session
}

function sessionEvent(type: string, seq: number, time: number, data: unknown): SessionEvent {
  return { type, seq, time, data } as unknown as SessionEvent
}

interface Captured {
  readonly appended: string[][]
  readonly paths: string[]
  readonly observer: ShadowObserver
}

function observerWithCapture(options: {
  readonly bufferBound?: number
  readonly failAppend?: boolean
} = {}): Captured {
  const appended: string[][] = []
  const paths: string[] = []
  const observer = new ShadowObserver({
    config: { ...config, bufferBound: options.bufferBound ?? config.bufferBound },
    now: () => 1_756_728_000_000,
    observerId: 'observer-fixture',
    appendLines: async (path, lines) => {
      if (options.failAppend) throw new Error('disk full')
      paths.push(path)
      appended.push([...lines])
    },
  })
  return { appended, paths, observer }
}

function parsed(lines: string[][]): Array<ObserverEnvelope | ObserverStats> {
  return lines.flat().map(line => JSON.parse(line) as ObserverEnvelope | ObserverStats)
}

describe('shadow observer configuration', () => {
  it('is off unless one exact goal, session, and run identity are declared', () => {
    expect(resolveShadowObserverConfig({})).toBeUndefined()
    expect(resolveShadowObserverConfig({ [ENV_GOAL_ID]: '   ' })).toBeUndefined()
    expect(resolveShadowObserverConfig({ [ENV_GOAL_ID]: 'not an id' })).toBeUndefined()
    expect(resolveShadowObserverConfig({
      [ENV_GOAL_ID]: goalId,
      [ENV_SESSION_ID]: sessionId,
    })).toBeUndefined()
    const resolved = resolveShadowObserverConfig({
      [ENV_GOAL_ID]: goalId,
      [ENV_SESSION_ID]: sessionId,
      [ENV_RUN_IDENTITY]: JSON.stringify(runIdentity),
      LOOPX_DSH_SHADOW_OBSERVER_LEDGER_DIR: '/tmp/ledger',
      LOOPX_DSH_SHADOW_OBSERVER_BUFFER_BOUND: '9',
    })
    expect(resolved).toEqual({
      goalId,
      sessionId,
      runIdentity,
      ledgerDir: '/tmp/ledger',
      bufferBound: 9,
    })
    expect(resolveShadowObserverConfig({
      [ENV_GOAL_ID]: goalId,
      [ENV_SESSION_ID]: sessionId,
      [ENV_RUN_IDENTITY]: JSON.stringify({ ...runIdentity, extra: 'not-pinned' }),
    })).toBeUndefined()
    expect(resolveShadowObserverConfig({
      [ENV_GOAL_ID]: goalId,
      [ENV_SESSION_ID]: sessionId,
      [ENV_RUN_IDENTITY]: JSON.stringify({
        ...runIdentity,
        worker_id: publicSafetyFixture.unsafe_identity_tokens[0],
      }),
    })).toBeUndefined()
  })

  it('imports nothing from the driver and owns no send path', () => {
    const source = readFileSync(join(TEST_DIR, '../src/observer.ts'), 'utf8')
    expect(source).not.toMatch(/from '\.\/driver/u)
    expect(source).not.toMatch(/from '\.\/cli/u)
    expect(source).not.toMatch(/from '\.\/managed-runtime/u)
    expect(source).not.toMatch(/\.send\(/u)
    expect(source).not.toMatch(/\.inbox\b/u)
    expect(source).not.toMatch(/setTimeout|setInterval/u)
  })
})

describe('shadow observer envelopes', () => {
  it('maps DSH session events into the shared envelope shape', async () => {
    const { appended, paths, observer } = observerWithCapture({ bufferBound: 16 })
    const session = fakeSession()
    observer.observeSessionCreated(session)
    observer.observeSessionEvent(session, sessionEvent('turn/start', 1, 1_756_728_001_000, { turn: 1 }))
    observer.observeSessionEvent(session, sessionEvent('tool/call', 2, 1_756_728_002_000, {
      turn: 1, step: 1, callId: 'call-1', name: 'bash', arguments: '{"cmd":"rm -rf /"}',
    }))
    observer.observeSessionEvent(session, sessionEvent('tool/result', 3, 1_756_728_003_000, {
      turn: 1, step: 1, message: { id: 'm', role: 'user', content: [], source: { kind: 'tool', callId: 'call-1' } },
      error: { name: 'ToolError', code: 'timeout' },
    }))
    observer.observeSessionEvent(session, sessionEvent('assistant/chunk', 4, 1_756_728_003_500, { turn: 1, step: 1 }))
    observer.observeSessionEvent(session, sessionEvent('todo/write', 5, 1_756_728_004_000, { todos: [] }))
    observer.observeSessionEvent(session, sessionEvent('turn/end', 6, 1_756_728_005_000, {
      turn: 1,
      reason: { kind: 'completed' },
    }))
    await observer.flush()

    const records = parsed(appended)
    const envelopes = records.filter(
      (record): record is ObserverEnvelope => record.schema_version === OBSERVER_ENVELOPE_SCHEMA_VERSION,
    )
    expect(new Set(paths)).toEqual(new Set([ledgerPath(config)]))
    expect(envelopes.map(item => item.event_kind)).toEqual([
      'session_started', 'turn_started', 'tool_called', 'tool_completed', 'unsupported', 'turn_ended',
    ])
    expect(envelopes.map(item => item.sequence)).toEqual([0, 1, 2, 3, 4, 5])
    for (const envelope of envelopes) {
      expect(Object.keys(envelope).filter(key => key !== 'agent_id').sort()).toEqual([...ENVELOPE_FIELDS].sort())
      expect(envelope.goal_id).toBe(goalId)
      expect(envelope.session_id).toBe('session-fixture')
      expect(envelope.observer_id).toBe('observer-fixture')
    }
    expect(envelopes[0]?.clock).toEqual({ source: 'observer_wall_clock', uncertainty_ms: 50 })
    expect(envelopes[1]?.clock).toEqual({ source: 'harness_event_time', uncertainty_ms: 0 })
    expect(envelopes[1]?.observed_at).toBe('2025-09-01T12:00:01.000Z')
    expect(envelopes[2]?.summary).toEqual({ turn: 1, step: 1, tool_name: 'bash' })
    expect(envelopes[2]?.source_refs).toEqual({ event_seq: '2', tool_call_id: 'call-1' })
    expect(envelopes[3]?.summary).toEqual({ turn: 1, step: 1, status: 'error', error_class: 'timeout' })
    expect(envelopes[4]?.summary).toEqual({ source_event_type: 'todo/write' })
    expect(envelopes[5]?.summary).toEqual({ turn: 1, reason: 'completed' })
    expect(JSON.stringify(records)).not.toContain('rm -rf')

    const stats = records.at(-1) as ObserverStats
    expect(stats.schema_version).toBe(OBSERVER_STATS_SCHEMA_VERSION)
    expect(Object.keys(stats).sort()).toEqual([...STATS_FIELDS].sort())
    expect(stats.outbound_endpoints).toEqual([])
    expect(stats.observation_entered_worker_context).toBe(false)
    expect(stats.observation_entered_scheduler_inputs).toBe(false)
    expect(stats.run_identity).toEqual(runIdentity)
    expect(stats.event_sources).toEqual(['session/created', 'session/disposed', 'session/event'])
    expect(stats.observed_event_count).toBe(6)
    expect(stats.accepted_event_count).toBe(6)
    expect(stats.observed_event_count).toBe(
      stats.accepted_event_count + stats.rejected_event_count + stats.backpressure_drop_count,
    )
  })

  it('enforces shared public-safety counterfactuals before the first append', async () => {
    expect(publicSafetyFixture.schema_version).toBe(
      'reliability_diagnostics_public_safety_counterfactuals_v0',
    )
    const { appended, observer } = observerWithCapture({ bufferBound: 32 })
    const session = fakeSession()
    let sequence = 1
    for (const value of publicSafetyFixture.unsafe_summary_tokens) {
      observer.observeSessionEvent(
        session,
        sessionEvent('tool/call', sequence, 1_756_728_001_000 + sequence, {
          turn: 1, step: sequence, callId: `call-${sequence}`, name: value,
        }),
      )
      sequence += 1
    }
    for (const value of publicSafetyFixture.unsafe_summary_tokens) {
      observer.observeSessionEvent(
        session,
        sessionEvent('tool/result', sequence, 1_756_728_001_000 + sequence, {
          turn: 1,
          step: sequence,
          error: { code: value },
          message: { source: { callId: `call-${sequence}` } },
        }),
      )
      sequence += 1
    }
    for (const value of publicSafetyFixture.unsafe_identity_tokens) {
      observer.observeSessionEvent(
        session,
        sessionEvent('tool/call', sequence, 1_756_728_001_000 + sequence, {
          turn: 1, step: sequence, callId: value, name: 'bash',
        }),
      )
      sequence += 1
    }
    for (const value of publicSafetyFixture.invalid_source_ref_tokens) {
      observer.observeSessionEvent(
        session,
        sessionEvent('tool/call', sequence, 1_756_728_001_000 + sequence, {
          turn: 1, step: sequence, callId: value, name: 'bash',
        }),
      )
      sequence += 1
    }
    for (const value of publicSafetyFixture.safe_summary_tokens) {
      observer.observeSessionEvent(
        session,
        sessionEvent('tool/call', sequence, 1_756_728_001_000 + sequence, {
          turn: 1, step: sequence, callId: `call-${sequence}`, name: value,
        }),
      )
      sequence += 1
    }
    for (const value of publicSafetyFixture.safe_identity_tokens) {
      observer.observeSessionEvent(
        session,
        sessionEvent('tool/call', sequence, 1_756_728_001_000 + sequence, {
          turn: 1, step: sequence, callId: value, name: 'bash',
        }),
      )
      sequence += 1
    }
    await observer.flush()

    const ledgerBytes = appended.flat().join('\n')
    for (const value of [
      ...publicSafetyFixture.unsafe_summary_tokens,
      ...publicSafetyFixture.unsafe_identity_tokens,
      ...publicSafetyFixture.invalid_source_ref_tokens,
    ]) expect(ledgerBytes).not.toContain(value)
    const records = parsed(appended)
    const envelopes = records.filter(
      (record): record is ObserverEnvelope => record.schema_version === OBSERVER_ENVELOPE_SCHEMA_VERSION,
    )
    expect(envelopes).toHaveLength(
      publicSafetyFixture.invalid_source_ref_tokens.length
        + publicSafetyFixture.safe_summary_tokens.length
        + publicSafetyFixture.safe_identity_tokens.length,
    )
    const stats = records.at(-1) as ObserverStats
    const unsafeCount = (publicSafetyFixture.unsafe_summary_tokens.length * 2)
      + publicSafetyFixture.unsafe_identity_tokens.length
    expect(stats.rejected_by_reason).toEqual({ public_safety_violation: unsafeCount })
    expect(stats.observed_event_count).toBe(envelopes.length + unsafeCount)
  })

  it('drops with a count while a flush is in flight and the buffer is full', async () => {
    const appended: string[][] = []
    let release: (() => void) | undefined
    const observer = new ShadowObserver({
      config: { ...config, bufferBound: 2 },
      now: () => 1_756_728_000_000,
      observerId: 'observer-fixture',
      appendLines: async (_path, lines) => {
        appended.push([...lines])
        if (release === undefined) await new Promise<void>(resolve => { release = resolve })
      },
    })
    const session = fakeSession()
    // Two observations fill the buffer and start a flush that stays pending.
    observer.observeSessionEvent(session, sessionEvent('step/start', 1, 1_756_728_001_000, { turn: 1, step: 1 }))
    observer.observeSessionEvent(session, sessionEvent('step/start', 2, 1_756_728_002_000, { turn: 1, step: 2 }))
    // Two more refill the bound; the fifth has nowhere to go and is dropped.
    observer.observeSessionEvent(session, sessionEvent('step/start', 3, 1_756_728_003_000, { turn: 1, step: 3 }))
    observer.observeSessionEvent(session, sessionEvent('step/start', 4, 1_756_728_004_000, { turn: 1, step: 4 }))
    observer.observeSessionEvent(session, sessionEvent('step/start', 5, 1_756_728_005_000, { turn: 1, step: 5 }))
    release?.()
    await observer.flush()
    const records = parsed(appended)
    const stats = records.at(-1) as ObserverStats
    expect(stats.buffer_bound).toBe(2)
    expect(stats.accepted_event_count).toBe(4)
    expect(stats.backpressure_drop_count).toBe(1)
    expect(stats.observed_event_count).toBe(5)
    expect(stats.peak_buffered_event_count).toBe(2)
    // The sequence still advances for the dropped event so the loss is visible.
    const envelopes = records.filter(
      (record): record is ObserverEnvelope => record.schema_version === OBSERVER_ENVELOPE_SCHEMA_VERSION,
    )
    expect(envelopes.map(item => item.sequence)).toEqual([0, 1, 2, 3])
  })

  it('waits for the catch-up batch requested during an in-flight flush', async () => {
    let releaseFirst = () => {}
    let releaseSecond = () => {}
    let markSecondStarted = () => {}
    const firstGate = new Promise<void>(resolve => { releaseFirst = resolve })
    const secondGate = new Promise<void>(resolve => { releaseSecond = resolve })
    const secondStarted = new Promise<void>(resolve => { markSecondStarted = resolve })
    const appended: string[][] = []
    const observer = new ShadowObserver({
      config: { ...config, bufferBound: 1 },
      now: () => 1_756_728_000_000,
      observerId: 'observer-fixture',
      appendLines: async (_path, lines) => {
        appended.push([...lines])
        if (appended.length === 1) await firstGate
        if (appended.length === 2) {
          markSecondStarted()
          await secondGate
        }
      },
    })
    const session = fakeSession()
    observer.observeSessionEvent(session, sessionEvent('step/start', 1, 1_756_728_001_000, { turn: 1, step: 1 }))
    observer.observeSessionEvent(session, sessionEvent('step/start', 2, 1_756_728_002_000, { turn: 1, step: 2 }))

    let completed = false
    const completion = observer.flush().then(() => { completed = true })
    releaseFirst()
    await secondStarted
    expect(completed).toBe(false)
    releaseSecond()
    await completion

    const envelopes = parsed(appended).filter(
      (record): record is ObserverEnvelope => record.schema_version === OBSERVER_ENVELOPE_SCHEMA_VERSION,
    )
    expect(envelopes.map(item => item.sequence)).toEqual([0, 1])
    expect(observer.stats().flush_attempt_count).toBe(2)
  })

  it('preserves the typed DSH turn-end reason used by recovery diagnostics', async () => {
    const { appended, observer } = observerWithCapture()
    observer.observeSessionEvent(
      fakeSession(),
      sessionEvent('turn/end', 1, 1_756_728_001_000, {
        turn: 2,
        reason: { kind: 'error', error: { code: 'UPSTREAM_FAILURE' } },
      }),
    )
    await observer.flush()
    const [envelope] = parsed(appended).filter(
      (record): record is ObserverEnvelope => record.schema_version === OBSERVER_ENVELOPE_SCHEMA_VERSION,
    )
    expect(envelope?.event_kind).toBe('turn_ended')
    expect(envelope?.summary).toEqual({ turn: 2, reason: 'error' })
    expect(JSON.stringify(envelope)).not.toContain('UPSTREAM_FAILURE')
  })

  it('counts hook and flush failures instead of throwing', async () => {
    const { observer } = observerWithCapture({ failAppend: true })
    const session = fakeSession()
    expect(() => observer.observeSessionEvent(session, undefined as unknown as SessionEvent)).not.toThrow()
    observer.observeSessionCreated(session)
    await expect(observer.flush()).resolves.toBeUndefined()
    const stats = observer.stats()
    expect(stats.observer_failure_count).toBe(2)
    expect(stats.backpressure_drop_count).toBe(1)
    expect(stats.rejected_by_reason).toEqual({ observer_internal_failure: 1 })
    expect(stats.observed_event_count).toBe(
      stats.accepted_event_count + stats.rejected_event_count + stats.backpressure_drop_count,
    )
  })

  it('contains logger failures while reporting an observer failure', async () => {
    const observer = new ShadowObserver({
      config,
      observerId: 'observer-fixture',
      appendLines: async () => { throw new Error('disk full') },
      warn: () => { throw new Error('logger unavailable') },
    })
    observer.observeSessionCreated(fakeSession())
    await expect(observer.flush()).resolves.toBeUndefined()
    expect(observer.stats().observer_failure_count).toBe(1)
    expect(observer.stats().backpressure_drop_count).toBe(1)
  })

  it('rejects rather than attributing another session to the configured goal', () => {
    const { observer } = observerWithCapture()
    observer.observeSessionCreated(fakeSession('different-session'))
    observer.observeSessionCreated(fakeSession())
    const stats = observer.stats()
    expect(stats.accepted_event_count).toBe(1)
    expect(stats.rejected_event_count).toBe(1)
    expect(stats.rejected_by_reason).toEqual({ identity_invalid: 1 })
  })
})

describe('registerShadowObserver', () => {
  it('registers only read-only session publication hooks', () => {
    type Handler = (...args: unknown[]) => unknown
    const handlers = new Map<string, Handler[]>()
    let disposeEffect: (() => unknown) | undefined
    const warnings: string[] = []
    const ctx = {
      logger: { warn(message: string) { warnings.push(message) } },
      on(event: string, handler: Handler) {
        handlers.set(event, [...(handlers.get(event) ?? []), handler])
      },
      effect(effect: () => Generator<unknown, void, unknown>) {
        const yielded = effect().next().value
        if (typeof yielded === 'function') disposeEffect = yielded as () => unknown
      },
    } as unknown as Context
    const observer = registerShadowObserver(ctx, config)
    expect([...handlers.keys()].sort()).toEqual([
      'session/created', 'session/disposed', 'session/event',
    ])
    const session = fakeSession()
    handlers.get('session/created')?.[0]?.(session)
    expect(observer.stats().observed_event_count).toBe(1)
    expect(warnings).toEqual([])
    expect(typeof disposeEffect).toBe('function')
  })
})

describe('Cordis and DSH session publication integration', () => {
  it('observes a live session through create, append, and disposal, then flushes on context disposal', async () => {
    const ledgerDir = await mkdtemp(join(tmpdir(), 'loopx-observer-cordis-'))
    const sessionId = SessionId('session-cordis-integration')
    const integrationConfig: ShadowObserverConfig = {
      ...config,
      sessionId,
      ledgerDir,
      bufferBound: 8,
    }
    const ctx = new Context()

    try {
      const observer = registerShadowObserver(ctx, integrationConfig)
      const sessionStoreFiber = await ctx.plugin(SessionStore)
      const session = sessionStoreFiber.ctx.sessions.create(sessionId, {
        meta: { cwd: ledgerDir },
      })

      session.append('user/message', {
        id: 'message-cordis-integration' as UserMessage['id'],
        role: 'user',
        content: [{ type: 'text', text: 'synthetic observer integration event' }],
        source: { kind: 'user' },
      }, { surfaceOp: 'append' })

      await sessionStoreFiber.dispose()
      await ctx.fiber.dispose()

      const records = (await readFile(ledgerPath(integrationConfig), 'utf8'))
        .trim()
        .split('\n')
        .map(line => JSON.parse(line) as Record<string, unknown>)
      const envelopes = records.filter(record => record.schema_version === OBSERVER_ENVELOPE_SCHEMA_VERSION)
      const stats = records.find(record => record.schema_version === OBSERVER_STATS_SCHEMA_VERSION)

      expect(envelopes.map(record => record.event_kind)).toEqual([
        'session_started', 'user_message', 'session_disposed',
      ])
      expect(stats).toMatchObject({
        observed_event_count: 3,
        accepted_event_count: 3,
        rejected_event_count: 0,
        observer_failure_count: 0,
      })
      expect(observer.stats().accepted_event_count).toBe(3)
    } finally {
      await ctx.fiber.dispose()
      await rm(ledgerDir, { recursive: true, force: true })
    }
  })
})
