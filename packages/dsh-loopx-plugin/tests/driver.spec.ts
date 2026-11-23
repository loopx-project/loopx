import { createHash, randomUUID } from 'node:crypto'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Context, Logger, LoggerService } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import { SessionSeq } from '@deepseek-ai/dsh-session'
import type { SessionEvent, UserMessage } from '@deepseek-ai/dsh-session'
import type { FileRunner } from '../src/cli.ts'
import { LoopXCliError } from '../src/cli.ts'
import {
  apply as applyDriver,
  LoopXContinuationDriver,
} from '../src/driver.ts'
import type { DriverClock } from '../src/driver.ts'
import {
  GoalBarCoordinator,
  goalBarCoordinator,
} from '../src/goalbar/events.ts'

let testHome: string
beforeEach(() => {
  testHome = mkdtempSync(join(tmpdir(), 'dsh-loopx-driver-test-'))
  vi.stubEnv('DSH_HOME', testHome)
})
afterEach(() => {
  vi.unstubAllEnvs()
  rmSync(testHome, { recursive: true, force: true })
})

const goalId = 'goal-fixture'
const agentId = 'agent-fixture'
const sessionId = 'session-fixture'

interface FakeAgent {
  readonly agent: Agent
  readonly cancelCalls: number
  readonly maintenanceCalls: number
  readonly nextTurn: UserMessage[]
  readonly nextStep: UserMessage[]
  appendEvent(event: SessionEvent): void
  replaceSession(headerId?: string, events?: SessionEvent[]): void
  setMaintenanceBusy(busy: boolean): void
  setStatus(status: 'idle' | 'running'): void
}

function userMessage(text: string): UserMessage {
  return {
    id: randomUUID() as UserMessage['id'],
    role: 'user',
    content: [{ type: 'text', text }],
    source: { kind: 'user' },
  }
}

function fakeAgent(id = sessionId): FakeAgent {
  const nextTurn: UserMessage[] = []
  const nextStep: UserMessage[] = []
  let status: 'idle' | 'running' = 'idle'
  let cancelCalls = 0
  let maintenanceCalls = 0
  let maintenanceBusy = false
  let idle = Promise.resolve()
  let releaseMaintenance: (() => void) | undefined
  const inbox = {
    nextTurn,
    nextStep,
    get hasPending() {
      return nextTurn.length > 0 || nextStep.length > 0
    },
    remove(id: UserMessage['id']) {
      for (const queue of [nextStep, nextTurn]) {
        const index = queue.findIndex(message => message.id === id)
        if (index >= 0) {
          queue.splice(index, 1)
          return true
        }
      }
      return false
    },
  }
  interface FixtureHeader {
    version: number
    id: string
    createdAt: number
    cwd: string
    seedLength: number
  }
  // The installed generation exposes one `snapshotEvents()` reader instead of
  // the retired `events` property, so the fixture owns the log and hands it out.
  let sessionEvents: SessionEvent[] = []
  const fixtureSession = (header: FixtureHeader) => ({
    id,
    header,
    snapshotEvents: () => sessionEvents,
    surface: { nodes: [] as never[] },
  })
  let session = fixtureSession({
    version: 0,
    id,
    createdAt: 1,
    cwd: '/fixture/project',
    seedLength: 0,
  })
  const agent = {
    id,
    options: {},
    get session() { return session },
    inbox,
    ctx: {},
    get status() { return status },
    cancel() { cancelCalls += 1 },
    whenIdle: () => idle,
    runMaintenance: <T>(task: (signal: AbortSignal) => Promise<T>) => {
      maintenanceCalls += 1
      if (maintenanceBusy) throw new Error(`agent "${id}" already has active work`)
      return task(new AbortController().signal)
    },
    send(message: UserMessage, target: 'next-turn' | 'next-step') {
      (target === 'next-step' ? nextStep : nextTurn).push(message)
    },
    followup(message: UserMessage) { nextTurn.push(message) },
    steer(message: UserMessage) { nextStep.push(message) },
    inject(message: UserMessage) { nextStep.push(message) },
  } as unknown as Agent
  return {
    agent,
    get cancelCalls() { return cancelCalls },
    get maintenanceCalls() { return maintenanceCalls },
    nextTurn,
    nextStep,
    appendEvent(event) { sessionEvents.push(event) },
    replaceSession(headerId, events = [...sessionEvents]) {
      sessionEvents = events
      session = fixtureSession({
        ...session.header,
        ...(headerId === undefined ? {} : { id: headerId }),
      })
    },
    setMaintenanceBusy(busy) {
      maintenanceBusy = busy
      if (busy) idle = new Promise<void>(resolve => { releaseMaintenance = resolve })
      else releaseMaintenance?.()
    },
    setStatus(value) { status = value },
  }
}

function sessionEvent(
  type: string,
  data: unknown,
): SessionEvent {
  return { type, data } as unknown as SessionEvent
}

function turnEndEvent(
  seq: number,
  reason: 'completed' | 'max-tokens' = 'completed',
): SessionEvent<'turn/end'> {
  return {
    type: 'turn/end',
    seq: SessionSeq(seq),
    time: 1,
    data: {
      turn: 0,
      reason: { kind: reason },
    },
  }
}

interface AppliedDriverRuntime {
  emitSessionEvent(session: Agent['session'], event: SessionEvent): void
  announceAgent(event: 'agent/created' | 'agent/session-start' | 'agent/error'): void
  dispose(): Promise<void>
  /** Replay the cordis fiber-disposal sequence for one unload cause. */
  unloadFiber(mode: 'evict' | 'direct' | 'restart'): Promise<void>
}

function appliedDriverRuntime(
  liveAgent: Agent,
  initiallyObserved: boolean,
  options: { readonly withFiber?: boolean } = {},
): AppliedDriverRuntime {
  type Handler = (...args: unknown[]) => unknown
  const handlers = new Map<string, Handler[]>()
  let disposeEffect: (() => unknown) | undefined
  // Simulates the cordis plugin fiber owning this applied Driver. `evict`
  // mirrors dsh-hmr module replacement: the runtime leaves the registry
  // before the fiber disposal; `direct` mirrors fiber.dispose() from user
  // disable/uninstall or a host cascade, where the runtime is still
  // registered when cordis emits internal/plugin for the cleared uid.
  let runtimeRegistered = true
  const fiber = options.withFiber
    ? { uid: 7 as number | null, runtime: { callback: function dshLoopxPlugin() {} } }
    : undefined
  const ctx = {
    agents: {
      get: (id: string) => String(liveAgent.id) === String(id) ? liveAgent : undefined,
      list: () => initiallyObserved ? [liveAgent] : [],
      withoutInitiator: (operation: () => Promise<unknown>) => operation(),
    },
    logger: { warn() {} },
    inject() {},
    ...(fiber === undefined ? {} : {
      fiber,
      registry: { has: (_plugin: unknown) => runtimeRegistered },
    }),
    on(event: string, handler: Handler) {
      const registered = handlers.get(event) ?? []
      registered.push(handler)
      handlers.set(event, registered)
    },
    effect(effect: () => Generator<unknown, void, unknown>) {
      const iterator = effect()
      const yielded = iterator.next().value
      if (typeof yielded === 'function') disposeEffect = yielded as () => unknown
    },
  } as unknown as Context
  applyDriver(ctx)
  return {
    announceAgent(event) {
      for (const handler of handlers.get(event) ?? []) {
        handler({ agent: liveAgent, source: 'resume' })
      }
    },
    emitSessionEvent(session, event) {
      for (const handler of handlers.get('session/event') ?? []) {
        handler(session, event)
      }
    },
    async dispose() {
      await disposeEffect?.()
    },
    async unloadFiber(mode) {
      if (fiber === undefined) throw new Error('appliedDriverRuntime was built without a fiber')
      if (mode === 'evict') runtimeRegistered = false
      if (mode !== 'restart') {
        // A disposal clears the fiber uid before the unload; a restart keeps
        // the uid and never emits internal/plugin.
        fiber.uid = null
        for (const handler of handlers.get('internal/plugin') ?? []) handler(fiber)
      }
      if (mode === 'direct') runtimeRegistered = false
      await disposeEffect?.()
    },
  }
}

function userSkillInvocationEvent(
  name = 'loopx',
  form = 'instructions',
): SessionEvent {
  return sessionEvent('user/message', {
    id: randomUUID(),
    role: 'user',
    content: [{ type: 'text', text: `Loaded ${name}.` }],
    source: { kind: 'skill-invocation', name, form },
  })
}

function modelSkillCallEvent(
  callId: string,
  rawArguments: string,
  name = 'skill',
): SessionEvent {
  return sessionEvent('tool/call', {
    turn: 0,
    step: 0,
    callId,
    name,
    arguments: rawArguments,
  })
}

function modelSkillResultEvent(
  callId: string,
  options: {
    readonly blockCallId?: string
    readonly eventError?: { name: string; code: string }
    readonly extraBlock?: boolean
    readonly isError?: boolean
  } = {},
): SessionEvent {
  const resultBlock = {
    type: 'tool-result',
    toolCallId: options.blockCallId ?? callId,
    content: [],
    isError: options.isError ?? false,
  }
  return sessionEvent('tool/result', {
    turn: 0,
    step: 0,
    message: {
      id: randomUUID(),
      role: 'user',
      content: options.extraBlock
        ? [resultBlock, { type: 'text', text: 'unexpected extra block' }]
        : [resultBlock],
      source: { kind: 'tool', callId },
    },
    ...(options.eventError === undefined ? {} : { error: options.eventError }),
  })
}

function observeActivated(
  driver: LoopXContinuationDriver,
  host: FakeAgent,
): void {
  host.appendEvent(userSkillInvocationEvent())
  driver.observeAgent(host.agent)
}

function publishEvent(
  driver: LoopXContinuationDriver,
  host: FakeAgent,
  event: SessionEvent,
): void {
  host.appendEvent(event)
  driver.onSessionEvent(host.agent, event)
}

interface RunnerFixture {
  readonly runner: FileRunner
  readonly calls: string[][]
  readonly quotaCalls: string[][]
  readonly heartbeatArgv: string[][]
  heartbeatCalls: number
}

// Relevant scheduler fields emitted by hint.ts for a generic_cli human gate.
function schedulerWaitHint() {
  return {
    schema_version: 'scheduler_hint_v0',
    source: 'quota.should-run',
    action: 'backoff_waiting_for_user',
    cadence_class: 'human_gate',
    unchanged_poll: {
      limits: { local_scheduler: 3, codex_cli_tui: 3, claude_code_loop: 3 },
      after_limits: {
        local_scheduler: 'stop_tick_loop',
        codex_cli_tui: 'update_goal_blocked_keep_loopx_active',
        claude_code_loop: 'stop_loop',
      },
      final_quota_replan_check_enabled: true,
      final_quota_replan_check_action: 'rerun_quota_should_run_once',
    },
    reset_policy: {
      reset_token: 'scheduler-token',
      host_state_key: 'scheduler_hint.reset_policy.reset_token',
    },
    cold_path_detail: {
      schema_version: 'scheduler_hint_detail_v0',
      source: 'quota.should-run',
      local_scheduler: {
        recommended_interval_minutes: 30,
        max_interval_minutes: 120,
        unchanged_poll_backoff_multiplier: 2,
        example_progression_minutes: [30, 60, 120],
        unchanged_poll_limit: 3,
        after_limit: 'stop_tick_loop',
        no_spend_for_cadence_change: true,
      },
    },
  }
}

function schedulerClock() {
  const timers: Array<{ callback: () => void; delay: number }> = []
  return {
    timers,
    setTimeout(callback: () => void, delay: number) {
      const timer = { callback, delay }
      timers.push(timer)
      return timer
    },
    clearTimeout() {},
  }
}

function runnerFixture(options: {
  readonly quotaExitFailures?: number
  readonly heartbeatExitFailures?: number
  readonly shouldRun?: boolean | (() => boolean)
  readonly waitMinutes?: number
  readonly schedulerMode?: 'poll' | 'stop' | 'missing'
  readonly unchangedPollLimit?: number
  readonly schedulerHint?: () => Record<string, unknown>
  readonly quotaAgentId?: string
  readonly quotaTurnInstanceId?: string
  readonly heartbeatTurnInstanceId?: string
  readonly heartbeatTaskBody?: string
  readonly rewardMemoryGuidance?: string
  readonly recovery?: () => Record<string, unknown>
  readonly typedQuotaFailure?: boolean
  readonly bindingStatus?: 'bound' | 'missing' | (() => 'bound' | 'missing')
} = {}): RunnerFixture {
  const calls: string[][] = []
  const quotaCalls: string[][] = []
  const heartbeatArgv: string[][] = []
  let quotaExitFailures = options.quotaExitFailures ?? 0
  let heartbeatExitFailures = options.heartbeatExitFailures ?? 0
  const fixture: RunnerFixture = {
    calls,
    quotaCalls,
    heartbeatArgv,
    heartbeatCalls: 0,
    runner: async (_file, args) => {
      calls.push([...args])
      if (args.at(-1) === '--version') {
        return { exitCode: 0, stdout: 'loopx 2.4.0\n', stderr: '' }
      }
      if (args.includes('resolve-agent-thread')) {
        const threadIdIndex = args.indexOf('--thread-id')
        const threadId = args[threadIdIndex + 1]
        const bindingStatus = typeof options.bindingStatus === 'function'
          ? options.bindingStatus()
          : options.bindingStatus ?? 'bound'
        if (bindingStatus === 'missing') {
          return {
            exitCode: 0,
            stdout: JSON.stringify({
              ok: true,
              schema_version: 'loopx_thread_agent_binding_resolution_v0',
              host_surface: 'deepseek-harness-native',
              thread_id: threadId,
              status: 'unbound',
              matches: [],
            }),
            stderr: '',
          }
        }
        return {
          exitCode: 0,
          stdout: JSON.stringify({
            ok: true,
            schema_version: 'loopx_thread_agent_binding_resolution_v0',
            host_surface: 'deepseek-harness-native',
            thread_id: threadId,
            status: 'bound',
            goal_id: goalId,
            agent_id: agentId,
            matches: [{ goal_id: goalId, agent_id: agentId }],
          }),
          stderr: '',
        }
      }
      if (args.includes('should-run')) {
        quotaCalls.push([...args])
        if (quotaExitFailures > 0) {
          quotaExitFailures -= 1
          return { exitCode: 1, stdout: '', stderr: 'transient diagnostic' }
        }
        const shouldRun = typeof options.shouldRun === 'function'
          ? options.shouldRun()
          : options.shouldRun ?? true
        const turnInstanceId = options.quotaTurnInstanceId
          ?? args[args.indexOf('--turn-instance-id') + 1]
        const schedulerHint = options.schedulerHint?.() ?? (shouldRun
          ? { action: 'run_now' }
          : options.schedulerMode === 'stop'
            ? {
                action: 'stop_until_explicit_resume',
                unchanged_poll: { local_scheduler: 'stop' },
              }
            : options.schedulerMode === 'missing'
              ? { action: 'wait' }
              : {
                  schema_version: 'scheduler_hint_v0',
                  action: 'backoff_until_state_change',
                  reset_policy: { reset_token: 'scheduler-token' },
                  unchanged_poll: {
                    limits: { local_scheduler: options.unchangedPollLimit ?? null },
                  },
                  cold_path_detail: {
                    schema_version: 'scheduler_hint_detail_v0',
                    local_scheduler: {
                      recommended_interval_minutes: options.waitMinutes ?? 5,
                    },
                  },
                })
        return {
          exitCode: shouldRun && !options.typedQuotaFailure ? 0 : 1,
          stdout: JSON.stringify({
            ok: options.typedQuotaFailure ? false : true,
            mode: 'should-run',
            goal_id: goalId,
            should_run: shouldRun,
            agent_identity: {
              agent_id: options.quotaAgentId ?? agentId,
              registered: true,
            },
            ...(shouldRun ? {
              heartbeat_receipt: {
                schema_version: 'heartbeat_quota_receipt_v0',
                turn_instance_id: turnInstanceId,
                status: 'committed',
              },
            } : {}),
            scheduler_hint: schedulerHint,
            ...(options.recovery === undefined ? {} : {
              unsettled_host_turn_recovery: options.recovery(),
            }),
            ...(options.rewardMemoryGuidance === undefined ? {} : {
              reward_memory_recall: {
                schema_version: 'agent_turn_recall_v0',
                status: 'applied',
                context: {
                  schema_version: 'agent_turn_recall_context_v0',
                  guidance: [{
                    candidate_ref: 'candidate:reviewed',
                    target_class: 'soft_preference',
                    content_summary: options.rewardMemoryGuidance,
                  }],
                },
              },
            }),
          }),
          stderr: '',
        }
      }
      if (args.includes('heartbeat-prompt')) {
        fixture.heartbeatCalls += 1
        heartbeatArgv.push([...args])
        if (heartbeatExitFailures > 0) {
          heartbeatExitFailures -= 1
          return { exitCode: 1, stdout: '', stderr: 'transient diagnostic' }
        }
        const turnInstanceId = options.heartbeatTurnInstanceId
          ?? args[args.indexOf('--turn-instance-id') + 1]
        return {
          exitCode: 0,
          stdout: JSON.stringify({
            ok: true,
            schema_version: 'heartbeat_agent_input_v1',
            goal_id: goalId,
            agent_id: agentId,
            turn_instance_id: turnInstanceId,
            task_body: options.heartbeatTaskBody
              ?? `LOOPX_TURN=${turnInstanceId}\nContinue through the authoritative LoopX workflow.`,
          }),
          stderr: '',
        }
      }
      throw new Error(`unexpected argv: ${args.join(' ')}`)
    },
  }
  return fixture
}

async function waitFor(predicate: () => boolean): Promise<void> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (predicate()) return
    await new Promise<void>(resolve => setTimeout(resolve, 0))
  }
  throw new Error('condition was not reached')
}

function makeDriver(fixture: RunnerFixture): LoopXContinuationDriver {
  return new LoopXContinuationDriver({
    runner: fixture.runner,
    isLiveAgent: () => true,
    makeTurnInstanceId: () => 'turn-stable',
    retryDelaysMs: [0, 0],
  })
}

function jobNotice(kind = 'tool-jobs', form = 'notice'): UserMessage {
  return { ...userMessage('Background work completed.'), source: { kind, form } } as unknown as UserMessage
}

