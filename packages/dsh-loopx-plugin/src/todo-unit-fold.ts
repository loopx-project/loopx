import { createHash, randomUUID } from 'node:crypto'
import type { AssistantMessage, ToolResultMessage, UserMessage } from '@deepseek-ai/dsh-llm'
import type { Session, SessionEvent, SessionSeq } from '@deepseek-ai/dsh-session'
import type { FileRunner, LoopXCommand } from './cli.ts'

export const TODO_UNIT_ANCHOR_SCHEMA_VERSION = 'loopx_semantic_unit_anchor_v0' as const
export const TODO_UNIT_FOLD_SCHEMA_VERSION = 'loopx_semantic_unit_fold_swap_v0' as const
export const DEFAULT_TODO_FOLD_MIN_NET_SAVINGS_TOKENS = 64
export const DEFAULT_TODO_FOLD_AMORTIZATION_DIVISOR = 20
export const DEFAULT_TODO_FOLD_MAX_OBS_BYTES = 16_384

const BARE_SHA256_RE = /^[a-f0-9]{64}$/u
const DISALLOWED_SHELL_OPERATORS = new Set([';', '|', '&', '&&', '||', '>', '>>', '<', '<<', '<<<', '(', ')'])
const READ_ONLY_PROBES = new Set(['cat', 'head', 'tail', 'wc', 'ls', 'stat', 'pwd', 'rg', 'grep'])
const READ_ONLY_GIT_SUBCOMMANDS = new Set(['status', 'diff', 'log', 'show', 'rev-parse'])

export interface LoopXTodoFoldMessageSource {
  readonly kind: 'loopx-todo-fold'
  readonly schemaVersion: typeof TODO_UNIT_FOLD_SCHEMA_VERSION
  readonly form: 'recall'
  readonly todoId: string
  readonly turnInstanceId: string
  readonly completionReceiptId: string
  readonly completionSha256: string
  readonly contextEpoch: string
  readonly prefixDigest: string
  readonly reclaimedSeqRange: readonly [SessionSeq, SessionSeq]
}

declare module '@deepseek-ai/dsh-llm' {
  interface MessageSourceMap {
    'loopx-todo-fold': LoopXTodoFoldMessageSource
  }
}

export interface TodoUnitFoldPolicy {
  readonly enabled: boolean
  readonly minNetSavingsTokens?: number | undefined
  readonly coldPrefillAmortizationTokens?: number | undefined
  readonly maxObsOutputBytes?: number | undefined
  readonly rejectOnTruncation?: boolean | undefined
  readonly obsNextCmd?: TodoUnitObsNextCommand | undefined
  readonly resolveObsNextCmd?: ((context: {
    readonly goalId: string
    readonly agentId: string
    readonly settledTodoId: string
    readonly nextTodoId?: string | undefined
    readonly quota: Record<string, unknown>
  }) => TodoUnitObsNextCommand | undefined) | undefined
  readonly defaultFailureFacts?: readonly string[] | undefined
  readonly defaultOpenObligations?: readonly string[] | undefined
}

export interface TodoUnitAnchorInput {
  readonly todoId: string
  readonly turnInstanceId: string
  readonly contextEpoch: string
  readonly anchorStepIndex: number
  readonly anchorSurfaceSeq?: SessionSeq | undefined
}

export interface TodoUnitAnchor {
  readonly schemaVersion: typeof TODO_UNIT_ANCHOR_SCHEMA_VERSION
  readonly todoId: string
  readonly turnInstanceId: string
  readonly contextEpoch: string
  readonly anchorStepIndex: number
  readonly prefixNodeCount: number
  readonly prefixSurfaceSeqs: readonly SessionSeq[]
  readonly anchorSurfaceSeq?: SessionSeq | undefined
  readonly prefixDigest: string
  readonly anchorSha256: string
}

export interface TodoUnitSourceRef {
  readonly path: string
  readonly sha256: string
}

export interface TodoUnitCompletionReceipt {
  readonly todoId: string
  readonly completionReceiptId: string
  readonly exitCode: number
  readonly sha256: string
  readonly failureFacts: readonly string[]
  readonly openObligations: readonly string[]
  readonly sourceRef: TodoUnitSourceRef
}

export interface TodoUnitObsNextCommand {
  readonly command: string | readonly string[]
  readonly initiator?: string | undefined
  readonly exitCode?: number | null | undefined
  readonly output?: string | undefined
  readonly timedOut?: boolean | undefined
  readonly truncated?: boolean | undefined
  readonly latencyMs?: number | undefined
  readonly rejectOnTruncation?: boolean | undefined
}

