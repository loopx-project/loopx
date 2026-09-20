import { mkdtemp, mkdir, rename, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { Agent } from '@deepseek-ai/dsh-agent'
import { SessionSeq } from '@deepseek-ai/dsh-session'
import type { SessionEvent } from '@deepseek-ai/dsh-session'
import {
  LoopXCliError,
} from '../src/cli.ts'
import type {
  FileResult,
  FileRunner,
  FileRunOptions,
  LoopXCommand,
} from '../src/cli.ts'
import {
  GoalBarCoordinator,
} from '../src/goalbar/events.ts'
import {
  createGoalBarService,
  decodeGoalBarLifecycleExecutionV1,
} from '../src/goalbar/service.ts'
import type { GoalBarRequestV1 } from '../src/goalbar/protocol.ts'

const sessionId = 'session-fixture'
const goalId = 'goal-fixture'
const loopxAgentId = 'agent-fixture'
const command: LoopXCommand = {
  file: 'loopx',
  prefix: [],
  skillCommand: 'loopx',
  version: 'loopx 0.5.0',
}

function turnEnd(seq: number): SessionEvent<'turn/end'> {
  return {
    type: 'turn/end',
    seq: SessionSeq(seq),
    time: 1,
    data: { turn: 0, reason: { kind: 'completed' } },
  }
}

function stepEnd(seq: number): SessionEvent<'step/end'> {
  return { type: 'step/end', seq, time: 1, data: {} } as SessionEvent<'step/end'>
}

interface AgentFixture {
  readonly agent: Agent
  readonly events: SessionEvent[]
  setStatus(status: 'idle' | 'running'): void
}

function agentFixture(
  id = sessionId,
  initialStatus: 'idle' | 'running' = 'idle',
  eventSource?: (() => SessionEvent[]) | undefined,
  cwd = '/fixture/project',
): AgentFixture {
  const events: SessionEvent[] = []
  let status = initialStatus
  const session = {
    id,
    header: { version: 0, id, createdAt: 1, cwd },
    snapshotEvents: () => eventSource?.() ?? events,
    surface: { nodes: [] },
  }
  const agent = {
    id,
    options: {},
    session,
    inbox: { nextTurn: [], nextStep: [] },
    ctx: {},
    get status() { return status },
    cancel() {},
    whenIdle: async () => {},
    runMaintenance: async <T>(task: (signal: AbortSignal) => Promise<T>) => (
      task(new AbortController().signal)
    ),
    send() {},
    followup() {},
    steer() {},
    inject() {},
  } as unknown as Agent
  return {
    agent,
    events,
    setStatus(value) { status = value },
  }
}

function bindingPayload(
  threadId = sessionId,
  mode: 'bound' | 'missing' | 'ambiguous' = 'bound',
): Record<string, unknown> {
  if (mode === 'missing') {
    return {
      ok: true,
      schema_version: 'loopx_thread_agent_binding_resolution_v0',
      host_surface: 'deepseek-harness-native',
      thread_id: threadId,
      status: 'missing',
      goal_id: null,
      agent_id: null,
      matches: [],
    }
  }
  if (mode === 'ambiguous') {
    return {
      ok: false,
      schema_version: 'loopx_thread_agent_binding_resolution_v0',
      host_surface: 'deepseek-harness-native',
      thread_id: threadId,
      status: 'ambiguous',
      goal_id: null,
      agent_id: null,
      error_kind: 'thread_agent_binding_ambiguous',
      matches: [
        { goal_id: goalId, agent_id: loopxAgentId },
        { goal_id: 'goal-other', agent_id: 'agent-other' },
      ],
    }
  }
  return {
    ok: true,
    schema_version: 'loopx_thread_agent_binding_resolution_v0',
    host_surface: 'deepseek-harness-native',
    thread_id: threadId,
    status: 'bound',
    goal_id: goalId,
    agent_id: loopxAgentId,
    matches: [{ goal_id: goalId, agent_id: loopxAgentId }],
  }
}

function activationPayload(
  state: 'active' | 'stopped',
): Record<string, unknown> {
  const active = state === 'active'
  return {
    ok: true,
    schema_version: 'loopx_goal_activation_transition_v1',
    dry_run: true,
    execute: false,
    goal_id: goalId,
    before_state: state,
    after_state: 'stopped',
    changed: active,
    written: false,
    partial_write: false,
    readback: {
      schema_version: 'loopx_goal_activation_readback_v1',
      status: active ? 'not_executed' : 'not_required',
      verified: !active,
    },
  }
}

function todoPayload(): Record<string, unknown> {
  const item = { status: 'open', text: 'private text must be discarded' }
  return {
    ok: true,
    read_only: true,
    command: 'list',
    goal_id: goalId,
    agent_id_filter: loopxAgentId,
    role: 'agent',
    explicit_limit: 1,
    todo_count: 1,
    returned_todo_count: 1,
    todos: [item],
    todo_list_projection: {
      schema_version: 'agent_lane_todo_list_projection_v0',
      view: 'explicit_limit_cold_path',
      item_limit_per_role: 1,
      counts_cover_full_match: true,
      matched_todo_count: 5,
      returned_todo_count: 1,
    },
    agent_todos: {
      done_count: 3,
      open_count: 2,
      total_count: 5,
      items: [item],
    },
  }
}

function executionPayload(
  target: 'active' | 'stopped',
  options: {
    readonly changed?: boolean
    readonly written?: boolean
    readonly partialWrite?: boolean
  } = {},
): Record<string, unknown> {
  return {
    ok: true,
    schema_version: 'loopx_goal_activation_transition_v1',
    dry_run: false,
    execute: true,
    goal_id: goalId,
    before_state: target,
    after_state: target,
    changed: options.changed ?? false,
    written: options.written ?? false,
    partial_write: options.partialWrite ?? false,
    readback: {
      schema_version: 'loopx_goal_activation_readback_v1',
      status: 'verified',
      verified: true,
      goal_id: goalId,
      expected_state: target,
      source_state: target,
      target_state: target,
    },
  }
}

interface Harness {
  readonly coordinator: GoalBarCoordinator
  readonly calls: string[][]
  readonly mutationOptions: FileRunOptions[]
  readonly warnings: string[]
  readonly driverCalls: Array<'evaluateActivatedSession' | 'cancelQueued'>
  readonly service: ReturnType<typeof createGoalBarService>
  readonly host: AgentFixture
  bindingMode: 'bound' | 'missing' | 'ambiguous'
  activation: 'active' | 'stopped'
  currentAgent: Agent | undefined
  mutation?: ((args: readonly string[]) => Promise<FileResult>) | undefined
  onBinding?: (() => void) | undefined
  onActivation?: (() => Promise<void>) | undefined
  onTodo?: (() => Promise<void>) | undefined
  unregisterDriver(): void
}

type HarnessMutableState = Pick<
  Harness,
  | 'bindingMode'
  | 'activation'
  | 'currentAgent'
  | 'mutation'
  | 'onBinding'
  | 'onActivation'
  | 'onTodo'
>

function harness(options: {
  readonly status?: 'idle' | 'running'
  readonly watchTimeoutMs?: number
  readonly eventSource?: () => SessionEvent[]
  readonly driver?: 'applied' | 'unavailable' | 'none'
  readonly cwd?: string
} = {}): Harness {
  const host = agentFixture(
    sessionId,
    options.status ?? 'idle',
    options.eventSource,
    options.cwd,
  )
  const coordinator = new GoalBarCoordinator()
  const calls: string[][] = []
  const mutationOptions: FileRunOptions[] = []
  const warnings: string[] = []
  const driverCalls: Array<'evaluateActivatedSession' | 'cancelQueued'> = []
  const state: HarnessMutableState = {
    bindingMode: 'bound',
    activation: 'stopped',
    currentAgent: host.agent as Agent | undefined,
    mutation: undefined as Harness['mutation'],
    onBinding: undefined as Harness['onBinding'],
    onActivation: undefined as Harness['onActivation'],
    onTodo: undefined as Harness['onTodo'],
  }
  const runner: FileRunner = async (_file, args, runOptions) => {
    calls.push([...args])
    if (args.includes('resolve-agent-thread')) {
      state.onBinding?.()
      const thread = args[args.indexOf('--thread-id') + 1] ?? ''
      const payload = bindingPayload(thread, state.bindingMode)
      return {
        exitCode: state.bindingMode === 'ambiguous' ? 2 : 0,
        stdout: JSON.stringify(payload),
        stderr: '',
      }
    }
    if (args.includes('goal-lifecycle') && args.includes('--execute')) {
      mutationOptions.push(runOptions)
      if (state.mutation !== undefined) return state.mutation(args)
      const operation = args[args.indexOf('--operation') + 1]
      state.activation = operation === 'resume' ? 'active' : 'stopped'
      return {
        exitCode: 0,
        stdout: JSON.stringify(executionPayload(state.activation)),
        stderr: '',
      }
    }
    if (args.includes('goal-lifecycle')) {
      await state.onActivation?.()
      return {
        exitCode: 0,
        stdout: JSON.stringify(activationPayload(state.activation)),
        stderr: '',
      }
    }
    if (args.includes('todo')) {
      await state.onTodo?.()
      return { exitCode: 0, stdout: JSON.stringify(todoPayload()), stderr: '' }
    }
    throw new Error(`/private/unexpected argv ${args.join(' ')}`)
  }
  let unregisterDriver = (): void => {}
  if (options.driver !== 'none') {
    const receipt = options.driver === 'unavailable'
      ? { kind: 'unavailable' as const, reason: 'driver_unavailable' as const }
      : { kind: 'applied' as const }
    unregisterDriver = coordinator.registerDriverBridge({
      evaluateActivatedSession: async () => {
        driverCalls.push('evaluateActivatedSession')
        return receipt
      },
      cancelQueued: async () => {
        driverCalls.push('cancelQueued')
        return receipt
      },
    })
  }
  const service = createGoalBarService({
    getAgent: id => id === sessionId ? state.currentAgent : undefined,
    coordinator,
    command,
    runner,
    retryDelaysMs: [0, 0],
    watchTimeoutMs: options.watchTimeoutMs ?? 20,
    actionTimeoutMs: 100,
    warn: message => { warnings.push(message) },
  })
  return Object.assign(state, {
    coordinator,
    calls,
    mutationOptions,
    warnings,
    driverCalls,
    service,
    host,
    unregisterDriver,
  })
}

function readRequest(): Extract<GoalBarRequestV1, { readonly op: 'read' }> {
  return { v: 'loopx_goalbar_request_v2', op: 'read', sessionId }
}

function watchRequest(
  afterSessionEventSeq: number | null,
  options: {
    readonly sourceRevision?: string
    readonly expected?: { readonly goalId: string; readonly loopxAgentId: string } | null
    readonly agentStatus?: 'idle' | 'running' | null
  } = {},
): Extract<GoalBarRequestV1, { readonly op: 'watch' }> {
  return {
    v: 'loopx_goalbar_request_v2',
    op: 'watch',
    sessionId,
    afterSessionEventSeq,
    sourceRevision: options.sourceRevision
      ?? 'sha256:04284a0332528476ac54e743cb76d5c0731985225b77926e7f6a32941db96c42',
    expected: options.expected ?? null,
    agentStatus: options.agentStatus ?? 'idle',
  }
}

function actionRequest(
  op: 'start' | 'pause',
): Extract<GoalBarRequestV1, { readonly op: 'start' | 'pause' }> {
  return {
    v: 'loopx_goalbar_request_v2',
    op,
    sessionId,
    expected: { goalId, loopxAgentId },
  }
}

function pending<T>(): {
  readonly promise: Promise<T>
  resolve(value: T): void
} {
  let resolve!: (value: T) => void
  return {
    promise: new Promise<T>(done => { resolve = done }),
    resolve(value) { resolve(value) },
  }
}

async function eventually(predicate: () => boolean): Promise<void> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (predicate()) return
    await new Promise<void>(resolve => setTimeout(resolve, 0))
  }
  throw new Error('condition not reached')
}

