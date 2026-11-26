import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it, vi } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import type { Agent, AgentStatus } from '@deepseek-ai/dsh-agent'
import type { MessageId, ToolCallId, UserMessage } from '@deepseek-ai/dsh-llm'
import { SessionStore } from '@deepseek-ai/dsh-session'
import type {
  Session,
  SessionSeq,
} from '@deepseek-ai/dsh-session'
import type { FileRunner } from '../src/cli.ts'
import { LoopXContinuationDriver } from '../src/driver.ts'
import type {
  TodoUnitCompletionReceipt,
  TodoUnitObsRunner,
} from '../src/driver.ts'
import { deriveMessagesPrefixDigest } from '../src/todo-unit-fold.ts'

async function createRealSessionHarness(cwd: string) {
  const ctx = new Context()
  const storeFiber = await ctx.plugin(SessionStore)
  const sessionId = 'session-todo-fold-real' as Agent['id']
  const session = storeFiber.ctx.sessions.create(sessionId, { meta: { cwd } })
  let status: AgentStatus = 'idle'
  const queued: UserMessage[] = []
  const nextStep: UserMessage[] = []
  const agent = {
    id: sessionId,
    options: {},
    session,
    inbox: {
      nextTurn: queued,
      nextStep,
      get hasPending() { return queued.length > 0 || nextStep.length > 0 },
      remove: (id: UserMessage['id']) => {
        for (const list of [nextStep, queued]) {
          const idx = list.findIndex(m => m.id === id)
          if (idx >= 0) {
            list.splice(idx, 1)
            return true
          }
        }
        return false
      },
    },
    ctx,
    get status() { return status },
    cancel() {},
    whenIdle: async () => {},
    runMaintenance: async <T>(fn: (signal: AbortSignal) => Promise<T>) => fn(new AbortController().signal),
    send() {},
    followup(msg: UserMessage) { queued.push(msg) },
    steer() {},
    inject() {},
  } as unknown as Agent

  return {
    ctx,
    session,
    agent,
    queued,
    setStatus(next: AgentStatus) { status = next },
    async dispose() {
      await storeFiber.dispose()
      await ctx.fiber.dispose()
    },
  }
}

function appendPrefixQ(session: Session): SessionSeq[] {
  session.append('system/message', {
    turn: 1,
    step: 1,
    message: {
      id: 'sys-1' as MessageId,
      role: 'system',
      content: [{ type: 'text', text: 'System instructions.' }],
      source: { kind: 'system-prompt' },
    },
  }, { surfaceOp: 'append' })

  session.append('user/message', {
    id: 'user-q-1' as UserMessage['id'],
    role: 'user',
    content: [{ type: 'text', text: 'Parent goal prompt Q.' }],
    source: { kind: 'user' },
  }, { surfaceOp: 'append' })

  const callId = 'call-q-1' as ToolCallId
  session.append('tool/call', {
    turn: 1,
    step: 1,
    callId,
    name: 'read_file',
    arguments: '{"path":"README.md"}',
  })
  session.append('assistant/message', {
    turn: 1,
    step: 1,
    message: {
      id: 'asst-q-1' as MessageId,
      role: 'assistant',
      content: [{ type: 'tool-call', id: callId, name: 'read_file', arguments: '{"path":"README.md"}' } as unknown as { type: 'text'; text: string }],
      source: { kind: 'model', provider: 'mock', model: 'mock-1' },
    },
    stream: [],
  }, { surfaceOp: 'append' })
  session.append('tool/result', {
    turn: 1,
    step: 1,
    message: {
      id: 'res-q-1' as MessageId,
      role: 'tool',
      toolCallId: callId,
      content: [{ type: 'text', text: 'README summary content.' }],
      source: { kind: 'tool', callId },
    },
  }, { surfaceOp: 'append' })

  return [...session.surface.nodes]
}

function appendTodoExploratorySuffix(session: Session, suffixTag: string, turn = 2): SessionSeq[] {
  const before = session.surface.nodes.length
  const callId = `call-${suffixTag}-1` as ToolCallId
  const bloat = `exploratory search output for ${suffixTag} `.repeat(80)
  session.append('user/message', {
    id: `user-${suffixTag}` as UserMessage['id'],
    role: 'user',
    content: [{ type: 'text', text: `Start Todo ${suffixTag}` }],
    source: { kind: 'user' },
  }, { surfaceOp: 'append' })
  session.append('tool/call', {
    turn,
    step: 1,
    callId,
    name: 'search',
    arguments: '{"q":"loopx"}',
  })
  session.append('assistant/message', {
    turn,
    step: 1,
    message: {
      id: `asst-${suffixTag}-1` as MessageId,
      role: 'assistant',
      content: [{ type: 'tool-call', id: callId, name: 'search', arguments: '{"q":"loopx"}' } as unknown as { type: 'text'; text: string }],
      source: { kind: 'model', provider: 'mock', model: 'mock-1' },
    },
    stream: [],
  }, { surfaceOp: 'append' })
  session.append('tool/result', {
    turn,
    step: 1,
    message: {
      id: `res-${suffixTag}-1` as MessageId,
      role: 'tool',
      toolCallId: callId,
      content: [{ type: 'text', text: bloat }],
      source: { kind: 'tool', callId },
    },
  }, { surfaceOp: 'append' })
  session.append('assistant/message', {
    turn,
    step: 2,
    message: {
      id: `asst-${suffixTag}-2` as MessageId,
      role: 'assistant',
      content: [{ type: 'text', text: `Completed analysis for ${suffixTag}.` }],
      source: { kind: 'model', provider: 'mock', model: 'mock-1' },
    },
    stream: [],
  }, { surfaceOp: 'append' })
  return session.surface.nodes.slice(before)
}

const VALID_RECEIPT: TodoUnitCompletionReceipt = {
  todoId: 'todo-s1-1',
  completionReceiptId: 'cqr-s1-1',
  exitCode: 0,
  sha256: 'a'.repeat(64),
  failureFacts: ['earlier test warned on missing flag'],
  openObligations: ['run downstream package verification'],
  sourceRef: { path: 'captures/todo-s1-1.json', sha256: 'b'.repeat(64) },
}

