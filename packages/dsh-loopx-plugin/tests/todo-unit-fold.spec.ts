import { mkdtemp, rm } from 'node:fs/promises'
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
  const agent = {
    id: sessionId,
    options: {},
    session,
    inbox: { nextTurn: [], nextStep: [], hasPending: false, remove: () => false },
    ctx,
    get status() { return status },
    cancel() {},
    whenIdle: async () => {},
    runMaintenance: async <T>(fn: (signal: AbortSignal) => Promise<T>) => fn(new AbortController().signal),
    send() {},
    followup() {},
    steer() {},
    inject() {},
  } as unknown as Agent

  return {
    ctx,
    session,
    agent,
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

function appendTodoExploratorySuffix(session: Session, suffixTag: string): SessionSeq[] {
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
    turn: 2,
    step: 1,
    callId,
    name: 'search',
    arguments: '{"q":"loopx"}',
  })
  session.append('assistant/message', {
    turn: 2,
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
    turn: 2,
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
    turn: 2,
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
})