describe('host job continuation', () => {
  it.each(['attach', 'session-start', 'session-replacement', 'idle', 'command-done'])(
    'admits beside a pending settled-job notice at %s', async boundary => {
      const fixture = runnerFixture()
      const host = fakeAgent()
      const driver = makeDriver(fixture)
      host.appendEvent(userSkillInvocationEvent())
      host.setStatus('running')
      driver.observeAgent(host.agent)
      const completed = jobNotice()
      host.nextStep.push(completed)
      host.setStatus('idle')
      try {
        if (boundary === 'attach') driver.observeAgent(host.agent)
        else if (boundary === 'session-start') driver.onSessionStart(host.agent)
        else if (boundary === 'session-replacement') {
          host.replaceSession()
          driver.onAgentStatus(host.agent, 'idle')
        } else if (boundary === 'command-done') {
          publishEvent(driver, host, sessionEvent('command/run', { commandId: 'command' }))
          publishEvent(driver, host, sessionEvent('command/done', { commandId: 'command' }))
        } else driver.onAgentStatus(host.agent, 'idle')
        await waitFor(() => host.nextTurn.length === 1)
        const queued = host.nextTurn.shift() as UserMessage
        host.nextStep.shift()
        host.setStatus('running')
        driver.onInboxClaimed(host.agent, completed)
        driver.onInboxClaimed(host.agent, queued)
        expect(await driver.onPreStep(host.agent, [queued, completed], new AbortController().signal,
          async () => ({ kind: 'enter', messages: [queued, completed, jobNotice()] })))
          .toMatchObject({ kind: 'enter' })
      } finally { await driver.dispose() }
    },
  )

  it.each([['other-plugin', 'notice'], ['tool-jobs', 'snapshot']])(
    'keeps %s/%s input behind the competing-input fence', async (kind, form) => {
      const fixture = runnerFixture()
      const host = fakeAgent()
      host.nextStep.push(jobNotice(kind, form))
      const driver = makeDriver(fixture)
      try {
        observeActivated(driver, host)
        await new Promise(resolve => setTimeout(resolve, 0))
        expect(fixture.quotaCalls).toHaveLength(0)
        expect(host.nextTurn).toHaveLength(0)
      } finally { await driver.dispose() }
    },
  )

  it('keeps an admission alive when a settled-job notice arrives during its quota await', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    let release!: () => void
    let started = false
    const gate = new Promise<void>(resolve => { release = resolve })
    const driver = new LoopXContinuationDriver({ isLiveAgent: () => true, retryDelaysMs: [0, 0],
      runner: async (file, args, options) => {
        if (args.includes('should-run')) { started = true; await gate }
        return fixture.runner(file, args, options)
      } })
    try {
      observeActivated(driver, host)
      await waitFor(() => started)
      const completed = jobNotice()
      host.nextStep.push(completed)
      driver.onInboxInserted(host.agent, completed)
      release()
      await waitFor(() => host.nextTurn.length === 1)
      expect(fixture.quotaCalls).toHaveLength(1)
    } finally { release(); await driver.dispose() }
  })

  it.each(['reject', 'aborted reject', 'filtered enter'])(
    'restores the original claimed batch once after downstream %s', async outcome => {
      const fixture = runnerFixture()
      const host = fakeAgent()
      const driver = makeDriver(fixture)
      const send = vi.spyOn(host.agent, 'send')
      try {
        observeActivated(driver, host)
        await waitFor(() => host.nextTurn.length === 1)
        const queued = host.nextTurn.shift() as UserMessage
        const context = { ...userMessage('runtime'), source: { kind: 'runtime-context', form: 'snapshot' } } as unknown as UserMessage
        const completed = jobNotice()
        const original = [queued, context, completed]
        let downstreamCalls = 0
        const controller = new AbortController()
        host.setStatus('running')
        driver.onInboxClaimed(host.agent, queued)
        expect(await driver.onPreStep(host.agent, original, controller.signal, async () => {
          downstreamCalls += 1
          original.splice(0, original.length)
          if (outcome === 'aborted reject') controller.abort()
          return outcome === 'filtered enter'
            ? { kind: 'enter', messages: [context] }
            : { kind: 'reject' }
        })).toEqual({ kind: 'reject' })
        expect(downstreamCalls).toBe(1)
        expect(host.nextStep).toEqual([context, completed])
        expect(send).toHaveBeenCalledTimes(2)
        expect(send).toHaveBeenCalledWith(context, 'next-step', outcome !== 'aborted reject')
        expect(send).toHaveBeenCalledWith(completed, 'next-step', outcome !== 'aborted reject')
      } finally { await driver.dispose() }
    },
  )

  it.each(['throw', 'abort then throw'])(
    'restores a claimed job notice exactly once after downstream %s', async outcome => {
      const fixture = runnerFixture()
      const host = fakeAgent()
      const driver = makeDriver(fixture)
      const send = vi.spyOn(host.agent, 'send')
      try {
        observeActivated(driver, host)
        await waitFor(() => host.nextTurn.length === 1)
        const queued = host.nextTurn.shift()!
        const notice = jobNotice()
        host.setStatus('running')
        driver.onInboxClaimed(host.agent, queued)
        driver.onInboxClaimed(host.agent, notice)
        const controller = new AbortController()
        const error = new Error('downstream failure')
        const abort = outcome === 'abort then throw'
        await expect(driver.onPreStep(host.agent, [queued, notice], controller.signal, async () => {
          if (abort) controller.abort()
          throw error
        })).rejects.toBe(error)
        expect(send).toHaveBeenCalledExactlyOnceWith(notice, 'next-step', !abort)
        expect(host.nextStep).toEqual([notice])
        expect(host.nextTurn).toEqual([])
      } finally { await driver.dispose() }
    },
  )

  it.each(['running', 'stopping'])(
    'waits ten minutes for %s host jobs and starts a fresh window on the next Turn', async status => {
      const fixture = runnerFixture()
      const host = fakeAgent()
      const clock = schedulerClock()
      const now = vi.spyOn(Date, 'now').mockReturnValue(1_000)
      const list = vi.fn(() => [{ owner: host.agent.id, status }])
      const driver = new LoopXContinuationDriver({ isLiveAgent: () => true, runner: fixture.runner,
        clock, retryDelaysMs: [0, 0], getJobs: () => ({ list }) })
      try {
        host.appendEvent(turnEndEvent(1))
        observeActivated(driver, host)
        await waitFor(() => clock.timers.length === 1)
        expect(clock.timers[0]?.delay).toBe(600_000)
        expect(list).toHaveBeenCalledWith(host.agent.id)
        expect(fixture.quotaCalls).toHaveLength(0)
        now.mockReturnValue(601_000)
        clock.timers[0]?.callback()
        await waitFor(() => host.nextTurn.length === 1)
        const queued = host.nextTurn.shift() as UserMessage
        host.setStatus('running')
        driver.onInboxClaimed(host.agent, queued)
        publishEvent(driver, host, sessionEvent('turn/start', { turn: 1 }))
        publishEvent(driver, host, sessionEvent('user/message', queued))
        publishEvent(driver, host, turnEndEvent(2))
        host.setStatus('idle')
        driver.onAgentStatus(host.agent, 'idle')
        await waitFor(() => clock.timers.length === 2)
        expect(clock.timers[1]?.delay).toBe(600_000)
        expect(fixture.quotaCalls).toHaveLength(1)
        await driver.cancelQueued({ agent: host.agent, session: host.agent.session })
        now.mockReturnValue(1_201_000)
        clock.timers[1]?.callback()
        await new Promise(resolve => setTimeout(resolve, 0))
        expect(fixture.quotaCalls).toHaveLength(1)
      } finally { now.mockRestore(); await driver.dispose() }
    },
  )

  it.each(['command without a Turn', 'Driver replacement'])(
    'keeps the original host-job deadline across %s', async interruption => {
      const fixture = runnerFixture()
      const host = fakeAgent()
      const clock = schedulerClock()
      const now = vi.spyOn(Date, 'now').mockReturnValue(1_000)
      const createDriver = () => new LoopXContinuationDriver({ runner: fixture.runner, isLiveAgent: () => true,
        clock, getJobs: () => ({ list: () => [{ owner: host.agent.id, status: 'running' }] }) })
      let driver = createDriver()
      try {
        host.appendEvent(turnEndEvent(1))
        observeActivated(driver, host)
        await waitFor(() => clock.timers.length === 1)
        expect(clock.timers[0]?.delay).toBe(600_000)
        now.mockReturnValue(541_000)
        if (interruption === 'command without a Turn') {
          publishEvent(driver, host, sessionEvent('command/run', { commandId: 'command' }))
          publishEvent(driver, host, sessionEvent('command/done', { commandId: 'command' }))
        } else {
          await driver.dispose('replacement')
          driver = createDriver()
          driver.observeAgent(host.agent)
        }
        await waitFor(() => clock.timers.length === 2)
        expect(fixture.quotaCalls).toHaveLength(0)
        expect(clock.timers[1]?.delay).toBe(60_000)
        now.mockReturnValue(601_000)
        clock.timers[1]?.callback()
        await waitFor(() => host.nextTurn.length === 1)
        expect(fixture.quotaCalls).toHaveLength(1)
      } finally { now.mockRestore(); await driver.dispose() }
    },
  )

  it.each([false, true])('reuses the deferred Core Turn identity (maintenance busy: %s)', async maintenanceBusy => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const clock = schedulerClock()
    const list = vi.fn<() => Array<{ owner: Agent['id']; status: string }>>(() => [])
    let started = false
    let release!: () => void
    const gate = new Promise<void>(resolve => { release = resolve })
    const makeTurnInstanceId = vi.fn(() => randomUUID())
    const driver = new LoopXContinuationDriver({ isLiveAgent: () => true, clock, getJobs: () => ({ list }),
      makeTurnInstanceId, retryDelaysMs: [0, 0],
      runner: async (file, args, options) => {
        if (args.includes('should-run')) { started = true; await gate }
        return fixture.runner(file, args, options)
      } })
    const enter = async (queued: UserMessage) => {
      host.setStatus('running')
      driver.onInboxClaimed(host.agent, queued)
      return driver.onPreStep(host.agent, [queued], new AbortController().signal,
        async () => ({ kind: 'enter', messages: [queued] }))
    }
    try {
      host.appendEvent(turnEndEvent(1))
      observeActivated(driver, host)
      await waitFor(() => started)
      list.mockReturnValue([{ owner: host.agent.id, status: 'running' }])
      release()
      await waitFor(() => clock.timers.length > 0 || host.nextTurn.length > 0)
      const early = host.nextTurn.shift()
      expect(early === undefined ? undefined : (await enter(early)).kind).toBeUndefined()
      expect(clock.timers[0]?.delay).toBe(600_000)
      expect(fixture.quotaCalls).toHaveLength(1)

      list.mockReturnValue([])
      const notice = jobNotice()
      if (maintenanceBusy) host.setMaintenanceBusy(true)
      host.nextStep.push(notice)
      driver.onInboxInserted(host.agent, notice)
      if (maintenanceBusy) {
        await waitFor(() => host.maintenanceCalls === 2)
        expect(host.nextTurn).toEqual([])
        host.setMaintenanceBusy(false)
      }
      await waitFor(() => host.nextTurn.length === 1)
      const queued = host.nextTurn.shift()!
      const turn = makeTurnInstanceId.mock.results[0]?.value
      expect(queued.source).toMatchObject({ turnInstanceId: turn })
      expect(makeTurnInstanceId).toHaveBeenCalledTimes(1)
      expect(fixture.quotaCalls).toHaveLength(2)
      expect(fixture.heartbeatCalls).toBe(1)
      expect(await enter(queued)).toMatchObject({ kind: 'enter' })
      expect(fixture.quotaCalls).toHaveLength(4)
      for (const args of fixture.quotaCalls) expect(args[args.indexOf('--turn-instance-id') + 1]).toBe(turn)
    } finally { release(); await driver.dispose() }
  })

  it.each(['command without a Turn', 'Driver replacement'])(
    'keeps the deferred Core Turn when quota revalidation is interrupted by %s', async interruption => {
      const fixture = runnerFixture()
      const host = fakeAgent()
      const clock = schedulerClock()
      const admission = Promise.withResolvers<void>()
      const revalidation = Promise.withResolvers<void>()
      const quotaTurns: Array<string | undefined> = []
      let jobRunning = false
      let turn = 0
      const createDriver = () => new LoopXContinuationDriver({
        isLiveAgent: () => true, clock, retryDelaysMs: [0, 0],
        getJobs: () => ({ list: () => jobRunning ? [{ owner: host.agent.id, status: 'running' }] : [] }),
        makeTurnInstanceId: () => `turn-${++turn}`,
        runner: async (file, args, options) => {
          if (args.includes('should-run')) {
            const request = quotaTurns.push(args[args.indexOf('--turn-instance-id') + 1])
            if (request === 1) await admission.promise
            if (request === 2) await revalidation.promise
          }
          return fixture.runner(file, args, options)
        },
      })
      let driver = createDriver()
      try {
        host.appendEvent(turnEndEvent(1))
        observeActivated(driver, host)
        await waitFor(() => quotaTurns.length === 1)
        jobRunning = true
        admission.resolve()
        await waitFor(() => clock.timers.length === 1)
        expect(host.nextTurn).toEqual([])

        jobRunning = false
        const notice = jobNotice()
        host.agent.send(notice, 'next-step', false)
        driver.onInboxInserted(host.agent, notice)
        await waitFor(() => quotaTurns.length === 2)
        expect(quotaTurns).toEqual(['turn-1', 'turn-1'])
        if (interruption === 'command without a Turn') {
          publishEvent(driver, host, sessionEvent('command/run', { commandId: 'command' }))
          publishEvent(driver, host, sessionEvent('command/done', { commandId: 'command' }))
          revalidation.resolve()
        } else {
          const disposed = driver.dispose('replacement')
          revalidation.resolve()
          await disposed
          driver = createDriver()
          driver.observeAgent(host.agent)
        }
        await waitFor(() => host.nextTurn.length === 1)
        expect(host.nextTurn[0]?.source).toMatchObject({ turnInstanceId: 'turn-1' })
        expect(quotaTurns).toEqual(['turn-1', 'turn-1', 'turn-1'])
        expect(fixture.heartbeatCalls).toBe(1)
      } finally {
        admission.resolve()
        revalidation.resolve()
        await driver.dispose()
      }
    },
  )

  it.each(['withdrawn human input across replacement', 'Core owner stop'])(
    'never queues the deferred Core Turn after %s', async interruption => {
      let runnable = true
      const fixture = runnerFixture({ shouldRun: () => runnable, schedulerMode: 'stop' })
      const host = fakeAgent()
      const clock = schedulerClock()
      const list = vi.fn<() => Array<{ owner: Agent['id']; status: string }>>(() => [])
      let started = false
      let release!: () => void
      const gate = new Promise<void>(resolve => { release = resolve })
      const makeTurnInstanceId = vi.fn(() => randomUUID())
      const createDriver = () => new LoopXContinuationDriver({
        isLiveAgent: () => true, clock, getJobs: () => ({ list }), makeTurnInstanceId,
        runner: async (file, args, options) => {
          if (args.includes('should-run')) { started = true; await gate }
          return fixture.runner(file, args, options)
        },
      })
      let driver = createDriver()
      try {
        host.appendEvent(turnEndEvent(1))
        observeActivated(driver, host)
        await waitFor(() => started)
        list.mockReturnValue([{ owner: host.agent.id, status: 'running' }])
        release()
        await waitFor(() => clock.timers.length === 1)
        const deferredTurn = makeTurnInstanceId.mock.results[0]?.value
        expect(host.nextTurn).toEqual([])
        if (interruption === 'withdrawn human input across replacement') {
          const message = userMessage('A different task')
          host.agent.send(message, 'next-step', false)
          driver.onInboxInserted(host.agent, message)
          expect(host.agent.inbox.remove(message.id)).toBe(true)
          await driver.dispose('replacement')
          driver = createDriver()
          driver.observeAgent(host.agent)
          await waitFor(() => clock.timers.length === 2)
        } else {
          runnable = false
        }
        list.mockReturnValue([])
        if (runnable) {
          driver.onAgentStatus(host.agent, 'idle')
        } else {
          const notice = jobNotice()
          host.agent.send(notice, 'next-step', false)
          driver.onInboxInserted(host.agent, notice)
        }
        await waitFor(() => host.nextTurn.length > 0 || fixture.quotaCalls.length > 1)
        expect(host.nextTurn.map(message => message.source)).not.toContainEqual(
          expect.objectContaining({ turnInstanceId: deferredTurn }),
        )
        if (runnable) {
          await waitFor(() => host.nextTurn.length === 1)
          expect(host.nextTurn[0]?.source).toMatchObject({ turnInstanceId: makeTurnInstanceId.mock.results[1]?.value })
        } else {
          expect(host.nextTurn).toEqual([])
          expect(fixture.quotaCalls[1]).toContain(deferredTurn)
        }
      } finally { release(); await driver.dispose() }
    },
  )

  it('rechecks immediately when the last owned job settles and retires the fallback timer', async () => {
    const fixture = runnerFixture({ shouldRun: false, schedulerMode: 'stop' })
    const host = fakeAgent()
    const clock = schedulerClock()
    const clearTimeout = vi.spyOn(clock, 'clearTimeout')
    const list = vi.fn(() => [
      { owner: host.agent.id, status: 'running' },
      { owner: undefined, status: 'running' },
    ])
    const driver = new LoopXContinuationDriver({ runner: fixture.runner, isLiveAgent: () => true,
      clock, getJobs: () => ({ list }) })
    try {
      host.appendEvent(turnEndEvent(1))
      observeActivated(driver, host)
      await waitFor(() => clock.timers.length === 1)
      list.mockReturnValue([{ owner: undefined, status: 'running' }])
      const completed = jobNotice()
      host.nextStep.push(completed)
      driver.onInboxInserted(host.agent, completed)
      await waitFor(() => fixture.quotaCalls.length === 1)
      await new Promise(resolve => setTimeout(resolve, 0))
      expect(clearTimeout).toHaveBeenCalledWith(clock.timers[0])
      clock.timers[0]?.callback()
      expect(fixture.quotaCalls).toHaveLength(1)
      expect(host.nextTurn).toEqual([])
    } finally { await driver.dispose() }
  })

  it('honors Core recovery attention after an expired host-job wait', async () => {
    const fixture = runnerFixture({ shouldRun: false, schedulerMode: 'stop',
      recovery: () => ({ schema_version: 'unsettled_host_turn_recovery_v0', state: 'attention_required' }) })
    const host = fakeAgent()
    const clock = schedulerClock()
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000)
    const driver = new LoopXContinuationDriver({ runner: fixture.runner, isLiveAgent: () => true,
      clock, getJobs: () => ({ list: () => [{ owner: host.agent.id, status: 'running' }] }) })
    try {
      host.appendEvent(turnEndEvent(1))
      observeActivated(driver, host)
      expect(clock.timers[0]?.delay).toBe(600_000)
      expect(fixture.quotaCalls).toHaveLength(0)
      now.mockReturnValue(601_000)
      clock.timers[0]?.callback()
      const capture = { agent: host.agent, session: host.agent.session }
      await waitFor(() => driver.hasRecoveryAttention(capture, { goalId, loopxAgentId: agentId }))
      expect(fixture.quotaCalls).toHaveLength(1)
      expect(fixture.heartbeatCalls).toBe(0)
      expect(clock.timers).toHaveLength(1)
      expect(host.nextTurn).toEqual([])
    } finally { now.mockRestore(); await driver.dispose() }
  })
})