describe('dsh-loopx-plugin Todo semantic-unit FoldAndPrefetch', () => {
  it('stays strictly default-off with zero surface or KV prefix drift', async () => {
    const cwd = await mkdtemp(join(tmpdir(), 'dsh-fold-off-'))
    const harness = await createRealSessionHarness(cwd)
    try {
      appendPrefixQ(harness.session)
      appendTodoExploratorySuffix(harness.session, 'off')
      const beforeNodes = [...harness.session.surface.nodes]
      const beforeMessages = harness.session.deriveMessages()

      const driver = new LoopXContinuationDriver({ isLiveAgent: () => true })
      expect(driver.anchorTodoUnit(harness.agent, {
        todoId: 'todo-s1-1',
        turnInstanceId: 'turn-s1-1',
        contextEpoch: 'epoch-1',
        anchorStepIndex: 2,
      })).toBeNull()

      const outcome = await driver.onTodoUnitSettled(harness.agent, {
        todoId: 'todo-s1-1',
        turnInstanceId: 'turn-s1-1',
        contextEpoch: 'epoch-1',
        nowStepIndex: 5,
        completionReceipt: VALID_RECEIPT,
      })
      expect(outcome.operator).toBe('KeepRaw')
      expect(outcome.status).toBe('disabled')
      expect(harness.session.surface.nodes).toEqual(beforeNodes)
      expect(harness.session.deriveMessages()).toEqual(beforeMessages)
    } finally {
      await harness.dispose()
      await rm(cwd, { recursive: true, force: true })
    }
  })

  it('pops contiguous exploratory suffix [anchor..now] -> [mu + Obs(next_cmd)] while keeping parent prefix Q byte-identical', async () => {
    const cwd = await mkdtemp(join(tmpdir(), 'dsh-fold-on-'))
    const harness = await createRealSessionHarness(cwd)
    try {
      const prefixSeqs = appendPrefixQ(harness.session)
      const prefixMessagesBefore = harness.session.deriveMessages()
      const prefixDigestBefore = deriveMessagesPrefixDigest(harness.session, prefixSeqs.length)

      const obsRunner = vi.fn<TodoUnitObsRunner>(async () => ({
        exitCode: 0,
        output: 'NEXT_CMD_READY: clean git tree',
      }))
      const driver = new LoopXContinuationDriver({
        isLiveAgent: () => true,
        todoUnitFold: { enabled: true, minNetSavingsTokens: 64 },
        runObsCommand: obsRunner,
      })

      const anchor = driver.anchorTodoUnit(harness.agent, {
        todoId: 'todo-s1-1',
        turnInstanceId: 'turn-s1-1',
        contextEpoch: 'epoch-dsh-1',
        anchorStepIndex: 2,
      })
      expect(anchor).not.toBeNull()
      expect(anchor?.prefixDigest).toBe(prefixDigestBefore)

      const suffixSeqs = appendTodoExploratorySuffix(harness.session, 'fold')
      expect(harness.session.surface.nodes).toEqual([...prefixSeqs, ...suffixSeqs])

      const outcome = await driver.onTodoUnitSettled(harness.agent, {
        todoId: 'todo-s1-1',
        turnInstanceId: 'turn-s1-1',
        contextEpoch: 'epoch-dsh-1',
        nowStepIndex: 6,
        completionReceipt: VALID_RECEIPT,
        obsNextCmd: { command: ['git', 'status', '--short'] },
      })

      expect(outcome.operator).toBe('FoldToReceipt')
      expect(outcome.status).toBe('committed')
      expect(outcome.preservesPrefixKv).toBe(true)
      expect(outcome.reclaimRange).toEqual([2, 6])
      expect(outcome.reclaimSeqRange).toEqual([suffixSeqs[0], suffixSeqs.at(-1)])
      expect(outcome.netSavingsTokens).toBeGreaterThan(200)
      expect(obsRunner).toHaveBeenCalledTimes(1)

      // Parent prefix Q nodes and derived messages are byte-identical (KV cache intact).
      expect(harness.session.surface.nodes.slice(0, prefixSeqs.length)).toEqual(prefixSeqs)
      expect(harness.session.deriveMessages().slice(0, prefixSeqs.length)).toEqual(prefixMessagesBefore)
      expect(deriveMessagesPrefixDigest(harness.session, prefixSeqs.length)).toBe(prefixDigestBefore)

      // Contiguous suffix replaced by exact single [mu + Obs(next_cmd)] node.
      expect(harness.session.surface.nodes.length).toBe(prefixSeqs.length + 1)
      const replacementMsg = harness.session.deriveMessages().at(-1) as UserMessage
      expect(replacementMsg.role).toBe('user')
      expect(replacementMsg.source.kind).toBe('loopx-todo-fold')
      const blockText = (replacementMsg.content[0] as { type: 'text'; text: string }).text
      expect(blockText).toContain('[Semantic-Unit Fold Receipt mu | trust=tool]')
      expect(blockText).toContain('run downstream package verification')
      expect(blockText).toContain('--- obs_next_cmd ---')
      expect(blockText).toContain('NEXT_CMD_READY: clean git tree')

      // Idempotent duplicate settlement replays without running obsNextCmd again.
      const replay = await driver.foldAndPrefetch(harness.agent, {
        todoId: 'todo-s1-1',
        turnInstanceId: 'turn-s1-1',
        contextEpoch: 'epoch-dsh-1',
        nowStepIndex: 6,
        completionReceipt: VALID_RECEIPT,
        obsNextCmd: { command: ['git', 'status', '--short'] },
      })
      expect(replay.foldedEventSeq).toBe(outcome.foldedEventSeq)
      expect(obsRunner).toHaveBeenCalledTimes(1)
    } finally {
      await harness.dispose()
      await rm(cwd, { recursive: true, force: true })
    }
  })

  it('fails closed atomically to KeepRaw on unpaired tool-call cuts, missing receipt, obs error, negative savings, or busy agent', async () => {
    const cwd = await mkdtemp(join(tmpdir(), 'dsh-fold-fail-'))
    const harness = await createRealSessionHarness(cwd)
    try {
      // Build parent prefix with an assistant tool-call whose tool/result lands AFTER the anchor cut!
      harness.session.append('system/message', {
        turn: 1,
        step: 1,
        message: {
          id: 'sys-split' as MessageId,
          role: 'system',
          content: [{ type: 'text', text: 'System instructions.' }],
          source: { kind: 'system-prompt' },
        },
      }, { surfaceOp: 'append' })
      const splitCallId = 'call-split' as ToolCallId
      harness.session.append('tool/call', {
        turn: 1,
        step: 1,
        callId: splitCallId,
        name: 'probe',
        arguments: '{}',
      })
      harness.session.append('assistant/message', {
        turn: 1,
        step: 1,
        message: {
          id: 'asst-split' as MessageId,
          role: 'assistant',
          content: [{ type: 'tool-call', id: splitCallId, name: 'probe', arguments: '{}' } as unknown as { type: 'text'; text: string }],
          source: { kind: 'model', provider: 'mock', model: 'mock-1' },
        },
        stream: [],
      }, { surfaceOp: 'append' })

      const obsRunner = vi.fn<TodoUnitObsRunner>(async () => ({ exitCode: 1, output: 'boom' }))
      const driver = new LoopXContinuationDriver({
        isLiveAgent: () => true,
        todoUnitFold: { enabled: true, minNetSavingsTokens: 64 },
        runObsCommand: obsRunner,
      })

      // Anchor BEFORE matching tool/result lands -> cut splits assistant tool-call from tool/result!
      driver.anchorTodoUnit(harness.agent, {
        todoId: 'todo-split',
        turnInstanceId: 'turn-split',
        contextEpoch: 'epoch-split',
        anchorStepIndex: 1,
      })
      harness.session.append('tool/result', {
        turn: 1,
        step: 1,
        message: {
          id: 'res-split' as MessageId,
          role: 'tool',
          toolCallId: splitCallId,
          content: [{ type: 'text', text: 'huge result '.repeat(100) }],
          source: { kind: 'tool', callId: splitCallId },
        },
      }, { surfaceOp: 'append' })
      const beforeNodes = [...harness.session.surface.nodes]
      const splitRes = await driver.onTodoUnitSettled(harness.agent, {
        todoId: 'todo-split',
        turnInstanceId: 'turn-split',
        contextEpoch: 'epoch-split',
        nowStepIndex: 3,
        completionReceipt: { ...VALID_RECEIPT, todoId: 'todo-split' },
        obsNextCmd: { command: ['git', 'status'] },
      })
      expect(splitRes.operator).toBe('KeepRaw')
      expect(splitRes.fallbackReason).toBe('unpaired_tool_calls')
      expect(harness.session.surface.nodes).toEqual(beforeNodes)
      expect(obsRunner).not.toHaveBeenCalled()

      // Now re-anchor AFTER split pair closes cleanly -> test missing receipt, busy agent, obs failure, negative savings.
      driver.anchorTodoUnit(harness.agent, {
        todoId: 'todo-s1-1',
        turnInstanceId: 'turn-s1-1',
        contextEpoch: 'epoch-clean',
        anchorStepIndex: 3,
      })
      appendTodoExploratorySuffix(harness.session, 'clean')
      const cleanNodesBefore = [...harness.session.surface.nodes]

      harness.setStatus('running')
      const busyRes = await driver.onTodoUnitSettled(harness.agent, {
        todoId: 'todo-s1-1',
        turnInstanceId: 'turn-s1-1',
        contextEpoch: 'epoch-clean',
        nowStepIndex: 6,
        completionReceipt: VALID_RECEIPT,
      })
      expect(busyRes.operator).toBe('KeepRaw')
      expect(busyRes.fallbackReason).toBe('non_quiescent_boundary')
      expect(harness.session.surface.nodes).toEqual(cleanNodesBefore)
      harness.setStatus('idle')

      const missingRes = await driver.onTodoUnitSettled(harness.agent, {
        todoId: 'todo-s1-1',
        turnInstanceId: 'turn-s1-1',
        contextEpoch: 'epoch-clean',
        nowStepIndex: 6,
      })
      expect(missingRes.operator).toBe('KeepRaw')
      expect(missingRes.fallbackReason).toBe('missing_completion_receipt')
      expect(harness.session.surface.nodes).toEqual(cleanNodesBefore)

      const obsErrRes = await driver.onTodoUnitSettled(harness.agent, {
        todoId: 'todo-s1-1',
        turnInstanceId: 'turn-s1-1',
        contextEpoch: 'epoch-clean',
        nowStepIndex: 6,
        completionReceipt: VALID_RECEIPT,
        obsNextCmd: { command: ['git', 'status'] },
      })
      expect(obsErrRes.operator).toBe('KeepRaw')
      expect(obsErrRes.fallbackReason).toBe('command_error')
      expect(obsRunner).toHaveBeenCalledTimes(1)
      expect(harness.session.surface.nodes).toEqual(cleanNodesBefore)

      const negSavingsRes = await driver.onTodoUnitSettled(harness.agent, {
        todoId: 'todo-s1-1',
        turnInstanceId: 'turn-s1-1',
        contextEpoch: 'epoch-clean',
        nowStepIndex: 6,
        completionReceipt: VALID_RECEIPT,
        baselineTokens: 200,
        replacementTokens: 250,
      })
      expect(negSavingsRes.operator).toBe('KeepRaw')
      expect(negSavingsRes.fallbackReason).toBe('negative_net_savings')
      expect(harness.session.surface.nodes).toEqual(cleanNodesBefore)
    } finally {
      await harness.dispose()
      await rm(cwd, { recursive: true, force: true })
    }
  })

  it('keeps automatic multi-Todo execution strictly default-off with zero settlement calls or surface rewrites', async () => {
    const cwd = await mkdtemp(join(tmpdir(), 'dsh-auto-fold-off-'))
    const harness = await createRealSessionHarness(cwd)
    try {
      const prefixSeqs = appendPrefixQ(harness.session)
      const cliLog: string[] = []
      let currentTodo = 'todo_1'
      let stepSeq = 0
      const runner: FileRunner = async (_file, args) => {
        cliLog.push(args.join(' '))
        if (args.includes('resolve-agent-thread')) {
          return {
            exitCode: 0,
            stderr: '',
            stdout: JSON.stringify({
              schema_version: 'loopx_thread_agent_binding_resolution_v0',
              ok: true,
              status: 'bound',
              host_surface: 'deepseek-harness-native',
              thread_id: harness.agent.id,
              goal_id: 'goal-auto',
              agent_id: 'agent-auto',
              matches: [{ goal_id: 'goal-auto', agent_id: 'agent-auto' }],
            }),
          }
        }
        if (args.includes('quota') && args.includes('should-run')) {
          const turnIdx = args.indexOf('--turn-instance-id')
          const turnId = turnIdx >= 0 ? args[turnIdx + 1]! : 'turn-x'
          return {
            exitCode: 0,
            stderr: '',
            stdout: JSON.stringify({
              ok: true,
              mode: 'should-run',
              goal_id: 'goal-auto',
              should_run: true,
              agent_identity: { agent_id: 'agent-auto', registered: true },
              heartbeat_receipt: {
                schema_version: 'heartbeat_receipt_v0',
                status: 'committed',
                turn_instance_id: turnId,
                settlement_identity: { goal_id: 'goal-auto', agent_id: 'agent-auto', turn_instance_id: turnId, todo_id: currentTodo },
              },
            }),
          }
        }
        if (args.includes('heartbeat-prompt')) {
          const turnIdx = args.indexOf('--turn-instance-id')
          const turnId = turnIdx >= 0 ? args[turnIdx + 1]! : 'turn-x'
          return {
            exitCode: 0,
            stderr: '',
            stdout: JSON.stringify({
              schema_version: 'heartbeat_agent_input_v1',
              ok: true,
              goal_id: 'goal-auto',
              agent_id: 'agent-auto',
              turn_instance_id: turnId,
              task_body: `LOOPX_TURN=${turnId} work=${currentTodo}`,
            }),
          }
        }
        throw new Error(`unexpected cli call: ${args.join(' ')}`)
      }

      const queued = harness.queued
      let turnCounter = 0
      const driver = new LoopXContinuationDriver({
        isLiveAgent: () => true,
        runner,
        resolveCommand: async () => ({ file: 'loopx', prefix: [], skillCommand: 'loopx', version: 'loopx 2.4.0' }),
        makeTurnInstanceId: () => `turn-off-${++turnCounter}`,
      })

      // Activate driver cleanly inside session.
      const actEvent = harness.session.append('user/message', {
        id: 'act-off' as UserMessage['id'],
        role: 'user',
        content: [{ type: 'text', text: 'loopx' }],
        source: { kind: 'skill-invocation', name: 'loopx', form: 'instructions' } as never,
      }, { surfaceOp: 'append' })
      driver.observeAgent(harness.agent)
      await vi.waitFor(() => expect(queued.length).toBe(1))

      // Execute Turn 1 (exploratory bloat remains raw after turn ends).
      const msg1 = queued.shift()!
      driver.onInboxClaimed(harness.agent, msg1)
      await driver.onPreStep(harness.agent, [msg1], new AbortController().signal, async () => ({ kind: 'proceed', messages: [msg1] } as never))
      harness.setStatus('running')
      const ev1 = harness.session.append('user/message', msg1, { surfaceOp: 'append' })
      driver.onSessionEvent(harness.agent, ev1)
      driver.onSessionEvent(harness.agent, harness.session.append('step/start', { turn: 2, step: ++stepSeq } as never))
      const todo1Nodes = appendTodoExploratorySuffix(harness.session, 'auto-off')
      driver.onSessionEvent(harness.agent, harness.session.append('turn/end', { turn: 2, reason: { kind: 'completed' } } as never))
      currentTodo = 'todo_2'
      harness.setStatus('idle')
      driver.onAgentStatus(harness.agent, 'idle')

      await vi.waitFor(() => expect(queued.length).toBe(1))
      // Default-off must NOT call `quota receipt --include-settlement`, and surface keeps all raw nodes!
      expect(cliLog.some(line => line.includes('receipt'))).toBe(false)
      expect(harness.session.surface.nodes.length).toBe(prefixSeqs.length + 1 + 1 + todo1Nodes.length)
      expect(actEvent.seq).toBeGreaterThan(0)
    } finally {
      await harness.dispose()
      await rm(cwd, { recursive: true, force: true })
    }
  })

  it('automatically anchors, folds upon verified Core settlement, prefetches obs_next_cmd, and swaps next step context while preserving KV prefix', async () => {
    const cwd = await mkdtemp(join(tmpdir(), 'dsh-auto-fold-on-'))
    const harness = await createRealSessionHarness(cwd)
    try {
      const sourcePath = join(cwd, 'rollout.jsonl')
      const sourceContent = '{"event":"todo_complete","todo_id":"todo_real_1"}\n'
      await writeFile(sourcePath, sourceContent, 'utf8')
      const { createHash } = await import('node:crypto')
      const sourceSha = createHash('sha256').update(sourceContent, 'utf8').digest('hex')

      const prefixSeqs = appendPrefixQ(harness.session)
      const prefixMessagesBefore = harness.session.deriveMessages()
      const prefixDigestBefore = deriveMessagesPrefixDigest(harness.session, prefixSeqs.length)

      let activeTodo = 'todo_real_1'
      let settledTurn1 = false
      let stepSeq = 0
      const settlementCalls: string[] = []
      const obsRunner = vi.fn<TodoUnitObsRunner>(async argv => ({
        exitCode: 0,
        output: `OBS_OUTPUT cmd=${argv.join(' ')} status=clean`,
      }))

      const runner: FileRunner = async (_file, args) => {
        if (args.includes('resolve-agent-thread')) {
          return {
            exitCode: 0,
            stderr: '',
            stdout: JSON.stringify({
              schema_version: 'loopx_thread_agent_binding_resolution_v0',
              ok: true,
              status: 'bound',
              host_surface: 'deepseek-harness-native',
              thread_id: harness.agent.id,
              goal_id: 'goal-real',
              agent_id: 'agent-real',
              matches: [{ goal_id: 'goal-real', agent_id: 'agent-real' }],
            }),
          }
        }
        if (args.includes('quota') && args.includes('should-run')) {
          const turnIdx = args.indexOf('--turn-instance-id')
          const turnId = turnIdx >= 0 ? args[turnIdx + 1]! : 'turn-x'
          return {
            exitCode: 0,
            stderr: '',
            stdout: JSON.stringify({
              ok: true,
              mode: 'should-run',
              goal_id: 'goal-real',
              should_run: true,
              agent_identity: { agent_id: 'agent-real', registered: true },
              selected_todo: { todo_id: activeTodo, text: `Execute ${activeTodo}` },
              heartbeat_receipt: {
                schema_version: 'heartbeat_receipt_v0',
                status: 'committed',
                turn_instance_id: turnId,
                settlement_identity: { goal_id: 'goal-real', agent_id: 'agent-real', turn_instance_id: turnId, todo_id: activeTodo },
              },
            }),
          }
        }
        if (args.includes('quota') && args.includes('receipt') && args.includes('--include-settlement')) {
          const turnIdx = args.indexOf('--turn-instance-id')
          const turnId = turnIdx >= 0 ? args[turnIdx + 1]! : 'turn-x'
          settlementCalls.push(turnId)
          return {
            exitCode: 0,
            stderr: '',
            stdout: JSON.stringify({
              ok: true,
              schema_version: 'quota_receipt_readback_v0',
              goal_id: 'goal-real',
              agent_id: 'agent-real',
              turn_instance_id: turnId,
              settlement_verified: settledTurn1,
              settlement_conflict_reason: settledTurn1 ? null : 'missing_completion_event',
              completion_receipt: settledTurn1
                ? {
                  todo_id: 'todo_real_1',
                  completion_receipt_id: `ev-${turnId}`,
                  exit_code: 0,
                  sha256: 'c'.repeat(64),
                  failure_facts: ['todo_real_1 initial lint note'],
                  open_obligations: ['verify todo_real_2 downstream'],
                  source_ref: { path: sourcePath, sha256: sourceSha },
                }
                : null,
            }),
          }
        }
        if (args.includes('heartbeat-prompt')) {
          const turnIdx = args.indexOf('--turn-instance-id')
          const turnId = turnIdx >= 0 ? args[turnIdx + 1]! : 'turn-x'
          return {
            exitCode: 0,
            stderr: '',
            stdout: JSON.stringify({
              schema_version: 'heartbeat_agent_input_v1',
              ok: true,
              goal_id: 'goal-real',
              agent_id: 'agent-real',
              turn_instance_id: turnId,
              selected_todo: { todo_id: activeTodo },
              task_body: `LOOPX_TURN=${turnId} work=${activeTodo}`,
            }),
          }
        }
        throw new Error(`unexpected cli call: ${args.join(' ')}`)
      }

      const queued = harness.queued
      let turnCounter = 0
      const driver = new LoopXContinuationDriver({
        isLiveAgent: () => true,
        runner,
        resolveCommand: async () => ({ file: 'loopx', prefix: [], skillCommand: 'loopx', version: 'loopx 2.4.0' }),
        makeTurnInstanceId: () => `turn-on-${++turnCounter}`,
        todoUnitFold: {
          enabled: true,
          minNetSavingsTokens: 64,
          resolveObsNextCmd: ({ settledTodoId, nextTodoId }) => {
            expect(settledTodoId).toBe('todo_real_1')
            expect(nextTodoId).toBe('todo_real_2')
            return {
              command: ['git', 'status', '--short'],
              initiator: 'turn_driver',
              rejectOnTruncation: true,
            }
          },
        },
        runObsCommand: obsRunner,
      })

      // Activate LoopX (still part of parent prefix Q before Todo 1 anchors!).
      const actEvent = harness.session.append('user/message', {
        id: 'act-on' as UserMessage['id'],
        role: 'user',
        content: [{ type: 'text', text: 'loopx' }],
        source: { kind: 'skill-invocation', name: 'loopx', form: 'instructions' } as never,
      }, { surfaceOp: 'append' })
      const qLenWithActivation = harness.session.surface.nodes.length
      const qMessagesWithActivation = harness.session.deriveMessages()
      const qDigestWithActivation = deriveMessagesPrefixDigest(harness.session, qLenWithActivation)
      expect(qMessagesWithActivation.slice(0, prefixSeqs.length)).toEqual(prefixMessagesBefore)
      expect(deriveMessagesPrefixDigest(harness.session, prefixSeqs.length)).toBe(prefixDigestBefore)
      expect(actEvent.seq).toBeGreaterThan(0)

      driver.observeAgent(harness.agent)
      await vi.waitFor(() => expect(queued.length).toBe(1))

      // Execute Turn 1 (todo_real_1) with bloated exploratory tool output, then settle Turn 1!
      const msg1 = queued.shift()!
      driver.onInboxClaimed(harness.agent, msg1)
      await driver.onPreStep(harness.agent, [msg1], new AbortController().signal, async () => ({ kind: 'proceed', messages: [msg1] } as never))
      harness.setStatus('running')
      const ev1 = harness.session.append('user/message', msg1, { surfaceOp: 'append' })
      driver.onSessionEvent(harness.agent, ev1)
      driver.onSessionEvent(harness.agent, harness.session.append('step/start', { turn: 2, step: ++stepSeq } as never))
      appendTodoExploratorySuffix(harness.session, 'todo_real_1_bloat')
      expect(harness.session.deriveMessages().some(m => JSON.stringify(m).includes('exploratory search output for todo_real_1_bloat'))).toBe(true)

      driver.onSessionEvent(harness.agent, harness.session.append('turn/end', { turn: 2, reason: { kind: 'completed' } } as never))
      settledTurn1 = true
      activeTodo = 'todo_real_2'
      harness.setStatus('idle')
      driver.onAgentStatus(harness.agent, 'idle')

      // Driver wakes, settles fold automatically via Core receipt readback, runs obsNextCmd, and queues Todo 2!
      await vi.waitFor(() => expect(queued.length).toBe(1))
      const msg2 = queued.shift()!
      expect(String((msg2.content[0] as { text: string }).text)).toContain('work=todo_real_2')
      expect(settlementCalls).toEqual(['turn-on-1'])
      expect(obsRunner).toHaveBeenCalledTimes(1)

      // Next model step context (`deriveMessages()`) physically popped Todo 1 bloat while preserving parent KV prefix Q!
      const msgsBeforeTodo2 = harness.session.deriveMessages()
      expect(deriveMessagesPrefixDigest(harness.session, qLenWithActivation)).toBe(qDigestWithActivation)
      expect(msgsBeforeTodo2.slice(0, qLenWithActivation)).toEqual(qMessagesWithActivation)
      expect(msgsBeforeTodo2.some(m => JSON.stringify(m).includes('exploratory search output for todo_real_1_bloat'))).toBe(false)
      expect(msgsBeforeTodo2.length).toBe(qLenWithActivation + 1)

      const foldMsg = msgsBeforeTodo2.at(-1) as UserMessage
      const foldText = (foldMsg.content[0] as { type: 'text'; text: string }).text
      expect(foldMsg.source.kind).toBe('loopx-todo-fold')
      expect(foldText).toContain('[Semantic-Unit Fold Receipt mu | trust=tool]')
      expect(foldText).toContain('todo_id: todo_real_1')
      expect(foldText).toContain('completion_receipt_id: ev-turn-on-1')
      expect(foldText).toContain('OBS_OUTPUT cmd=git status --short status=clean')
      expect(foldText).toContain('verify todo_real_2 downstream')
    } finally {
      await harness.dispose()
      await rm(cwd, { recursive: true, force: true })
    }
  })

  it.each(['unsettled_progress', 'obs_command_error', 'recovery_conflict', 'aborted_turn'] as const)(
    'automatically fails closed to KeepRaw on real %s during multi-Todo driver lifecycle',
    async mode => {
      const cwd = await mkdtemp(join(tmpdir(), `dsh-auto-fail-${mode}-`))
      const harness = await createRealSessionHarness(cwd)
      try {
        const sourcePath = join(cwd, 'rollout.jsonl')
        await writeFile(sourcePath, '{"event":"ok"}\n', 'utf8')
        const { createHash } = await import('node:crypto')
        const sourceSha = createHash('sha256').update('{"event":"ok"}\n', 'utf8').digest('hex')

        appendPrefixQ(harness.session)
        harness.session.append('user/message', {
          id: `act-${mode}` as UserMessage['id'],
          role: 'user',
          content: [{ type: 'text', text: 'loopx' }],
          source: { kind: 'skill-invocation', name: 'loopx', form: 'instructions' } as never,
        }, { surfaceOp: 'append' })

        let stepSeq = 0
        let firstCall = true
        const obsRunner = vi.fn<TodoUnitObsRunner>(async () => ({
          exitCode: mode === 'obs_command_error' ? 2 : 0,
          output: mode === 'obs_command_error' ? 'boom' : 'OK',
        }))
        const runner: FileRunner = async (_file, args) => {
          if (args.includes('resolve-agent-thread')) {
            return {
              exitCode: 0,
              stderr: '',
              stdout: JSON.stringify({
                schema_version: 'loopx_thread_agent_binding_resolution_v0',
                ok: true,
                status: 'bound',
                host_surface: 'deepseek-harness-native',
                thread_id: harness.agent.id,
                goal_id: 'goal-fail',
                agent_id: 'agent-fail',
                matches: [{ goal_id: 'goal-fail', agent_id: 'agent-fail' }],
              }),
            }
          }
          if (args.includes('quota') && args.includes('should-run')) {
            const turnIdx = args.indexOf('--turn-instance-id')
            const turnId = turnIdx >= 0 ? args[turnIdx + 1]! : 'turn-x'
            const recovering = !firstCall && mode === 'recovery_conflict'
            firstCall = false
            return {
              exitCode: 0,
              stderr: '',
              stdout: JSON.stringify({
                ok: true,
                mode: 'should-run',
                goal_id: 'goal-fail',
                should_run: !recovering,
                ...(recovering
                  ? {
                    unsettled_host_turn_recovery: {
                      schema_version: 'unsettled_host_turn_recovery_v0',
                      state: 'attention_required',
                    },
                  }
                  : {}),
                agent_identity: { agent_id: 'agent-fail', registered: true },
                selected_todo: { todo_id: 'todo_fail_auto' },
                heartbeat_receipt: {
                  schema_version: 'heartbeat_receipt_v0',
                  status: 'committed',
                  turn_instance_id: turnId,
                  settlement_identity: { goal_id: 'goal-fail', agent_id: 'agent-fail', turn_instance_id: turnId, todo_id: 'todo_fail_auto' },
                },
              }),
            }
          }
          if (args.includes('quota') && args.includes('receipt') && args.includes('--include-settlement')) {
            const turnIdx = args.indexOf('--turn-instance-id')
            const turnId = turnIdx >= 0 ? args[turnIdx + 1]! : 'turn-x'
            const verified = mode !== 'unsettled_progress'
            return {
              exitCode: 0,
              stderr: '',
              stdout: JSON.stringify({
                ok: true,
                schema_version: 'quota_receipt_readback_v0',
                goal_id: 'goal-fail',
                agent_id: 'agent-fail',
                turn_instance_id: turnId,
                settlement_verified: verified,
                settlement_conflict_reason: verified ? null : 'missing_completion_event',
                completion_receipt: verified
                  ? {
                    todo_id: 'todo_fail_auto',
                    completion_receipt_id: `ev-${turnId}`,
                    exit_code: 0,
                    sha256: 'd'.repeat(64),
                    failure_facts: ['preserved failure fact'],
                    open_obligations: ['preserved obligation'],
                    source_ref: { path: sourcePath, sha256: sourceSha },
                  }
                  : null,
              }),
            }
          }
          if (args.includes('heartbeat-prompt')) {
            const turnIdx = args.indexOf('--turn-instance-id')
            const turnId = turnIdx >= 0 ? args[turnIdx + 1]! : 'turn-x'
            return {
              exitCode: 0,
              stderr: '',
              stdout: JSON.stringify({
                schema_version: 'heartbeat_agent_input_v1',
                ok: true,
                goal_id: 'goal-fail',
                agent_id: 'agent-fail',
                turn_instance_id: turnId,
                task_body: `LOOPX_TURN=${turnId}`,
              }),
            }
          }
          throw new Error(`unexpected cli call: ${args.join(' ')}`)
        }

        const queued = harness.queued
        let turnCounter = 0
        const driver = new LoopXContinuationDriver({
          isLiveAgent: () => true,
          runner,
          resolveCommand: async () => ({ file: 'loopx', prefix: [], skillCommand: 'loopx', version: 'loopx 2.4.0' }),
          makeTurnInstanceId: () => `turn-fail-${++turnCounter}`,
          todoUnitFold: {
            enabled: true,
            minNetSavingsTokens: 64,
            obsNextCmd: { command: ['git', 'status', '--short'] },
          },
          runObsCommand: obsRunner,
        })

        driver.observeAgent(harness.agent)
        await vi.waitFor(() => expect(queued.length).toBe(1))
        const msg1 = queued.shift()!
        driver.onInboxClaimed(harness.agent, msg1)
        await driver.onPreStep(harness.agent, [msg1], new AbortController().signal, async () => ({ kind: 'proceed', messages: [msg1] } as never))
        harness.setStatus('running')
        driver.onSessionEvent(harness.agent, harness.session.append('user/message', msg1, { surfaceOp: 'append' }))
        driver.onSessionEvent(harness.agent, harness.session.append('step/start', { turn: 2, step: ++stepSeq } as never))
        appendTodoExploratorySuffix(harness.session, `raw-${mode}`)
        const rawNodesBeforeSettle = [...harness.session.surface.nodes]
        const rawMsgsBeforeSettle = harness.session.deriveMessages()

        driver.onSessionEvent(harness.agent, harness.session.append('turn/end', {
          turn: 2,
          reason: { kind: mode === 'aborted_turn' ? 'aborted' : 'completed' },
        } as never))
        harness.setStatus('idle')
        driver.onAgentStatus(harness.agent, 'idle')

        if (mode === 'recovery_conflict') {
          await driver.evaluateActivatedSession({ agent: harness.agent, session: harness.agent.session })
        } else if (mode === 'aborted_turn') {
          // Aborted turn holds pause until human resumes; verify raw messages stayed intact immediately.
          await Promise.resolve()
        } else {
          await vi.waitFor(() => expect(queued.length).toBe(1))
        }

        expect(harness.session.surface.nodes).toEqual(rawNodesBeforeSettle)
        expect(harness.session.deriveMessages()).toEqual(rawMsgsBeforeSettle)
        expect(harness.session.deriveMessages().some(m => JSON.stringify(m).includes(`exploratory search output for raw-${mode}`))).toBe(true)
        if (mode !== 'obs_command_error') {
          expect(obsRunner).not.toHaveBeenCalled()
        } else {
          expect(obsRunner).toHaveBeenCalledTimes(1)
        }
      } finally {
        await harness.dispose()
        await rm(cwd, { recursive: true, force: true })
      }
    },
  )

  it('keeps settled tail and mid-prefetch user corrections intact across real DSH inbox and continuation interleavings while running prefetch once', async () => {
    const runCase = async (mode: 'clean' | 'session_correction_mid_prefetch' | 'inbox_correction_mid_prefetch') => {
      const cwd = await mkdtemp(join(tmpdir(), `dsh-race-${mode}-`))
      const harness = await createRealSessionHarness(cwd)
      try {
        const sourcePath = join(cwd, 'rollout.jsonl')
        const sourceContent = `{"event":"settled","mode":"${mode}"}\n`
        await writeFile(sourcePath, sourceContent, 'utf8')
        const { createHash } = await import('node:crypto')
        const sourceSha = createHash('sha256').update(sourceContent, 'utf8').digest('hex')

        appendPrefixQ(harness.session)
        harness.session.append('user/message', {
          id: `act-${mode}` as UserMessage['id'],
          role: 'user',
          content: [{ type: 'text', text: 'loopx' }],
          source: { kind: 'skill-invocation', name: 'loopx', form: 'instructions' } as never,
        }, { surfaceOp: 'append' })

        let settledTurn1 = false
        let activeTodo = 'todo_real_1'
        let stepSeq = 0
        let obsStartedResolve!: () => void
        const obsStarted = new Promise<void>(resolve => { obsStartedResolve = resolve })
        let obsReleaseResolve!: () => void
        const obsRelease = new Promise<void>(resolve => { obsReleaseResolve = resolve })

        const obsRunner = vi.fn<TodoUnitObsRunner>(async () => {
          obsStartedResolve()
          if (mode !== 'clean') {
            await obsRelease
          }
          return { exitCode: 0, output: 'OBS_OUTPUT status=clean' }
        })

        const runner: FileRunner = async (_file, args) => {
          if (args.includes('resolve-agent-thread')) {
            return {
              exitCode: 0,
              stderr: '',
              stdout: JSON.stringify({
                schema_version: 'loopx_thread_agent_binding_resolution_v0',
                ok: true,
                status: 'bound',
                host_surface: 'deepseek-harness-native',
                thread_id: harness.agent.id,
                goal_id: 'goal-race',
                agent_id: 'agent-race',
                matches: [{ goal_id: 'goal-race', agent_id: 'agent-race' }],
              }),
            }
          }
          if (args.includes('quota') && args.includes('should-run')) {
            const turnIdx = args.indexOf('--turn-instance-id')
            const turnId = turnIdx >= 0 ? args[turnIdx + 1]! : 'turn-x'
            return {
              exitCode: 0,
              stderr: '',
              stdout: JSON.stringify({
                ok: true,
                mode: 'should-run',
                goal_id: 'goal-race',
                should_run: true,
                agent_identity: { agent_id: 'agent-race', registered: true },
                selected_todo: { todo_id: activeTodo, text: `Execute ${activeTodo}` },
                heartbeat_receipt: {
                  schema_version: 'heartbeat_receipt_v0',
                  status: 'committed',
                  turn_instance_id: turnId,
                  settlement_identity: { goal_id: 'goal-race', agent_id: 'agent-race', turn_instance_id: turnId, todo_id: activeTodo },
                },
              }),
            }
          }
          if (args.includes('quota') && args.includes('receipt') && args.includes('--include-settlement')) {
            const turnIdx = args.indexOf('--turn-instance-id')
            const turnId = turnIdx >= 0 ? args[turnIdx + 1]! : 'turn-x'
            return {
              exitCode: 0,
              stderr: '',
              stdout: JSON.stringify({
                ok: true,
                schema_version: 'quota_receipt_readback_v0',
                goal_id: 'goal-race',
                agent_id: 'agent-race',
                turn_instance_id: turnId,
                settlement_verified: settledTurn1,
                settlement_conflict_reason: settledTurn1 ? null : 'missing_completion_event',
                completion_receipt: settledTurn1
                  ? {
                    todo_id: 'todo_real_1',
                    completion_receipt_id: `ev-${turnId}`,
                    exit_code: 0,
                    sha256: 'e'.repeat(64),
                    failure_facts: [],
                    open_obligations: ['verify selected scope'],
                    source_ref: { path: sourcePath, sha256: sourceSha },
                  }
                  : null,
              }),
            }
          }
          if (args.includes('heartbeat-prompt')) {
            const turnIdx = args.indexOf('--turn-instance-id')
            const turnId = turnIdx >= 0 ? args[turnIdx + 1]! : 'turn-x'
            return {
              exitCode: 0,
              stderr: '',
              stdout: JSON.stringify({
                schema_version: 'heartbeat_agent_input_v1',
                ok: true,
                goal_id: 'goal-race',
                agent_id: 'agent-race',
                turn_instance_id: turnId,
                selected_todo: { todo_id: activeTodo },
                task_body: `LOOPX_TURN=${turnId} work=${activeTodo}`,
              }),
            }
          }
          throw new Error(`unexpected cli call: ${args.join(' ')}`)
        }

        const queued = harness.queued
        let turnCounter = 0
        const driver = new LoopXContinuationDriver({
          isLiveAgent: () => true,
          runner,
          resolveCommand: async () => ({ file: 'loopx', prefix: [], skillCommand: 'loopx', version: 'loopx 2.4.0' }),
          makeTurnInstanceId: () => `turn-race-${++turnCounter}`,
          todoUnitFold: {
            enabled: true,
            minNetSavingsTokens: 64,
            obsNextCmd: { command: ['git', 'status', '--short'] },
          },
          runObsCommand: obsRunner,
        })

        driver.observeAgent(harness.agent)
        await vi.waitFor(() => expect(queued.length).toBe(1))
        const msg1 = queued.shift()!
        driver.onInboxClaimed(harness.agent, msg1)
        await driver.onPreStep(harness.agent, [msg1], new AbortController().signal, async () => ({ kind: 'proceed', messages: [msg1] } as never))
        harness.setStatus('running')
        driver.onSessionEvent(harness.agent, harness.session.append('user/message', msg1, { surfaceOp: 'append' }))
        driver.onSessionEvent(harness.agent, harness.session.append('step/start', { turn: 2, step: ++stepSeq } as never))
        appendTodoExploratorySuffix(harness.session, `race-${mode}`)
        driver.onSessionEvent(harness.agent, harness.session.append('turn/end', { turn: 2, reason: { kind: 'completed' } } as never))

        settledTurn1 = true
        activeTodo = 'todo_real_2'
        harness.setStatus('idle')
        driver.onAgentStatus(harness.agent, 'idle')

        await obsStarted

        const correctionText = `USER_CORRECTION_${mode}: preserve selected todo_real_2 scope`
        const correctionMessage: UserMessage = {
          id: `corr-${mode}` as UserMessage['id'],
          role: 'user',
          content: [{ type: 'text', text: correctionText }],
          source: { kind: 'user' },
        }

        if (mode === 'clean') {
          await vi.waitFor(() => expect(queued.length).toBe(1))
          const derived = harness.session.deriveMessages()
          expect(obsRunner).toHaveBeenCalledTimes(1)
          expect(derived.some(m => m.role === 'user' && m.source.kind === 'loopx-todo-fold')).toBe(true)
          expect(derived.some(m => JSON.stringify(m).includes(`exploratory search output for race-${mode}`))).toBe(false)
          return
        }

        if (mode === 'session_correction_mid_prefetch') {
          const corrEv = harness.session.append('user/message', correctionMessage, { surfaceOp: 'append' })
          driver.onSessionEvent(harness.agent, corrEv)
          obsReleaseResolve()

          await vi.waitFor(() => expect(queued.length).toBe(1))
          expect(String((queued[0]!.content[0] as { text: string }).text)).toContain('work=todo_real_2')
        } else {
          queued.push(correctionMessage)
          driver.onInboxInserted(harness.agent, correctionMessage)
          obsReleaseResolve()

          // Automatic continuation stays fenced while user inbox correction is pending, and raw tail stays un-folded.
          await vi.waitFor(() => expect(obsRunner).toHaveBeenCalledTimes(1))
          await Promise.resolve()
          expect(queued).toEqual([correctionMessage])
          expect(harness.session.deriveMessages().some(m => m.role === 'user' && m.source.kind === 'loopx-todo-fold')).toBe(false)

          // Now claim and execute the inbox user correction turn, then return idle so todo_real_2 continues.
          const claimedCorrection = queued.shift()!
          driver.onInboxClaimed(harness.agent, claimedCorrection)
          harness.setStatus('running')
          driver.onSessionEvent(harness.agent, harness.session.append('user/message', claimedCorrection, { surfaceOp: 'append' }))
          driver.onSessionEvent(harness.agent, harness.session.append('step/start', { turn: 3, step: ++stepSeq } as never))
          driver.onSessionEvent(harness.agent, harness.session.append('turn/end', { turn: 3, reason: { kind: 'completed' } } as never))
          harness.setStatus('idle')
          driver.onAgentStatus(harness.agent, 'idle')

          await vi.waitFor(() => expect(queued.length).toBe(1))
          expect(String((queued[0]!.content[0] as { text: string }).text)).toContain('work=todo_real_2')
        }

        const finalMessages = harness.session.deriveMessages()
        const finalSerialized = JSON.stringify(finalMessages)
        expect(obsRunner).toHaveBeenCalledTimes(1)
        expect(finalMessages.some(m => m.role === 'user' && m.source.kind === 'loopx-todo-fold')).toBe(false)
        expect(finalSerialized).toContain(correctionText)
        expect(finalSerialized).toContain(`exploratory search output for race-${mode}`)
        expect(harness.session.snapshotEvents().some(ev => JSON.stringify(ev).includes(correctionText))).toBe(true)
      } finally {
        await harness.dispose()
        await rm(cwd, { recursive: true, force: true })
      }
    }

    await runCase('clean')
    await runCase('session_correction_mid_prefetch')
    await runCase('inbox_correction_mid_prefetch')
  })

  it('aborts FoldToReceipt to KeepRaw if a user message or segment advance lands during foldAndPrefetch', async () => {
    const cwd = await mkdtemp(join(tmpdir(), 'dsh-fold-segment-'))
    const harness = await createRealSessionHarness(cwd)
    try {
      appendPrefixQ(harness.session)

      let obsStartedResolve!: () => void
      let obsStarted = new Promise<void>(resolve => { obsStartedResolve = resolve })
      let obsReleaseResolve!: () => void
      let obsRelease = new Promise<void>(resolve => { obsReleaseResolve = resolve })
      const obsRunner = vi.fn<TodoUnitObsRunner>(async () => {
        obsStartedResolve()
        await obsRelease
        return { exitCode: 0, output: 'NEXT_OK' }
      })

      const driver = new LoopXContinuationDriver({
        isLiveAgent: () => true,
        todoUnitFold: { enabled: true, minNetSavingsTokens: 64 },
        runObsCommand: obsRunner,
      })

      driver.anchorTodoUnit(harness.agent, {
        todoId: 'todo-s1-1',
        turnInstanceId: 'turn-s1-1',
        contextEpoch: 'epoch-dsh-1',
        anchorStepIndex: 2,
      })
      appendTodoExploratorySuffix(harness.session, 'mid-fold')

      const settlePromise1 = driver.foldAndPrefetch(harness.agent, {
        todoId: 'todo-s1-1',
        turnInstanceId: 'turn-s1-1',
        contextEpoch: 'epoch-dsh-1',
        nowStepIndex: 5,
        completionReceipt: VALID_RECEIPT,
        obsNextCmd: { command: ['git', 'status', '--short'] },
      })
      await obsStarted

      // User sends a correction while obsNextCmd is awaiting.
      harness.session.append('user/message', {
        id: 'corr-direct' as UserMessage['id'],
        role: 'user',
        content: [{ type: 'text', text: 'Keep this raw correction' }],
        source: { kind: 'user' },
      }, { surfaceOp: 'append' })
      obsReleaseResolve()

      const res1 = await settlePromise1
      expect(res1.operator).toBe('KeepRaw')
      expect(res1.status).toBe('fallback_keep_raw')
      expect(res1.fallbackReason).toBe('user_message_during_prefetch')
      expect(obsRunner).toHaveBeenCalledTimes(1)
      const msgsAfter1 = JSON.stringify(harness.session.deriveMessages())
      expect(msgsAfter1).toContain('Keep this raw correction')
      expect(msgsAfter1).toContain('exploratory search output for mid-fold')

      // Segment advance (new anchor for next Todo) during await also falls back cleanly without removing the new anchor.
      obsStarted = new Promise<void>(resolve => { obsStartedResolve = resolve })
      obsRelease = new Promise<void>(resolve => { obsReleaseResolve = resolve })
      driver.anchorTodoUnit(harness.agent, {
        todoId: 'todo-s1-2',
        turnInstanceId: 'turn-s1-2',
        contextEpoch: 'epoch-dsh-1',
        anchorStepIndex: 6,
      })
      appendTodoExploratorySuffix(harness.session, 'mid-seg', 3)

      const receipt2: TodoUnitCompletionReceipt = {
        ...VALID_RECEIPT,
        todoId: 'todo-s1-2',
        completionReceiptId: 'cqr-s1-2',
      }
      const settlePromise2 = driver.foldAndPrefetch(harness.agent, {
        todoId: 'todo-s1-2',
        turnInstanceId: 'turn-s1-2',
        contextEpoch: 'epoch-dsh-1',
        nowStepIndex: 8,
        completionReceipt: receipt2,
        obsNextCmd: { command: ['git', 'status', '--short'] },
      })
      await obsStarted

      driver.onSessionEvent(harness.agent, harness.session.append('step/start', { turn: 3, step: 9 } as never))
      driver.anchorTodoUnit(harness.agent, {
        todoId: 'todo-s1-2',
        turnInstanceId: 'turn-s1-2b',
        contextEpoch: 'epoch-dsh-1',
        anchorStepIndex: 9,
      })
      obsReleaseResolve()

      const res2 = await settlePromise2
      expect(res2.operator).toBe('KeepRaw')
      expect(res2.status).toBe('fallback_keep_raw')
      expect(res2.fallbackReason).toBe('segment_advanced_during_prefetch')
      expect(obsRunner).toHaveBeenCalledTimes(2)
    } finally {
      await harness.dispose()
      await rm(cwd, { recursive: true, force: true })
    }
  })

  it('coalesces concurrent same-foldKey invocations in flight and allows sequential retry only after failure or KeepRaw', async () => {
    const cwd = await mkdtemp(join(tmpdir(), 'dsh-fold-coalesce-'))
    const harness = await createRealSessionHarness(cwd)
    try {
      appendPrefixQ(harness.session)

      let obsStartedResolve!: () => void
      let obsStarted = new Promise<void>(resolve => { obsStartedResolve = resolve })
      let obsReleaseResolve!: () => void
      let obsRelease = new Promise<void>(resolve => { obsReleaseResolve = resolve })
      let failMode: 'throw' | 'error_code' | 'ok' = 'throw'

      const obsRunner = vi.fn<TodoUnitObsRunner>(async () => {
        obsStartedResolve()
        await obsRelease
        if (failMode === 'throw') throw new Error('transient obs failure')
        if (failMode === 'error_code') return { exitCode: 2, output: 'transient obs exit 2' }
        return { exitCode: 0, output: 'NEXT_COALESCED_OK' }
      })

      const driver = new LoopXContinuationDriver({
        isLiveAgent: () => true,
        todoUnitFold: { enabled: true, minNetSavingsTokens: 64 },
        runObsCommand: obsRunner,
      })

      driver.anchorTodoUnit(harness.agent, {
        todoId: VALID_RECEIPT.todoId,
        turnInstanceId: 'turn-coalesce-1',
        contextEpoch: 'epoch-coalesce-1',
        anchorStepIndex: 2,
      })
      appendTodoExploratorySuffix(harness.session, 'coalesce', 2)

      const req = {
        todoId: VALID_RECEIPT.todoId,
        turnInstanceId: 'turn-coalesce-1',
        contextEpoch: 'epoch-coalesce-1',
        nowStepIndex: 5,
        completionReceipt: VALID_RECEIPT,
        obsNextCmd: { command: ['git', 'status', '--short'] as const },
      }

      // 1. Concurrent failure rejects both callers once and cleans up in-flight map for retry.
      const concurrentErr = Promise.allSettled([
        driver.onTodoUnitSettled(harness.agent, req),
        driver.foldAndPrefetch(harness.agent, req),
      ])
      await obsStarted
      obsReleaseResolve()
      const errResults = await concurrentErr
      expect(obsRunner).toHaveBeenCalledTimes(1)
      expect(errResults.map(r => r.status)).toEqual(['rejected', 'rejected'])

      // 2. Concurrent KeepRaw (exitCode != 0) coalesces to 1 obs invocation and remains retryable.
      obsStarted = new Promise<void>(resolve => { obsStartedResolve = resolve })
      obsRelease = new Promise<void>(resolve => { obsReleaseResolve = resolve })
      failMode = 'error_code'
      const concurrentKeepRaw = Promise.all([
        driver.onTodoUnitSettled(harness.agent, req),
        driver.foldAndPrefetch(harness.agent, req),
      ])
      await obsStarted
      obsReleaseResolve()
      const [kr1, kr2] = await concurrentKeepRaw
      expect(obsRunner).toHaveBeenCalledTimes(2)
      expect(kr1.operator).toBe('KeepRaw')
      expect(kr2).toEqual(kr1)

      // 3. Concurrent success across both public entrypoints executes obs once, writes one FoldToReceipt, and replays cached thereafter.
      obsStarted = new Promise<void>(resolve => { obsStartedResolve = resolve })
      obsRelease = new Promise<void>(resolve => { obsReleaseResolve = resolve })
      failMode = 'ok'
      const p1 = driver.onTodoUnitSettled(harness.agent, req)
      const p2 = driver.foldAndPrefetch(harness.agent, req)
      await obsStarted
      obsReleaseResolve()
      const [ok1, ok2] = await Promise.all([p1, p2])
      expect(obsRunner).toHaveBeenCalledTimes(3)
      expect(ok1.operator).toBe('FoldToReceipt')
      expect(ok2).toEqual(ok1)
      expect(ok1.foldedEventSeq).not.toBeNull()
      expect(
        harness.session.deriveMessages().filter(m => m.role === 'user' && m.source.kind === 'loopx-todo-fold'),
      ).toHaveLength(1)

      const replay = await driver.foldAndPrefetch(harness.agent, req)
      expect(replay).toEqual(ok1)
      expect(obsRunner).toHaveBeenCalledTimes(3)
    } finally {
      await harness.dispose()
      await rm(cwd, { recursive: true, force: true })
    }
  })
})