describe('GoalBar Host read/watch', () => {
  it('captures the cursor before CLI, runs activation/Todo in parallel, and loses no wake', async () => {
    const fixture = harness()
    fixture.host.events.push(turnEnd(3))
    const activation = pending<void>()
    const todo = pending<void>()
    let bindingCalls = 0
    fixture.onBinding = () => {
      bindingCalls += 1
      if (bindingCalls === 1) fixture.host.events.push(turnEnd(4))
    }
    fixture.onActivation = () => activation.promise
    fixture.onTodo = () => todo.promise

    const reading = fixture.service.handle(
      readRequest(),
      new AbortController().signal,
    )
    await eventually(() => (
      fixture.calls.some(args => args.includes('goal-lifecycle'))
      && fixture.calls.some(args => args.includes('todo'))
    ))
    activation.resolve()
    todo.resolve()
    const response = await reading

    expect(response.result).toMatchObject({
      kind: 'present',
      baseSessionEventSeq: 3,
      snapshot: { progress: { processed: 3, remaining: 2, total: 5 } },
    })
    const callsBeforeWatch = fixture.calls.length
    const watched = await fixture.service.handle(
      watchRequest(3, { sourceRevision: `sha256:${'0'.repeat(64)}` }),
      new AbortController().signal,
    )
    expect(watched.result).toEqual({ kind: 'source_changed', sessionEventSeq: 4 })
    expect(fixture.calls).toHaveLength(callsBeforeWatch)
    await fixture.service.dispose()
  })

  it('fences a replaced Agent after asynchronous reads', async () => {
    const fixture = harness()
    fixture.onBinding = () => {
      fixture.currentAgent = agentFixture().agent
    }
    const response = await fixture.service.handle(
      readRequest(),
      new AbortController().signal,
    )
    expect(response.result).toEqual({
      kind: 'fault',
      code: 'session_unavailable',
      baseSessionEventSeq: null,
      sourceRevision: expect.stringMatching(/^sha256:[0-9a-f]{64}$/u),
    })
    await fixture.service.dispose()
  })

  it('logs ambiguity with only the fixed code, bounded Session id, and count', async () => {
    const fixture = harness()
    fixture.bindingMode = 'ambiguous'
    const response = await fixture.service.handle(
      readRequest(),
      new AbortController().signal,
    )
    expect(response.result).toEqual({
      kind: 'hidden',
      reason: 'binding_ambiguous',
      baseSessionEventSeq: null,
      sourceRevision: expect.stringMatching(/^sha256:[0-9a-f]{64}$/u),
    })
    expect(fixture.calls).toHaveLength(1)
    expect(fixture.warnings).toEqual([
      'dsh_loopx_goalbar_binding_ambiguous session_id=session-fixture count=2',
    ])
    await fixture.service.dispose()
  })

  it('closes the check/register race and supports independent waiters', async () => {
    let eventReads = 0
    const raceFixture = harness({
      eventSource: () => {
        eventReads += 1
        return eventReads === 1 ? [turnEnd(7)] : [turnEnd(7), turnEnd(8)]
      },
    })
    const raced = await raceFixture.service.handle(
      watchRequest(7, { sourceRevision: `sha256:${'0'.repeat(64)}` }),
      new AbortController().signal,
    )
    expect(raced.result).toEqual({ kind: 'source_changed', sessionEventSeq: 8 })
    expect(raceFixture.calls).toHaveLength(0)
    await raceFixture.service.dispose()

    const fixture = harness()
    const changedRequest = watchRequest(null, { sourceRevision: `sha256:${'0'.repeat(64)}` })
    const first = fixture.service.handle(changedRequest, new AbortController().signal)
    const second = fixture.service.handle(changedRequest, new AbortController().signal)
    await Promise.resolve()
    fixture.host.events.push(turnEnd(2))
    fixture.coordinator.publishSessionCandidate(
      fixture.host.agent.session,
      turnEnd(2),
    )
    expect((await first).result).toEqual({ kind: 'source_changed', sessionEventSeq: 2 })
    expect((await second).result).toEqual({ kind: 'source_changed', sessionEventSeq: 2 })
    expect(fixture.calls).toHaveLength(0)
    await fixture.service.dispose()
  })

  it('returns bounded timeout and contains abort, replacement, and disposal', async () => {
    const timeoutFixture = harness({ watchTimeoutMs: 2 })
    timeoutFixture.host.events.push(turnEnd(4))
    expect((await timeoutFixture.service.handle(
      watchRequest(4),
      new AbortController().signal,
    )).result).toEqual({ kind: 'timeout', sessionEventSeq: 4 })
    await timeoutFixture.service.dispose()

    const abortFixture = harness()
    const controller = new AbortController()
    const aborted = abortFixture.service.handle(watchRequest(null), controller.signal)
    controller.abort()
    expect((await aborted).result).toEqual({
      kind: 'fault', code: 'session_unavailable',
    })
    await abortFixture.service.dispose()

    const replacementFixture = harness()
    const replaced = replacementFixture.service.handle(
      watchRequest(null),
      new AbortController().signal,
    )
    replacementFixture.currentAgent = agentFixture().agent
    replacementFixture.coordinator.invalidateSession(
      replacementFixture.host.agent.session,
    )
    expect((await replaced).result).toEqual({
      kind: 'fault', code: 'session_unavailable',
    })
    await replacementFixture.service.dispose()

    const disposalFixture = harness()
    const disposed = disposalFixture.service.handle(
      watchRequest(null),
      new AbortController().signal,
    )
    await disposalFixture.service.dispose()
    expect((await disposed).result).toEqual({
      kind: 'fault', code: 'session_unavailable',
    })
  })

  it('publishes with no waiter as a zero-CLI no-op', async () => {
    const fixture = harness()
    fixture.host.events.push(turnEnd(1))
    fixture.coordinator.publishSessionCandidate(fixture.host.agent.session, turnEnd(1))
    expect(fixture.calls).toHaveLength(0)
    await fixture.service.dispose()
  })

  it('absorbs unchanged candidates and publishes status without a CLI read', async () => {
    const fixture = harness({ watchTimeoutMs: 5 })
    const unchanged = fixture.service.handle(
      watchRequest(null),
      new AbortController().signal,
    )
    await Promise.resolve()
    fixture.host.events.push(turnEnd(1))
    fixture.coordinator.publishSessionCandidate(fixture.host.agent.session, turnEnd(1))
    expect((await unchanged).result).toEqual({ kind: 'timeout', sessionEventSeq: 1 })
    expect(fixture.calls).toHaveLength(0)

    const runtime = fixture.service.handle(
      watchRequest(1),
      new AbortController().signal,
    )
    await Promise.resolve()
    fixture.host.setStatus('running')
    fixture.coordinator.publishAgentStatus(fixture.host.agent.session, 'running')
    expect((await runtime).result).toEqual({
      kind: 'runtime_changed',
      sessionEventSeq: 1,
      agentStatus: 'running',
    })
    expect(fixture.calls).toHaveLength(0)
    await fixture.service.dispose()
  })

  it('detects mid-turn and lease-time authoritative file changes without CLI reads', async () => {
    const cwd = await mkdtemp(join(tmpdir(), 'loopx-goalbar-service-'))
    await mkdir(join(cwd, '.loopx'), { recursive: true })
    const fixture = harness({ cwd, watchTimeoutMs: 50 })
    fixture.bindingMode = 'missing'
    try {
      const initial = await fixture.service.handle(readRequest(), new AbortController().signal)
      if (initial.result.kind !== 'hidden') throw new Error('expected hidden fixture')
      const callsAfterRead = fixture.calls.length
      const watching = fixture.service.handle(watchRequest(
        initial.result.baseSessionEventSeq,
        {
          sourceRevision: initial.result.sourceRevision,
          agentStatus: 'idle',
        },
      ), new AbortController().signal)
      await Promise.resolve()
      await writeFile(join(cwd, '.loopx', 'registry.json'), '{"goals":[]}', 'utf8')
      fixture.host.events.push(stepEnd(1))
      fixture.host.setStatus('running')
      fixture.coordinator.publishAgentStatus(fixture.host.agent.session, 'running')
      expect((await watching).result).toEqual({
        kind: 'source_changed', sessionEventSeq: 1,
      })
      expect(fixture.calls).toHaveLength(callsAfterRead)

      fixture.host.setStatus('idle')
      const current = await fixture.service.handle(readRequest(), new AbortController().signal)
      if (current.result.kind !== 'hidden') throw new Error('expected hidden fixture')
      const callsBeforeTurn = fixture.calls.length
      const dedupedTurn = fixture.service.handle(watchRequest(
        current.result.baseSessionEventSeq,
        { sourceRevision: current.result.sourceRevision, agentStatus: 'idle' },
      ), new AbortController().signal)
      await Promise.resolve()
      fixture.host.events.push(turnEnd(2))
      fixture.coordinator.publishSessionCandidate(fixture.host.agent.session, turnEnd(2))
      expect((await dedupedTurn).result).toEqual({
        kind: 'timeout', sessionEventSeq: 2,
      })
      expect(fixture.calls).toHaveLength(callsBeforeTurn)

      const leaseAnchor = await fixture.service.handle(readRequest(), new AbortController().signal)
      if (leaseAnchor.result.kind !== 'hidden') throw new Error('expected hidden fixture')
      const callsBeforeLease = fixture.calls.length
      const lease = fixture.service.handle(watchRequest(
        leaseAnchor.result.baseSessionEventSeq,
        { sourceRevision: leaseAnchor.result.sourceRevision, agentStatus: 'idle' },
      ), new AbortController().signal)
      await writeFile(join(cwd, '.loopx', 'registry.json'), '{"goals":[1]}', 'utf8')
      expect((await lease).result).toEqual({
        kind: 'source_changed', sessionEventSeq: 2,
      })
      expect(fixture.calls).toHaveLength(callsBeforeLease)
    } finally {
      await fixture.service.dispose()
      await rm(cwd, { recursive: true, force: true })
    }
  })

  it('retries a read when the active state is atomically replaced during CLI reads', async () => {
    const cwd = await mkdtemp(join(tmpdir(), 'loopx-goalbar-stable-read-'))
    const stateDir = join(cwd, '.codex', 'goals', goalId)
    await mkdir(join(cwd, '.loopx'), { recursive: true })
    await mkdir(stateDir, { recursive: true })
    await writeFile(join(cwd, '.loopx', 'registry.json'), '{"goals":[]}', 'utf8')
    const statePath = join(stateDir, 'ACTIVE_GOAL_STATE.md')
    await writeFile(statePath, 'state=one', 'utf8')
    const fixture = harness({ cwd })
    let replacements = 0
    fixture.onActivation = async () => {
      if (replacements > 0) return
      replacements += 1
      const replacement = join(stateDir, 'ACTIVE_GOAL_STATE.md.next')
      await writeFile(replacement, 'state=two', 'utf8')
      await rename(replacement, statePath)
    }
    try {
      const result = await fixture.service.handle(readRequest(), new AbortController().signal)
      expect(result.result.kind).toBe('present')
      expect(fixture.calls.filter(args => args.includes('resolve-agent-thread')))
        .toHaveLength(2)
      if (result.result.kind !== 'present') throw new Error('expected present fixture')
      const wrongAgent = await fixture.service.handle(watchRequest(
        result.result.baseSessionEventSeq,
        {
          sourceRevision: result.result.sourceRevision,
          expected: { goalId, loopxAgentId: 'agent-other' },
          agentStatus: 'idle',
        },
      ), new AbortController().signal)
      expect(wrongAgent.result.kind).toBe('source_changed')
    } finally {
      await fixture.service.dispose()
      await rm(cwd, { recursive: true, force: true })
    }
  })

  it('fails closed when a watch cursor is beyond the exact Session log', async () => {
    const fixture = harness()
    expect((await fixture.service.handle(
      watchRequest(1),
      new AbortController().signal,
    )).result).toEqual({ kind: 'fault', code: 'session_unavailable' })
    expect(fixture.calls).toHaveLength(0)
    await fixture.service.dispose()
  })
})