export interface TodoUnitFoldRequest {
  readonly todoId: string
  readonly turnInstanceId: string
  readonly contextEpoch: string
  readonly nowStepIndex: number
  readonly anchor?: TodoUnitAnchor | undefined
  readonly completionReceipt?: TodoUnitCompletionReceipt | undefined
  readonly obsNextCmd?: TodoUnitObsNextCommand | undefined
  readonly baselineTokens?: number | undefined
  readonly replacementTokens?: number | undefined
  readonly quiescentBoundary?: boolean | undefined
  readonly failureFacts?: readonly string[] | undefined
  readonly openObligations?: readonly string[] | undefined
}

export interface TodoUnitReceiptMu {
  readonly trustLevel: 'tool'
  readonly todoId: string
  readonly completionReceiptId: string
  readonly sha256: string
  readonly turnInstanceId: string
  readonly contextEpoch: string
  readonly prefixDigest: string
  readonly reclaimRange: readonly [number, number]
  readonly reclaimSeqRange: readonly [SessionSeq, SessionSeq]
  readonly exitCode: number
  readonly failureFacts: readonly string[]
  readonly openObligations: readonly string[]
  readonly sourceRef: TodoUnitSourceRef
}

export interface TodoUnitFoldOutcome {
  readonly schemaVersion: typeof TODO_UNIT_FOLD_SCHEMA_VERSION
  readonly operator: 'FoldToReceipt' | 'KeepRaw'
  readonly status: 'committed' | 'fallback_keep_raw' | 'disabled'
  readonly fallbackReason: string | null
  readonly preservesRawState: boolean
  readonly preservesPrefixKv: boolean
  readonly todoId: string
  readonly turnInstanceId: string
  readonly contextEpoch: string
  readonly prefixDigest: string | null
  readonly reclaimRange: readonly [number, number] | null
  readonly reclaimSeqRange: readonly [SessionSeq, SessionSeq] | null
  readonly foldedEventSeq: SessionSeq | null
  readonly receiptMu: TodoUnitReceiptMu | null
  readonly obsNextCmd: {
    readonly allowed: boolean
    readonly exitCode: number | null
    readonly truncated: boolean
    readonly output: string | null
  } | null
  readonly replacementText: string | null
  readonly preservedFailureFacts: readonly string[]
  readonly preservedOpenObligations: readonly string[]
  readonly baselineTokens: number | null
  readonly replacementTokens: number | null
  readonly netSavingsTokens: number | null
  readonly amortizedNetSavingsTokens: number | null
  readonly errorObservation: string | null
}

function sha256Hex(text: string): string {
  return createHash('sha256').update(text, 'utf8').digest('hex')
}

export function deriveMessagesPrefixDigest(
  session: Pick<Session, 'surface' | 'deriveMessages'>,
  prefixCount: number,
): string {
  const nodes = session.surface.nodes.slice(0, prefixCount)
  const messages = session.deriveMessages().slice(0, prefixCount)
  return sha256Hex(JSON.stringify({ nodes, messages }))
}

function approximateTokens(text: string): number {
  const bytes = Buffer.byteLength(text, 'utf8')
  return bytes === 0 ? 0 : Math.ceil(bytes / 4)
}

function cleanFacts(values: readonly string[] | undefined, limit: number): string[] {
  if (!Array.isArray(values)) return []
  const seen = new Set<string>()
  const out: string[] = []
  for (const item of values) {
    if (typeof item !== 'string') continue
    const trimmed = item.trim()
    if (!trimmed) continue
    const bounded = trimmed.length > 512 ? `${trimmed.slice(0, 509)}...` : trimmed
    if (seen.has(bounded)) continue
    seen.add(bounded)
    out.push(bounded)
    if (out.length >= limit) break
  }
  return out
}

function splitCommandTokens(rawCommand: string | readonly string[]): string[] | null {
  if (Array.isArray(rawCommand)) {
    const argv: string[] = []
    for (const entry of rawCommand) {
      if (typeof entry !== 'string' || !entry.trim()) return null
      argv.push(entry)
    }
    return argv.length > 0 ? argv : null
  }
  if (typeof rawCommand !== 'string') return null
  const trimmed = rawCommand.trim()
  if (!trimmed) return null
  const parts = trimmed.split(/\s+/u)
  return parts.length > 0 ? parts : null
}