describe('driver diagnostic records', () => {
  function diagnosticHome() {
    const home = mkdtempSync(join(tmpdir(), 'dsh-loopx-driver-'))
    vi.stubEnv('DSH_HOME', home)
    const logs = join(home, 'logs')
    const file = join(logs, 'dsh-loopx-driver.jsonl')
    return {
      home, logs, file,
      read: () => readFileSync(file, 'utf8').trim().split('\n').map(line => JSON.parse(line)),
      cleanup() {
        rmSync(home, { recursive: true, force: true })
      },
    }
  }

  it('persists error pauses and diagnostics under a tilde-expanded DSH home', async () => {
    vi.stubEnv('HOME', testHome)
    vi.stubEnv('USERPROFILE', testHome)
    vi.stubEnv('DSH_HOME', '~\\.dsh')
    const home = join(testHome, '.dsh')
    const host = fakeAgent()
    host.appendEvent(userSkillInvocationEvent())
    const originalCwd = process.cwd()
    process.chdir(testHome)
    try {
      const runtime = appliedDriverRuntime(host.agent, false)
      try {
        runtime.announceAgent('agent/error')
        const pauseId = createHash('sha256').update(host.agent.session.id).digest('hex')
        const pause = readFileSync(join(home, 'dsh-loopx-driver-pauses', pauseId), 'utf8')
        expect(JSON.parse(pause)).toMatchObject({ paused: true })
        const log = readFileSync(join(home, 'logs', 'dsh-loopx-driver.jsonl'), 'utf8')
        expect(JSON.parse(log)).toMatchObject({ kind: 'driver_start' })
      } finally {
        await runtime.dispose()
      }
    } finally {
      process.chdir(originalCwd)
    }
  })

  it('writes diagnostics to the default DSH home when DSH_HOME is unset', async () => {
    vi.stubEnv('HOME', testHome)
    vi.stubEnv('USERPROFILE', testHome)
    vi.stubEnv('DSH_HOME', undefined)
    const driver = makeDriver(runnerFixture())
    try {
      const log = readFileSync(join(testHome, '.dsh', 'logs', 'dsh-loopx-driver.jsonl'), 'utf8')
      expect(JSON.parse(log)).toMatchObject({ kind: 'driver_start' })
    } finally { await driver.dispose() }
  })

  it.each(['unbound', 'ambiguous', 'different goal', 'different agent'] as const)(
    'skips cold sessions whose binding becomes %s during the lifecycle read', async change => {
      const diagnostics = diagnosticHome()
      const host = fakeAgent()
      const { agent } = host
      host.appendEvent(userSkillInvocationEvent())
      const fixture = runnerFixture()
      let lifecycleRead = false
      const driver = new LoopXContinuationDriver({
        isLiveAgent: () => false,
        runner: async (file, args, options) => {
          if (args.includes('goal-lifecycle')) {
            lifecycleRead = true
            return { exitCode: 0, stderr: '', stdout: JSON.stringify({
              ok: true, schema_version: 'loopx_goal_activation_transition_v1',
              dry_run: true, execute: false, goal_id: goalId,
              before_state: 'active', after_state: 'stopped',
              changed: true, written: false, partial_write: false,
              readback: { schema_version: 'loopx_goal_activation_readback_v1', status: 'not_executed', verified: false },
            }) }
          }
          const result = await fixture.runner(file, args, options)
          if (!lifecycleRead || !args.includes('resolve-agent-thread')) return result
          const payload = JSON.parse(result.stdout)
          if (change === 'unbound' || change === 'ambiguous') payload.status = change
          if (change === 'different goal') payload.goal_id = 'other-goal'
          if (change === 'different agent') payload.agent_id = 'other-agent'
          const match = { goal_id: payload.goal_id, agent_id: payload.agent_id }
          payload.matches = change === 'unbound' ? [] : change === 'ambiguous'
            ? [match, { ...match, agent_id: 'other-agent' }] : [match]
          return { ...result, stdout: JSON.stringify(payload) }
        },
      })
      const resolveAgent = vi.fn(async () => ({ agent }))
      try {
        await driver.resumePersistedSessions({
          listSessions: async () => [{ header: agent.session.header, persisted: true }],
          inspectSession: async (_id, read) => read(agent.session),
        }, { resolveAgent }, () => false, new AbortController().signal)
        expect(resolveAgent).not.toHaveBeenCalled()
        expect(diagnostics.read()).toContainEqual(expect.objectContaining({
          sessionId, kind: 'resume_skipped', reason: 'binding_changed',
        }))
      } finally {
        await driver.dispose()
        diagnostics.cleanup()
      }
    },
  )

  it('loads only eligible cold sessions and leaves admission to agent/created', async () => {
    const diagnostics = diagnosticHome()
    const skipped = [
      'not_activated', 'no_exact_binding', 'ambiguous_binding', 'goalbar_paused',
      'error_paused', 'persisted_error_pause', 'core_stopped', 'native_goal_active',
    ]
    const hosts = new Map([...skipped, 'eligible'].map(id => [id, fakeAgent(id)]))
    const previous = fakeAgent('persisted_error_pause')
    const live = new Set<Agent>([previous.agent, hosts.get('goalbar_paused')!.agent])
    const fixture = runnerFixture()
    let thread = ''
    const createDriver = () => new LoopXContinuationDriver({
      isLiveAgent: agent => live.has(agent), errorPauseHome: diagnostics.home,
      runner: async (file, args, options) => {
        if (args.includes('resolve-agent-thread')) {
          thread = args[args.indexOf('--thread-id') + 1]!
          const result = await fixture.runner(file, args, options)
          if (thread === 'no_exact_binding' || thread === 'ambiguous_binding') {
            const payload = JSON.parse(result.stdout)
            payload.status = thread === 'no_exact_binding' ? 'missing' : 'ambiguous'
            payload.matches = thread === 'no_exact_binding' ? [] : [
              ...payload.matches, { goal_id: goalId, agent_id: 'other-agent' },
            ]
            return { ...result, stdout: JSON.stringify(payload) }
          }
          return result
        }
        if (args.includes('goal-lifecycle')) {
          expect(args).not.toContain('--execute')
          const active = thread !== 'core_stopped'
          return { exitCode: 0, stderr: '', stdout: JSON.stringify({
            ok: true, schema_version: 'loopx_goal_activation_transition_v1',
            dry_run: true, execute: false, goal_id: goalId,
            before_state: active ? 'active' : 'stopped', after_state: 'stopped',
            changed: active, written: false, partial_write: false,
            readback: { schema_version: 'loopx_goal_activation_readback_v1',
              status: active ? 'not_executed' : 'not_required', verified: !active },
          }) }
        }
        return fixture.runner(file, args, options)
      },
    })
    let driver = createDriver()
    const resolveAgent = vi.fn(async (id: Agent['id']) => {
      expect(fixture.quotaCalls).toHaveLength(0)
      const { agent } = hosts.get(id)!
      live.add(agent)
      driver.onSessionStart(agent)
      return { agent }
    })
    const listSessions = async () => [...hosts.values()].map(({ agent }) => ({
      header: agent.session.header, persisted: true,
    }))
    const inspectSession = async <T>(id: Agent['id'], read: (session: Agent['session']) => T) =>
      read(hosts.get(id)!.agent.session)
    const runResume = () => driver.resumePersistedSessions(
      { listSessions, inspectSession }, { resolveAgent },
      id => [...live].some(agent => agent.id === id), new AbortController().signal,
    )
    try {
      for (const [id, host] of hosts) {
        if (id !== 'not_activated') host.appendEvent(userSkillInvocationEvent())
      }
      hosts.get('error_paused')!.appendEvent(turnEndEvent(1, 'max-tokens'))
      hosts.get('native_goal_active')!.appendEvent(sessionEvent('goal/change', { goal: { phase: 'active' } }))
      previous.appendEvent(userSkillInvocationEvent())
      driver.onAgentError(previous.agent)
      const paused = hosts.get('goalbar_paused')!.agent
      await driver.cancelQueued({ agent: paused, session: paused.session })
      live.clear()

      await runResume()
      expect(resolveAgent.mock.calls).toEqual([['eligible']])
      await waitFor(() => hosts.get('eligible')!.nextTurn.length === 1)
      expect(fixture.quotaCalls).toHaveLength(1)
      expect(diagnostics.read()).toEqual(expect.arrayContaining([
        ...skipped.map(id => expect.objectContaining({
          sessionId: id, kind: 'resume_skipped',
          reason: id === 'ambiguous_binding' ? 'no_exact_binding'
            : id === 'persisted_error_pause' ? 'error_paused' : id,
        })),
        expect.objectContaining({ sessionId: 'eligible', kind: 'resume_candidate' }),
        expect.objectContaining({ sessionId: 'eligible', kind: 'resume_loaded' }),
      ]))
      await driver.dispose('replacement')
      driver = createDriver()
      await runResume()
      expect(resolveAgent.mock.calls).toEqual([['eligible']])
    } finally {
      await driver.dispose()
      diagnostics.cleanup()
    }
  })

  it('bounds cold log inspection when the Host services become available', async () => {
    const diagnostics = diagnosticHome()
    const hosts = Array.from({ length: 34 }, (_, index) => fakeAgent(`cold-${index}`))
    const ctx = new Context()
    ctx.reflect.provide('agents', {
      get: () => undefined,
      list: () => [],
      withoutInitiator: (operation: () => Promise<unknown>) => operation(),
    } as never)
    ctx.reflect.provide('loopxBootstrap', { state: 'ready' })
    const fiber = ctx.plugin(applyDriver)
    const inspected: Agent['id'][] = []
    const inspectSession = async <T>(id: Agent['id'], read: (session: Agent['session']) => T) => {
      inspected.push(id)
      return read(hosts.find(({ agent }) => agent.id === id)!.agent.session)
    }
    const resolveAgent = vi.fn()
    try {
      ctx.reflect.provide('sessionQuery', {
        listSessions: async () => hosts.map(({ agent }) => ({ header: agent.session.header, persisted: true })),
        inspectSession,
      })
      expect(inspected).toEqual([])
      ctx.reflect.provide('sessionController', { resolveAgent })
      await waitFor(() => inspected.length >= 32)
      expect(inspected).toHaveLength(32)
      expect(inspected.at(-1)).toBe('cold-31')
      expect(resolveAgent).not.toHaveBeenCalled()
    } finally {
      await fiber.dispose()
      diagnostics.cleanup()
    }
  })

  it('leaves an Agent restored externally to its active native goal until the native goal pauses', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = new LoopXContinuationDriver({ runner: fixture.runner, isLiveAgent: () => true })
    try {
      host.appendEvent(userSkillInvocationEvent())
      host.appendEvent(sessionEvent('goal/change', { goal: { phase: 'active' } }))
      driver.onSessionStart(host.agent)
      await new Promise(resolve => setTimeout(resolve, 0))
      expect(fixture.quotaCalls).toEqual([])
      expect(host.nextTurn).toEqual([])
      publishEvent(driver, host, sessionEvent('goal/change', { goal: { phase: 'paused' } }))
      await waitFor(() => host.nextTurn.length === 1)
      expect(fixture.quotaCalls).toHaveLength(1)
    } finally { await driver.dispose() }
  })

  it('looks up an optional jobs service after Driver attachment and logs the actual wait', async () => {
    const diagnostics = diagnosticHome()
    const host = fakeAgent()
    host.appendEvent(userSkillInvocationEvent())
    host.appendEvent(turnEndEvent(1))
    host.setStatus('running')
    vi.spyOn(host.agent, 'runMaintenance').mockRejectedValue(new LoopXCliError('transport', 'unexpected admission', false))
    const ctx = new Context()
    ctx.reflect.provide('agents', {
      get: (id: string) => id === host.agent.id ? host.agent : undefined,
      list: () => [host.agent],
      withoutInitiator: (operation: () => Promise<unknown>) => operation(),
    } as never)
    ctx.reflect.provide('loopxBootstrap', { state: 'ready' })
    const observed = vi.spyOn(LoopXContinuationDriver.prototype, 'observeAgent')
    const fiber = ctx.plugin(applyDriver)
    try {
      await waitFor(() => observed.mock.calls.length === 1)
      const list = vi.fn(() => [{ owner: host.agent.id, status: 'running' }])
      ctx.reflect.provide('jobs', { list })
      host.setStatus('idle')
      ctx.emit('agent/status', { agent: host.agent, status: 'idle' })
      await waitFor(() => list.mock.calls.length === 1)
      expect(list).toHaveBeenCalledWith(host.agent.id)
      expect(diagnostics.read()).toContainEqual(expect.objectContaining({
        sessionId, kind: 'job_wait', activeJobs: 1, waitUntil: expect.any(Number),
      }))
    } finally {
      await fiber.dispose()
      observed.mockRestore()
      diagnostics.cleanup()
    }
  })

  it('persists exact readiness blockers and suppresses duplicates for 60 seconds', async () => {
    const diagnostics = diagnosticHome()
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000)
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    const notice = jobNotice()
    try {
      host.setStatus('running')
      observeActivated(driver, host)
      driver.onAgentError(host.agent)
      host.nextStep.push(userMessage('Pending input'), notice)
      host.setStatus('idle')
      driver.onInboxInserted(host.agent, notice)
      now.mockReturnValue(60_999)
      driver.onInboxInserted(host.agent, notice)
      await new Promise(resolve => setTimeout(resolve, 0))

      const blocked = {
        sessionId, kind: 'not_ready', blockers: ['pauseAfterTurnError', 'inbox_pending'], inboxSourceKinds: ['user'],
      }
      expect(diagnostics.read()).toMatchObject([
        { time: expect.any(String), sessionId: null, kind: 'driver_start' },
        { sessionId, kind: 'observe', status: 'running', activated: true },
        blocked,
      ])
      now.mockReturnValue(61_000)
      driver.onInboxInserted(host.agent, notice)
      await new Promise(resolve => setTimeout(resolve, 0))
      expect(diagnostics.read().filter(entry => entry.kind === 'not_ready')).toMatchObject([blocked, blocked])
      expect(fixture.quotaCalls).toHaveLength(0)
    } finally {
      now.mockRestore()
      await driver.dispose()
      diagnostics.cleanup()
    }
  })

  it('queues normally with an unusable diagnostics home and logs after filesystem recovery', async () => {
    const diagnostics = diagnosticHome()
    const blockedHome = join(diagnostics.home, 'blocked')
    writeFileSync(blockedHome, '')
    vi.stubEnv('DSH_HOME', blockedHome)
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    try {
      observeActivated(driver, host)
      await waitFor(() => host.nextTurn.length === 1)
      expect(host.nextTurn[0]?.source).toMatchObject({ kind: 'loopx-continuation', turnInstanceId: 'turn-stable' })
      expect(fixture.quotaCalls).toHaveLength(1)

      rmSync(blockedHome)
      await driver.dispose()
      const log = readFileSync(join(blockedHome, 'logs', 'dsh-loopx-driver.jsonl'), 'utf8')
      expect(JSON.parse(log)).toMatchObject({ sessionId: null, kind: 'driver_dispose' })
    } finally {
      await driver.dispose()
      diagnostics.cleanup()
    }
  })

  it('rotates the diagnostics log at 5 MiB and replaces the previous archive', async () => {
    const diagnostics = diagnosticHome()
    const fixture = runnerFixture()
    const host = fakeAgent()
    const archive = `${diagnostics.file}.1`
    const full = Buffer.alloc(5 * 1024 * 1024)
    mkdirSync(diagnostics.logs)
    writeFileSync(diagnostics.file, full)
    writeFileSync(archive, 'old archive')
    const driver = makeDriver(fixture)
    try {
      observeActivated(driver, host)
      await waitFor(() => host.nextTurn.length === 1)
      expect(readFileSync(archive).equals(full)).toBe(true)
      expect(diagnostics.read()).toMatchObject([
        { sessionId: null, kind: 'driver_start' },
        { sessionId, kind: 'observe', status: 'idle', activated: true },
        { sessionId, kind: 'plan', planKind: 'queue', bindingStatus: 'bound' },
        { sessionId, kind: 'queued', turnInstanceId: 'turn-stable' },
      ])
    } finally {
      await driver.dispose()
      diagnostics.cleanup()
    }
  })

  it('writes skipped requests and safe admission warnings to the diagnostics log', async () => {
    const diagnostics = diagnosticHome()
    const fixture = runnerFixture({ typedQuotaFailure: true })
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    try {
      driver.observeAgent(host.agent)
      observeActivated(driver, host)
      await waitFor(() => driver.readContinuationAdmission({ agent: host.agent, session: host.agent.session }).state === 'failed')
      expect(diagnostics.read()).toEqual(expect.arrayContaining([
        expect.objectContaining({ sessionId, kind: 'request_skipped', reason: 'not_activated' }),
        expect.objectContaining({ sessionId, kind: 'warn',
          message: expect.stringContaining('admission_failed stage=quota kind=typed_failure') }),
      ]))
    } finally { await driver.dispose(); diagnostics.cleanup() }
  })

  it.each(['before', 'downstream'])('preserves user input with a throwing Cordis exporter in the %s batch', async phase => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const service = new LoggerService(new Context())
    const exporter = { export: vi.fn(() => { throw new Error('exporter unavailable') }) }
    const removeExporter = service.exporter(exporter)
    const logger = new Logger({ name: 'fixture', level: 2 }, service)
    const driver = new LoopXContinuationDriver({
      isLiveAgent: () => true, runner: fixture.runner,
      warn: message => { logger.warn(message) },
    })
    try {
      observeActivated(driver, host)
      await waitFor(() => host.nextTurn.length === 1)
      const submitted = host.nextTurn.shift()!
      const other = userMessage('pending user input')
      const next = vi.fn(async () => ({ kind: 'enter' as const, messages: [submitted, other] }))
      host.setStatus('running')
      driver.onInboxClaimed(host.agent, submitted)
      await expect(driver.onPreStep(host.agent, phase === 'before' ? [submitted, other] : [submitted],
        new AbortController().signal, next,
      )).resolves.toEqual(phase === 'before'
        ? { kind: 'reject' }
        : { kind: 'enter', messages: [other] })
      expect(exporter.export).toHaveBeenCalledOnce()
      expect(host.nextStep).toEqual(phase === 'before' ? [other] : [])
      expect(next).toHaveBeenCalledTimes(phase === 'before' ? 0 : 1)
      await expect(driver.onPreStep(host.agent, [submitted], new AbortController().signal, next))
        .resolves.toEqual({ kind: 'reject' })
      expect(host.nextStep).toEqual(phase === 'before' ? [other] : [])
      expect(host.cancelCalls).toBe(0)
    } finally {
      removeExporter()
      await driver.dispose()
    }
  })

  it('records the maintenance signal independently of the Driver controller', async () => {
    const host = fakeAgent()
    const maintenance = new AbortController()
    const warnings: string[] = []
    vi.spyOn(host.agent, 'runMaintenance').mockImplementation(task => task(maintenance.signal))
    const driver = new LoopXContinuationDriver({
      isLiveAgent: () => true,
      resolveCommand: async () => {
        maintenance.abort()
        throw new LoopXCliError('missing', 'PRIVATE output', false)
      },
      warn: message => warnings.push(message),
    })
    try {
      observeActivated(driver, host)
      await waitFor(() => warnings.length > 0)
      expect(warnings[0]).toContain('stage=resolve-command kind=missing')
      expect(warnings[0]).toContain('signal_aborted=true')
    } finally { await driver.dispose() }
  })

  it.each([
    ['resolve-command', '--version'], ['resolve-binding', 'resolve-agent-thread'],
    ['quota', 'should-run'], ['heartbeat', 'heartbeat-prompt'],
  ])('identifies %s failures without publishing command output', async (stage, arg) => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const warnings: string[] = []
    const driver = new LoopXContinuationDriver({
      isLiveAgent: () => true, retryDelaysMs: [0, 0],
      runner: async (file, args, options) => {
        if (args.includes(arg)) throw new LoopXCliError('missing', 'PRIVATE command output', false, undefined, { osCode: 'ENOENT' })
        return fixture.runner(file, args, options)
      },
      warn: message => warnings.push(message),
    })
    try {
      observeActivated(driver, host)
      await waitFor(() => warnings.length > 0)
      expect(warnings).toHaveLength(1)
      expect(warnings[0]).toContain(`admission_failed stage=${stage} kind=missing`)
      expect(warnings[0]).toMatch(/code=ENOENT .*candidate=(path-loopx|python-module) cwd_exists=(true|false).* elapsed_ms=\d+$/u)
      expect(warnings.join('')).not.toContain('PRIVATE')
      expect(host.nextTurn).toHaveLength(0)
    } finally { await driver.dispose() }
  })

  it('distinguishes a typed maintenance-slot failure', async () => {
    const host = fakeAgent()
    const warnings: string[] = []
    vi.spyOn(host.agent, 'runMaintenance').mockImplementation(() => { throw new LoopXCliError('transport', 'PRIVATE', true) })
    const driver = new LoopXContinuationDriver({ isLiveAgent: () => true, warn: message => warnings.push(message) })
    try {
      observeActivated(driver, host)
      await waitFor(() => warnings.length > 0)
      expect(warnings[0]).toContain('stage=maintenance-slot kind=transport')
      expect(warnings.join('')).not.toContain('PRIVATE')
    } finally { await driver.dispose() }
  })

  it.each(['before', 'downstream'])('records only competing source kinds in the %s batch', async phase => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const warnings: string[] = []
    const driver = new LoopXContinuationDriver({
      isLiveAgent: () => true, runner: fixture.runner, retryDelaysMs: [0, 0],
      warn: message => warnings.push(message),
    })
    try {
      observeActivated(driver, host)
      await waitFor(() => host.nextTurn.length === 1)
      const submitted = host.nextTurn.shift()!
      const other = userMessage('PRIVATE message text')
      host.setStatus('running')
      driver.onInboxClaimed(host.agent, submitted)
      expect(await driver.onPreStep(host.agent, phase === 'before' ? [submitted, other] : [submitted],
        new AbortController().signal, async () => ({ kind: 'enter', messages: [submitted, other] }),
      )).toEqual(phase === 'before'
        ? { kind: 'reject' }
        : { kind: 'enter', messages: [other] })
      expect(warnings[0]).toContain(`reason=${phase === 'before' ? 'competing-messages' : 'downstream-batch'} other_kinds=user`)
      expect(warnings.join('')).not.toContain('PRIVATE')
    } finally { await driver.dispose() }
  })
})