describe('GoalBar Host lifecycle authority', () => {
  it('uses one immediate action lane and blocks later reads through child reap', async () => {
    const fixture = harness()
    const mutation = pending<FileResult>()
    fixture.mutation = async () => mutation.promise
    const first = fixture.service.handle(
      actionRequest('start'),
      new AbortController().signal,
    )
    await eventually(() => fixture.calls.some(args => args.includes('--execute')))
    const callCountAtLaunch = fixture.calls.length

    const second = await fixture.service.handle(
      actionRequest('start'),
      new AbortController().signal,
    )
    expect(second.result).toEqual({ kind: 'rejected', code: 'action_in_flight' })
    const reading = fixture.service.handle(readRequest(), new AbortController().signal)
    await new Promise<void>(resolve => setTimeout(resolve, 0))
    expect(fixture.calls).toHaveLength(callCountAtLaunch)

    fixture.activation = 'active'
    mutation.resolve({
      exitCode: 0,
      stdout: JSON.stringify(executionPayload('active')),
      stderr: '',
    })
    expect((await first).result.kind).toBe('succeeded')
    expect(fixture.driverCalls).toEqual(['evaluateActivatedSession'])
    expect((await reading).result.kind).toBe('present')
    await fixture.service.dispose()
  })

  it('ignores browser disconnect after launch and accepts idempotent verified readback', async () => {
    const fixture = harness()
    const mutation = pending<FileResult>()
    fixture.mutation = async () => mutation.promise
    const controller = new AbortController()
    const action = fixture.service.handle(actionRequest('start'), controller.signal)
    await eventually(() => fixture.calls.some(args => args.includes('--execute')))
    controller.abort()
    fixture.activation = 'active'
    mutation.resolve({
      exitCode: 0,
      stdout: JSON.stringify(executionPayload('active', {
        changed: false,
        written: false,
      })),
      stderr: '/private/diagnostic',
    })
    const response = await action
    expect(response.result.kind).toBe('succeeded')
    expect(fixture.mutationOptions).toHaveLength(1)
    expect(fixture.mutationOptions[0]?.signal).toBeUndefined()
    const executionCall = fixture.calls.find(args => args.includes('--execute'))
    expect(executionCall).toEqual([
      '--registry', '.loopx/registry.json',
      '--format', 'json',
      'goal-lifecycle',
      '--goal-id', goalId,
      '--operation', 'resume',
      '--actor-kind', 'owner',
      '--execute',
    ])
    await fixture.service.dispose()
  })

  it('pauses an active running Agent with stop and only cancels queued continuation', async () => {
    const fixture = harness({ status: 'running' })
    fixture.activation = 'active'

    const response = await fixture.service.handle(
      actionRequest('pause'),
      new AbortController().signal,
    )

    expect(response.result).toMatchObject({
      kind: 'succeeded',
      snapshot: {
        goalActivation: 'stopped',
        agentStatus: 'running',
      },
    })
    expect(fixture.calls.find(args => args.includes('--execute'))).toEqual([
      '--registry', '.loopx/registry.json',
      '--format', 'json',
      'goal-lifecycle',
      '--goal-id', goalId,
      '--operation', 'stop',
      '--actor-kind', 'owner',
      '--execute',
    ])
    expect(fixture.driverCalls).toEqual(['cancelQueued'])
    expect(fixture.driverCalls).not.toContain('evaluateActivatedSession')
    await fixture.service.dispose()
  })

  it('rejects Pause when the Goal is already stopped', async () => {
    const fixture = harness()

    const response = await fixture.service.handle(
      actionRequest('pause'),
      new AbortController().signal,
    )

    expect(response.result).toEqual({ kind: 'rejected', code: 'not_actionable' })
    expect(fixture.calls.some(args => args.includes('--execute'))).toBe(false)
    expect(fixture.driverCalls).toHaveLength(0)
    await fixture.service.dispose()
  })

  it('does not launch a mutation when the request aborts during validation', async () => {
    const fixture = harness()
    const activation = pending<void>()
    fixture.onActivation = () => activation.promise
    const controller = new AbortController()
    const action = fixture.service.handle(actionRequest('start'), controller.signal)
    await eventually(() => fixture.calls.some(args => (
      args.includes('goal-lifecycle') && !args.includes('--execute')
    )))

    controller.abort()
    activation.resolve()
    const response = await action

    expect(response.result).toEqual({
      kind: 'rejected', code: 'binding_validation_failed',
    })
    expect(fixture.calls.some(args => args.includes('--execute'))).toBe(false)
    expect(fixture.driverCalls).toHaveLength(0)
    await fixture.service.dispose()
  })

  it('does not launch a mutation after exact Agent/Session replacement during validation', async () => {
    const fixture = harness()
    const activation = pending<void>()
    fixture.onActivation = () => activation.promise
    const action = fixture.service.handle(
      actionRequest('start'),
      new AbortController().signal,
    )
    await eventually(() => fixture.calls.some(args => (
      args.includes('goal-lifecycle') && !args.includes('--execute')
    )))

    fixture.currentAgent = agentFixture().agent
    activation.resolve()
    const response = await action

    expect(response.result).toEqual({
      kind: 'rejected', code: 'binding_validation_failed',
    })
    expect(fixture.calls.some(args => args.includes('--execute'))).toBe(false)
    expect(fixture.driverCalls).toHaveLength(0)
    await fixture.service.dispose()
  })

  it('does not retry a verified mutation when the live Session is replaced after launch', async () => {
    const fixture = harness()
    fixture.unregisterDriver()
    fixture.coordinator.registerDriverBridge({
      evaluateActivatedSession: async () => ({
        kind: 'unavailable', reason: 'session_unavailable',
      }),
      cancelQueued: async () => ({
        kind: 'unavailable', reason: 'session_unavailable',
      }),
    })
    let mutationCalls = 0
    fixture.mutation = async () => {
      mutationCalls += 1
      fixture.activation = 'active'
      fixture.currentAgent = agentFixture().agent
      return {
        exitCode: 0,
        stdout: JSON.stringify(executionPayload('active')),
        stderr: '',
      }
    }

    const response = await fixture.service.handle(
      actionRequest('start'),
      new AbortController().signal,
    )

    expect(response.result).toMatchObject({
      kind: 'applied_with_warning',
      code: 'driver_sync_failed',
      snapshot: {
        sessionId,
        goalActivation: 'active',
        agentStatus: 'idle',
      },
    })
    expect(mutationCalls).toBe(1)
    expect(fixture.calls.filter(args => args.includes('--execute'))).toHaveLength(1)
    await fixture.service.dispose()
  })

  it('requires fresh double-id binding and exact transition legality', async () => {
    const fixture = harness({ status: 'running' })
    const runningStart = await fixture.service.handle(
      actionRequest('start'),
      new AbortController().signal,
    )
    expect(runningStart.result).toEqual({ kind: 'rejected', code: 'not_actionable' })
    expect(fixture.calls.some(args => args.includes('--execute'))).toBe(false)
    await fixture.service.dispose()

    const missing = harness()
    missing.bindingMode = 'missing'
    expect((await missing.service.handle(
      actionRequest('start'),
      new AbortController().signal,
    )).result).toEqual({ kind: 'rejected', code: 'binding_validation_failed' })
    await missing.service.dispose()

    const mismatch = harness()
    const request = actionRequest('start')
    const mismatched = {
      ...request,
      expected: { ...request.expected, goalId: 'goal-stale' },
    }
    expect((await mismatch.service.handle(
      mismatched,
      new AbortController().signal,
    )).result).toEqual({ kind: 'rejected', code: 'binding_mismatch' })
    await mismatch.service.dispose()
  })

  it('maps partial, malformed, nonzero, timeout, and abort mutation results to unknown', async () => {
    const cases: Array<() => Promise<FileResult>> = [
      async () => ({
        exitCode: 0,
        stdout: JSON.stringify(executionPayload('active', { partialWrite: true })),
        stderr: '',
      }),
      async () => ({ exitCode: 0, stdout: '{bad-json', stderr: '/private/path' }),
      async () => ({
        exitCode: 1,
        stdout: JSON.stringify(executionPayload('active')),
        stderr: 'raw stderr',
      }),
      async () => { throw new LoopXCliError('timeout', 'raw timeout', false) },
      async () => { throw new LoopXCliError('aborted', 'raw abort', false) },
    ]
    for (const mutation of cases) {
      const fixture = harness()
      fixture.mutation = mutation
      const response = await fixture.service.handle(
        actionRequest('start'),
        new AbortController().signal,
      )
      expect(response.result).toEqual({
        kind: 'unknown', code: 'operation_result_unknown',
      })
      expect(fixture.calls.filter(args => args.includes('--execute'))).toHaveLength(1)
      await fixture.service.dispose()
    }
  })

  it('returns driver and post-read warnings without rollback or snapshot leakage', async () => {
    const driverFailure = harness({ driver: 'unavailable' })
    const warned = await driverFailure.service.handle(
      actionRequest('start'),
      new AbortController().signal,
    )
    expect(warned.result).toMatchObject({
      kind: 'applied_with_warning',
      code: 'driver_sync_failed',
      snapshot: { goalActivation: 'active' },
    })
    expect(driverFailure.calls.filter(args => args.includes('--execute'))).toHaveLength(1)
    await driverFailure.service.dispose()

    const postFailure = harness()
    let bindingCalls = 0
    postFailure.onBinding = () => {
      bindingCalls += 1
      if (bindingCalls > 1) postFailure.bindingMode = 'missing'
    }
    const failedRead = await postFailure.service.handle(
      actionRequest('start'),
      new AbortController().signal,
    )
    expect(failedRead.result).toEqual({
      kind: 'applied_with_warning', code: 'post_read_failed',
    })
    expect(failedRead.result).not.toHaveProperty('snapshot')
    expect(postFailure.calls.filter(args => args.includes('--execute'))).toHaveLength(1)
    await postFailure.service.dispose()
  })
})

describe('lifecycle receipt decoder', () => {
  it('accepts changed/written false but rejects partial and additive readback', () => {
    expect(decodeGoalBarLifecycleExecutionV1(
      executionPayload('active'), goalId, 'active',
    )).toBe(true)
    expect(decodeGoalBarLifecycleExecutionV1(
      executionPayload('active', { partialWrite: true }), goalId, 'active',
    )).toBe(false)
    const additive = executionPayload('active')
    ;(additive.readback as Record<string, unknown>).private_path = '/private/path'
    expect(decodeGoalBarLifecycleExecutionV1(additive, goalId, 'active')).toBe(false)
  })
})