function validateReadOnlyObsCommand(command: string | readonly string[], initiator: string): {
  allowed: boolean
  reason: string | null
  argv: string[]
} {
  if (initiator === 'history_view_policy' || initiator === 'passive_plugin') {
    return { allowed: false, reason: 'passive_plugin_blocked', argv: [] }
  }
  const argv = splitCommandTokens(command)
  if (!argv) return { allowed: false, reason: 'missing_prefetch_command', argv: [] }
  for (const tok of argv) {
    if (tok.includes('\n') || tok.includes('\r') || tok.includes('\0') || DISALLOWED_SHELL_OPERATORS.has(tok)) {
      return { allowed: false, reason: 'shell_metacharacters_disallowed', argv: [] }
    }
    if (tok === '--execute' || tok === '--spend' || tok === '--apply') {
      return { allowed: false, reason: 'mutating_flag_disallowed', argv: [] }
    }
  }
  const head = argv[0]?.split('/').pop() ?? ''
  if (head === 'git') {
    const sub = argv.slice(1).find(tok => !tok.startsWith('-')) ?? ''
    if (!READ_ONLY_GIT_SUBCOMMANDS.has(sub)) {
      return { allowed: false, reason: 'disallowed_git_command', argv: [] }
    }
    return { allowed: true, reason: null, argv }
  }
  if (READ_ONLY_PROBES.has(head)) {
    return { allowed: true, reason: null, argv }
  }
  if ((head === 'loopx' || head === 'python' || head === 'python3') && argv.includes('--readonly')) {
    return { allowed: true, reason: null, argv }
  }
  if (head === 'loopx' && argv.some((tok, idx) => tok === 'quota' && argv[idx + 1] === 'context-read')) {
    return { allowed: true, reason: null, argv }
  }
  return { allowed: false, reason: 'disallowed_prefetch_command', argv: [] }
}

function toolCallIdsFromAssistantMessage(message: AssistantMessage): string[] {
  const ids: string[] = []
  for (const block of message.content) {
    if (typeof block !== 'object' || block === null) continue
    const candidate = block as { type?: unknown; id?: unknown; callId?: unknown }
    if (candidate.type !== 'tool-call') continue
    const callId = typeof candidate.id === 'string'
      ? candidate.id
      : typeof candidate.callId === 'string'
        ? candidate.callId
        : ''
    if (callId) ids.push(callId)
  }
  return ids
}

export function hasValidDshToolCallPairing(
  session: Pick<Session, 'surface' | 'snapshotEvents' | 'deriveEventMessage'>,
  prefixCount: number,
): boolean {
  const nodes = session.surface.nodes
  if (prefixCount < 0 || prefixCount > nodes.length) return false
  const events = session.snapshotEvents()
  const eventBySeq = new Map<SessionSeq, SessionEvent>()
  for (const ev of events) {
    eventBySeq.set(ev.seq, ev)
  }
  const retainedSet = new Set(nodes.slice(0, prefixCount))
  const shadowedSet = new Set(nodes.slice(prefixCount))

  const callToTurnStep = new Map<string, string>()
  const assistantCallsByGroup = new Map<string, string[]>()
  const resultSeqByCallId = new Map<string, SessionSeq>()

  for (const ev of events) {
    if (ev.type === 'tool/call') {
      const key = `${ev.data.turn}:${ev.data.step}`
      callToTurnStep.set(ev.data.callId, key)
    } else if (ev.type === 'assistant/message') {
      if (!retainedSet.has(ev.seq) && !shadowedSet.has(ev.seq)) continue
      const key = `${ev.data.turn}:${ev.data.step}`
      const msg = session.deriveEventMessage(ev) as AssistantMessage | null
      const blockIds = msg ? toolCallIdsFromAssistantMessage(msg) : []
      if (blockIds.length > 0) {
        assistantCallsByGroup.set(key, blockIds)
        for (const id of blockIds) {
          callToTurnStep.set(id, key)
        }
      }
    } else if (ev.type === 'tool/result') {
      if (!retainedSet.has(ev.seq) && !shadowedSet.has(ev.seq)) continue
      resultSeqByCallId.set(ev.data.message.toolCallId, ev.seq)
    }
  }

  for (const seq of nodes) {
    const ev = eventBySeq.get(seq)
    if (!ev) return false
    const inShadowed = shadowedSet.has(seq)
    if (ev.type === 'assistant/message') {
      const key = `${ev.data.turn}:${ev.data.step}`
      const blockIds = assistantCallsByGroup.get(key) ?? []
      for (const callId of blockIds) {
        const resSeq = resultSeqByCallId.get(callId)
        if (resSeq === undefined) return false
        if (shadowedSet.has(resSeq) !== inShadowed) return false
      }
    } else if (ev.type === 'tool/result') {
      const callId = (ev.data.message as ToolResultMessage).toolCallId
      const groupKey = callToTurnStep.get(callId)
      let matchedAssistantInRetained = false
      let matchedAssistantInShadowed = false
      for (const otherSeq of nodes) {
        const other = eventBySeq.get(otherSeq)
        if (other?.type !== 'assistant/message') continue
        if (`${other.data.turn}:${other.data.step}` === groupKey) {
          if (shadowedSet.has(otherSeq)) matchedAssistantInShadowed = true
          else matchedAssistantInRetained = true
        }
      }
      if (inShadowed && matchedAssistantInRetained) return false
      if (!inShadowed && matchedAssistantInShadowed) return false
      if (!matchedAssistantInRetained && !matchedAssistantInShadowed) return false
    }
  }

  const firstShadowedSeq = nodes[prefixCount] ?? Number.MAX_SAFE_INTEGER
  for (const [callId, groupKey] of callToTurnStep.entries()) {
    const assistantLive = nodes.some(seq => {
      const ev = eventBySeq.get(seq)
      return ev?.type === 'assistant/message' && `${ev.data.turn}:${ev.data.step}` === groupKey
    })
    const suffixCall = events.some(
      ev => ev.type === 'tool/call' && ev.data.callId === callId && ev.seq >= firstShadowedSeq,
    )
    if ((assistantLive || suffixCall) && !resultSeqByCallId.has(callId)) return false
  }
  return true
}

