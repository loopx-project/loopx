import { mkdtemp, mkdir, readFile, rm, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { LoopXCliError } from '../src/cli.ts'
import type { FileResult, FileRunner, LoopXCommand } from '../src/cli.ts'
import {
  decodeAgentLaneTodoProgressV0,
  decodeGoalActivationPreviewV1,
  decodeThreadAgentBindingResolutionV0,
  computeGoalBarSourceRevision,
  readGoalBarModel,
  readStableGoalBarModel,
} from '../src/goalbar/read-model.ts'

const sessionId = 'dsh-session-1'
const goalId = 'goal-one'
const loopxAgentId = 'codex-main-control'
const hostilePath = ['', 'sensitive-host', 'project'].join('/')
const strictRegistryFixture = new URL('./fixtures/project-registry-strict-v1.json', import.meta.url)

describe('GoalBar source revision', () => {
  it('covers existence, equal-size replacement, and the bound active state', async () => {
    const cwd = await mkdtemp(join(tmpdir(), 'loopx-goalbar-revision-'))
    try {
      const registryDir = join(cwd, '.loopx')
      const stateDir = join(cwd, '.loopx', 'goals', goalId)
      await mkdir(registryDir, { recursive: true })
      await mkdir(stateDir, { recursive: true })
      const registry = join(registryDir, 'registry.json')
      const state = join(stateDir, 'ACTIVE_GOAL_STATE.md')

      const missing = await computeGoalBarSourceRevision({ cwd })
      await writeFile(registry, '{"goals":[]}', 'utf8')
      const registryPresent = await computeGoalBarSourceRevision({ cwd })
      const exactBinding = { goalId, loopxAgentId }
      const boundMissing = await computeGoalBarSourceRevision({ cwd, ...exactBinding })
      await writeFile(state, 'state=one', 'utf8')
      const first = await computeGoalBarSourceRevision({ cwd, ...exactBinding })
      await writeFile(state, 'state=two', 'utf8')
      const equalSizeReplacement = await computeGoalBarSourceRevision({ cwd, ...exactBinding })

      expect(new Set([missing, registryPresent, boundMissing, first, equalSizeReplacement]).size)
        .toBe(5)
      expect(equalSizeReplacement).toMatch(/^sha256:[0-9a-f]{64}$/u)
      expect(await computeGoalBarSourceRevision({
        cwd,
        goalId,
        loopxAgentId: 'codex-side-control',
      })).not.toBe(equalSizeReplacement)
    } finally {
      await rm(cwd, { recursive: true, force: true })
    }
  })

  it('follows the registered legacy state and the migrated state without dual reads', async () => {
    const cwd = await mkdtemp(join(tmpdir(), 'loopx-goalbar-route-'))
    try {
      const registry = join(cwd, '.loopx', 'registry.json')
      const oldState = join(cwd, '.codex', 'goals', goalId, 'ACTIVE_GOAL_STATE.md')
      const newState = join(cwd, '.loopx', 'goals', goalId, 'ACTIVE_GOAL_STATE.md')
      await mkdir(join(cwd, '.loopx', 'goals', goalId), { recursive: true })
      await mkdir(join(cwd, '.codex', 'goals', goalId), { recursive: true })
      const binding = { cwd, goalId, loopxAgentId }
      await writeFile(oldState, 'old state', 'utf8')
      await writeFile(newState, 'new state', 'utf8')
      await writeFile(registry, JSON.stringify({ goals: [{ id: goalId,
        state_file: `.codex/goals/${goalId}/ACTIVE_GOAL_STATE.md` }] }), 'utf8')
      const legacy = await computeGoalBarSourceRevision(binding)
      await writeFile(newState, 'newer state', 'utf8')
      expect(await computeGoalBarSourceRevision(binding)).toBe(legacy)
      await writeFile(registry, JSON.stringify({ goals: [{ id: goalId,
        state_file: `.loopx/goals/${goalId}/ACTIVE_GOAL_STATE.md` }] }), 'utf8')
      const migrated = await computeGoalBarSourceRevision(binding)
      expect(migrated).not.toBe(legacy)
      await writeFile(newState, 'final state', 'utf8')
      expect(await computeGoalBarSourceRevision(binding)).not.toBe(migrated)
    } finally {
      await rm(cwd, { recursive: true, force: true })
    }
  })

  it('accepts the Python strict registry wire form and hashes only its declared legacy state', async () => {
    const cwd = await mkdtemp(join(tmpdir(), 'loopx-goalbar-strict-route-'))
    const registry = join(cwd, '.loopx', 'registry.json')
    const oldState = join(cwd, '.codex', 'goals', 'goal-fixture', 'ACTIVE_GOAL_STATE.md')
    const newState = join(cwd, '.loopx', 'goals', 'goal-fixture', 'ACTIVE_GOAL_STATE.md')
    try {
      await mkdir(join(cwd, '.loopx', 'goals', 'goal-fixture'), { recursive: true })
      await mkdir(join(cwd, '.codex', 'goals', 'goal-fixture'), { recursive: true })
      const fixture = await readFile(strictRegistryFixture)
      await writeFile(registry, fixture)
      await writeFile(oldState, 'legacy state', 'utf8')
      await writeFile(newState, 'decoy state', 'utf8')
      const binding = { cwd, goalId: 'goal-fixture', loopxAgentId }
      const before = await computeGoalBarSourceRevision(binding)
      await writeFile(newState, 'changed decoy', 'utf8')
      expect(await computeGoalBarSourceRevision(binding)).toBe(before)
      await writeFile(oldState, 'changed legacy', 'utf8')
      const changedState = await computeGoalBarSourceRevision(binding)
      expect(changedState).not.toBe(before)
      await writeFile(registry, Buffer.concat([fixture, Buffer.from(' ')]))
      expect(await computeGoalBarSourceRevision(binding)).not.toBe(changedState)
      await writeFile(registry, fixture.toString('utf8').replace('"fraction": 1.0', '"fraction": 1.00'))
      expect(await computeGoalBarSourceRevision(binding)).toMatch(/^sha256:[0-9a-f]{64}$/u)
    } finally {
      await rm(cwd, { recursive: true, force: true })
    }
  })

  it('rejects tampered and duplicate-key strict registry payloads', async () => {
    const cwd = await mkdtemp(join(tmpdir(), 'loopx-goalbar-strict-invalid-'))
    try {
      const registry = join(cwd, '.loopx', 'registry.json')
      await mkdir(join(cwd, '.loopx'), { recursive: true })
      const fixture = await readFile(strictRegistryFixture, 'utf8')
      const binding = { cwd, goalId: 'goal-fixture', loopxAgentId }
      await writeFile(registry, fixture.replace('goal-fixture', 'other-goal'), 'utf8')
      await expect(computeGoalBarSourceRevision(binding)).rejects.toThrow('digest')
      await writeFile(registry, fixture.replace(
        '"id": "goal-fixture",', '"id": "goal-fixture", "id": "goal-fixture",',
      ), 'utf8')
      await expect(computeGoalBarSourceRevision(binding)).rejects.toThrow('duplicate')
      await writeFile(registry, fixture.replace('envelope_v1', 'envelope_v2'), 'utf8')
      await expect(computeGoalBarSourceRevision(binding)).rejects.toThrow('lifecycle-only')
    } finally {
      await rm(cwd, { recursive: true, force: true })
    }
  })

  it('rejects unsafe goal path segments and non-normalized cwd values', async () => {
    await expect(computeGoalBarSourceRevision({
      cwd: '/fixture/project',
      goalId: '../outside',
    })).rejects.toThrow('safe path segment')
    await expect(computeGoalBarSourceRevision({ cwd: 'relative/project' }))
      .rejects.toThrow('absolute and normalized')
  })

  it('rejects an authoritative source symlink that resolves outside the project', async () => {
    const cwd = await mkdtemp(join(tmpdir(), 'loopx-goalbar-symlink-'))
    const outside = await mkdtemp(join(tmpdir(), 'loopx-goalbar-outside-'))
    try {
      await mkdir(join(cwd, '.loopx'), { recursive: true })
      const outsideRegistry = join(outside, 'registry.json')
      await writeFile(outsideRegistry, '{}', 'utf8')
      await symlink(outsideRegistry, join(cwd, '.loopx', 'registry.json'))
      await expect(computeGoalBarSourceRevision({ cwd }))
        .rejects.toThrow('outside project cwd')
    } finally {
      await rm(cwd, { recursive: true, force: true })
      await rm(outside, { recursive: true, force: true })
    }
  })
})

const command: LoopXCommand = {
  file: 'loopx',
  prefix: [],
  skillCommand: 'loopx',
  version: 'loopx 0.5.0',
}

function jsonResult(payload: unknown, exitCode = 0, stderr = ''): FileResult {
  return { exitCode, stdout: JSON.stringify(payload), stderr }
}

function bindingPayload(
  status: 'missing' | 'bound' | 'ambiguous' | 'unavailable' = 'bound',
): Record<string, unknown> {
  const base = {
    schema_version: 'loopx_thread_agent_binding_resolution_v0',
    host_surface: 'deepseek-harness-native',
    thread_id: sessionId,
  }
  if (status === 'missing') {
    return {
      ...base,
      ok: true,
      status,
      goal_id: null,
      agent_id: null,
      matches: [],
    }
  }
  if (status === 'bound') {
    return {
      ...base,
      ok: true,
      status,
      goal_id: goalId,
      agent_id: loopxAgentId,
      matches: [{ goal_id: goalId, agent_id: loopxAgentId }],
    }
  }
  if (status === 'ambiguous') {
    return {
      ...base,
      ok: false,
      status,
      goal_id: null,
      agent_id: null,
      matches: [
        { goal_id: goalId, agent_id: loopxAgentId },
        { goal_id: 'goal-two', agent_id: 'codex-side-control' },
      ],
      error_kind: 'thread_agent_binding_ambiguous',
      error: 'host thread is bound to more than one Goal or Agent',
    }
  }
  return {
    ...base,
    ok: false,
    status,
    goal_id: null,
    agent_id: null,
    matches: [],
    error_kind: 'thread_agent_binding_resolution_failed',
    error: 'thread binding authority could not be read',
  }
}

function activationPayload(
  activation: 'active' | 'stopped' = 'active',
): Record<string, unknown> {
  const changed = activation === 'active'
  return {
    ok: true,
    schema_version: 'loopx_goal_activation_transition_v1',
    dry_run: true,
    execute: false,
    goal_id: goalId,
    before_state: activation,
    after_state: 'stopped',
    changed,
    written: false,
    partial_write: false,
    source_registry: `${hostilePath}/.loopx/registry.json`,
    activation: { reason: 'RAW_LIFECYCLE_REASON_SENTINEL' },
    readback: {
      schema_version: 'loopx_goal_activation_readback_v1',
      status: changed ? 'not_executed' : 'not_required',
      verified: !changed,
    },
  }
}

function todoPayload(options: {
  readonly processed?: number
  readonly remaining?: number
  readonly item?: Record<string, unknown> | undefined
} = {}): Record<string, unknown> {
  const processed = options.processed ?? 17
  const remaining = options.remaining ?? 4
  const items = options.item === undefined ? [] : [options.item]
  const total = processed + remaining
  return {
    ok: true,
    dry_run: true,
    read_only: true,
    command: 'list',
    goal_id: goalId,
    role: 'agent',
    status_filter: null,
    source: 'markdown_active_state',
    todo_count: items.length,
    todos: items,
    state_file: `${hostilePath}/.local/goals/goal-one/ACTIVE_GOAL_STATE.md`,
    project: hostilePath,
    agent_id_filter: loopxAgentId,
    explicit_limit: 1,
    returned_todo_count: items.length,
    todo_list_projection: {
      schema_version: 'agent_lane_todo_list_projection_v0',
      view: 'explicit_limit_cold_path',
      matched_todo_count: total,
      returned_todo_count: items.length,
      item_limit_per_role: 1,
      counts_cover_full_match: true,
      full_detail_cold_paths: ['todo list without --limit', 'active state'],
    },
    agent_todos: {
      schema_version: 'todo_summary_v0',
      total_count: total,
      open_count: remaining,
      done_count: processed,
      items,
    },
  }
}

function readFixture(options: {
  readonly binding?: FileResult | undefined
  readonly activation?: FileResult | undefined
  readonly todo?: FileResult | undefined
} = {}): {
  readonly runner: FileRunner
  readonly calls: Array<{ readonly args: readonly string[]; readonly cwd: string | undefined }>
} {
  const calls: Array<{ args: readonly string[]; cwd: string | undefined }> = []
  return {
    calls,
    runner: async (_file, args, runOptions) => {
      calls.push({ args: [...args], cwd: runOptions.cwd })
      if (args.includes('resolve-agent-thread')) {
        return options.binding ?? jsonResult(bindingPayload())
      }
      if (args.includes('goal-lifecycle')) {
        return options.activation ?? jsonResult(activationPayload())
      }
      if (args.includes('todo')) {
        return options.todo ?? jsonResult(todoPayload({
          item: {
            todo_id: 'todo_agent_open_001',
            status: 'open',
            text: 'RAW_TODO_TEXT_SENTINEL',
            note: hostilePath,
          },
        }))
      }
      throw new Error('unexpected argv')
    },
  }
}

describe('thread binding V0 decoding', () => {
  it('accepts the authoritative 0/1/>1 relations', () => {
    expect(decodeThreadAgentBindingResolutionV0(
      bindingPayload('missing'), sessionId, 0,
    )).toEqual({ kind: 'missing' })
    expect(decodeThreadAgentBindingResolutionV0(
      bindingPayload('bound'), sessionId, 0,
    )).toEqual({ kind: 'bound', goalId, loopxAgentId })
    expect(decodeThreadAgentBindingResolutionV0(
      bindingPayload('ambiguous'), sessionId, 1,
    )).toEqual({ kind: 'ambiguous', uniquePairCount: 2 })
    expect(decodeThreadAgentBindingResolutionV0(
      bindingPayload('unavailable'), sessionId, 1,
    )).toEqual({ kind: 'unavailable' })
  })

  it('deduplicates identical resolver pairs before admission', () => {
    const duplicate = bindingPayload('bound')
    duplicate.matches = [
      { goal_id: goalId, agent_id: loopxAgentId },
      { goal_id: goalId, agent_id: loopxAgentId },
    ]
    expect(decodeThreadAgentBindingResolutionV0(duplicate, sessionId, 0)).toEqual({
      kind: 'bound', goalId, loopxAgentId,
    })

    const ambiguous = bindingPayload('ambiguous')
    ambiguous.matches = [
      { goal_id: goalId, agent_id: loopxAgentId },
      { goal_id: goalId, agent_id: loopxAgentId },
      { goal_id: 'goal-two', agent_id: 'codex-side-control' },
    ]
    expect(decodeThreadAgentBindingResolutionV0(ambiguous, sessionId, 1)).toEqual({
      kind: 'ambiguous', uniquePairCount: 2,
    })
  })

  it('fails closed on schema, echo, relation, status, and exit mismatches', () => {
    const mutations: Array<(payload: Record<string, unknown>) => void> = [
      payload => { payload.schema_version = 'old' },
      payload => { payload.host_surface = 'codex-app' },
      payload => { payload.thread_id = 'another-session' },
      payload => { payload.goal_id = 'another-goal' },
      payload => { payload.status = 'unbound' },
      payload => { payload.matches = [{ goal_id: goalId, agent_id: 'INVALID AGENT' }] },
    ]
    for (const mutate of mutations) {
      const payload = bindingPayload('bound')
      mutate(payload)
      expect(decodeThreadAgentBindingResolutionV0(payload, sessionId, 0)).toBeUndefined()
    }
    expect(decodeThreadAgentBindingResolutionV0(
      bindingPayload('bound'), sessionId, 1,
    )).toBeUndefined()
    expect(decodeThreadAgentBindingResolutionV0(
      bindingPayload('ambiguous'), sessionId, 0,
    )).toBeUndefined()
  })
})

describe('activation preview V1 decoding', () => {
  it('accepts only the two exact zero-write stop-preview relations', () => {
    expect(decodeGoalActivationPreviewV1(
      activationPayload('active'), goalId, 0,
    )).toBe('active')
    expect(decodeGoalActivationPreviewV1(
      activationPayload('stopped'), goalId, 0,
    )).toBe('stopped')
  })

  it('does not infer activation from prose, registry status, or malformed readback', () => {
    const mutations: Array<(payload: Record<string, unknown>) => void> = [
      payload => { payload.goal_id = 'another-goal' },
      payload => { payload.dry_run = false },
      payload => { payload.execute = true },
      payload => { payload.written = true },
      payload => { payload.partial_write = true },
      payload => { payload.after_state = 'active' },
      payload => { payload.changed = false },
      payload => { payload.status = 'stopped'; payload.before_state = undefined },
      payload => { (payload.readback as Record<string, unknown>).status = 'verified' },
    ]
    for (const mutate of mutations) {
      const payload = activationPayload('active')
      mutate(payload)
      expect(decodeGoalActivationPreviewV1(payload, goalId, 0)).toBeUndefined()
    }
    expect(decodeGoalActivationPreviewV1(
      activationPayload('active'), goalId, 1,
    )).toBeUndefined()
  })
})

describe('Agent-lane Todo projection V0 decoding', () => {
  it('uses full counts above twelve while discarding the one returned item', () => {
    const payload = todoPayload({
      processed: 17,
      remaining: 4,
      item: {
        status: 'blocked',
        text: 'RAW_TODO_TEXT_SENTINEL',
        note: hostilePath,
      },
    })
    const progress = decodeAgentLaneTodoProgressV0(
      payload, goalId, loopxAgentId, 0,
    )
    expect(progress).toEqual({ processed: 17, remaining: 4, total: 21 })
    expect(JSON.stringify(progress)).not.toContain('RAW_TODO_TEXT_SENTINEL')
    expect(JSON.stringify(progress)).not.toContain(hostilePath)
  })

  it('projects deferred as processed, blocked as remaining, and an empty lane as 0/0', () => {
    expect(decodeAgentLaneTodoProgressV0(
      todoPayload({
        processed: 2,
        remaining: 1,
        item: { status: 'deferred', text: 'cold detail' },
      }),
      goalId,
      loopxAgentId,
      0,
    )).toEqual({ processed: 2, remaining: 1, total: 3 })

    expect(decodeAgentLaneTodoProgressV0(
      todoPayload({
        processed: 1,
        remaining: 2,
        item: { status: 'blocked', text: 'cold blocker' },
      }),
      goalId,
      loopxAgentId,
      0,
    )).toEqual({ processed: 1, remaining: 2, total: 3 })

    expect(decodeAgentLaneTodoProgressV0(
      todoPayload({ processed: 0, remaining: 0 }),
      goalId,
      loopxAgentId,
      0,
    )).toEqual({ processed: 0, remaining: 0, total: 0 })
  })

  it('rejects booleans, negative/unsafe integers, overflow, and broken count relations', () => {
    const mutateAndReject = (mutate: (payload: Record<string, unknown>) => void): void => {
      const payload = todoPayload({ processed: 2, remaining: 3 })
      mutate(payload)
      expect(decodeAgentLaneTodoProgressV0(
        payload, goalId, loopxAgentId, 0,
      )).toBeUndefined()
    }
    mutateAndReject(payload => {
      (payload.agent_todos as Record<string, unknown>).done_count = true
    })
    mutateAndReject(payload => {
      (payload.agent_todos as Record<string, unknown>).open_count = -1
    })
    mutateAndReject(payload => {
      (payload.agent_todos as Record<string, unknown>).total_count = Number.MAX_SAFE_INTEGER + 1
    })
    mutateAndReject(payload => {
      (payload.agent_todos as Record<string, unknown>).done_count = Number.MAX_SAFE_INTEGER
      ;(payload.agent_todos as Record<string, unknown>).open_count = 1
      ;(payload.agent_todos as Record<string, unknown>).total_count = Number.MAX_SAFE_INTEGER
      ;(payload.todo_list_projection as Record<string, unknown>).matched_todo_count = Number.MAX_SAFE_INTEGER
    })
    mutateAndReject(payload => {
      (payload.todo_list_projection as Record<string, unknown>).matched_todo_count = 6
    })
    mutateAndReject(payload => { payload.returned_todo_count = 2 })
    mutateAndReject(payload => { payload.explicit_limit = 12 })
    mutateAndReject(payload => { payload.role = 'all' })
  })
})

describe('GoalBar read orchestration', () => {
  it('stops after zero or ambiguous bindings', async () => {
    for (const [status, exitCode, expected] of [
      ['missing', 0, { kind: 'hidden', reason: 'binding_missing' }],
      ['ambiguous', 1, {
        kind: 'hidden', reason: 'binding_ambiguous', uniquePairCount: 2,
      }],
    ] as const) {
      const fixture = readFixture({
        binding: jsonResult(bindingPayload(status), exitCode),
      })
      await expect(readGoalBarModel({
        command,
        cwd: '/project',
        runner: fixture.runner,
        retryDelaysMs: [0, 0],
        sessionId,
        agentStatus: 'idle',
      })).resolves.toEqual(expected)
      expect(fixture.calls).toHaveLength(1)
    }
  })

  it('runs fixed read-only argv and emits only the minimal snapshot', async () => {
    const fixture = readFixture()
    const result = await readGoalBarModel({
      command,
      cwd: '/project',
      runner: fixture.runner,
      retryDelaysMs: [0, 0],
      sessionId,
      agentStatus: 'running',
    })

    expect(result).toEqual({
      kind: 'present',
      snapshot: {
        sessionId,
        goalId,
        loopxAgentId,
        goalActivation: 'active',
        agentStatus: 'running',
        progress: { processed: 17, remaining: 4, total: 21 },
      },
    })
    expect(JSON.stringify(result)).not.toContain(hostilePath)
    expect(JSON.stringify(result)).not.toContain('RAW_TODO_TEXT_SENTINEL')
    expect(fixture.calls.every(call => call.cwd === '/project')).toBe(true)
    expect(fixture.calls.map(call => call.args)).toContainEqual([
      '--registry', '.loopx/registry.json',
      '--format', 'json',
      'resolve-agent-thread',
      '--host-surface', 'deepseek-harness-native',
      '--thread-id', sessionId,
    ])
    expect(fixture.calls.map(call => call.args)).toContainEqual([
      '--registry', '.loopx/registry.json',
      '--format', 'json',
      'goal-lifecycle',
      '--goal-id', goalId,
      '--operation', 'stop',
    ])
    expect(fixture.calls.map(call => call.args)).toContainEqual([
      '--registry', '.loopx/registry.json',
      '--format', 'json',
      'todo', 'list',
      '--goal-id', goalId,
      '--role', 'agent',
      '--agent-id', loopxAgentId,
      '--limit', '1',
    ])
    expect(fixture.calls.flatMap(call => call.args)).not.toContain('--execute')
  })

  it('maps hostile failures to fixed codes without reflecting payload, stderr, or stacks', async () => {
    const fixture = readFixture({
      binding: jsonResult({
        ...bindingPayload('bound'),
        status: 'unknown',
        stdout: 'RAW_TODO_TEXT_SENTINEL',
        stack: `RAW_STACK_SENTINEL at ${hostilePath}/src/index.ts`,
      }, 1, `${hostilePath}: RAW_STDERR_SENTINEL`),
    })
    const result = await readGoalBarModel({
      command,
      cwd: '/project',
      runner: fixture.runner,
      retryDelaysMs: [0, 0],
      sessionId,
      agentStatus: 'idle',
    })
    expect(result).toEqual({ kind: 'fault', code: 'binding_read_failed' })
    expect(JSON.stringify(result)).not.toMatch(/RAW_(?:TODO|STACK|STDERR)_/u)
  })

  it('maps a missing executable and invalid session input to fixed faults', async () => {
    await expect(readGoalBarModel({
      command,
      cwd: '/project',
      runner: async () => {
        throw new LoopXCliError('missing', 'raw executable path', false)
      },
      retryDelaysMs: [0, 0],
      sessionId,
      agentStatus: 'idle',
    })).resolves.toEqual({ kind: 'fault', code: 'cli_unavailable' })

    let calls = 0
    await expect(readGoalBarModel({
      command,
      cwd: '/project',
      runner: async () => {
        calls += 1
        return jsonResult(bindingPayload())
      },
      sessionId: 'invalid session',
      agentStatus: 'idle',
    })).resolves.toEqual({ kind: 'fault', code: 'protocol_mismatch' })
    expect(calls).toBe(0)
  })
})

describe('GoalBar stable observation guards', () => {
  it('rebinds when registry changes after exact sourceBefore is observed', async () => {
    const fixture = readFixture()
    const registryA = `sha256:${'a'.repeat(64)}`
    const mixedExact = `sha256:${'b'.repeat(64)}`
    const registryB = `sha256:${'c'.repeat(64)}`
    const exactB = `sha256:${'d'.repeat(64)}`
    const revisions = [
      registryA,
      registryA,
      mixedExact,
      registryB,
      registryB,
      registryB,
      exactB,
      registryB,
      exactB,
    ]
    let revisionCalls = 0

    const result = await readStableGoalBarModel({
      command,
      cwd: '/project',
      runner: fixture.runner,
      sessionId,
      agentStatus: 'idle',
    }, async () => revisions[revisionCalls++] as string)

    expect(result.model.kind).toBe('present')
    expect(result.sourceRevision).toBe(exactB)
    expect(fixture.calls.filter(call => call.args.includes('resolve-agent-thread')))
      .toHaveLength(2)
    expect(fixture.calls.filter(call => call.args.includes('goal-lifecycle')))
      .toHaveLength(1)
    expect(fixture.calls.filter(call => call.args.includes('todo')))
      .toHaveLength(1)
    expect(revisionCalls).toBe(revisions.length)
  })
})