describe('pre-step authority diagnostics', () => {
  it.each(['human input', 'verified Start'])(
    'retains a typed admission failure across Driver replacement until %s', async recovery => {
      const fixture = runnerFixture()
      const denied = runnerFixture({ typedQuotaFailure: true })
      const host = fakeAgent()
      const capture = { agent: host.agent, session: host.agent.session }
      const failed = { state: 'failed', category: 'admission_rejected' }
      const onAdmissionChange = vi.fn()
      let reject = false
      const createDriver = () => new LoopXContinuationDriver({ isLiveAgent: () => true,
        retryDelaysMs: [0, 0], onAdmissionChange,
        runner: (file, args, options) => (reject ? denied : fixture).runner(file, args, options),
      })
      let driver = createDriver()
      try {
        observeActivated(driver, host)
        await waitFor(() => host.nextTurn.length === 1)
        const queued = host.nextTurn.shift()!
        host.setStatus('running')
        driver.onInboxClaimed(host.agent, queued)
        reject = true
        const next = vi.fn(async () => ({ kind: 'enter' as const, messages: [queued] }))
        expect(await driver.onPreStep(host.agent, [queued], new AbortController().signal, next))
          .toEqual({ kind: 'reject' })
        expect(next).not.toHaveBeenCalled()
        expect(driver.readContinuationAdmission(capture)).toEqual(failed)
        await driver.dispose('replacement')

        driver = createDriver()
        host.setStatus('idle')
        driver.observeAgent(host.agent)
        driver.onAgentStatus(host.agent, 'idle')
        await new Promise(resolve => setTimeout(resolve, 0))
        expect(denied.quotaCalls).toHaveLength(1)
        expect(host.nextTurn).toEqual([])
        expect(driver.readContinuationAdmission(capture)).toEqual(failed)

        reject = false
        onAdmissionChange.mockClear()
        if (recovery === 'human input') {
          driver.onInboxInserted(host.agent, userMessage('Resume'))
          expect(driver.readContinuationAdmission(capture)).toEqual({ state: 'clear' })
          driver.onAgentStatus(host.agent, 'idle')
          await waitFor(() => host.nextTurn.length === 1)
        } else {
          await expect(driver.evaluateActivatedSession(capture)).resolves.toEqual({ kind: 'applied' })
          expect(host.nextTurn).toHaveLength(1)
        }
        expect(driver.readContinuationAdmission(capture)).toEqual({ state: 'clear' })
        expect(onAdmissionChange).toHaveBeenCalledExactlyOnceWith(capture.session)
      } finally { await driver.dispose() }
    },
  )

  it('surfaces a typed quota rejection at pre-step and waits for explicit Start', async () => {
    const fixture = runnerFixture()
    const denied = runnerFixture({ typedQuotaFailure: true })
    const host = fakeAgent()
    const capture = { agent: host.agent, session: host.agent.session }
    const onAdmissionChange = vi.fn()
    let reject = false
    const driver = new LoopXContinuationDriver({
      isLiveAgent: () => true, retryDelaysMs: [0, 0], onAdmissionChange,
      runner: (file, args, options) => (reject ? denied : fixture).runner(file, args, options),
    })
    try {
      observeActivated(driver, host)
      await waitFor(() => host.nextTurn.length === 1)
      const submitted = host.nextTurn.shift()!
      host.setStatus('running')
      driver.onInboxClaimed(host.agent, submitted)
      reject = true
      const next = vi.fn(async () => ({ kind: 'enter' as const, messages: [submitted] }))
      await expect(driver.onPreStep(host.agent, [submitted], new AbortController().signal, next))
        .resolves.toEqual({ kind: 'reject' })
      expect(next).not.toHaveBeenCalled()
      expect(driver.readContinuationAdmission(capture)).toEqual({ state: 'failed', category: 'admission_rejected' })
      expect(onAdmissionChange).toHaveBeenCalledWith(capture.session)
      host.setStatus('idle')
      driver.onAgentStatus(host.agent, 'idle')
      await new Promise(resolve => setTimeout(resolve, 0))
      expect(denied.quotaCalls).toHaveLength(1)
      reject = false
      await expect(driver.evaluateActivatedSession(capture)).resolves.toEqual({ kind: 'applied' })
      expect(host.nextTurn).toHaveLength(1)
      expect(driver.readContinuationAdmission(capture)).toEqual({ state: 'clear' })
    } finally { await driver.dispose() }
  })

  it.each(['admission', 'pre-step'])(
    'retries on the next idle after a recoverable receipt write failure at %s', async phase => {
      const fixture = runnerFixture()
      const host = fakeAgent()
      const capture = { agent: host.agent, session: host.agent.session }
      let failing = phase === 'admission'
      // Core's own projection when it cannot persist this Turn's heartbeat receipt.
      const writeFailed = (args: readonly string[]) => ({ exitCode: 1, stderr: '', stdout: JSON.stringify({
        ok: false, mode: 'should-run', goal_id: goalId, decision: 'skip', should_run: false,
        effective_action: 'heartbeat_receipt_write_failed', state: 'blocked_health',
        agent_identity: { agent_id: agentId, registered: true },
        heartbeat_receipt: { schema_version: 'heartbeat_quota_receipt_v0', status: 'write_failed',
          turn_instance_id: args[args.indexOf('--turn-instance-id') + 1], stall_observation: 'not_evaluated' },
      }) })
      const driver = new LoopXContinuationDriver({ isLiveAgent: () => true, retryDelaysMs: [0, 0],
        runner: async (file, args, options) => failing && args.includes('should-run')
          ? writeFailed(args) : fixture.runner(file, args, options) })
      try {
        observeActivated(driver, host)
        if (phase === 'pre-step') {
          await waitFor(() => host.nextTurn.length === 1)
          const queued = host.nextTurn.shift()!
          host.setStatus('running')
          driver.onInboxClaimed(host.agent, queued)
          failing = true
          const next = vi.fn(async () => ({ kind: 'enter' as const, messages: [queued] }))
          expect(await driver.onPreStep(host.agent, [queued], new AbortController().signal, next))
            .toEqual({ kind: 'reject' })
          expect(next).not.toHaveBeenCalled()
        }
        await waitFor(() => driver.readContinuationAdmission(capture).state === 'failed')
        failing = false
        host.setStatus('idle')
        driver.onAgentStatus(host.agent, 'idle')
        await waitFor(() => host.nextTurn.length === 1)
        expect(driver.readContinuationAdmission(capture)).toEqual({ state: 'clear' })
      } finally { await driver.dispose() }
    },
  )

  it('holds a genuine typed quota rejection at admission until explicit Start', async () => {
    const fixture = runnerFixture()
    const denied = runnerFixture({ typedQuotaFailure: true })
    const host = fakeAgent()
    const capture = { agent: host.agent, session: host.agent.session }
    let reject = true
    const driver = new LoopXContinuationDriver({ isLiveAgent: () => true, retryDelaysMs: [0, 0],
      runner: (file, args, options) => (reject ? denied : fixture).runner(file, args, options) })
    try {
      observeActivated(driver, host)
      await waitFor(() => driver.readContinuationAdmission(capture).state === 'failed')
      reject = false
      driver.onAgentStatus(host.agent, 'idle')
      await new Promise(resolve => setTimeout(resolve, 0))
      expect(fixture.quotaCalls).toHaveLength(0)
      expect(host.nextTurn).toEqual([])
      await expect(driver.evaluateActivatedSession(capture)).resolves.toEqual({ kind: 'applied' })
      expect(host.nextTurn).toHaveLength(1)
    } finally { await driver.dispose() }
  })

  it('records a failed recheck before rejecting its continuation', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const warnings: string[] = []
    let failRecheck = false
    const driver = new LoopXContinuationDriver({
      isLiveAgent: () => true, retryDelaysMs: [0, 0],
      runner: (file, args, options) => {
        if (failRecheck) throw new LoopXCliError('missing', 'PRIVATE path', false, undefined, { osCode: 'ENOENT' })
        return fixture.runner(file, args, options)
      },
      warn: message => warnings.push(message),
    })
    try {
      observeActivated(driver, host)
      await waitFor(() => host.nextTurn.length === 1)
      const submitted = host.nextTurn.shift()!
      host.setStatus('running')
      driver.onInboxClaimed(host.agent, submitted)
      failRecheck = true
      expect(await driver.onPreStep(host.agent, [submitted], new AbortController().signal,
        async () => ({ kind: 'enter', messages: [submitted] }),
      )).toEqual({ kind: 'reject' })
      expect(warnings[0]).toContain('admission_failed stage=pre-step-recheck kind=missing')
      expect(warnings[1]).toContain('reason=authority-before-next other_kinds=none')
      expect(warnings.join('')).not.toContain('PRIVATE')
    } finally { await driver.dispose() }
  })
})