export function recordDshTodoUnitAnchor(
  session: Pick<Session, 'surface' | 'deriveMessages'>,
  input: TodoUnitAnchorInput,
): TodoUnitAnchor {
  if (!input.todoId.trim()) throw new Error('todoId must be non-empty')
  if (!input.turnInstanceId.trim()) throw new Error('turnInstanceId must be non-empty')
  if (!input.contextEpoch.trim()) throw new Error('contextEpoch must be non-empty')
  if (!Number.isSafeInteger(input.anchorStepIndex) || input.anchorStepIndex < 0) {
    throw new Error('anchorStepIndex must be a non-negative integer')
  }
  const nodes = session.surface.nodes
  let prefixNodeCount = nodes.length
  if (input.anchorSurfaceSeq !== undefined) {
    const idx = nodes.indexOf(input.anchorSurfaceSeq)
    if (idx < 0) throw new Error('anchorSurfaceSeq not found in current session surface')
    prefixNodeCount = idx
  }
  const prefixSurfaceSeqs = nodes.slice(0, prefixNodeCount)
  const prefixDigest = deriveMessagesPrefixDigest(session, prefixNodeCount)
  const core = {
    schemaVersion: TODO_UNIT_ANCHOR_SCHEMA_VERSION,
    todoId: input.todoId.trim(),
    turnInstanceId: input.turnInstanceId.trim(),
    contextEpoch: input.contextEpoch.trim(),
    anchorStepIndex: input.anchorStepIndex,
    prefixNodeCount,
    prefixSurfaceSeqs,
    ...(input.anchorSurfaceSeq !== undefined ? { anchorSurfaceSeq: input.anchorSurfaceSeq } : {}),
    prefixDigest,
  }
  const anchorSha256 = sha256Hex(JSON.stringify(core))
  return { ...core, anchorSha256 }
}

