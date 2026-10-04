import { randomUUID } from 'node:crypto'
import { describe, expect, it, vi } from 'vitest'
import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import { SessionSeq } from '@deepseek-ai/dsh-session'
import type { SessionEvent, UserMessage } from '@deepseek-ai/dsh-session'
import type { FileRunner } from '../src/cli.ts'
import {
  apply as applyDriver,
  LoopXContinuationDriver,
} from '../src/driver.ts'
import type { DriverClock } from '../src/driver.ts'
import {
  GoalBarCoordinator,
  goalBarCoordinator,
} from '../src/goalbar/events.ts'

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
    whenIdle: async () => {},
    runMaintenance: async <T>(task: (signal: AbortSignal) => Promise<T>) => {
      maintenanceCalls += 1
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
  announceAgent(event: 'agent/created' | 'agent/session-start'): void
  dispose(): Promise<void>
}

function appliedDriverRuntime(
  liveAgent: Agent,
  initiallyObserved: boolean,
): AppliedDriverRuntime {
  type Handler = (...args: unknown[]) => unknown
  const handlers = new Map<string, Handler[]>()
  let disposeEffect: (() => unknown) | undefined
  const ctx = {
    agents: {
      get: (id: string) => String(liveAgent.id) === String(id) ? liveAgent : undefined,
      list: () => initiallyObserved ? [liveAgent] : [],
      withoutInitiator: (operation: () => Promise<unknown>) => operation(),
    },
    logger: { warn() {} },
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

function runnerFixture(options: {
  readonly quotaExitFailures?: number
  readonly heartbeatExitFailures?: number
  readonly shouldRun?: boolean | (() => boolean)
  readonly waitMinutes?: number
  readonly schedulerMode?: 'poll' | 'stop' | 'missing'
  readonly unchangedPollLimit?: number
  readonly quotaAgentId?: string
  readonly quotaTurnInstanceId?: string
  readonly heartbeatTurnInstanceId?: string
  readonly heartbeatTaskBody?: string
  readonly rewardMemoryGuidance?: string
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
        return { exitCode: 0, stdout: 'loopx 0.5.0\n', stderr: '' }
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
        const schedulerHint = shouldRun
          ? { action: 'run_now' }
          : options.schedulerMode === 'stop'
            ? {
                action: 'stop_until_explicit_resume',
                unchanged_poll: { local_scheduler: 'stop' },
              }
            : options.schedulerMode === 'missing'
              ? { action: 'wait' }
              : {
                  action: 'wait',
                  reset_policy: { reset_token: 'scheduler-token' },
                  unchanged_poll: {
                    local_scheduler: {
                      recommended_interval_minutes: options.waitMinutes ?? 5,
                      ...(options.unchangedPollLimit === undefined
                        ? {}
                        : { unchanged_poll_limit: options.unchangedPollLimit }),
                    },
                  },
                }
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

describe('same-session LoopX driver', () => {
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

  it('queues one authoritative task and revalidates the same receipt before entry', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)

    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)
    const queued = host.nextTurn[0] as UserMessage
    expect(queued.content).toEqual([
      {
        type: 'text',
        text: 'LOOPX_TURN=turn-stable\nContinue through the authoritative LoopX workflow.',
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

  it('rejects when a downstream pre-step replaces the canonical message', async () => {
    const fixture = runnerFixture()
    const host = fakeAgent()
    const driver = makeDriver(fixture)
    observeActivated(driver, host)
    await waitFor(() => host.nextTurn.length === 1)
    const queued = host.nextTurn.shift() as UserMessage
    host.setStatus('running')
    driver.onInboxClaimed(host.agent, queued)
    const replaced = {
      ...queued,
      content: [{ type: 'text' as const, text: 'downstream replacement' }],
    } satisfies UserMessage

    const decision = await driver.onPreStep(
      host.agent,
      [queued],
      new AbortController().signal,
      async () => ({ kind: 'enter', messages: [replaced] }),
    )

    expect(decision).toEqual({ kind: 'reject' })
    expect(fixture.quotaCalls).toHaveLength(2)
    await driver.dispose()
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

  it('fails closed when the canonical task body contains a conflicting turn id', async () => {
    const fixture = runnerFixture({
      heartbeatTaskBody: [
        'LOOPX_TURN=turn-stable',
        'LOOPX_TURN=turn-other',
        'Continue through the authoritative LoopX workflow.',
      ].join('\n'),
    })
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

  it('Pause preserves the exact claimed turn after Goal stop without opening new work', async () => {
    let shouldRun = true
    const fixture = runnerFixture({ shouldRun: () => shouldRun })
    const host = fakeAgent()
    const driver = makeDriver(fixture)
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
    await waitFor(() => fixture.quotaCalls.length === 2)
    expect(host.nextTurn).toHaveLength(0)
    await driver.dispose()
    expect(host.cancelCalls).toBe(0)
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