describe('same-session LoopX driver', () => {
  it.each(['runtime-context', 'malformed-driver', 'admitted-driver'] as const)(
    'does not infer recovery from an unrelated completed %s Turn on reopen', async kind => {
      const fixture = runnerFixture()
      const host = fakeAgent()
      host.appendEvent(userSkillInvocationEvent())
      host.appendEvent(sessionEvent('turn/end', { turn: 0, reason: { kind: 'max-tokens' } }))
      host.appendEvent(sessionEvent('turn/start', { turn: 1 }))
      host.appendEvent(sessionEvent('user/message', {
        ...userMessage('completed background input'), source: kind === 'runtime-context'
          ? { kind }
          : kind === 'malformed-driver'
            ? { kind: 'loopx-continuation' }
            : { kind: 'loopx-continuation', schemaVersion: 'loopx_dsh_continuation_v0',
                goalId, agentId, turnInstanceId: 'completed-recovery-turn' },
      } as unknown as UserMessage))
      host.appendEvent(sessionEvent('turn/end', { turn: 1, reason: { kind: 'completed' } }))
      const driver = makeDriver(fixture)
      try {
        driver.observeAgent(host.agent)
        if (kind === 'admitted-driver') await waitFor(() => host.nextTurn.length === 1)
        else {
          await new Promise<void>(resolve => setTimeout(resolve, 0))
          expect(fixture.quotaCalls).toEqual([])
          expect(host.nextTurn).toEqual([])
        }
      } finally { await driver.dispose() }
    },
  )

  it.each(['max-tokens', 'aborted'] as const)(
    'retains a reopened Session error pause after %s until explicit Start', async reason => {
      const fixture = runnerFixture()
      const host = fakeAgent()
      host.appendEvent(userSkillInvocationEvent())
      host.appendEvent(sessionEvent('turn/end', { turn: 0, reason: { kind: reason } }))
      const driver = makeDriver(fixture)
      try {
        driver.observeAgent(host.agent)
        driver.onSessionStart(host.agent)
        await new Promise<void>(resolve => setTimeout(resolve, 0))
        expect(fixture.quotaCalls).toEqual([])
        await driver.evaluateActivatedSession({ agent: host.agent, session: host.agent.session })
        expect(host.nextTurn).toHaveLength(1)
      } finally { await driver.dispose() }
    },
  )

  it.each(['user', 'runtime-context'] as const)(
    'reconstructs an error pause using typed %s input, never message wording', async kind => {
      const fixture = runnerFixture()
      const host = fakeAgent()
      host.appendEvent(userSkillInvocationEvent())
      host.appendEvent(sessionEvent('turn/end', { turn: 0, reason: { kind: 'max-tokens' } }))
      host.appendEvent(sessionEvent('user/message', {
        ...userMessage('Start LoopX again'), source: { kind },
      } as unknown as UserMessage))
      const driver = makeDriver(fixture)
      try {
        driver.observeAgent(host.agent)
        if (kind === 'user') await waitFor(() => host.nextTurn.length === 1)
        else {
          await new Promise<void>(resolve => setTimeout(resolve, 0))
          expect(fixture.quotaCalls).toEqual([])
        }
      } finally { await driver.dispose() }
    },
  )

  it.each([undefined, null, 'false'])(
    'does not clear an observed recovery hold from an invalid should_run value (%s)', async invalid => {
      const host = fakeAgent()
      let corrupt = false
      const fixture = runnerFixture({ shouldRun: false, schedulerMode: 'stop',
        recovery: () => ({ schema_version: 'unsettled_host_turn_recovery_v0', state: 'attention_required' }) })
      const driver = new LoopXContinuationDriver({ isLiveAgent: () => true,
        runner: async (file, args, options) => {
          const result = await fixture.runner(file, args, options)
          if (!corrupt || !args.includes('should-run')) return result
          return { ...result, stdout: JSON.stringify({ ...JSON.parse(result.stdout), should_run: invalid }) }
        },
      })
      try {
        host.appendEvent(userSkillInvocationEvent())
        const capture = { agent: host.agent, session: host.agent.session }
        const expected = { goalId, loopxAgentId: agentId }
        await driver.evaluateActivatedSession(capture)
        expect(driver.hasRecoveryAttention(capture, expected)).toBe(true)
        corrupt = true
        await driver.evaluateActivatedSession(capture)
        expect(driver.hasRecoveryAttention(capture, expected)).toBe(true)
        expect(host.nextTurn).toEqual([])
      } finally { await driver.dispose() }
    },
  )

  it.each(['nextStep', 'nextTurn'] as const)(
    'does not let pending host context in %s undo a rejected continuation pause', async queue => {
      const fixture = runnerFixture()
      const host = fakeAgent()
      const driver = makeDriver(fixture)
      try {
        observeActivated(driver, host)
        await waitFor(() => host.nextTurn.length === 1)
        const submitted = host.nextTurn.shift()!
        host.setStatus('running')
        driver.onInboxClaimed(host.agent, submitted)
        const context = { ...userMessage('transient context'), source: { kind: 'runtime-context' } } as unknown as UserMessage
        expect(await driver.onPreStep(host.agent, [submitted], new AbortController().signal, async () => {
          host[queue].push(context)
          return { kind: 'enter', messages: [{ ...submitted, content: [{ type: 'text', text: 'rewritten' }] }] }
        })).toEqual({ kind: 'reject' })
        host[queue].splice(0)
        const calls = fixture.quotaCalls.length
        host.setStatus('idle')
        for (let i = 0; i < 3; i += 1) driver.onAgentStatus(host.agent, 'idle')
        await new Promise<void>(resolve => setTimeout(resolve, 0))
        expect(fixture.quotaCalls).toHaveLength(calls)
        expect(host.nextTurn).toEqual([])
        await driver.evaluateActivatedSession({ agent: host.agent, session: host.agent.session })
        expect(host.nextTurn).toHaveLength(1)
      } finally { await driver.dispose() }
    },
  )

  it('retains cancellation ownership through replacement until an actual removal', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const first = makeDriver(fixture)
    observeActivated(first, host)
    await waitFor(() => host.nextTurn.length === 1)
    const submitted = host.nextTurn.shift()!
    host.setStatus('running')
    first.onInboxClaimed(host.agent, submitted)
    await first.dispose('replacement')
    const successor = makeDriver(fixture)
    successor.observeAgent(host.agent)
    await successor.dispose('removal')
    expect(host.cancelCalls).toBe(1)
  })

  it('retains an error pause through Driver replacement and resumes only for human input or Start', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const first = makeDriver(fixture)
    observeActivated(first, host)
    await waitFor(() => host.nextTurn.length === 1)
    first.onAgentError(host.agent)
    await first.dispose('replacement')
    const calls = fixture.quotaCalls.length
    const successor = makeDriver(fixture)
    try {
      successor.observeAgent(host.agent)
      successor.onAgentStatus(host.agent, 'idle')
      await new Promise<void>(resolve => setTimeout(resolve, 0))
      expect(fixture.quotaCalls).toHaveLength(calls)
      expect(host.nextTurn).toEqual([])
      await successor.evaluateActivatedSession({ agent: host.agent, session: host.agent.session })
      expect(host.nextTurn).toHaveLength(1)
    } finally { await successor.dispose() }
  })

  it('clears a verified obsolete recovery hold even when the new heartbeat body cannot be read', async () => {
    const host = fakeAgent()
    let runnable = false
    const fixture = runnerFixture({
      shouldRun: () => runnable, schedulerMode: 'stop',
      recovery: () => ({ schema_version: 'unsettled_host_turn_recovery_v0', state: runnable ? 'clear' : 'attention_required' }),
    })
    const driver = new LoopXContinuationDriver({
      isLiveAgent: agent => agent === host.agent, retryDelaysMs: [0, 0],
      runner: (file, args, options) => {
        if (runnable && args.includes('heartbeat-prompt')) {
          throw new LoopXCliError('transport', 'body unavailable', false)
        }
        return fixture.runner(file, args, options)
      },
    })
    try {
      host.appendEvent(userSkillInvocationEvent())
      const capture = { agent: host.agent, session: host.agent.session }
      const expected = { goalId, loopxAgentId: agentId }
      await driver.evaluateActivatedSession(capture)
      expect(driver.hasRecoveryAttention(capture, expected)).toBe(true)
      runnable = true
      await driver.evaluateActivatedSession(capture)
      expect(driver.hasRecoveryAttention(capture, expected)).toBe(false)
      expect(host.nextTurn).toEqual([])
    } finally { await driver.dispose() }
  })

  it('does not report a cancelled maintenance probe as a missing CLI', async () => {
    const host = fakeAgent()
    const maintenance = new AbortController()
    const warnings: string[] = []
    let probes = 0
    vi.spyOn(host.agent, 'runMaintenance').mockImplementation(task => task(maintenance.signal))
    const driver = new LoopXContinuationDriver({
      isLiveAgent: () => true,
      runner: async () => {
        probes += 1
        maintenance.abort()
        return { exitCode: 1, stdout: '', stderr: '' }
      },
      warn: message => warnings.push(message),
    })
    try {
      observeActivated(driver, host)
      await waitFor(() => probes > 0)
      await new Promise<void>(resolve => setTimeout(resolve, 0))
      expect(warnings).toEqual([])
      expect(host.nextTurn).toHaveLength(0)
      expect(probes).toBe(1)
    } finally { await driver.dispose() }
  })

  it('retries admission after attach-time maintenance becomes free without an idle event', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    host.setMaintenanceBusy(true)

    try {
      observeActivated(driver, host)
      await new Promise<void>(resolve => setTimeout(resolve, 0))
      expect(host.maintenanceCalls).toBe(1)
      expect(fixture.calls).toHaveLength(0)
      expect(host.nextTurn).toHaveLength(0)

      host.setMaintenanceBusy(false)
      await waitFor(() => host.nextTurn.length === 1)
      driver.onAgentStatus(host.agent, 'idle')
      driver.onAgentStatus(host.agent, 'idle')
      await new Promise<void>(resolve => setTimeout(resolve, 0))

      expect(host.maintenanceCalls).toBe(2)
      expect(fixture.quotaCalls).toHaveLength(1)
      expect(fixture.heartbeatCalls).toBe(1)
      expect(host.nextTurn).toHaveLength(1)
    } finally {
      await driver.dispose()
    }
  })

  it('waits for persistent maintenance contention without polling or spinning', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const clock = { setTimeout: vi.fn(), clearTimeout: vi.fn() }
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      clock,
    })
    const whenIdle = vi.spyOn(host.agent, 'whenIdle')
    host.setMaintenanceBusy(true)

    try {
      observeActivated(driver, host)
      await new Promise<void>(resolve => setTimeout(resolve, 0))
      await new Promise<void>(resolve => setTimeout(resolve, 0))

      expect(whenIdle).toHaveBeenCalledTimes(1)
      expect(host.maintenanceCalls).toBe(1)
      expect(clock.setTimeout).not.toHaveBeenCalled()
      expect(fixture.calls).toHaveLength(0)
      expect(host.nextTurn).toHaveLength(0)
    } finally {
      await driver.dispose()
    }
  })

  it.each(['Pause', 'dispose', 'human input', 'Session replacement', 'Agent disposal'])(
    'cancels pending maintenance admission retry on %s',
    async action => {
      const fixture = runnerFixture()
      const host = fakeAgent()
      const driver = makeDriver(fixture)
      host.setMaintenanceBusy(true)

      try {
        observeActivated(driver, host)
        await new Promise<void>(resolve => setTimeout(resolve, 0))
        expect(host.maintenanceCalls).toBe(1)

        if (action === 'Pause') {
          await expect(driver.cancelQueued({ agent: host.agent, session: host.agent.session }))
            .resolves.toEqual({ kind: 'applied' })
        } else if (action === 'dispose') {
          await driver.dispose()
        } else if (action === 'human input') {
          const human = userMessage('human priority')
          host.nextTurn.push(human)
          driver.onInboxInserted(host.agent, human)
        } else if (action === 'Session replacement') {
          host.replaceSession(undefined, [])
          driver.onSessionStart(host.agent)
        } else {
          driver.onAgentDisposed(host.agent)
        }
        host.setMaintenanceBusy(false)
        await new Promise<void>(resolve => setTimeout(resolve, 0))

        expect(host.maintenanceCalls).toBe(1)
        expect(fixture.calls).toHaveLength(0)
        expect(host.nextTurn.filter(message => message.source.kind === 'loopx-continuation'))
          .toHaveLength(0)
      } finally {
        await driver.dispose()
      }
    },
  )

  it.each([
    ['typed authority denial', new LoopXCliError('typed_failure', 'denied', false)],
    ['invalid schema', new LoopXCliError('invalid_schema', 'incompatible', false)],
    ['missing CLI', new LoopXCliError('missing', 'unavailable', false)],
    ['cancellation', new LoopXCliError('aborted', 'cancelled', false)],
    ['busy-looking admission error', new Error(`agent "${sessionId}" already has active work`)],
  ])('does not retry %s after the maintenance callback starts', async (_label, error) => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const whenIdle = vi.spyOn(host.agent, 'whenIdle')
    let resolutions = 0
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      resolveCommand: async () => {
        resolutions += 1
        throw error
      },
    })
    host.setMaintenanceBusy(true)

    try {
      observeActivated(driver, host)
      await new Promise<void>(resolve => setTimeout(resolve, 0))
      host.setMaintenanceBusy(false)
      await waitFor(() => resolutions === 1)
      await new Promise<void>(resolve => setTimeout(resolve, 0))

      expect(host.maintenanceCalls).toBe(2)
      expect(whenIdle).toHaveBeenCalledTimes(1)
      expect(resolutions).toBe(1)
      expect(fixture.calls).toHaveLength(0)
      expect(host.nextTurn).toHaveLength(0)
    } finally {
      await driver.dispose()
    }
  })

  it('keeps a newly observed inactive Agent at zero LoopX I/O and zero timers', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const timers: unknown[] = []
    let detachedCalls = 0
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      runDetached: operation => {
        detachedCalls += 1
        return operation()
      },
      clock: {
        setTimeout(_callback, delay) {
          const timer = { delay }
          timers.push(timer)
          return timer
        },
        clearTimeout() {},
      },
    })

    driver.observeAgent(host.agent)
    await new Promise<void>(resolve => setTimeout(resolve, 0))

    expect(fixture.calls).toHaveLength(0)
    expect(host.maintenanceCalls).toBe(0)
    expect(detachedCalls).toBe(0)
    expect(timers).toHaveLength(0)
    await driver.dispose()
  })

  it('keeps every ordinary inactive lifecycle transition at zero LoopX I/O', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const timers: unknown[] = []
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      clock: {
        setTimeout(_callback, delay) {
          const timer = { delay }
          timers.push(timer)
          return timer
        },
        clearTimeout() {},
      },
    })

    driver.observeAgent(host.agent)
    driver.onSessionStart(host.agent)
    const human = userMessage('ordinary work mentioning loopx only as prose')
    driver.onInboxInserted(host.agent, human)
    driver.onInboxClaimed(host.agent, human)
    publishEvent(driver, host, sessionEvent('user/message', human))
    publishEvent(driver, host, sessionEvent('turn/end', {
      turn: 0,
      reason: { kind: 'completed' },
    }))
    driver.onAgentStatus(host.agent, 'idle')
    driver.onAgentStatus(host.agent, 'idle')
    await new Promise<void>(resolve => setTimeout(resolve, 0))

    expect(fixture.calls).toHaveLength(0)
    expect(host.maintenanceCalls).toBe(0)
    expect(timers).toHaveLength(0)
    await driver.dispose()
  })

  it('activates only the exact Session with a user-explicit loopx invocation', async () => {
    const fixture = runnerFixture()
    const first = fakeAgent()
    const second = fakeAgent('another-session')
    const driver = makeDriver(fixture)

    driver.observeAgent(first.agent)
    driver.observeAgent(second.agent)
    expect(fixture.calls).toHaveLength(0)

    publishEvent(driver, first, userSkillInvocationEvent())
    await waitFor(() => first.nextTurn.length === 1)
    const activatedCalls = fixture.calls.length

    driver.onSessionStart(second.agent)
    driver.onAgentStatus(second.agent, 'idle')
    await new Promise<void>(resolve => setTimeout(resolve, 0))

    expect(second.nextTurn).toHaveLength(0)
    expect(fixture.calls).toHaveLength(activatedCalls)

    publishEvent(driver, second, userSkillInvocationEvent())
    await waitFor(() => second.nextTurn.length === 1)

    const resolvedThreadIds = fixture.calls
      .filter(call => call.includes('resolve-agent-thread'))
      .map(call => call[call.indexOf('--thread-id') + 1])
    expect(resolvedThreadIds).toEqual([sessionId, 'another-session'])
    expect(first.nextTurn).toHaveLength(1)
    expect(second.nextTurn).toHaveLength(1)
    expect(fixture.heartbeatCalls).toBe(2)
    await driver.dispose()
  })

  it('activates after a successful paired model skill call for loopx', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)

    driver.observeAgent(host.agent)
    publishEvent(driver, host, modelSkillCallEvent('call-loopx', '{"name":"loopx"}'))
    await new Promise<void>(resolve => setTimeout(resolve, 0))
    expect(fixture.calls).toHaveLength(0)

    publishEvent(driver, host, modelSkillResultEvent('call-loopx'))
    await waitFor(() => host.nextTurn.length === 1)

    expect(fixture.heartbeatCalls).toBe(1)
    await driver.dispose()
  })

  it('injects reviewed Reward Memory guidance into the admitted task body', async () => {
    const fixture = runnerFixture({
      rewardMemoryGuidance: 'Reuse the verified exact-readback sequence.',
    })
    const host = fakeAgent()
    const driver = makeDriver(fixture)

    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)

    const content = host.nextTurn[0]?.content
    expect(Array.isArray(content)).toBe(true)
    const text = (content?.[0] as { text?: string } | undefined)?.text ?? ''
    expect(text).toContain('LoopX private Reward Memory context:')
    expect(text).toContain('Reuse the verified exact-readback sequence.')
    expect(text).toContain('guidance_only')
    await driver.dispose()
  })

  it('ignores malformed, failed, unrelated, prose-only, and plugin-authored signals', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    driver.observeAgent(host.agent)

    const nonSignals = [
      userSkillInvocationEvent('another-skill'),
      userSkillInvocationEvent('loopx', 'catalog'),
      sessionEvent('user/message', userMessage('please run loopx')),
      sessionEvent('user/message', {
        ...userMessage('LoopX initialization finished.'),
        source: { kind: 'plugin', plugin: 'dsh-loopx-plugin/init-command' },
      }),
      sessionEvent('user/message', {
        ...userMessage('LoopX initialization finished.'),
        source: { kind: 'loopx-initialization', plugin: 'dsh-loopx-plugin/init-command' },
      }),
      sessionEvent('user/message', {
        ...userMessage('Continue through LoopX.'),
        source: { kind: 'plugin', plugin: 'dsh-loopx-plugin/driver' },
      }),
      modelSkillCallEvent('malformed', '{not-json'),
      modelSkillResultEvent('malformed'),
      modelSkillCallEvent('json-null', 'null'),
      modelSkillResultEvent('json-null'),
      modelSkillCallEvent('json-array', '[{"name":"loopx"}]'),
      modelSkillResultEvent('json-array'),
      modelSkillCallEvent('json-string', '"loopx"'),
      modelSkillResultEvent('json-string'),
      modelSkillCallEvent('other-skill', '{"name":"another-skill"}'),
      modelSkillResultEvent('other-skill'),
      modelSkillCallEvent('missing-result', '{"name":"loopx"}'),
      modelSkillCallEvent('errored', '{"name":"loopx"}'),
      modelSkillResultEvent('errored', {
        eventError: { name: 'SkillError', code: 'UNAVAILABLE' },
      }),
      modelSkillCallEvent('block-error', '{"name":"loopx"}'),
      modelSkillResultEvent('block-error', { isError: true }),
      modelSkillCallEvent('mismatched-block', '{"name":"loopx"}'),
      modelSkillResultEvent('mismatched-block', { blockCallId: 'different-call' }),
      modelSkillCallEvent('extra-block', '{"name":"loopx"}'),
      modelSkillResultEvent('extra-block', { extraBlock: true }),
      modelSkillCallEvent('wrong-source', '{"name":"loopx"}'),
      sessionEvent('tool/result', {
        turn: 0,
        step: 0,
        message: {
          id: randomUUID(),
          role: 'user',
          content: [{
            type: 'tool-result',
            toolCallId: 'wrong-source',
            content: [],
            isError: false,
          }],
          source: {
            kind: 'plugin',
            plugin: 'not-a-tool',
            callId: 'wrong-source',
          },
        },
      }),
      modelSkillCallEvent('duplicate-result', '{"name":"loopx"}'),
      modelSkillResultEvent('duplicate-result', {
        eventError: { name: 'SkillError', code: 'FAILED' },
      }),
      modelSkillResultEvent('duplicate-result'),
      modelSkillResultEvent('unmatched'),
      modelSkillCallEvent('shell-loopx', '{"name":"loopx"}', 'shell'),
      modelSkillResultEvent('shell-loopx'),
      modelSkillCallEvent('superseded', '{"name":"loopx"}'),
      modelSkillCallEvent('superseded', '{"name":"another-skill"}'),
      modelSkillResultEvent('superseded'),
    ]
    for (const event of nonSignals) publishEvent(driver, host, event)
    driver.onSessionStart(host.agent)
    driver.onAgentStatus(host.agent, 'idle')
    await new Promise<void>(resolve => setTimeout(resolve, 0))

    expect(fixture.calls).toHaveLength(0)
    expect(host.maintenanceCalls).toBe(0)
    expect(host.nextTurn).toHaveLength(0)
    await driver.dispose()
  })

  it('restores activation from exact paired Session history in a fresh Driver', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    host.appendEvent(modelSkillCallEvent('historical-call', '{"name":"loopx"}'))
    host.appendEvent(modelSkillResultEvent('historical-call'))

    const driver = makeDriver(fixture)
    expect(fixture.calls).toHaveLength(0)
    driver.observeAgent(host.agent)
    await waitFor(() => host.nextTurn.length === 1)

    expect(fixture.calls.some(call => call.includes('resolve-agent-thread'))).toBe(true)
    await driver.dispose()
  })

  it('leaves legacy or compacted history without invocation evidence inactive', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    host.appendEvent(sessionEvent('user/message', userMessage('legacy continuation')))
    host.appendEvent(sessionEvent('user/message', {
      ...userMessage('compacted summary mentioning loopx'),
      source: { kind: 'plugin', plugin: 'dsh-compaction-basic' },
    }))
    const driver = makeDriver(fixture)

    driver.observeAgent(host.agent)
    driver.onAgentStatus(host.agent, 'idle')
    await new Promise<void>(resolve => setTimeout(resolve, 0))

    expect(fixture.calls).toHaveLength(0)
    expect(host.nextTurn).toHaveLength(0)
    await driver.dispose()
  })

  it('stops an activated Session with a missing binding before quota, heartbeat, or timers', async () => {
    const fixture = runnerFixture({ bindingStatus: 'missing' })
    const host = fakeAgent()
    const timers: unknown[] = []
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      retryDelaysMs: [0, 0],
      clock: {
        setTimeout(_callback, delay) {
          const timer = { delay }
          timers.push(timer)
          return timer
        },
        clearTimeout() {},
      },
    })

    observeActivated(driver, host)
    await waitFor(() => fixture.calls.some(call => call.includes('resolve-agent-thread')))
    await new Promise<void>(resolve => setTimeout(resolve, 0))

    expect(fixture.quotaCalls).toHaveLength(0)
    expect(fixture.heartbeatCalls).toBe(0)
    expect(timers).toHaveLength(0)
    expect(host.nextTurn).toHaveLength(0)
    await driver.dispose()
  })

  it('contains a synchronous detached-runner failure without retrying', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const warnings: string[] = []
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      runDetached: () => { throw new Error('detached runner unavailable') },
      warn: message => warnings.push(message),
    })

    host.appendEvent(userSkillInvocationEvent())
    expect(() => driver.observeAgent(host.agent)).not.toThrow()
    expect(warnings).toHaveLength(1)
    expect(fixture.calls).toHaveLength(0)
    await driver.dispose()
  })

  it('stays paused after any terminal Agent error until new human input', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)

    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)
    driver.onAgentError(host.agent)
    driver.onAgentStatus(host.agent, 'idle')
    await new Promise<void>(resolve => setTimeout(resolve, 0))

    expect(host.nextTurn).toHaveLength(0)
    await driver.dispose()
  })

  it('stays paused after max-token termination outside an automatic reservation', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    host.setStatus('running')
    const driver = makeDriver(fixture)

    observeActivated(driver, host)
    driver.onSessionEvent(host.agent, {
      type: 'turn/end',
      data: { reason: { kind: 'max-tokens' } },
    } as unknown as Parameters<LoopXContinuationDriver['onSessionEvent']>[1])
    host.setStatus('idle')
    driver.onAgentStatus(host.agent, 'idle')
    await new Promise<void>(resolve => setTimeout(resolve, 0))

    expect(fixture.calls).toHaveLength(0)
    expect(host.nextTurn).toHaveLength(0)
    await driver.dispose()
  })

  it('refuses a Session whose header id does not match the live Agent', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    host.replaceSession('different-session-header')
    const driver = makeDriver(fixture)

    observeActivated(driver, host)
    await new Promise<void>(resolve => setTimeout(resolve, 0))

    expect(fixture.calls).toHaveLength(0)
    expect(host.nextTurn).toHaveLength(0)
    await driver.dispose()
  })

  it.each([
    ['POSIX', 'LOOPX_TURN=turn-stable\nContinue through the authoritative LoopX workflow.'],
    ['PowerShell', '$LOOPX_TURN = "turn-stable"\nContinue through the authoritative LoopX workflow.'],
  ])('queues one authoritative %s task and revalidates the same receipt before entry', async (_shell, heartbeatTaskBody) => {
    const fixture = runnerFixture({ heartbeatTaskBody })
    const host = fakeAgent()
    const driver = makeDriver(fixture)

    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)
    const queued = host.nextTurn[0] as UserMessage
    expect(queued.content).toEqual([
      {
        type: 'text',
        text: heartbeatTaskBody,
      },
    ])
    expect(queued.source).toEqual({
      kind: 'loopx-continuation',
      schemaVersion: 'loopx_dsh_continuation_v0',
      goalId,
      agentId,
      turnInstanceId: 'turn-stable',
    })
    expect(fixture.quotaCalls).toHaveLength(1)
    expect(fixture.heartbeatArgv).toHaveLength(1)
    const heartbeatCall = fixture.heartbeatArgv[0] as string[]
    expect(heartbeatCall[heartbeatCall.indexOf('--turn-instance-id') + 1])
      .toBe('turn-stable')

    host.nextTurn.splice(0, 1)
    host.setStatus('running')
    driver.onInboxClaimed(host.agent, queued)
    const decision = await driver.onPreStep(
      host.agent,
      [queued],
      new AbortController().signal,
      async () => ({ kind: 'enter', messages: [queued] }),
    )

    expect(decision.kind).toBe('enter')
    expect(fixture.quotaCalls).toHaveLength(3)
    for (const call of fixture.quotaCalls) {
      const index = call.indexOf('--turn-instance-id')
      expect(call[index + 1]).toBe('turn-stable')
    }
    await driver.dispose()
  })

  it('rejects the reserved message when its typed source identity is replaced', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)
    const queued = host.nextTurn.shift() as UserMessage
    const tampered = {
      ...queued,
      source: { kind: 'user' as const },
    } satisfies UserMessage
    host.setStatus('running')
    driver.onInboxClaimed(host.agent, tampered)

    let downstreamCalls = 0
    const decision = await driver.onPreStep(
      host.agent,
      [tampered],
      new AbortController().signal,
      async () => {
        downstreamCalls += 1
        return { kind: 'enter', messages: [tampered] }
      },
    )

    expect(decision).toEqual({ kind: 'reject' })
    expect(downstreamCalls).toBe(0)
    expect(fixture.quotaCalls).toHaveLength(1)
    await driver.dispose()
  })

  it('rejects the claimed message when its typed source has an extra field', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)
    const queued = host.nextTurn.shift() as UserMessage
    const tampered = {
      ...queued,
      source: {
        ...queued.source,
        unexpected: 'not-part-of-the-continuation-schema',
      } as unknown as UserMessage['source'],
    } satisfies UserMessage
    host.setStatus('running')
    driver.onInboxClaimed(host.agent, tampered)

    let downstreamCalls = 0
    const decision = await driver.onPreStep(
      host.agent,
      [tampered],
      new AbortController().signal,
      async () => {
        downstreamCalls += 1
        return { kind: 'enter', messages: [tampered] }
      },
    )

    expect(decision).toEqual({ kind: 'reject' })
    expect(downstreamCalls).toBe(0)
    expect(fixture.quotaCalls).toHaveLength(1)
    await driver.dispose()
  })

  it('rejects the claimed message when its canonical content drifts', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)
    const queued = host.nextTurn.shift() as UserMessage
    host.setStatus('running')
    driver.onInboxClaimed(host.agent, queued)
    const tampered = {
      ...queued,
      content: [{ type: 'text' as const, text: 'replacement content' }],
    } satisfies UserMessage

    const decision = await driver.onPreStep(
      host.agent,
      [tampered],
      new AbortController().signal,
      async () => ({ kind: 'enter', messages: [tampered] }),
    )

    expect(decision).toEqual({ kind: 'reject' })
    expect(fixture.quotaCalls).toHaveLength(1)
    await driver.dispose()
  })

  it.each(['rewritten', 'missing', 'malformed replacement', 'relabeled as user'] as const)(
    'holds a %s downstream continuation until an explicit evaluation',
    async change => {
      const fixture = runnerFixture()
      const host = fakeAgent()
      const driver = makeDriver(fixture)
      try {
        observeActivated(driver, host)
        await waitFor(() => host.nextTurn.length === 1)
        const queued = host.nextTurn.shift() as UserMessage
        host.setStatus('running')
        driver.onInboxClaimed(host.agent, queued)
        const context = {
          ...userMessage('derived host context'),
          source: { kind: 'runtime-context' },
        } as unknown as UserMessage
        const rewritten: UserMessage = {
          ...queued,
          content: [{ type: 'text', text: 'downstream replacement' }],
        }
        const alteredSource = { ...queued.source, unexpected: true }
        const malformed: UserMessage = {
          ...queued,
          id: randomUUID() as UserMessage['id'],
          source: alteredSource,
        }
        const cases = {
          rewritten: [rewritten],
          missing: [context],
          'malformed replacement': [malformed, context],
          'relabeled as user': [{ ...queued, source: { kind: 'user' as const } }],
        }

        const decision = await driver.onPreStep(
          host.agent, [queued], new AbortController().signal,
          async () => ({ kind: 'enter', messages: cases[change] }),
        )
        expect(decision).toEqual({ kind: 'reject' })

        host.setStatus('idle')
        driver.onAgentStatus(host.agent, 'idle')
        await new Promise<void>(resolve => setTimeout(resolve, 0))
        expect(host.nextStep).toEqual([])
        expect(host.nextTurn).toEqual([])
        expect(fixture.quotaCalls).toHaveLength(2)

        await expect(driver.evaluateActivatedSession({ agent: host.agent, session: host.agent.session }))
          .resolves.toEqual({ kind: 'applied' })
        expect(host.nextTurn).toHaveLength(1)
        expect(fixture.quotaCalls).toHaveLength(3)
      } finally {
        await driver.dispose()
      }
    },
  )

  it('keeps a queued user message in the inbox when a downstream listener returns it too', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    try {
      observeActivated(driver, host)
      await waitFor(() => host.nextTurn.length === 1)
      const queued = host.nextTurn.shift() as UserMessage
      host.setStatus('running')
      driver.onInboxClaimed(host.agent, queued)
      const human = userMessage('independent input during admission')

      const decision = await driver.onPreStep(
        host.agent, [queued], new AbortController().signal, async () => {
          host.agent.followup(human)
          driver.onInboxInserted(host.agent, human)
          return { kind: 'enter', messages: [human] }
        },
      )
      expect(decision).toEqual({ kind: 'reject' })
      expect(host.nextTurn).toEqual([human])
      expect(host.nextStep).toEqual([])

      // The host consumes the queued human input before its next idle event.
      expect(host.nextTurn.shift()).toBe(human)
      host.setStatus('idle')
      driver.onAgentStatus(host.agent, 'idle')
      await waitFor(() => host.nextTurn.length === 1)
      expect(host.nextTurn[0]?.source.kind).toBe('loopx-continuation')
      expect(fixture.quotaCalls).toHaveLength(3)
    } finally {
      await driver.dispose()
    }
  })

  it('rejects when a downstream pre-step adds a typed source field', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)
    const queued = host.nextTurn.shift() as UserMessage
    host.setStatus('running')
    driver.onInboxClaimed(host.agent, queued)
    const tampered = {
      ...queued,
      source: {
        ...queued.source,
        unexpected: 'not-part-of-the-continuation-schema',
      } as unknown as UserMessage['source'],
    } satisfies UserMessage

    const decision = await driver.onPreStep(
      host.agent,
      [queued],
      new AbortController().signal,
      async () => ({ kind: 'enter', messages: [tampered] }),
    )

    expect(decision).toEqual({ kind: 'reject' })
    expect(fixture.quotaCalls).toHaveLength(2)
    await driver.dispose()
  })

  it('fails closed when quota returns a different heartbeat receipt id', async () => {
    const fixture = runnerFixture({ quotaTurnInstanceId: 'turn-other' })
    const host = fakeAgent()
    const warnings: string[] = []
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      makeTurnInstanceId: () => 'turn-stable',
      retryDelaysMs: [0, 0],
      warn: message => warnings.push(message),
    })
    observeActivated(driver, host)
    await waitFor(() => warnings.length === 1)

    expect(host.nextTurn).toHaveLength(0)
    expect(fixture.heartbeatCalls).toBe(0)
    expect(warnings[0]).toContain('invalid_schema')
    await driver.dispose()
  })

  it('fails closed when heartbeat returns a different exact id', async () => {
    const fixture = runnerFixture({ heartbeatTurnInstanceId: 'turn-other' })
    const host = fakeAgent()
    const warnings: string[] = []
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      makeTurnInstanceId: () => 'turn-stable',
      retryDelaysMs: [0, 0],
      warn: message => warnings.push(message),
    })
    observeActivated(driver, host)
    await waitFor(() => warnings.length === 1)

    expect(host.nextTurn).toHaveLength(0)
    expect(fixture.heartbeatCalls).toBe(1)
    expect(warnings[0]).toContain('invalid_schema')
    await driver.dispose()
  })

  it('projects a schema or identity admission failure with one safe category', async () => {
    const fixture = runnerFixture({ quotaTurnInstanceId: 'turn-other' })
    const host = fakeAgent()
    const warnings: string[] = []
    const changes: Array<Agent['session']> = []
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      makeTurnInstanceId: () => 'turn-stable',
      retryDelaysMs: [0, 0],
      warn: message => warnings.push(message),
      onAdmissionChange: session => { changes.push(session) },
    })
    observeActivated(driver, host)
    await waitFor(() => warnings.length === 1)

    expect(host.nextTurn).toHaveLength(0)
    expect(driver.readContinuationAdmission({
      agent: host.agent,
      session: host.agent.session,
    })).toEqual({ state: 'failed', category: 'invalid_response' })
    expect(changes).toEqual([host.agent.session])
    // The projection carries no raw warning text or command output.
    expect(JSON.stringify(driver.readContinuationAdmission({
      agent: host.agent,
      session: host.agent.session,
    }))).not.toContain('invalid_schema')
    await driver.dispose()
  })

  it('projects typed authority rejections and untyped CLI exits distinctly', async () => {
    const typed = runnerFixture({ typedQuotaFailure: true })
    const typedHost = fakeAgent()
    const typedDriver = new LoopXContinuationDriver({
      runner: typed.runner,
      isLiveAgent: () => true,
      retryDelaysMs: [0, 0],
    })
    observeActivated(typedDriver, typedHost)
    await waitFor(() => typed.quotaCalls.length === 1)
    expect(typedDriver.readContinuationAdmission({
      agent: typedHost.agent,
      session: typedHost.agent.session,
    })).toEqual({ state: 'failed', category: 'admission_rejected' })
    await typedDriver.dispose()

    const unreachable = runnerFixture({ quotaExitFailures: 3 })
    const unreachableHost = fakeAgent()
    const unreachableDriver = new LoopXContinuationDriver({
      runner: unreachable.runner,
      isLiveAgent: () => true,
      retryDelaysMs: [0, 0],
    })
    observeActivated(unreachableDriver, unreachableHost)
    await waitFor(() => unreachable.quotaCalls.length === 3)
    expect(unreachableDriver.readContinuationAdmission({
      agent: unreachableHost.agent,
      session: unreachableHost.agent.session,
    })).toEqual({ state: 'failed', category: 'cli_unreachable' })
    await unreachableDriver.dispose()
  })

  it('keeps the projection clear for admitted, waiting, and exact-session reads', async () => {
    const fixture = runnerFixture({ shouldRun: false, waitMinutes: 3 })
    const host = fakeAgent()
    const changes: Array<Agent['session']> = []
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      retryDelaysMs: [0, 0],
      onAdmissionChange: session => { changes.push(session) },
    })
    observeActivated(driver, host)
    await waitFor(() => fixture.quotaCalls.length === 1)

    // A scheduler wait is an admitted outcome, not a failure.
    expect(driver.readContinuationAdmission({
      agent: host.agent,
      session: host.agent.session,
    })).toEqual({ state: 'clear' })
    expect(changes).toEqual([])

    const admittedHost = fakeAgent('another-session')
    const admittedDriver = makeDriver(runnerFixture())
    observeActivated(admittedDriver, admittedHost)
    await waitFor(() => admittedHost.nextTurn.length === 1)
    expect(admittedDriver.readContinuationAdmission({
      agent: admittedHost.agent,
      session: admittedHost.agent.session,
    })).toEqual({ state: 'clear' })
    await admittedDriver.dispose()

    // A stale capture for a replaced Session never reports the latch.
    host.replaceSession(undefined, [])
    expect(driver.readContinuationAdmission({
      agent: host.agent,
      session: host.agent.session,
    })).toEqual({ state: 'clear' })
    await driver.dispose()
  })

  it('clears the projected failure when admission succeeds again or Pause takes over', async () => {
    const inner = runnerFixture()
    let quotaFailures = 3
    const runner: FileRunner = async (file, args, options) => {
      if (args.includes('should-run') && quotaFailures > 0) {
        quotaFailures -= 1
        return { exitCode: 1, stdout: '', stderr: 'transient diagnostic' }
      }
      return inner.runner(file, args, options)
    }
    const host = fakeAgent()
    const changes: number[] = []
    const driver = new LoopXContinuationDriver({
      runner,
      isLiveAgent: () => true,
      makeTurnInstanceId: () => 'turn-stable',
      retryDelaysMs: [0, 0],
      onAdmissionChange: () => { changes.push(changes.length) },
    })
    observeActivated(driver, host)
    await waitFor(() => driver.readContinuationAdmission({
      agent: host.agent, session: host.agent.session,
    }).state === 'failed')

    // The GoalBar Start bridge re-runs admission; success clears the latch.
    const receipt = await driver.evaluateActivatedSession({
      agent: host.agent,
      session: host.agent.session,
    })
    expect(receipt).toEqual({ kind: 'applied' })
    await waitFor(() => host.nextTurn.length === 1)
    expect(driver.readContinuationAdmission({
      agent: host.agent, session: host.agent.session,
    })).toEqual({ state: 'clear' })
    expect(changes).toHaveLength(2)
    await driver.dispose()

    // Pause retires the latch together with the Goal's continuation authority.
    const pausedFixture = runnerFixture({ heartbeatTurnInstanceId: 'turn-other' })
    const pausedHost = fakeAgent()
    const pausedChanges: number[] = []
    const pausedDriver = new LoopXContinuationDriver({
      runner: pausedFixture.runner,
      isLiveAgent: () => true,
      makeTurnInstanceId: () => 'turn-stable',
      retryDelaysMs: [0, 0],
      onAdmissionChange: () => { pausedChanges.push(pausedChanges.length) },
    })
    observeActivated(pausedDriver, pausedHost)
    await waitFor(() => pausedDriver.readContinuationAdmission({
      agent: pausedHost.agent, session: pausedHost.agent.session,
    }).state === 'failed')
    const pauseReceipt = await pausedDriver.cancelQueued({
      agent: pausedHost.agent,
      session: pausedHost.agent.session,
    })
    expect(pauseReceipt).toEqual({ kind: 'applied' })
    expect(pausedDriver.readContinuationAdmission({
      agent: pausedHost.agent, session: pausedHost.agent.session,
    })).toEqual({ state: 'clear' })
    expect(pausedChanges).toHaveLength(2)
    await pausedDriver.dispose()
  })

  it.each([
    ['wrong POSIX id', 'LOOPX_TURN=turn-other'],
    ['wrong PowerShell id', '$LOOPX_TURN = "turn-other"'],
    ['conflicting POSIX ids', 'LOOPX_TURN=turn-stable\nLOOPX_TURN=turn-other'],
    ['duplicate PowerShell ids', '$LOOPX_TURN = "turn-stable"\n$LOOPX_TURN = "turn-stable"'],
    ['mixed shell duplicates', 'LOOPX_TURN=turn-stable\n$LOOPX_TURN = "turn-stable"'],
  ])('fails closed when the canonical task body contains %s', async (_case, heartbeatTaskBody) => {
    const fixture = runnerFixture({ heartbeatTaskBody })
    const host = fakeAgent()
    const warnings: string[] = []
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      makeTurnInstanceId: () => 'turn-stable',
      retryDelaysMs: [0, 0],
      warn: message => warnings.push(message),
    })
    observeActivated(driver, host)
    await waitFor(() => warnings.length === 1)

    expect(host.nextTurn).toHaveLength(0)
    expect(fixture.heartbeatCalls).toBe(1)
    expect(warnings[0]).toContain('invalid_schema')
    await driver.dispose()
  })

  it('removes its queued reservation when human input arrives', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)

    const human = userMessage('human priority')
    host.nextTurn.push(human)
    driver.onInboxInserted(host.agent, human)

    expect(host.nextTurn).toEqual([human])
    await driver.dispose()
  })

  it('cancels a claimed automatic step when the Driver is disposed', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)
    const queued = host.nextTurn.shift() as UserMessage
    host.setStatus('running')
    driver.onInboxClaimed(host.agent, queued)

    await driver.dispose()

    expect(host.cancelCalls).toBe(1)
  })

  it('cancels a claimed automatic step when the Driver is disposed for removal', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)
    const queued = host.nextTurn.shift() as UserMessage
    host.setStatus('running')
    driver.onInboxClaimed(host.agent, queued)

    await driver.dispose('removal')

    expect(host.cancelCalls).toBe(1)
  })

  it('keeps a claimed automatic step running across replacement disposal', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)
    const queued = host.nextTurn.shift() as UserMessage
    host.setStatus('running')
    driver.onInboxClaimed(host.agent, queued)

    await driver.dispose('replacement')

    expect(host.cancelCalls).toBe(0)
  })

  it('retires queued future work but no running turn on replacement disposal', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)
    const callsAtReplacement = fixture.calls.length

    await driver.dispose('replacement')

    expect(host.nextTurn).toHaveLength(0)
    expect(host.cancelCalls).toBe(0)
    host.setStatus('idle')
    driver.onAgentStatus(host.agent, 'idle')
    await new Promise<void>(resolve => setTimeout(resolve, 0))
    expect(fixture.calls).toHaveLength(callsAtReplacement)
  })

  it('drains an admitted turn across replacement and admits freshly only after it settles', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const first = makeDriver(fixture)
    observeActivated(first, host)
    await waitFor(() => host.nextTurn.length === 1)
    const admitted = host.nextTurn.shift() as UserMessage
    expect((admitted.source as { turnInstanceId?: string }).turnInstanceId)
      .toBe('turn-stable')
    host.setStatus('running')
    first.onInboxClaimed(host.agent, admitted)
    publishEvent(first, host, sessionEvent('user/message', admitted))
    const callsBeforeReplacement = fixture.calls.length

    await first.dispose('replacement')
    expect(host.cancelCalls).toBe(0)

    const second = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      makeTurnInstanceId: () => 'turn-fresh',
      retryDelaysMs: [0, 0],
    })
    second.observeAgent(host.agent)
    await new Promise<void>(resolve => setTimeout(resolve, 0))

    // The replacement Driver admits nothing while the drained Turn runs.
    expect(host.nextTurn).toHaveLength(0)
    expect(fixture.calls).toHaveLength(callsBeforeReplacement)

    publishEvent(second, host, turnEndEvent(1))
    host.setStatus('idle')
    second.onAgentStatus(host.agent, 'idle')
    await waitFor(() => host.nextTurn.length === 1)
    const followup = host.nextTurn.shift() as UserMessage
    expect((followup.source as { turnInstanceId?: string }).turnInstanceId)
      .toBe('turn-fresh')
    await second.dispose()
    expect(host.cancelCalls).toBe(0)
  })

  it('retries an untyped CLI exit twice with one stable idempotency key', async () => {
    const fixture = runnerFixture({ quotaExitFailures: 2 })
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)

    expect(fixture.quotaCalls).toHaveLength(3)
    expect(new Set(fixture.quotaCalls.map(call => (
      call[call.indexOf('--turn-instance-id') + 1]
    )))).toEqual(new Set(['turn-stable']))
    await driver.dispose()
  })

  it('retries the canonical heartbeat read with the admitted receipt id', async () => {
    const fixture = runnerFixture({ heartbeatExitFailures: 2 })
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)

    expect(fixture.heartbeatArgv).toHaveLength(3)
    expect(new Set(fixture.heartbeatArgv.map(call => (
      call[call.indexOf('--turn-instance-id') + 1]
    )))).toEqual(new Set(['turn-stable']))
    expect(host.nextTurn[0]?.source).toMatchObject({ turnInstanceId: 'turn-stable' })
    await driver.dispose()
  })

  it('stops after the finite CLI attempt budget instead of scheduling an error loop', async () => {
    const fixture = runnerFixture({ quotaExitFailures: 3 })
    const host = fakeAgent()
    const timers: unknown[] = []
    const warnings: string[] = []
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      retryDelaysMs: [0, 0],
      warn: message => warnings.push(message),
      clock: {
        setTimeout(_callback, delay) {
          const value = { delay }
          timers.push(value)
          return value
        },
        clearTimeout() {},
      },
    })

    observeActivated(driver, host)
    await waitFor(() => fixture.quotaCalls.length === 3 && warnings.length === 1)

    expect(host.nextTurn).toHaveLength(0)
    expect(fixture.heartbeatCalls).toBe(0)
    expect(timers).toHaveLength(0)
    await driver.dispose()
  })

  it('recognizes a typed LoopX quota failure without retrying or scheduling', async () => {
    const fixture = runnerFixture({ typedQuotaFailure: true })
    const host = fakeAgent()
    const timers: unknown[] = []
    const warnings: string[] = []
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      retryDelaysMs: [0, 0],
      warn: message => warnings.push(message),
      clock: {
        setTimeout(_callback, delay) {
          const value = { delay }
          timers.push(value)
          return value
        },
        clearTimeout() {},
      },
    })

    observeActivated(driver, host)
    await waitFor(() => warnings.length === 1)

    expect(fixture.quotaCalls).toHaveLength(1)
    expect(warnings[0]).toContain('typed_failure')
    expect(host.nextTurn).toHaveLength(0)
    expect(timers).toHaveLength(0)
    await driver.dispose()
  })

  it('uses LoopX scheduler wait without invoking the heartbeat command', async () => {
    const fixture = runnerFixture({ shouldRun: false, waitMinutes: 7 })
    const host = fakeAgent()
    const timers: Array<{ delay: number; cleared: boolean }> = []
    const clock: DriverClock = {
      setTimeout(_callback, delay) {
        const timer = { delay, cleared: false }
        timers.push(timer)
        return timer
      },
      clearTimeout(handle) {
        (handle as { cleared: boolean }).cleared = true
      },
    }
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      makeTurnInstanceId: () => 'turn-stable',
      retryDelaysMs: [0, 0],
      clock,
    })
    observeActivated(driver, host)
    await waitFor(() => timers.length === 1)

    expect(timers[0]?.delay).toBe(7 * 60_000)
    expect(fixture.heartbeatCalls).toBe(0)
    expect(host.nextTurn).toHaveLength(0)
    await driver.dispose()
  })

  it('schedules detailed waits, stops at the limit and resets on a new token', async () => {
    const hint = schedulerWaitHint()
    let runnable = false
    const fixture = runnerFixture({
      shouldRun: () => runnable,
      schedulerHint: () => hint,
    })
    const clock = schedulerClock()
    const host = fakeAgent()
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      makeTurnInstanceId: () => 'turn-stable',
      clock,
    })
    try {
      observeActivated(driver, host)
      for (const [index, minutes] of [30, 60, 120].entries()) {
        await waitFor(() => clock.timers.length === index + 1)
        expect(clock.timers[index]?.delay).toBe(minutes * 60_000)
        expect(fixture.quotaCalls[index]).toContain('--include-detail')
        expect(fixture.quotaCalls[index]).toContain('scheduler')
        clock.timers[index]?.callback()
      }
      await waitFor(() => fixture.quotaCalls.length === 4)
      expect(clock.timers).toHaveLength(3)
      expect(fixture.heartbeatCalls).toBe(0)
      expect(host.nextTurn).toHaveLength(0)

      hint.reset_policy.reset_token = 'changed-token'
      await driver.evaluateActivatedSession({ agent: host.agent, session: host.agent.session })
      expect(clock.timers).toHaveLength(4)
      expect(clock.timers[3]?.delay).toBe(30 * 60_000)

      runnable = true
      clock.timers[3]?.callback()
      await waitFor(() => host.nextTurn.length === 1)
      expect(fixture.heartbeatCalls).toBe(1)
      expect(clock.timers).toHaveLength(4)
    } finally {
      await driver.dispose()
    }
  })

  it.each([45, 7_200])('bounds a detailed wait by recheck_after_seconds=%s', async seconds => {
    const hint = schedulerWaitHint()
    const fixture = runnerFixture({
      shouldRun: false,
      schedulerHint: () => ({
        ...hint,
        cold_path_detail: {
          ...hint.cold_path_detail,
          local_scheduler: {
            ...hint.cold_path_detail.local_scheduler,
            recheck_after_seconds: seconds,
          },
        },
      }),
    })
    const clock = schedulerClock()
    const host = fakeAgent()
    const driver = new LoopXContinuationDriver({ runner: fixture.runner, isLiveAgent: () => true, clock })
    try {
      observeActivated(driver, host)
      await waitFor(() => clock.timers.length === 1)
      expect(clock.timers[0]?.delay).toBe(Math.min(30 * 60_000, seconds * 1_000))
      expect(fixture.heartbeatCalls).toBe(0)
    } finally {
      await driver.dispose()
    }
  })

  it.each([0, 1, null])('uses the compact unchanged-poll limit %s instead of the detail copy', async limit => {
    const hint = schedulerWaitHint()
    const fixture = runnerFixture({
      shouldRun: false,
      schedulerHint: () => ({
        ...hint,
        unchanged_poll: { ...hint.unchanged_poll, limits: { local_scheduler: limit } },
      }),
    })
    const clock = schedulerClock()
    const host = fakeAgent()
    const driver = new LoopXContinuationDriver({ runner: fixture.runner, isLiveAgent: () => true, clock })
    try {
      observeActivated(driver, host)
      const intervals = limit === null ? [30, 60, 120, 120] : [30].slice(0, limit)
      for (const [index, minutes] of intervals.entries()) {
        await waitFor(() => clock.timers.length === index + 1)
        expect(clock.timers[index]?.delay).toBe(minutes * 60_000)
        clock.timers[index]?.callback()
      }
      await waitFor(() => fixture.quotaCalls.length === intervals.length + 1)
      expect(clock.timers).toHaveLength(intervals.length + (limit === null ? 1 : 0))
    } finally {
      await driver.dispose()
    }
  })

  it('honors the typed unchanged-poll limit without an indefinite timer loop', async () => {
    const fixture = runnerFixture({
      shouldRun: false,
      waitMinutes: 2,
      unchangedPollLimit: 1,
    })
    const host = fakeAgent()
    const timers: Array<{ callback: () => void; delay: number }> = []
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      retryDelaysMs: [0, 0],
      clock: {
        setTimeout(callback, delay) {
          const timer = { callback, delay }
          timers.push(timer)
          return timer
        },
        clearTimeout() {},
      },
    })

    observeActivated(driver, host)
    await waitFor(() => timers.length === 1)
    timers[0]?.callback()
    await waitFor(() => fixture.quotaCalls.length === 2)

    expect(timers).toHaveLength(1)
    expect(host.nextTurn).toHaveLength(0)
    await driver.dispose()
  })

  it('does not poll when LoopX omits a typed local scheduler plan', async () => {
    const fixture = runnerFixture({ shouldRun: false, schedulerMode: 'missing' })
    const host = fakeAgent()
    const timers: unknown[] = []
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      clock: {
        setTimeout(_callback, delay) {
          const value = { delay }
          timers.push(value)
          return value
        },
        clearTimeout() {},
      },
    })

    observeActivated(driver, host)
    await waitFor(() => fixture.quotaCalls.length === 1)
    await new Promise<void>(resolve => setTimeout(resolve, 0))

    expect(timers).toHaveLength(0)
    await driver.dispose()
  })

  it('rejects a quota response for another Agent without scheduling it', async () => {
    const fixture = runnerFixture({
      shouldRun: false,
      quotaAgentId: 'another-agent',
    })
    const host = fakeAgent()
    const timers: unknown[] = []
    const warnings: string[] = []
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      warn: message => warnings.push(message),
      clock: {
        setTimeout(_callback, delay) {
          const value = { delay }
          timers.push(value)
          return value
        },
        clearTimeout() {},
      },
    })

    observeActivated(driver, host)
    await waitFor(() => warnings.length === 1)

    expect(host.nextTurn).toHaveLength(0)
    expect(timers).toHaveLength(0)
    await driver.dispose()
  })

  it('rejects an automatic message from a mixed human batch', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)
    const queued = host.nextTurn.shift() as UserMessage
    host.setStatus('running')
    driver.onInboxClaimed(host.agent, queued)
    const human = userMessage('new human work')
    driver.onInboxInserted(host.agent, human)

    const decision = await driver.onPreStep(
      host.agent,
      [queued, human],
      new AbortController().signal,
      async () => ({ kind: 'enter', messages: [queued, human] }),
    )

    expect(decision).toEqual({ kind: 'reject' })
    expect(host.nextStep).toEqual([human])
    expect(fixture.quotaCalls).toHaveLength(1)
    await driver.dispose()
  })

  it.each([
    ['runtime-context', 'before'], ['runtime-context', 'after'],
    ['time-context', 'before'], ['time-context', 'after'],
    ['skill-catalog', 'before'], ['skill-catalog', 'after'],
  ])('admits a continuation with %s context added %s downstream listeners', async (kind, phase) => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)
    const queued = host.nextTurn.shift() as UserMessage
    host.setStatus('running')
    driver.onInboxClaimed(host.agent, queued)
    const context = {
      ...userMessage('host context'),
      source: kind === 'skill-catalog'
        ? { kind, form: 'catalog', entries: [] }
        : { kind, form: 'snapshot', sections: [] },
    } as UserMessage
    const messages = [queued, context]
    const downstream = vi.fn(async () => ({ kind: 'enter' as const, messages }))

    const decision = await driver.onPreStep(
      host.agent, phase === 'before' ? messages : [queued],
      new AbortController().signal, downstream,
    )

    expect(downstream).toHaveBeenCalledOnce()
    expect(decision).toEqual({ kind: 'enter', messages })
    expect(host.nextStep).toEqual([])
    expect(fixture.quotaCalls).toHaveLength(3)
    await driver.dispose()
  })

  it.each(['before', 'after'])('preserves independent user input %s downstream listeners with host context', async phase => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)
    const queued = host.nextTurn.shift() as UserMessage
    host.setStatus('running')
    driver.onInboxClaimed(host.agent, queued)
    const context = { ...userMessage('host snapshot'), source: { kind: 'runtime-context' } } as unknown as UserMessage
    const human = userMessage('new human work')
    const messages = [queued, context, human]

    const decision = await driver.onPreStep(
      host.agent, phase === 'before' ? messages : [queued, context],
      new AbortController().signal,
      async () => ({ kind: 'enter', messages, startsRequestSeries: true }),
    )

    expect(decision).toEqual(phase === 'before'
      ? { kind: 'reject' }
      : { kind: 'enter', messages: [context, human], startsRequestSeries: true })
    expect(host.nextStep).toEqual(phase === 'before' ? [context, human] : [])
    expect(fixture.quotaCalls).toHaveLength(phase === 'before' ? 1 : 2)
    await driver.dispose()
  })

  it.each(['before', 'after'])('does not treat a plugin snapshot as host context %s downstream listeners', async phase => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)
    const queued = host.nextTurn.shift() as UserMessage
    host.setStatus('running')
    driver.onInboxClaimed(host.agent, queued)
    const plugin = { ...userMessage('competing plugin work'),
      source: { kind: 'plugin-snapshot', form: 'snapshot', sections: [] },
    } as unknown as UserMessage

    const decision = await driver.onPreStep(
      host.agent, phase === 'before' ? [queued, plugin] : [queued],
      new AbortController().signal,
      async () => ({ kind: 'enter', messages: [queued, plugin] }),
    )

    expect(decision).toEqual({ kind: 'reject' })
    expect(host.nextStep).toEqual([plugin])
    await driver.dispose()
  })

  it('pauses automatic continuation when a downstream pre-step fails', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)
    const queued = host.nextTurn.shift() as UserMessage
    host.setStatus('running')
    driver.onInboxClaimed(host.agent, queued)

    await expect(driver.onPreStep(
      host.agent,
      [queued],
      new AbortController().signal,
      async () => { throw new Error('downstream failed') },
    )).rejects.toThrow('downstream failed')

    host.setStatus('idle')
    driver.onAgentStatus(host.agent, 'idle')
    await new Promise<void>(resolve => setTimeout(resolve, 0))
    expect(host.nextTurn).toHaveLength(0)
    await driver.dispose()
  })

  it('fences automatic work across a UI command lifecycle', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)

    driver.onSessionEvent(host.agent, {
      type: 'command/run',
      data: { commandId: 'command-1', name: 'loopx-init', source: { kind: 'user' } },
    } as unknown as Parameters<LoopXContinuationDriver['onSessionEvent']>[1])
    expect(host.nextTurn).toHaveLength(0)

    driver.onSessionEvent(host.agent, {
      type: 'command/done',
      data: { commandId: 'command-1', kind: 'success' },
    } as unknown as Parameters<LoopXContinuationDriver['onSessionEvent']>[1])
    await waitFor(() => host.nextTurn.length === 1)
    await driver.dispose()
  })

  it('does not queue after the exact live Agent is replaced during a CLI await', async () => {
    const base = runnerFixture()
    let live = true
    let resolveStarted = false
    let releaseResolve: (() => void) | undefined
    const gate = new Promise<void>(resolve => { releaseResolve = resolve })
    const controlled: RunnerFixture = {
      ...base,
      runner: async (file, args, options) => {
        if (args.includes('resolve-agent-thread')) {
          resolveStarted = true
          await gate
        }
        return base.runner(file, args, options)
      },
    }
    const host = fakeAgent()
    const timers: unknown[] = []
    const driver = new LoopXContinuationDriver({
      runner: controlled.runner,
      isLiveAgent: () => live,
      retryDelaysMs: [0, 0],
      clock: {
        setTimeout(_callback, delay) {
          const value = { delay }
          timers.push(value)
          return value
        },
        clearTimeout() {},
      },
    })
    observeActivated(driver, host)
    await waitFor(() => resolveStarted)
    live = false
    releaseResolve?.()
    await waitFor(() => base.calls.some(args => args.includes('resolve-agent-thread')))

    expect(host.nextTurn).toHaveLength(0)
    expect(controlled.quotaCalls).toHaveLength(0)
    expect(timers).toHaveLength(0)
    await driver.dispose()
  })

  it('does not queue across replacement of the exact Session object', async () => {
    const base = runnerFixture()
    let resolveStarted = false
    let releaseResolve: (() => void) | undefined
    const gate = new Promise<void>(resolve => { releaseResolve = resolve })
    const controlled: RunnerFixture = {
      ...base,
      runner: async (file, args, options) => {
        if (args.includes('resolve-agent-thread')) {
          resolveStarted = true
          await gate
        }
        return base.runner(file, args, options)
      },
    }
    const host = fakeAgent()
    const driver = new LoopXContinuationDriver({
      runner: controlled.runner,
      isLiveAgent: () => true,
      retryDelaysMs: [0, 0],
    })

    observeActivated(driver, host)
    await waitFor(() => resolveStarted)
    host.replaceSession()
    releaseResolve?.()
    await waitFor(() => base.calls.some(args => args.includes('resolve-agent-thread')))

    expect(host.nextTurn).toHaveLength(0)
    expect(controlled.quotaCalls).toHaveLength(0)
    await driver.dispose()
  })

  it('recomputes activation from replacement history instead of carrying it over', async () => {
    const fixture = runnerFixture({ shouldRun: false, waitMinutes: 3 })
    const host = fakeAgent()
    const timers: Array<{
      callback: () => void
      cleared: boolean
      delay: number
    }> = []
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      retryDelaysMs: [0, 0],
      clock: {
        setTimeout(callback, delay) {
          const timer = { callback, cleared: false, delay }
          timers.push(timer)
          return timer
        },
        clearTimeout(handle) {
          (handle as { cleared: boolean }).cleared = true
        },
      },
    })

    observeActivated(driver, host)
    await waitFor(() => timers.length === 1)
    const callsBeforeReplacement = fixture.calls.length
    const maintenanceBeforeReplacement = host.maintenanceCalls

    host.replaceSession(undefined, [])
    driver.onSessionStart(host.agent)
    driver.onAgentStatus(host.agent, 'idle')
    timers[0]?.callback()
    await new Promise<void>(resolve => setTimeout(resolve, 0))

    expect(timers[0]?.cleared).toBe(true)
    expect(timers).toHaveLength(1)
    expect(fixture.calls).toHaveLength(callsBeforeReplacement)
    expect(host.maintenanceCalls).toBe(maintenanceBeforeReplacement)
    await driver.dispose()
  })
})