export function applyTodoUnitFoldAndPrefetch(
  session: Session,
  policy: TodoUnitFoldPolicy | undefined,
  request: TodoUnitFoldRequest,
  storedAnchor?: TodoUnitAnchor,
): TodoUnitFoldOutcome {
  const receipt = request.completionReceipt
  const preservedFailureFacts = cleanFacts(receipt?.failureFacts ?? request.failureFacts, 32)
  const preservedOpenObligations = cleanFacts(receipt?.openObligations ?? request.openObligations, 64)

  const keepRawOutcome = (
    status: 'disabled' | 'fallback_keep_raw',
    reason: string,
    baselineTokens: number | null = request.baselineTokens ?? null,
    replacementTokens: number | null = request.replacementTokens ?? null,
    prefixDigest: string | null = null,
    obsMeta: TodoUnitFoldOutcome['obsNextCmd'] = null,
  ): TodoUnitFoldOutcome => ({
    schemaVersion: TODO_UNIT_FOLD_SCHEMA_VERSION,
    operator: 'KeepRaw',
    status,
    fallbackReason: reason,
    preservesRawState: true,
    preservesPrefixKv: true,
    todoId: request.todoId,
    turnInstanceId: request.turnInstanceId,
    contextEpoch: request.contextEpoch,
    prefixDigest,
    reclaimRange: null,
    reclaimSeqRange: null,
    foldedEventSeq: null,
    receiptMu: null,
    obsNextCmd: obsMeta,
    replacementText: null,
    preservedFailureFacts,
    preservedOpenObligations,
    baselineTokens,
    replacementTokens,
    netSavingsTokens: null,
    amortizedNetSavingsTokens: null,
    errorObservation: status === 'disabled'
      ? null
      : `Todo-unit fold rolled back atomically to KeepRaw (${reason}).`,
  })

  if (policy?.enabled !== true) {
    return keepRawOutcome('disabled', 'disabled')
  }
  if (request.quiescentBoundary === false) {
    return keepRawOutcome('fallback_keep_raw', 'non_quiescent_boundary')
  }

  const anchor = request.anchor ?? storedAnchor
  if (!anchor) {
    return keepRawOutcome('fallback_keep_raw', 'missing_anchor')
  }
  const expectedAnchorCore = {
    schemaVersion: anchor.schemaVersion,
    todoId: anchor.todoId,
    turnInstanceId: anchor.turnInstanceId,
    contextEpoch: anchor.contextEpoch,
    anchorStepIndex: anchor.anchorStepIndex,
    prefixNodeCount: anchor.prefixNodeCount,
    prefixSurfaceSeqs: anchor.prefixSurfaceSeqs,
    ...(anchor.anchorSurfaceSeq !== undefined ? { anchorSurfaceSeq: anchor.anchorSurfaceSeq } : {}),
    prefixDigest: anchor.prefixDigest,
  }
  if (anchor.anchorSha256 !== sha256Hex(JSON.stringify(expectedAnchorCore))) {
    return keepRawOutcome('fallback_keep_raw', 'anchor_digest_mismatch')
  }
  if (anchor.todoId !== request.todoId) {
    return keepRawOutcome('fallback_keep_raw', 'todo_id_mismatch')
  }
  if (anchor.turnInstanceId !== request.turnInstanceId) {
    return keepRawOutcome('fallback_keep_raw', 'turn_instance_mismatch')
  }
  if (anchor.contextEpoch !== request.contextEpoch) {
    return keepRawOutcome('fallback_keep_raw', 'context_epoch_mismatch')
  }
  if (!Number.isSafeInteger(request.nowStepIndex) || request.nowStepIndex < anchor.anchorStepIndex) {
    return keepRawOutcome('fallback_keep_raw', 'invalid_reclaim_range')
  }

  const nodes = session.surface.nodes
  if (nodes.length <= anchor.prefixNodeCount) {
    return keepRawOutcome('fallback_keep_raw', 'empty_reclaim_suffix')
  }
  const currentPrefixSeqs = nodes.slice(0, anchor.prefixNodeCount)
  if (
    currentPrefixSeqs.length !== anchor.prefixSurfaceSeqs.length
    || currentPrefixSeqs.some((seq, i) => seq !== anchor.prefixSurfaceSeqs[i])
  ) {
    return keepRawOutcome('fallback_keep_raw', 'prefix_kv_not_preserved')
  }
  const livePrefixDigest = deriveMessagesPrefixDigest(session, anchor.prefixNodeCount)
  if (livePrefixDigest !== anchor.prefixDigest) {
    return keepRawOutcome('fallback_keep_raw', 'prefix_digest_mismatch', undefined, undefined, livePrefixDigest)
  }

  const shadowedSeqs = nodes.slice(anchor.prefixNodeCount)
  const startSeq = shadowedSeqs[0]
  const endSeq = shadowedSeqs.at(-1)
  if (startSeq === undefined || endSeq === undefined) {
    return keepRawOutcome('fallback_keep_raw', 'empty_reclaim_suffix', undefined, undefined, livePrefixDigest)
  }
  if (anchor.anchorSurfaceSeq !== undefined && anchor.anchorSurfaceSeq !== startSeq) {
    return keepRawOutcome('fallback_keep_raw', 'invalid_reclaim_range', undefined, undefined, livePrefixDigest)
  }
  if (anchor.prefixNodeCount === 0) {
    const firstEv = session.snapshotEvents().find(ev => ev.seq === startSeq)
    if (firstEv?.type === 'system/message') {
      return keepRawOutcome('fallback_keep_raw', 'prefix_kv_not_preserved', undefined, undefined, livePrefixDigest)
    }
  }
  if (!hasValidDshToolCallPairing(session, anchor.prefixNodeCount)) {
    return keepRawOutcome('fallback_keep_raw', 'unpaired_tool_calls', undefined, undefined, livePrefixDigest)
  }

  if (!receipt) {
    return keepRawOutcome('fallback_keep_raw', 'missing_completion_receipt', undefined, undefined, livePrefixDigest)
  }
  if (!receipt.todoId.trim() || !receipt.completionReceiptId.trim()) {
    return keepRawOutcome('fallback_keep_raw', 'invalid_completion_receipt', undefined, undefined, livePrefixDigest)
  }
  if (receipt.todoId.trim() !== request.todoId) {
    return keepRawOutcome('fallback_keep_raw', 'receipt_todo_mismatch', undefined, undefined, livePrefixDigest)
  }
  if (!BARE_SHA256_RE.test(receipt.sha256)) {
    return keepRawOutcome('fallback_keep_raw', 'invalid_completion_sha256', undefined, undefined, livePrefixDigest)
  }
  if (!receipt.sourceRef.path.trim() || !BARE_SHA256_RE.test(receipt.sourceRef.sha256)) {
    return keepRawOutcome('fallback_keep_raw', 'missing_source_ref', undefined, undefined, livePrefixDigest)
  }
  if (!Number.isSafeInteger(receipt.exitCode) || receipt.exitCode !== 0) {
    return keepRawOutcome('fallback_keep_raw', 'command_error', undefined, undefined, livePrefixDigest)
  }

  const maxObsBytes = policy.maxObsOutputBytes ?? DEFAULT_TODO_FOLD_MAX_OBS_BYTES
  const rejectTruncation = policy.rejectOnTruncation ?? true
  let obsMeta: TodoUnitFoldOutcome['obsNextCmd'] = null
  let obsText: string | null = null
  if (request.obsNextCmd !== undefined) {
    const obs = request.obsNextCmd
    const safety = validateReadOnlyObsCommand(obs.command, obs.initiator ?? 'turn_driver')
    const exitCode = obs.exitCode ?? null
    const truncated = obs.truncated === true
    const output = obs.output ?? ''
    let obsFail: string | null = null
    if (!safety.allowed) obsFail = safety.reason
    else if (obs.timedOut === true) obsFail = 'obs_command_timeout'
    else if (exitCode === null || exitCode !== 0) obsFail = 'command_error'
    else if ((obs.rejectOnTruncation ?? rejectTruncation) && truncated) obsFail = 'output_truncated'
    else if (Buffer.byteLength(output, 'utf8') > maxObsBytes) obsFail = 'output_overflow'
    obsMeta = {
      allowed: safety.allowed,
      exitCode,
      truncated,
      output: obsFail === null ? output : null,
    }
    if (obsFail !== null) {
      return keepRawOutcome('fallback_keep_raw', obsFail, undefined, undefined, livePrefixDigest, obsMeta)
    }
    obsText = output.length > 0 ? output : null
  }

  const reclaimRange: readonly [number, number] = [anchor.anchorStepIndex, request.nowStepIndex]
  const reclaimSeqRange: readonly [SessionSeq, SessionSeq] = [startSeq, endSeq]
  const receiptMu: TodoUnitReceiptMu = {
    trustLevel: 'tool',
    todoId: request.todoId,
    completionReceiptId: receipt.completionReceiptId.trim(),
    sha256: receipt.sha256,
    turnInstanceId: request.turnInstanceId,
    contextEpoch: request.contextEpoch,
    prefixDigest: livePrefixDigest,
    reclaimRange,
    reclaimSeqRange,
    exitCode: receipt.exitCode,
    failureFacts: preservedFailureFacts,
    openObligations: preservedOpenObligations,
    sourceRef: receipt.sourceRef,
  }
  const lines = [
    '[Semantic-Unit Fold Receipt mu | trust=tool]',
    `todo_id: ${receiptMu.todoId}`,
    `completion_receipt_id: ${receiptMu.completionReceiptId}`,
    `completion_sha256: ${receiptMu.sha256}`,
    `turn_instance_id: ${receiptMu.turnInstanceId}`,
    `context_epoch: ${receiptMu.contextEpoch}`,
    `prefix_digest: ${receiptMu.prefixDigest}`,
    `reclaim_range: [${reclaimRange[0]}..${reclaimRange[1]}]`,
    `exit_code: ${receiptMu.exitCode}`,
    `source_ref: ${receiptMu.sourceRef.path} (sha256:${receiptMu.sourceRef.sha256})`,
    `failure_facts: ${JSON.stringify(receiptMu.failureFacts)}`,
    `open_obligations: ${JSON.stringify(receiptMu.openObligations)}`,
  ]
  if (obsText !== null) {
    lines.push('--- obs_next_cmd ---', obsText)
  }
  const replacementText = lines.join('\n')

  const suffixDerivedMessages = session.deriveMessages().slice(anchor.prefixNodeCount)
  const baselineTokens = request.baselineTokens ?? approximateTokens(JSON.stringify(suffixDerivedMessages))
  const replacementTokens = request.replacementTokens ?? approximateTokens(replacementText)
  const minSavings = policy.minNetSavingsTokens ?? DEFAULT_TODO_FOLD_MIN_NET_SAVINGS_TOKENS
  const amortization =
    policy.coldPrefillAmortizationTokens
    ?? Math.ceil(replacementTokens / DEFAULT_TODO_FOLD_AMORTIZATION_DIVISOR)
  const rawNet = baselineTokens - replacementTokens
  const amortizedNet = rawNet - amortization
  if (rawNet <= 0 || amortizedNet < minSavings) {
    return keepRawOutcome(
      'fallback_keep_raw',
      'negative_net_savings',
      baselineTokens,
      replacementTokens,
      livePrefixDigest,
      obsMeta,
    )
  }

  const foldMessage: UserMessage = {
    id: randomUUID() as UserMessage['id'],
    role: 'user',
    content: [{ type: 'text', text: replacementText }],
    source: {
      kind: 'loopx-todo-fold',
      schemaVersion: TODO_UNIT_FOLD_SCHEMA_VERSION,
      form: 'recall',
      todoId: receiptMu.todoId,
      turnInstanceId: receiptMu.turnInstanceId,
      completionReceiptId: receiptMu.completionReceiptId,
      completionSha256: receiptMu.sha256,
      contextEpoch: receiptMu.contextEpoch,
      prefixDigest: livePrefixDigest,
      reclaimedSeqRange: reclaimSeqRange,
    },
  }

  let committedEvent: SessionEvent<'user/message'>
  try {
    committedEvent = session.append('user/message', foldMessage, {
      surfaceOp: { op: 'replace', startSeq, endSeq },
      sourceEventSeqs: [...shadowedSeqs],
    })
  } catch {
    return keepRawOutcome(
      'fallback_keep_raw',
      'surface_replace_rejected',
      baselineTokens,
      replacementTokens,
      livePrefixDigest,
      obsMeta,
    )
  }

  const postPrefixDigest = deriveMessagesPrefixDigest(session, anchor.prefixNodeCount)
  const prefixPreserved = postPrefixDigest === anchor.prefixDigest
  return {
    schemaVersion: TODO_UNIT_FOLD_SCHEMA_VERSION,
    operator: 'FoldToReceipt',
    status: 'committed',
    fallbackReason: null,
    preservesRawState: false,
    preservesPrefixKv: prefixPreserved,
    todoId: request.todoId,
    turnInstanceId: request.turnInstanceId,
    contextEpoch: request.contextEpoch,
    prefixDigest: postPrefixDigest,
    reclaimRange,
    reclaimSeqRange,
    foldedEventSeq: committedEvent.seq,
    receiptMu,
    obsNextCmd: obsMeta,
    replacementText,
    preservedFailureFacts,
    preservedOpenObligations,
    baselineTokens,
    replacementTokens,
    netSavingsTokens: rawNet,
    amortizedNetSavingsTokens: amortizedNet,
    errorObservation: null,
  }
}

export type TodoUnitObsRunner = (
  command: readonly string[],
  cwd: string,
) => Promise<{
  readonly exitCode: number | null
  readonly output: string
  readonly timedOut?: boolean | undefined
  readonly truncated?: boolean | undefined
}>

export function validateTodoUnitObsForExecution(
  obs: TodoUnitObsNextCommand | undefined,
): readonly string[] | null {
  if (!obs) return null
  if (obs.output !== undefined && obs.exitCode !== undefined) return null
  const safety = validateReadOnlyObsCommand(obs.command, obs.initiator ?? 'turn_driver')
  return safety.allowed ? safety.argv : null
}

function asObj(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined
}

export function extractTodoIdFromQuotaAndHeartbeat(
  quota: Record<string, unknown>,
  heartbeat?: Record<string, unknown>,
): string | undefined {
  const receiptIdentity = asObj(asObj(quota.heartbeat_receipt)?.settlement_identity)
  const candidates = [
    receiptIdentity?.todo_id,
    asObj(quota.selected_todo)?.todo_id,
    asObj(heartbeat?.selected_todo)?.todo_id,
    heartbeat?.todo_id,
  ]
  for (const candidate of candidates) {
    if (typeof candidate === 'string' && candidate.trim().length > 0) {
      return candidate.trim()
    }
  }
  return undefined
}