describe('GoalBar exact-Session Driver bridge', () => {
  it.each(['unfinished', 'bound', 'bound-body-failure', 'different-agent', 'no-step', 'one-completion'] as const)(
    'reconciles an original selection after Host completion on restore (%s)', async variant => {
      const host = fakeAgent()
      const original = 'dsh-prior-original-turn'
      const source = {
        kind: 'loopx-continuation', schemaVersion: 'loopx_dsh_continuation_v0',
        goalId, agentId, turnInstanceId: original, selectionRequired: true,
      } as const
      host.appendEvent(userSkillInvocationEvent())
      const completed = variant === 'unfinished' || variant === 'bound-body-failure' ? 2 : 1
      for (let turn = 0; turn < completed; turn += 1) {
        host.appendEvent(sessionEvent('turn/start', { turn }))
        host.appendEvent(sessionEvent('user/message', {
          ...userMessage('Continue the original Turn.'), source,
        }))
        if (variant !== 'no-step') host.appendEvent(sessionEvent('step/start', { turn, step: 0 }))
        host.appendEvent(sessionEvent('turn/end', { turn, reason: { kind: 'completed' } }))
      }
      const fixture = runnerFixture()
      const observed: string[] = []
      let receiptBound = variant === 'bound'
      const runner: FileRunner = async (file, args, options) => {
        if (variant === 'bound-body-failure' && receiptBound && args.includes('heartbeat-prompt')) {
          throw new LoopXCliError('transport', 'body unavailable after original selection', false)
        }
        if (!args.includes('receipt')) return fixture.runner(file, args, options)
        observed.push(args[args.indexOf('--turn-instance-id') + 1] ?? '')
        const identity = { goal_id: goalId, agent_id: agentId, turn_instance_id: original }
        return {
          exitCode: 0, stderr: '', stdout: JSON.stringify({
            ok: true, schema_version: 'quota_receipt_readback_v0', ...identity,
            agent_id: variant === 'different-agent' ? 'agent-other' : agentId,
            heartbeat_receipt: { status: 'committed', turn_instance_id: original,
              ...(receiptBound ? { settlement_identity: identity } : {
                selection_required: true, settlement_binding_owed: true,
              }),
            },
          }),
        }
      }
      const driver = new LoopXContinuationDriver({
        isLiveAgent: agent => agent === host.agent, runner, makeTurnInstanceId: () => 'fresh-turn',
      })
      const capture = { agent: host.agent, session: host.agent.session }
      try {
        driver.observeAgent(host.agent)
        if (variant === 'no-step' || variant === 'bound' || variant === 'one-completion') {
          await waitFor(() => host.nextTurn.length === 1)
          expect(observed).toEqual(variant === 'no-step' ? [] : [original])
          expect(fixture.quotaCalls[0]).toContain(variant === 'one-completion' ? original : 'fresh-turn')
        } else {
          await waitFor(() => observed.length === 1)
          const exactBinding = { goalId, loopxAgentId: agentId }
          if (variant === 'unfinished' || variant === 'bound-body-failure') {
            await waitFor(() => driver.hasRecoveryAttention(capture, exactBinding))
            expect(fixture.quotaCalls).toHaveLength(0)
            expect(host.nextTurn).toHaveLength(0)
            if (variant === 'bound-body-failure') {
              receiptBound = true
              await driver.evaluateActivatedSession(capture)
              expect(driver.hasRecoveryAttention(capture, exactBinding)).toBe(false)
              expect(host.nextTurn).toHaveLength(0)
              expect(fixture.quotaCalls[0]).toContain('fresh-turn')
              expect(observed).toEqual([original, original])
              return
            }
            await driver.evaluateActivatedSession(capture)
            await waitFor(() => host.nextTurn.length === 1)
            expect(fixture.quotaCalls[0]).toContain(original)
            expect(observed).toEqual([original, original])
          } else {
            await driver.dispose()
            expect(fixture.quotaCalls).toHaveLength(0)
          }
        }
      } finally {
        await driver.dispose()
      }
    },
  )

  it('projects only the observed Core recovery hold for the current binding', async () => {
    const host = fakeAgent()
    let recoveryState = 'attention_required'
    const { runner } = runnerFixture({
      shouldRun: false,
      schedulerMode: 'stop',
      recovery: () => ({
        schema_version: 'unsettled_host_turn_recovery_v0',
        state: recoveryState,
      }),
    })
    const driver = new LoopXContinuationDriver({ isLiveAgent: agent => agent === host.agent, runner })
    host.appendEvent(userSkillInvocationEvent())
    const capture = { agent: host.agent, session: host.agent.session }
    const expected = { goalId, loopxAgentId: agentId }
    await driver.evaluateActivatedSession(capture)
    expect(driver.hasRecoveryAttention(capture, expected)).toBe(true)
    expect(driver.hasRecoveryAttention(capture, { ...expected, loopxAgentId: 'different-agent' }))
      .toBe(false)
    expect(host.nextTurn).toHaveLength(0)

    recoveryState = 'recovery_required'
    await driver.evaluateActivatedSession(capture)
    expect(driver.hasRecoveryAttention(capture, expected)).toBe(false)
    await driver.dispose()
  })

  it.each(['agent/created', 'agent/session-start'] as const)(
    'resets resumed Session state on %s without activating an uninvoked Session',
    async event => {
      const host = fakeAgent()
      const reset = vi.spyOn(LoopXContinuationDriver.prototype, 'onSessionStart')
      const runtime = appliedDriverRuntime(host.agent, false)
      try {
        runtime.announceAgent(event)
        expect(reset).toHaveBeenCalledExactlyOnceWith(host.agent)
        await new Promise<void>(resolve => setTimeout(resolve, 0))
        expect(host.maintenanceCalls).toBe(0)
        expect(host.nextTurn).toHaveLength(0)
      } finally {
        reset.mockRestore()
        await runtime.dispose()
      }
    },
  )

  it('publishes the committed event seq before Driver state and activation gates', async () => {
    for (const initiallyObserved of [false, true]) {
      const host = fakeAgent()
      const runtime = appliedDriverRuntime(host.agent, initiallyObserved)
      const watches = goalBarCoordinator.openWatchService()
      const controller = new AbortController()
      const capturedSession = host.agent.session
      try {
        const changed = watches.waitForSessionChange(capturedSession, null, {
          signal: controller.signal,
          timeoutMs: 1_000,
          observedAgentStatus: host.agent.status,
          getAgentStatus: () => host.agent.status,
          isSessionCurrent: () => host.agent.session === capturedSession,
        })
        const event = turnEndEvent(initiallyObserved ? 41 : 17)
        host.appendEvent(event)
        runtime.emitSessionEvent(capturedSession, event)

        await expect(changed).resolves.toEqual({
          kind: 'candidate',
          sessionId,
          sessionEventSeq: event.seq,
        })
        expect(host.maintenanceCalls).toBe(0)
        expect(host.nextTurn).toHaveLength(0)
        await expect(goalBarCoordinator.cancelQueued({
          agent: host.agent,
          session: capturedSession,
        })).resolves.toEqual({ kind: 'applied' })
      } finally {
        controller.abort()
        watches.dispose()
        await runtime.dispose()
      }
    }
  })

  it('keeps watches exact to the captured Session and disposes pending waiters', async () => {
    const coordinator = new GoalBarCoordinator()
    const first = fakeAgent()
    const replacement = fakeAgent()
    const watches = coordinator.openWatchService()
    const controller = new AbortController()
    let settled = false
    const pending = watches.waitForSessionChange(first.agent.session, 3, {
      signal: controller.signal,
      timeoutMs: 1_000,
      observedAgentStatus: first.agent.status,
      getAgentStatus: () => first.agent.status,
      isSessionCurrent: () => true,
    }).then(receipt => {
      settled = true
      return receipt
    })

    coordinator.publishSessionCandidate(replacement.agent.session, turnEndEvent(9))
    await Promise.resolve()
    expect(settled).toBe(false)

    coordinator.publishSessionCandidate(first.agent.session, turnEndEvent(7))
    await expect(pending).resolves.toEqual({
      kind: 'candidate',
      sessionId,
      sessionEventSeq: 7,
    })

    const disposed = watches.waitForSessionChange(first.agent.session, 7, {
      signal: controller.signal,
      timeoutMs: 1_000,
      observedAgentStatus: first.agent.status,
      getAgentStatus: () => first.agent.status,
      isSessionCurrent: () => true,
    })
    watches.dispose()
    await expect(disposed).resolves.toEqual({ kind: 'service_disposed' })
  })

  it('echoes the watch cursor on timeout and fails closed on invalidation', async () => {
    const coordinator = new GoalBarCoordinator()
    const host = fakeAgent()
    const watches = coordinator.openWatchService()
    const controller = new AbortController()

    await expect(watches.waitForSessionChange(host.agent.session, 11, {
      signal: controller.signal,
      timeoutMs: 0,
      observedAgentStatus: host.agent.status,
      getAgentStatus: () => host.agent.status,
      isSessionCurrent: () => true,
    })).resolves.toEqual({ kind: 'timeout', sessionEventSeq: 11 })

    const unavailable = watches.waitForSessionChange(host.agent.session, null, {
      signal: controller.signal,
      timeoutMs: 1_000,
      observedAgentStatus: host.agent.status,
      getAgentStatus: () => host.agent.status,
      isSessionCurrent: () => true,
    })
    coordinator.invalidateSession(host.agent.session)
    await expect(unavailable).resolves.toEqual({ kind: 'session_unavailable' })

    const abortController = new AbortController()
    const aborted = watches.waitForSessionChange(host.agent.session, null, {
      signal: abortController.signal,
      timeoutMs: 1_000,
      observedAgentStatus: host.agent.status,
      getAgentStatus: () => host.agent.status,
      isSessionCurrent: () => true,
    })
    abortController.abort()
    await expect(aborted).resolves.toEqual({ kind: 'aborted' })
    watches.dispose()
  })

  it('returns fixed unavailable receipts for missing, throwing, or stale bridges', async () => {
    const coordinator = new GoalBarCoordinator()
    const host = fakeAgent()
    const capture = { agent: host.agent, session: host.agent.session }
    await expect(coordinator.cancelQueued(capture)).resolves.toEqual({
      kind: 'unavailable',
      reason: 'driver_unavailable',
    })

    const unregisterThrowing = coordinator.registerDriverBridge({
      async evaluateActivatedSession() { throw new Error('private driver failure') },
      async cancelQueued() { throw new Error('private driver failure') },
      readContinuationAdmission() { throw new Error('private driver failure') },
    })
    await expect(coordinator.evaluateActivatedSession(capture)).resolves.toEqual({
      kind: 'unavailable',
      reason: 'driver_unavailable',
    })
    unregisterThrowing()

    let calls = 0
    const unregister = coordinator.registerDriverBridge({
      async evaluateActivatedSession() {
        calls += 1
        return { kind: 'applied' }
      },
      async cancelQueued() {
        calls += 1
        return { kind: 'applied' }
      },
      readContinuationAdmission() {
        calls += 1
        return { state: 'clear' }
      },
    })
    host.replaceSession(undefined, [])
    await expect(coordinator.cancelQueued(capture)).resolves.toEqual({
      kind: 'unavailable',
      reason: 'session_unavailable',
    })
    expect(calls).toBe(0)
    unregister()
  })

  it('Start clears only the captured latch and awaits one real evaluation for an activated Session', async () => {
    const base = runnerFixture()
    let resolveStarted = false
    let releaseResolve: (() => void) | undefined
    const gate = new Promise<void>(resolve => { releaseResolve = resolve })
    const controlled: RunnerFixture = {
      ...base,
      runner: async (file, args, options) => {
        if (args.includes('resolve-agent-thread')) {
          resolveStarted = true
          await gate
        }
        return base.runner(file, args, options)
      },
    }
    const captured = fakeAgent()
    const unrelated = fakeAgent('another-session')
    const driver = new LoopXContinuationDriver({
      runner: controlled.runner,
      isLiveAgent: agent => agent === captured.agent || agent === unrelated.agent,
      retryDelaysMs: [0, 0],
    })
    captured.appendEvent(userSkillInvocationEvent())
    driver.onAgentError(captured.agent)
    driver.observeAgent(captured.agent)
    driver.observeAgent(unrelated.agent)
    let settled = false
    const receipt = driver.evaluateActivatedSession({
      agent: captured.agent,
      session: captured.agent.session,
    }).then(value => {
      settled = true
      return value
    })

    await waitFor(() => resolveStarted)
    expect(settled).toBe(false)
    expect(unrelated.maintenanceCalls).toBe(0)
    releaseResolve?.()

    await expect(receipt).resolves.toEqual({ kind: 'applied' })
    expect(captured.maintenanceCalls).toBe(1)
    expect(captured.nextTurn).toHaveLength(1)
    expect(unrelated.nextTurn).toHaveLength(0)
    expect(base.calls.filter(call => call.includes('resolve-agent-thread')))
      .toHaveLength(1)
    await driver.dispose()
  })

  it('Start leaves a Session without typed activation inactive and does not evaluate', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    driver.observeAgent(host.agent)
    driver.onAgentError(host.agent)

    await expect(driver.evaluateActivatedSession({
      agent: host.agent,
      session: host.agent.session,
    })).resolves.toEqual({ kind: 'applied' })
    await new Promise<void>(resolve => setTimeout(resolve, 0))

    expect(fixture.calls).toHaveLength(0)
    expect(host.maintenanceCalls).toBe(0)
    expect(host.nextTurn).toHaveLength(0)

    driver.onAgentStatus(host.agent, 'idle')
    await new Promise<void>(resolve => setTimeout(resolve, 0))
    expect(fixture.calls).toHaveLength(0)
    expect(host.nextTurn).toHaveLength(0)
    await driver.dispose()
  })

  it('Start fails closed when the captured Session is replaced during evaluation', async () => {
    const base = runnerFixture()
    let resolveStarted = false
    let releaseResolve: (() => void) | undefined
    const gate = new Promise<void>(resolve => { releaseResolve = resolve })
    const controlled: RunnerFixture = {
      ...base,
      runner: async (file, args, options) => {
        if (args.includes('resolve-agent-thread')) {
          resolveStarted = true
          await gate
        }
        return base.runner(file, args, options)
      },
    }
    const host = fakeAgent()
    const driver = new LoopXContinuationDriver({
      runner: controlled.runner,
      isLiveAgent: () => true,
      retryDelaysMs: [0, 0],
    })
    driver.observeAgent(host.agent)
    const capturedSession = host.agent.session
    host.appendEvent(userSkillInvocationEvent())
    const receipt = driver.evaluateActivatedSession({
      agent: host.agent,
      session: capturedSession,
    })

    await waitFor(() => resolveStarted)
    host.replaceSession(undefined, [])
    releaseResolve?.()

    await expect(receipt).resolves.toEqual({
      kind: 'unavailable',
      reason: 'session_unavailable',
    })
    expect(host.nextTurn).toHaveLength(0)
    await driver.dispose()
  })

  it.each([
    { plan: 'queue', shouldRun: true, interruption: 'Pause' },
    { plan: 'wait', shouldRun: false, interruption: 'Pause' },
    { plan: 'queue', shouldRun: true, interruption: 'maintenance cancellation' },
    { plan: 'wait', shouldRun: false, interruption: 'maintenance cancellation' },
  ])('discards a $plan plan returned after $interruption', async ({ shouldRun, interruption }) => {
    const fixture = runnerFixture({ shouldRun, waitMinutes: 5 })
    const clock = schedulerClock()
    const host = fakeAgent()
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      retryDelaysMs: [0, 0],
      clock,
    })
    const capture = { agent: host.agent, session: host.agent.session }
    vi.spyOn(host.agent, 'runMaintenance').mockImplementationOnce(async task => {
      const maintenance = new AbortController()
      const plan = await task(maintenance.signal)
      if (interruption === 'Pause') {
        await expect(driver.cancelQueued(capture)).resolves.toEqual({ kind: 'applied' })
      } else {
        maintenance.abort()
      }
      return plan
    })
    try {
      driver.observeAgent(host.agent)
      host.appendEvent(userSkillInvocationEvent())
      const interrupted = await driver.evaluateActivatedSession(capture)
      expect(host.nextTurn).toHaveLength(0)
      expect(clock.timers).toHaveLength(0)
      expect(interrupted).toEqual({ kind: 'unavailable', reason: 'evaluation_unavailable' })

      await expect(driver.evaluateActivatedSession(capture)).resolves.toEqual({ kind: 'applied' })
      expect(fixture.quotaCalls).toHaveLength(2)
      expect(host.nextTurn).toHaveLength(shouldRun ? 1 : 0)
      expect(clock.timers).toHaveLength(shouldRun ? 0 : 1)
    } finally {
      await driver.dispose()
    }
  })

  it('Pause removes only the captured Session scheduled continuation', async () => {
    const fixture = runnerFixture({ shouldRun: false, waitMinutes: 5 })
    const first = fakeAgent()
    const second = fakeAgent('another-session')
    const timers: Array<{ cleared: boolean }> = []
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      retryDelaysMs: [0, 0],
      clock: {
        setTimeout() {
          const timer = { cleared: false }
          timers.push(timer)
          return timer
        },
        clearTimeout(handle) {
          (handle as { cleared: boolean }).cleared = true
        },
      },
    })
    observeActivated(driver, first)
    observeActivated(driver, second)
    await waitFor(() => timers.length === 2)

    await expect(driver.cancelQueued({
      agent: first.agent,
      session: first.agent.session,
    })).resolves.toEqual({ kind: 'applied' })
    expect(timers[0]?.cleared).toBe(true)
    expect(timers[1]?.cleared).toBe(false)
    expect(first.cancelCalls).toBe(0)
    expect(second.cancelCalls).toBe(0)
    await driver.dispose()
  })

  it('Pause removes only the captured Session queued reservation', async () => {
    const fixture = runnerFixture()
    const first = fakeAgent()
    const second = fakeAgent('another-session')
    const driver = new LoopXContinuationDriver({
      runner: fixture.runner,
      isLiveAgent: () => true,
      retryDelaysMs: [0, 0],
    })
    observeActivated(driver, first)
    observeActivated(driver, second)
    await waitFor(() => first.nextTurn.length === 1 && second.nextTurn.length === 1)

    await expect(driver.cancelQueued({
      agent: first.agent,
      session: first.agent.session,
    })).resolves.toEqual({ kind: 'applied' })
    expect(first.nextTurn).toHaveLength(0)
    expect(second.nextTurn).toHaveLength(1)
    expect(first.cancelCalls).toBe(0)
    expect(second.cancelCalls).toBe(0)
    await driver.dispose()
  })

  it('Pause drains the exact claimed Turn without probing, then Start admits under a fresh identity', async () => {
    let shouldRun = true
    const fixture = runnerFixture({ shouldRun: () => shouldRun })
    const host = fakeAgent()
    let turn = 0
    const newDriver = () => new LoopXContinuationDriver({ runner: fixture.runner, isLiveAgent: () => true,
      makeTurnInstanceId: () => `pause-turn-${++turn}`, retryDelaysMs: [0, 0] })
    const driver = newDriver()
    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)
    const queued = host.nextTurn.shift() as UserMessage
    host.setStatus('running')
    driver.onInboxClaimed(host.agent, queued)

    // The Host has already committed goal-lifecycle stop before this receipt.
    shouldRun = false
    await expect(driver.cancelQueued({
      agent: host.agent,
      session: host.agent.session,
    })).resolves.toEqual({ kind: 'applied' })
    expect(host.cancelCalls).toBe(0)
    const decision = await driver.onPreStep(
      host.agent,
      [queued],
      new AbortController().signal,
      async () => ({ kind: 'enter', messages: [queued] }),
    )
    expect(decision.kind).toBe('enter')
    expect(fixture.quotaCalls).toHaveLength(1)
    expect(fixture.calls.filter(call => call.includes('resolve-agent-thread')))
      .toHaveLength(3)

    driver.onSessionEvent(host.agent, sessionEvent('user/message', queued))
    await expect(driver.cancelQueued({
      agent: host.agent,
      session: host.agent.session,
    })).resolves.toEqual({ kind: 'applied' })
    expect(host.cancelCalls).toBe(0)

    host.setStatus('idle')
    driver.onAgentStatus(host.agent, 'idle')
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(fixture.quotaCalls).toHaveLength(1)
    expect(host.nextTurn).toHaveLength(0)
    await driver.dispose('replacement')
    expect(host.cancelCalls).toBe(0)

    const replacement = newDriver()
    try {
      replacement.observeAgent(host.agent)
      await new Promise(resolve => setTimeout(resolve, 0))
      expect(fixture.quotaCalls).toHaveLength(1)
      shouldRun = true
      await expect(replacement.evaluateActivatedSession({ agent: host.agent, session: host.agent.session }))
        .resolves.toEqual({ kind: 'applied' })
      expect(host.nextTurn[0]?.source).toMatchObject({ turnInstanceId: 'pause-turn-2' })
      expect(fixture.quotaCalls).toHaveLength(2)
    } finally { await replacement.dispose() }
  })

  it('Pause preserves a claimed turn when stop lands during its quota recheck', async () => {
    let shouldRun = true
    const base = runnerFixture({ shouldRun: () => shouldRun })
    let blockQuota = false
    let quotaStarted = false
    let releaseQuota: (() => void) | undefined
    const gate = new Promise<void>(resolve => { releaseQuota = resolve })
    const controlled: RunnerFixture = {
      ...base,
      runner: async (file, args, options) => {
        if (blockQuota && args.includes('should-run')) {
          quotaStarted = true
          await gate
        }
        return base.runner(file, args, options)
      },
    }
    const host = fakeAgent()
    const driver = new LoopXContinuationDriver({
      runner: controlled.runner,
      isLiveAgent: () => true,
      makeTurnInstanceId: () => 'turn-stable',
      retryDelaysMs: [0, 0],
    })
    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)
    const queued = host.nextTurn.shift() as UserMessage
    host.setStatus('running')
    driver.onInboxClaimed(host.agent, queued)

    blockQuota = true
    const decision = driver.onPreStep(
      host.agent,
      [queued],
      new AbortController().signal,
      async () => ({ kind: 'enter', messages: [queued] }),
    )
    await waitFor(() => quotaStarted)
    shouldRun = false
    await driver.cancelQueued({ agent: host.agent, session: host.agent.session })
    releaseQuota?.()

    await expect(decision).resolves.toMatchObject({ kind: 'enter' })
    expect(base.quotaCalls).toHaveLength(2)
    expect(host.cancelCalls).toBe(0)
    await driver.dispose()
  })

  it('Pause preservation still rejects a claimed turn whose exact binding changed', async () => {
    let bindingStatus: 'bound' | 'missing' = 'bound'
    let shouldRun = true
    const fixture = runnerFixture({
      bindingStatus: () => bindingStatus,
      shouldRun: () => shouldRun,
    })
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)
    const queued = host.nextTurn.shift() as UserMessage
    host.setStatus('running')
    driver.onInboxClaimed(host.agent, queued)

    shouldRun = false
    bindingStatus = 'missing'
    await driver.cancelQueued({ agent: host.agent, session: host.agent.session })
    let nextCalls = 0
    const decision = await driver.onPreStep(
      host.agent,
      [queued],
      new AbortController().signal,
      async () => {
        nextCalls += 1
        return { kind: 'enter', messages: [queued] }
      },
    )

    expect(decision).toEqual({ kind: 'reject' })
    expect(nextCalls).toBe(0)
    expect(fixture.quotaCalls).toHaveLength(1)
    await driver.dispose()
    expect(host.cancelCalls).toBe(0)
  })
})

describe('plugin unload cause classification', () => {
  it('keeps cancellation ownership through an actual Cordis Fiber restart and removal', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const first = makeDriver(fixture)
    observeActivated(first, host)
    await waitFor(() => host.nextTurn.length === 1)
    const submitted = host.nextTurn.shift()!
    host.setStatus('running')
    first.onInboxClaimed(host.agent, submitted)
    await first.dispose('replacement')

    const ctx = new Context()
    ctx.reflect.provide('agents', {
      get: (id: string) => id === host.agent.id ? host.agent : undefined,
      list: () => [host.agent],
      withoutInitiator: (operation: () => Promise<unknown>) => operation(),
    } as never)
    ctx.reflect.provide('loopxBootstrap', { state: 'ready' })
    const observed = vi.spyOn(LoopXContinuationDriver.prototype, 'observeAgent')
    const fiber = ctx.plugin(applyDriver)
    try {
      await waitFor(() => observed.mock.calls.length === 1)
      await fiber.restart()
      await waitFor(() => observed.mock.calls.length === 2)
      expect(host.cancelCalls).toBe(0)
      await fiber.dispose()
      expect(host.cancelCalls).toBe(1)
    } finally {
      await fiber.dispose()
      observed.mockRestore()
    }
  })

  it.each([
    ['dsh-hmr module eviction', 'evict'],
    ['fiber restart', 'restart'],
  ] as const)('disposes the applied Driver for replacement on %s', async (_label, mode) => {
    const host = fakeAgent()
    const dispose = vi.spyOn(LoopXContinuationDriver.prototype, 'dispose')
    const runtime = appliedDriverRuntime(host.agent, false, { withFiber: true })
    try {
      await runtime.unloadFiber(mode)
      expect(dispose).toHaveBeenCalledExactlyOnceWith('replacement')
    } finally {
      dispose.mockRestore()
    }
  })

  it.each([
    ['direct fiber disposal', 'direct'],
    ['untyped legacy unload', 'plain'],
  ] as const)('disposes the applied Driver for removal on %s', async (_label, mode) => {
    const host = fakeAgent()
    const dispose = vi.spyOn(LoopXContinuationDriver.prototype, 'dispose')
    const runtime = appliedDriverRuntime(
      host.agent,
      false,
      mode === 'direct' ? { withFiber: true } : {},
    )
    try {
      if (mode === 'direct') await runtime.unloadFiber('direct')
      else await runtime.dispose()
      expect(dispose).toHaveBeenCalledExactlyOnceWith('removal')
    } finally {
      dispose.mockRestore()
    }
  })
})