export type DecodedQuotaSettlement =
  | { readonly verified: true; readonly completionReceipt: TodoUnitCompletionReceipt }
  | { readonly verified: false; readonly reason: string }

export function decodeQuotaSettlementReadback(
  payload: Record<string, unknown>,
  expectedGoalId: string,
  expectedAgentId: string,
  expectedTurnInstanceId: string,
  expectedTodoId?: string,
): DecodedQuotaSettlement {
  if (
    payload.schema_version !== 'quota_receipt_readback_v0'
    || payload.ok !== true
    || payload.goal_id !== expectedGoalId
    || payload.agent_id !== expectedAgentId
    || payload.turn_instance_id !== expectedTurnInstanceId
  ) {
    return { verified: false, reason: 'invalid_settlement_identity' }
  }
  if (payload.settlement_verified !== true) {
    return {
      verified: false,
      reason: typeof payload.settlement_conflict_reason === 'string'
        ? payload.settlement_conflict_reason
        : 'unsettled_turn',
    }
  }
  const raw = asObj(payload.completion_receipt)
  const rawSource = asObj(raw?.source_ref)
  const todoId = typeof raw?.todo_id === 'string' ? raw.todo_id.trim() : ''
  const receiptId = typeof raw?.completion_receipt_id === 'string' ? raw.completion_receipt_id.trim() : ''
  const sha256 = typeof raw?.sha256 === 'string' ? raw.sha256 : ''
  const sourcePath = typeof rawSource?.path === 'string' ? rawSource.path.trim() : ''
  const sourceSha = typeof rawSource?.sha256 === 'string' ? rawSource.sha256 : ''
  if (
    !todoId
    || (expectedTodoId !== undefined && expectedTodoId !== todoId)
    || !receiptId
    || raw?.exit_code !== 0
    || !BARE_SHA256_RE.test(sha256)
    || !sourcePath
    || !BARE_SHA256_RE.test(sourceSha)
  ) {
    return { verified: false, reason: 'invalid_completion_receipt' }
  }
  return {
    verified: true,
    completionReceipt: {
      todoId,
      completionReceiptId: receiptId,
      exitCode: 0,
      sha256,
      failureFacts: cleanFacts(Array.isArray(raw?.failure_facts) ? raw.failure_facts as string[] : undefined, 32),
      openObligations: cleanFacts(Array.isArray(raw?.open_obligations) ? raw.open_obligations as string[] : undefined, 64),
      sourceRef: { path: sourcePath, sha256: sourceSha },
    },
  }
}

export function createDefaultDshObsRunner(runner: FileRunner): TodoUnitObsRunner {
  return async (argv, cwd) => {
    const file = argv[0]
    if (!file) {
      return { exitCode: 127, output: '' }
    }
    try {
      const res = await runner(file, argv.slice(1), {
        cwd,
        timeoutMs: 5_000,
        maxOutputBytes: DEFAULT_TODO_FOLD_MAX_OBS_BYTES,
      })
      return {
        exitCode: res.exitCode,
        output: res.stdout,
        timedOut: false,
        truncated: Buffer.byteLength(res.stdout, 'utf8') > DEFAULT_TODO_FOLD_MAX_OBS_BYTES,
      }
    } catch {
      return { exitCode: null, output: '', timedOut: true }
    }
  }
}

export type { FileRunner, LoopXCommand }
